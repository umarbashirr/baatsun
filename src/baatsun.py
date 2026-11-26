#!/usr/bin/env python3
"""Background voice-dictation daemon.

Listens on a unix socket for a "toggle" command (sent by bin/baatsun-toggle,
which is bound to a GNOME keyboard shortcut). First toggle starts recording
audio via pw-record; second toggle stops it, transcribes with a local
faster-whisper model, and types the result into the focused window via
ydotool.
"""
import array
import json
import math
import os
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave

from evdev import InputDevice, ecodes, list_devices

import baatsun_cleanup
import baatsun_context
import baatsun_insert
import baatsun_stt
import baatsun_voice
from baatsun_config import (
    ACTIVATION_CHOICES,
    CONFIG_PATH,
    DEFAULT_CLEANUP_MODEL,
    DEFAULT_CLEANUP_REASONING,
    WHISPER_LANGUAGE,
    cleanup_ready,
    load_api_key,
    load_config,
    load_stt_api_key,
    resolve_beam_size,
    resolve_model,
    resolve_spoken_language,
    resolve_stt_backend,
)

config = load_config()

MODEL_SIZE = os.environ.get("BAATSUN_MODEL") or resolve_model(config)
COMPUTE_TYPE = os.environ.get("BAATSUN_COMPUTE_TYPE") or config["compute_type"]
# Resolved once, because it decides whether the local model is loaded at all —
# and loading it is what costs the ~570 MB the remote backend exists to avoid.
# The Settings panel restarts the daemon when this changes, the same way it
# does for the model.
STT_BACKEND = os.environ.get("BAATSUN_STT_BACKEND") or resolve_stt_backend(config)
# Read once: the model is loaded for it (see resolve_model).
HINGLISH = bool(config.get("hinglish"))
# Pinned to English even for Hinglish — see baatsun_stt.transcribe: unpinned,
# Scribe writes the Hindi parts in Devanagari, and the output here is English.
SCRIBE_LANGUAGE = WHISPER_LANGUAGE
# The language you speak, read once like the model it decides. None means
# detect it on each dictation. Hinglish still decodes as English (see above).
SPOKEN_LANGUAGE = resolve_spoken_language(config)
if SPOKEN_LANGUAGE != WHISPER_LANGUAGE:
    SCRIBE_LANGUAGE = SPOKEN_LANGUAGE
# Same path every other component uses: $XDG_RUNTIME_DIR, which systemd sets
# to /run/user/$UID for a user service.
SOCKET_PATH = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "baatsun.sock")
# Transcripts stay out of the journal unless you ask for them: the journal
# outlives "Clear history", and the README promises nothing else is kept.
LOG_TRANSCRIPTS = os.environ.get("BAATSUN_LOG_TRANSCRIPTS") == "1"
SAMPLE_RATE = "16000"

KEY_GROUPS = {
    "ctrl": {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL},
    "super": {ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA},
    "alt": {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT},
    "shift": {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT},
}


def resolve_hotkey(name):
    try:
        primary, secondary = name.split("+")
        return KEY_GROUPS[primary], KEY_GROUPS[secondary]
    except (ValueError, KeyError):
        print(f"[baatsun] invalid hotkey {name!r}, falling back to ctrl+super", file=sys.stderr)
        return KEY_GROUPS["ctrl"], KEY_GROUPS["super"]


PRIMARY_KEYS, SECONDARY_KEYS = resolve_hotkey(config["hotkey"])
# Held together with the hotkey, it turns a dictation into a voice edit of the
# selected text. Shift, unless the hotkey already uses Shift, then Alt.
COMMAND_KEYS = (KEY_GROUPS["alt"] if KEY_GROUPS["shift"] in (PRIMARY_KEYS, SECONDARY_KEYS)
                else KEY_GROUPS["shift"])

# What a recording is for. Set when it starts (and upgraded to a voice edit if
# the modifier joins mid-recording), and carried through to the finish thread.
DICTATE, COMMAND, NOTES = "dictate", "command", "notes"


def resolve_activation(name):
    if name in ACTIVATION_CHOICES:
        return name
    print(f"[baatsun] invalid activation {name!r}, falling back to hold", file=sys.stderr)
    return "hold"


ACTIVATION = resolve_activation(config.get("activation"))

HISTORY_DIR = os.path.expanduser("~/.local/share/baatsun")
HISTORY_PATH = os.path.join(HISTORY_DIR, "history.json")
HISTORY_LIMIT = 500

# Backstop for toggle mode, where nothing structurally ends a recording the way
# releasing the key does. Without it, a start you didn't notice runs until you
# do — filling /tmp at ~2 MB a minute and then handing whisper an hour of audio
# to chew through. Well past any real dictation, so it only ever catches
# mistakes.
MAX_RECORDING_SECONDS = 15 * 60

state_lock = threading.Lock()
state = {
    "recording": False,
    "proc": None,       # pw-record subprocess
    "wav_path": None,
    "timer": None,      # MAX_RECORDING_SECONDS watchdog for the current recording
    "started": None,    # monotonic clock at the start, for the recorded duration
    # A transcription is in flight on the finish thread. Recording and
    # transcribing are separate phases now (see finish_recording), so
    # "not recording" no longer means "ready to record".
    "finishing": False,
    # ElevenLabs live slicer, if this recording is being chopped into 20s
    # Scribe calls while the mic is still open. None for the local backend.
    "capture": None,
    "mode": DICTATE,
    # Where the text will land, as the recording started, for the pill.
    "surface": None,
}
# The last text this daemon put into a window, so "scratch that" can take it
# back. Only honoured in the same window, and only for a while.
last_insert_lock = threading.Lock()
last_insert = {"app": None, "text": "", "ts": 0.0}
SCRATCH_WINDOW_SECONDS = 10 * 60
# Windows the daemon has seen focused recently, for the Apps page: the place to
# check how each is classified and teach it otherwise. In memory only.
recent_windows_lock = threading.Lock()
recent_windows = []
RECENT_WINDOWS_LIMIT = 12
# The thread running the current transcription, so shutdown can wait for it to
# type what it has rather than exiting mid-dictation.
finish_thread = None
# How long that wait is allowed to take. A live ElevenLabs slice is 30s
# worst case and cleanup scales up to 90s on a 10-minute dump; this sits
# above that pair. systemd's default TimeoutStopSec is 90s, so a stuck
# cleanup can still get SIGKILL on shutdown — better than blocking logout.
FINISH_JOIN_SECONDS = 120
hotkey_state = {
    "pressed": {},        # device path -> keycodes currently held on it
    "held": False,        # whether the ctrl+meta combo is currently active
    "press_time": 0.0,    # monotonic time the combo was last formed (hybrid)
    "latched": False,     # hybrid: a tap left this recording running
}

# Keyboards are found by scanning /dev/input, and that scan used to run once at
# startup. Anything plugged in later was invisible, and unplugging a keyboard
# killed its watcher for good — so after one unplug/replug the hotkey quietly
# worked on the built-in keyboard only. Rescanning on a short interval picks up
# both cases. Unplugging also renumbers the event node, so watchers are tracked
# by path rather than by device.
DEVICE_SCAN_SECONDS = 2.0
DEVICE_DIR = "/dev/input"
watched_lock = threading.Lock()
watched_paths = set()

# Devices this daemon must never watch, matched against the device name in
# lowercase.
#
# ydotool types every transcript through a uinput device it creates for the call
# and tears down when it exits. That device advertises a full keyboard, so the
# scan above adopts it and the daemon spends the length of a transcript watching
# itself type. Two things follow, one cosmetic and one not:
#
#   - the node disappears the moment ydotool exits, so every dictation left a
#     "lost keyboard device /dev/input/eventN: [Errno 19] No such device" in the
#     journal, which is what sent us looking here;
#   - refresh_chord() pools held keys across *all* watched devices, so injected
#     keystrokes are indistinguishable from the real keyboard's. ydotool holds
#     shift down for every capital letter, so with a shift-based hotkey
#     (ctrl+shift, super+shift) typing a transcript into a window while the user
#     happens to be resting a finger on ctrl fires the chord — the app triggers
#     a dictation off its own output. The default ctrl+super chord never collides
#     with plain typing, which is why this survived this long.
#
# Matched by name rather than by BUS_VIRTUAL, deliberately: plenty of legitimate
# keyboards are virtual (remote desktop, Synergy/Barrier, on-screen keyboards)
# and excluding the whole bus would break the hotkey for those users to fix a
# problem this daemon causes itself.
INJECTOR_DEVICE_NAMES = ("ydotool",)

# Hybrid mode's dividing line between a tap and a hold. Below it, releasing the
# chord leaves the recording running until the next press; above it, the release
# ends the recording the way push-to-talk always has. Set so that a deliberate
# tap lands well under and the shortest useful held dictation — "yes", "no", a
# file name — lands well over.
TAP_SECONDS = 0.4

# One command per connection, and the only unbounded one is "focus" (a window
# title). 64 KB swallows any real title in a single read; the deadline below is
# only for the case where the stream split one anyway.
MAX_COMMAND_BYTES = 64 * 1024
FOCUS_READ_TIMEOUT = 0.5

# Last window the GNOME Shell extension told us had focus, used to decide
# whether a transcript is prose worth cleaning up or a coding prompt that must
# be typed verbatim. Stays empty on desktops without the extension, which
# baatsun_context reads as "developer" — the safe direction. Guarded by its own
# lock: it is written from socket threads and read mid-transcription.
focus_lock = threading.Lock()
focus = {"app": "", "title": ""}
# The state a newly-connected subscriber should be told about before anything
# else happens. Without this a GUI opened mid-transcription shows "Ready" until
# the next state change, which is the one moment it most needs to be right.
# Carries "type" from the start: it is sent verbatim to new subscribers, and a
# payload without it is silently ignored by every client's event dispatch.
current_state = {"type": "state", "state": "idle"}
current_state_lock = threading.Lock()

# Transcript history, newest-last, persisted to HISTORY_PATH. Guarded by
# history_lock, which is separate from state_lock so broadcasting to GUI
# subscribers never has to nest under the recording-state lock.
history_lock = threading.Lock()
history = []
next_entry_id = 1

# Sockets of connected "subscribe" clients (the GUI/tray apps), each fed a
# newline-delimited JSON event stream. Guarded by subscribers_lock.
subscribers_lock = threading.Lock()
subscribers = []
# Whole seconds, because that is what a struct timeval takes. Generous for a
# local socket to a client that is actually reading — anything approaching this
# means the client has stopped.
SUBSCRIBER_SEND_TIMEOUT = 5

model = None  # loaded lazily in main() before serving


def load_history():
    global history, next_entry_id
    try:
        with open(HISTORY_PATH, "r") as f:
            history = json.load(f)
    except (FileNotFoundError, ValueError):
        history = []
    next_entry_id = (max((e["id"] for e in history), default=0)) + 1


def save_history():
    # 0700 / 0600 like the API keys: this is everything you have ever said.
    os.makedirs(HISTORY_DIR, mode=0o700, exist_ok=True)
    tmp_path = HISTORY_PATH + ".tmp"
    try:
        os.remove(tmp_path)
    except OSError:
        pass
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(history, f)
    os.replace(tmp_path, HISTORY_PATH)


def add_history_entry(text, raw=None, app=None, context=None, secs=None,
                      usage=None, stt=None, mode=None, surface=None,
                      force_raw=False):
    """Record a transcript, keeping the pre-cleanup text when it differed.

    "raw" is omitted when cleanup made no change or didn't run, so the common
    entry stays the shape it has always been and the file doesn't double in
    size. When it is present the GUI offers to show it: cleanup rewords things,
    and you can't audit a rewrite you can't see next to the original.

    "app"/"context" say where this landed and how it was treated. Both are
    omitted when unknown rather than written empty, so an entry from a desktop
    with no focus reporting stays the shape it has always been, and so the GUI
    can tell "typed into nothing we could name" apart from "typed into a window
    called empty string". Entries written before this existed simply lack them.
    """
    global next_entry_id
    with history_lock:
        entry = {"id": next_entry_id, "text": text, "ts": time.time()}
        if raw is not None and (raw != text or force_raw):
            entry["raw"] = raw
        # Not a plain dictation: "snippet", "edit", "compose" or "notes".
        if mode:
            entry["mode"] = mode
        # Where it landed, by kind of place, for the Home page's breakdown.
        if surface:
            entry["surface"] = surface
        if app:
            entry["app"] = app
        if context:
            entry["context"] = context
        # How long the mic was actually open. Lets the Home page compute time
        # saved from what happened rather than from an assumed speaking rate;
        # entries without it fall back to an estimate.
        if secs:
            entry["secs"] = round(secs, 1)
        # What the cleanup call cost, straight from OpenAI's own count. Only
        # present on dictations that actually made a request, so the Home page
        # can tell a call that cost very little apart from one that never
        # happened, and older entries — written before this was recorded — fall
        # back to an estimate.
        if usage:
            entry["usage"] = usage
        # Which backend transcribed this and what it cost. "local" entries carry
        # a zero cost rather than nothing at all — see _finish_recording — so
        # the only entries without this are the ones written before it existed,
        # and the cost report can report those as unattributed instead of
        # silently counting them as free.
        if stt:
            entry["stt"] = stt
        next_entry_id += 1
        history.append(entry)
        del history[:-HISTORY_LIMIT]
        save_history()
    return entry


def clear_history():
    with history_lock:
        history.clear()
        save_history()


def delete_history_entry(entry_id):
    with history_lock:
        for i, entry in enumerate(history):
            if entry["id"] == entry_id:
                del history[i]
                save_history()
                return True
        return False


def find_history_entry(entry_id):
    with history_lock:
        for entry in history:
            if entry["id"] == entry_id:
                return entry
        return None


def broadcast(event):
    # The lock stays held across the sends: sendall() is not atomic, and two
    # broadcasts interleaving on one socket would corrupt the newline-delimited
    # stream the clients parse. What makes that safe is SUBSCRIBER_SEND_TIMEOUT
    # on each subscriber (see handle_subscribe) — without it a client that had
    # stopped reading would block here forever, and since this is on the
    # transcription path, one wedged GUI would take dictation down with it.
    payload = (json.dumps(event) + "\n").encode()
    with subscribers_lock:
        dead = []
        for sock in subscribers:
            try:
                sock.sendall(payload)
            except OSError:
                dead.append(sock)
        for sock in dead:
            subscribers.remove(sock)


def broadcast_state(state, **extra):
    """Announce a state change and remember it for late subscribers."""
    event = {"type": "state", "state": state, **extra}
    with current_state_lock:
        current_state.clear()
        current_state.update(event)
    broadcast(event)


def focus_event():
    """The focus payload sent to subscribers: where text will land, and how it
    will be treated when it gets there.

    The verdict is computed here rather than in the GUI because this is where
    the answer is actually decided — the same config and the same classifier
    the transcription path will consult. A GUI working it out for itself would
    be a second implementation to keep in step.
    """
    with focus_lock:
        app, title = focus["app"], focus["title"]
    cfg = load_config()
    return {
        "type": "focus",
        "app": app,
        "title": title,
        "context": baatsun_context.classify(app, title),
        # The finer answer: which set of layout conventions the next transcript
        # will be shaped to. Sent alongside "context" rather than instead of it
        # — that one is what decides whether anything is rewritten at all, and
        # it is the word history entries are recorded under.
        "surface": baatsun_context.surface(app, title),
        # What a rule for this window would be written against.
        "match": baatsun_context.suggest_match(app, title),
        "cleanup": bool(cleanup_ready(cfg)
                        and baatsun_context.should_clean(cfg, app, title)),
    }


def log(msg):
    print(f"[baatsun] {msg}", file=sys.stderr, flush=True)


BYTES_PER_SECOND = int(SAMPLE_RATE) * 2
# Live ElevenLabs slicing: look this far before and after each 20s boundary
# for a pause to cut at, in frames of SLICE_FRAME seconds.
SLICE_SEARCH_BACK = 4.0
SLICE_LOOKAHEAD = 2.0
SLICE_FRAME = 0.1


def _wav_pcm_offset(path):
    """Byte offset of PCM in a wav pw-record is still writing.

    PipeWire writes a 44-byte RIFF header first, then PCM. If the file is
    somehow raw, start at 0 rather than skipping 44 bytes of speech.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(12)
        if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
            return 44
    except OSError:
        pass
    return 0


def _write_pcm_wav(path, pcm):
    with wave.open(path, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(int(SAMPLE_RATE))
        out.writeframes(pcm)


def _merge_scribe_usage(usages):
    cost = 0.0
    secs = 0.0
    keyterms = 0
    any_cost = False
    for item in usages:
        if not item:
            continue
        if item.get("cost") is not None:
            cost += item["cost"]
            any_cost = True
        secs += item.get("secs") or 0
        keyterms = max(keyterms, item.get("keyterms") or 0)
    return {
        "backend": "elevenlabs",
        "model": baatsun_stt.MODEL_ID,
        "secs": round(secs, 1),
        "keyterms": keyterms,
        "cost": cost if any_cost else None,
    }


class LiveScribe:
    """Send 20s slices to ElevenLabs while the mic is still open.

    The 30s Scribe timeout is sized for ~20s of wav. A 5–10 minute
    brainstorm used to be one huge upload that died at 30s and typed
    nothing. Reading the growing wav in CHUNK_SECONDS pieces means those
    minutes are already transcribed by the time you release; finish waits
    only on the leftover tail plus ChatGPT cleanup.
    """

    def __init__(self, wav_path):
        self.path = wav_path
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.offset = 0
        self.jobs = []
        self.thread = None
        self.n = 0

    def start(self):
        self.thread = threading.Thread(
            target=self._watch, name="scribe-live", daemon=True)
        self.thread.start()

    def abort(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        with self.lock:
            jobs = list(self.jobs)
        for _thread, _box, chunk_path in jobs:
            try:
                os.remove(chunk_path)
            except OSError:
                pass

    def collect(self):
        """Join in-flight slices and the leftover tail.

        Returns (text, usage, failed), where failed counts slices that came
        back with nothing. The caller keeps the wav when any did: a transcript
        with a 20-second hole in it must not be the only copy of what was said.
        """
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        self._drain(final=True)
        texts = []
        usages = []
        failed = 0
        with self.lock:
            jobs = list(self.jobs)
        for thread, box, chunk_path in jobs:
            thread.join()
            if box.get("text"):
                texts.append(box["text"].strip())
            else:
                failed += 1
                log(f"elevenlabs chunk {box.get('n')} returned no text")
            if box.get("usage"):
                usages.append(box["usage"])
            try:
                os.remove(chunk_path)
            except OSError:
                pass
        return (" ".join(part for part in texts if part),
                _merge_scribe_usage(usages), failed)

    def _watch(self):
        for _ in range(40):
            if self.stop.is_set():
                return
            try:
                if os.path.getsize(self.path) >= 44:
                    self.offset = _wav_pcm_offset(self.path)
                    break
            except OSError:
                pass
            self.stop.wait(0.05)
        while not self.stop.wait(0.25):
            self._drain(final=False)

    def _drain(self, final):
        chunk = baatsun_stt.CHUNK_SECONDS * BYTES_PER_SECOND
        # Cut in the quietest moment near the nominal boundary rather than at
        # exactly CHUNK_SECONDS: a hard cut lands mid-word as often as not,
        # and both slices then mangle or drop that word. Waiting for
        # SLICE_LOOKAHEAD past the boundary gives the search room either side.
        lookahead = int(SLICE_LOOKAHEAD * BYTES_PER_SECOND)
        to_send = []
        with self.lock:
            try:
                size = os.path.getsize(self.path)
            except OSError:
                return
            available = size - self.offset
            while available >= chunk + lookahead:
                length = self._quiet_cut(self.offset, chunk, lookahead)
                to_send.append((self.offset, length))
                self.offset += length
                available = size - self.offset
            if final and available >= 1024:
                to_send.append((self.offset, available))
                self.offset += available
        for offset, length in to_send:
            self._emit(offset, length)

    def _quiet_cut(self, offset, chunk, lookahead):
        """Slice length ending at the quietest frame in [chunk - back, chunk + ahead].

        Falls back to the nominal length if the audio can't be read. Always
        a whole number of samples, so the next slice starts on a sample.
        """
        back = int(SLICE_SEARCH_BACK * BYTES_PER_SECOND)
        start = offset + chunk - back
        try:
            with open(self.path, "rb") as handle:
                handle.seek(start)
                window = handle.read(back + lookahead)
        except OSError:
            return chunk
        frame = int(SLICE_FRAME * BYTES_PER_SECOND) & ~1
        best_at, best_energy = None, None
        for at in range(0, len(window) - frame + 1, frame):
            samples = array.array("h")
            samples.frombytes(window[at:at + frame])
            if sys.byteorder == "big":
                samples.byteswap()
            energy = sum(abs(v) for v in samples)
            if best_energy is None or energy < best_energy:
                best_at, best_energy = at, energy
        if best_at is None:
            return chunk
        # Cut in the middle of the quiet frame.
        return (chunk - back + best_at + frame // 2) & ~1

    def _emit(self, offset, length):
        try:
            with open(self.path, "rb") as handle:
                handle.seek(offset)
                pcm = handle.read(length)
        except OSError as exc:
            log(f"elevenlabs chunk read failed: {exc}")
            return
        if len(pcm) < 1024:
            return
        fd, chunk_path = tempfile.mkstemp(prefix="baatsun-chunk-", suffix=".wav")
        os.close(fd)
        try:
            _write_pcm_wav(chunk_path, pcm)
        except OSError as exc:
            log(f"elevenlabs chunk write failed: {exc}")
            try:
                os.remove(chunk_path)
            except OSError:
                pass
            return
        seconds = len(pcm) / float(BYTES_PER_SECOND)
        box = {}
        with self.lock:
            self.n += 1
            n = self.n
            box["n"] = n
            thread = threading.Thread(
                target=self._run, args=(chunk_path, seconds, box, n),
                name=f"scribe-chunk-{n}", daemon=True)
            self.jobs.append((thread, box, chunk_path))
        thread.start()
        log(f"elevenlabs chunk {n} ({seconds:.0f}s) sent — transcribing while you talk")

    def _run(self, chunk_path, seconds, box, n):
        usage = {}
        try:
            text = baatsun_stt.transcribe(
                chunk_path, load_stt_api_key(),
                language=SCRIBE_LANGUAGE,
                vocabulary=load_config().get("vocabulary") or "",
                log=log, seconds=seconds, usage=usage)
        except Exception as exc:  # noqa: BLE001 — a dead slice must not hang collect()
            log(f"elevenlabs chunk {n} crashed: {type(exc).__name__}: {exc}")
            text = None
        box["text"] = text
        box["usage"] = usage
        if text:
            log(f"elevenlabs chunk {n} ready")
        else:
            log(f"elevenlabs chunk {n} failed")


# The pill's level meter: how often the mic level is sampled and broadcast, and
# the loudness range mapped onto 0..1. Quiet room noise sits near the floor,
# ordinary speech fills most of the range.
LEVEL_INTERVAL = 0.06
LEVEL_FLOOR_DB = -55.0
LEVEL_RANGE_DB = 45.0


def watch_levels(wav_path):
    """Broadcast the mic level while this recording runs.

    Reads the samples pw-record has appended since the last look and sends
    their loudness, so the pill's bars follow your voice instead of playing
    a canned animation. Cheap: ~1 KB of audio per tick, and no subscriber
    means no work beyond the read.
    """
    offset = None
    while state["recording"] and state["wav_path"] == wav_path:
        time.sleep(LEVEL_INTERVAL)
        try:
            size = os.path.getsize(wav_path)
            if offset is None:
                offset = _wav_pcm_offset(wav_path)
                if size <= offset:
                    offset = None
                    continue
                # Start from the newest audio, not the beginning.
                offset = max(offset, size - int(LEVEL_INTERVAL * BYTES_PER_SECOND))
            if size - offset < 64:
                continue
            with open(wav_path, "rb") as handle:
                handle.seek(offset)
                chunk = handle.read(size - offset)
        except OSError:
            continue
        offset += len(chunk) - (len(chunk) % 2)
        samples = array.array("h")
        samples.frombytes(chunk[: len(chunk) - (len(chunk) % 2)])
        if not samples:
            continue
        rms = math.sqrt(sum(x * x for x in samples) / len(samples))
        db = 20 * math.log10(max(rms, 1.0) / 32768.0)
        level = min(1.0, max(0.0, (db - LEVEL_FLOOR_DB) / LEVEL_RANGE_DB))
        broadcast({"type": "level", "v": round(level, 2)})


def focus_surface():
    """Where the next text will land, for the pill's tag; None for Baatsun itself."""
    with focus_lock:
        app, title = focus["app"], focus["title"]
    if not app or "baatsun" in app.lower():
        return None
    return baatsun_context.surface(app, title)


def start_recording(mode=DICTATE):
    """Start a recording. Callers hold state_lock. Returns True if one started.

    Refuses when one is already running: overwriting state here would orphan
    the running pw-record (mic held open, /tmp filling) and, on ElevenLabs, a
    LiveScribe that keeps uploading — and billing — slices forever.
    """
    if state["recording"] or state["proc"] is not None:
        return False
    fd, wav_path = tempfile.mkstemp(prefix="baatsun-", suffix=".wav")
    os.close(fd)
    try:
        proc = subprocess.Popen(
            [
                "pw-record",
                "--format=s16",
                f"--rate={SAMPLE_RATE}",
                "--channels=1",
                wav_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        # Raised inside a keyboard watcher, this used to be read as "lost
        # keyboard device" and kill the watcher.
        log(f"could not start pw-record: {exc} — is PipeWire installed?")
        try:
            os.remove(wav_path)
        except OSError:
            pass
        broadcast_state("idle", reason="mic_failed")
        return False
    state["recording"] = True
    state["mode"] = mode
    state["proc"] = proc
    state["wav_path"] = wav_path
    state["started"] = time.monotonic()
    capture = None
    if STT_BACKEND == "elevenlabs":
        # Chop the growing wav into CHUNK_SECONDS slices and send each to
        # Scribe now, so a 5–10 minute brainstorm is not one 30s timeout of
        # a 19 MB upload after you let go.
        capture = LiveScribe(wav_path)
        capture.start()
    state["capture"] = capture
    timer = threading.Timer(MAX_RECORDING_SECONDS, on_recording_timeout, args=(wav_path,))
    timer.daemon = True
    state["timer"] = timer
    timer.start()
    state["surface"] = focus_surface()
    broadcast_state("listening", mode=mode, surface=state["surface"])
    threading.Thread(target=watch_levels, args=(wav_path,), name="levels",
                     daemon=True).start()
    log(f"recording started ({mode}) -> {wav_path}")
    return True


def cancel_recording_timer():
    """Disarm the watchdog. Callers must already hold state_lock."""
    timer = state["timer"]
    state["timer"] = None
    if timer is not None:
        timer.cancel()


def on_recording_timeout(wav_path):
    """Stop and transcribe a recording that has run past the limit.

    Transcribes rather than discards: fifteen minutes of speech is worth
    keeping, and the person who hit this is far more likely to have been
    dictating than to have left the mic open. The wav_path check makes this a
    no-op if the recording it was armed for has already ended — cancel() loses
    the race when the timer has begun running but not yet taken the lock.
    """
    with state_lock:
        if not state["recording"] or state["wav_path"] != wav_path:
            return
        log(f"recording hit the {MAX_RECORDING_SECONDS // 60}-minute limit — "
            "stopping and transcribing")
        pending = stop_recording()
    start_finishing(pending)


def maybe_clean(text, app, title):
    """Return (text polished by OpenAI, what that cost), or the original.

    The second half of the pair is the token usage OpenAI reported, or None
    when no request was made, and it is what the Home page adds up into a spend
    figure. It comes back even when the cleaned text was rejected, because the
    call was still billed.

    Config is re-read per dictation rather than cached at startup so toggling
    cleanup in Settings takes effect immediately — the Settings panel restarts
    the daemon for the hotkey's sake, but this shouldn't depend on that.
    """
    cfg = load_config()
    if not cfg.get("cleanup_enabled"):
        return text, None

    api_key = load_api_key()
    if not api_key:
        log("cleanup is on but no API key is set — typing the raw transcript")
        return text, None

    if not baatsun_context.should_clean(cfg, app, title):
        log(f"context {baatsun_context.classify(app, title)} ({app or 'unknown'}) "
            "— typing the raw transcript")
        return text, None

    # Deliberately no new state here: the pill, tray and GUI all treat an
    # unrecognised state as idle, so announcing "cleaning" would drop the pill
    # to rest and re-enable the record button while the request is still in
    # flight. Staying "transcribing" keeps the sweep running, which is what a
    # user waiting on text actually needs to see.
    # Which kind of place this is going — an email, a chat message, a post —
    # so the cleanup pass lays it out the way that place expects. It only ever
    # changes the shape and the register; the words stay the speaker's.
    surface = baatsun_context.surface(app, title)
    usage = {}
    # Named apart from the module's global `model`, which is the whisper one.
    cleanup_model = cfg.get("cleanup_model") or DEFAULT_CLEANUP_MODEL
    cleaned = baatsun_cleanup.clean(
        text, api_key, cleanup_model, log=log,
        vocabulary=cfg.get("vocabulary") or "",
        line_breaks=(cfg.get("line_breaks", True)
                     and baatsun_context.allows_line_breaks(app, title)),
        hinglish=bool(cfg.get("hinglish")),
        strength=cfg.get("cleanup_strength") or "grammar",
        usage=usage,
        surface=surface,
        timeout=baatsun_cleanup.timeout_for(text, cleanup_model),
        reasoning=cfg.get("cleanup_reasoning") or DEFAULT_CLEANUP_REASONING,
        output_language=output_language_for_cleanup(),
    )
    # Empty when the request never got as far as an answer, which is a
    # different thing from a call that cost nothing.
    usage = usage or None
    if cleaned is None:
        return text, usage
    if cleaned != text:
        log(f"cleaned for {surface}: {cleaned!r}" if LOG_TRANSCRIPTS
            else f"cleaned for {surface} ({len(cleaned)} chars)")
    return cleaned, usage


def stop_recording():
    """Take the running recording off the state and hand back what finishes it.

    Callers hold state_lock. Returns a (proc, wav_path, secs, capture, mode) tuple
    to pass to finish_recording, or None when there was nothing running.
    capture is a LiveScribe for ElevenLabs, or None for the local backend.

    This is deliberately only the cheap half. Everything slow — waiting on
    pw-record, the transcription itself, the cleanup request, typing the result
    — used to run here, inside state_lock, which is the same lock every key
    event takes. A cloud slice can still take up to 30 seconds and a long
    cleanup up to 90, and for all of it the hotkey used to be dead. Splitting
    the phases is what lets the slow half run on its own thread with nothing
    held.
    """
    cancel_recording_timer()
    proc = state["proc"]
    wav_path = state["wav_path"]
    started = state["started"]
    capture = state["capture"]
    mode = state["mode"]
    state["recording"] = False
    state["proc"] = None
    state["wav_path"] = None
    state["started"] = None
    state["capture"] = None
    state["mode"] = DICTATE

    if proc is None or wav_path is None:
        return None

    # Held until finish_recording is done, so a second recording can't start on
    # top of a transcription that is still typing. That was previously a side
    # effect of holding state_lock across the whole thing; now it is the flag.
    state["finishing"] = True
    # Monotonic, so a clock adjustment mid-dictation can't make this negative.
    secs = None if started is None else time.monotonic() - started
    return proc, wav_path, secs, capture, mode


def start_finishing(pending):
    """Run the slow half on its own thread. Callers must NOT hold state_lock.

    A thread rather than the calling thread, because the callers are the evdev
    watchers and the socket handlers — the two things that most need to stay
    responsive while a transcription is running.
    """
    global finish_thread
    if pending is None:
        return
    finish_thread = threading.Thread(
        target=finish_recording, args=(pending,), name="finish", daemon=True)
    finish_thread.start()


def finish_recording(pending):
    """Transcribe, clean up, type, and record a stopped recording.

    Runs with no lock held. state["finishing"] is what keeps a second
    recording from overlapping this one, and it is cleared here whatever
    happens — leaving it set would wedge the hotkey for good.
    """
    proc, wav_path, secs, capture, mode = pending
    try:
        _finish_recording(proc, wav_path, secs, capture, mode)
    except Exception as exc:  # noqa: BLE001
        # Without this an unexpected error killed the thread silently: the
        # wav was deleted by the finally below, nothing was typed or saved,
        # and every UI sat on "transcribing" until the next dictation.
        import traceback
        log(f"dictation failed: {type(exc).__name__}: {exc}")
        traceback.print_exc(file=sys.stderr)
        broadcast_state("idle", reason="failed")
    finally:
        with state_lock:
            state["finishing"] = False


def _finish_recording(proc, wav_path, secs, capture, mode=DICTATE):
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    broadcast_state("transcribing", mode=mode, surface=state.get("surface"))

    # A remote transcription that fails takes the dictation with it — there is
    # no raw text to fall back on, the way a failed cleanup still has the
    # transcript. Keeping the wav turns that from "what you said is gone" into
    # "what you said is on disk", which is the difference between an annoyance
    # and a reason to distrust the tool.
    keep_wav = False
    try:
        if os.path.getsize(wav_path) < 1024:
            if capture is not None:
                capture.abort()
            log("recording too short, skipping")
            broadcast_state("idle", reason="too_short")
            return

        vocabulary = load_config().get("vocabulary") or ""
        # Which backend transcribed this, recorded per entry rather than read
        # from config at reporting time. The backend is a setting people flip:
        # totalling today's history against today's setting would bill every
        # local dictation you ever made at cloud rates the moment you switch.
        stt_usage = {}
        if STT_BACKEND == "elevenlabs":
            failed_slices = 0
            if capture is not None:
                text, stt_usage, failed_slices = capture.collect()
            else:
                text = baatsun_stt.transcribe(
                    wav_path, load_stt_api_key(),
                    language=SCRIBE_LANGUAGE, vocabulary=vocabulary, log=log,
                    seconds=secs, usage=stt_usage)
            if text and failed_slices:
                keep_wav = True
                log(f"{failed_slices} slice(s) failed, so the transcript has gaps; "
                    f"the full recording has been kept at {wav_path}")
            if not text:
                keep_wav = True
                log(f"the recording has been kept at {wav_path} — "
                    "dictate again, or transcribe it by hand")
                broadcast_state("idle", reason="failed")
                return
        else:
            # initial_prompt biases the decoder toward names it would otherwise
            # mangle ("Claude" heard as "cloud"). Cheaper and far more reliable
            # than asking the cleanup model to spot the mistake afterwards, and
            # it works even with cleanup switched off.
            #
            # beam_size: greedy decoding (1) was the main source of misheard
            # words; whisper's accuracy figures are all quoted at 5.
            # vad_filter: skip the silence before, between and after speech.
            # Less audio to decode (faster), and silence is where whisper
            # hallucinates "Thank you." and repeated lines.
            # condition_on_previous_text off: stops one misheard segment from
            # steering every one after it, and the repetition loops that come
            # with it.
            # without_timestamps: we never use them, and predicting them costs
            # decoder steps.
            segments, _info = model.transcribe(
                wav_path,
                language=SPOKEN_LANGUAGE,
                # Speaking another language and typing English is something
                # whisper does itself, better than translating its transcript.
                task="translate" if whisper_translates() else "transcribe",
                beam_size=resolve_beam_size(load_config()),
                initial_prompt=vocabulary or None,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300},
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            text = "".join(seg.text for seg in segments).strip()
            # Written rather than left out, so the cost report can tell a
            # dictation that was free apart from one whose backend is unknown.
            stt_usage = {"backend": "local", "cost": 0.0}

        if not text:
            log("empty transcript")
            broadcast_state("idle", reason="empty")
            return

        log(f"transcript: {text!r}" if LOG_TRANSCRIPTS
            else f"transcript ready ({len(text.split())} words)")
        # Read the focused window once and use it for every decision below.
        # Two separate reads could disagree if the user switched windows mid-
        # cleanup, and an entry that says "verbatim" about text that was in
        # fact rewritten is worse than one that is a window out of date.
        with focus_lock:
            app, title = focus["app"], focus["title"]
        cfg = load_config()
        # Taught corrections first: everything after should see the words the
        # speaker meant, including the snippet and command matching. History
        # keeps what was actually heard, so a correction can be audited.
        heard = text
        text = baatsun_voice.apply_corrections(text, cfg.get("corrections"))

        if mode == COMMAND:
            finish_voice_edit(text, app, title, secs, stt_usage)
            return
        if mode == NOTES:
            finish_notes(text, app, title, secs, stt_usage, heard=heard)
            return

        if baatsun_voice.match_command(text) == baatsun_voice.SCRATCH:
            scratch_last(app)
            return
        snippet = baatsun_voice.match_snippet(text, cfg.get("snippets"))
        if snippet is not None:
            log("snippet matched — typing it as saved")
            deliver(snippet, app, title, raw=heard, secs=secs, stt=stt_usage,
                    context=baatsun_context.classify(app, title), mode="snippet")
            return

        context = baatsun_context.classify(app, title)
        text, usage = maybe_clean(text, app, title)
        deliver(text, app, title, raw=heard, secs=secs, usage=usage,
                stt=stt_usage, context=context)
    except Exception:
        # Whatever failed, the audio is the only copy of what was said.
        keep_wav = True
        log(f"the recording has been kept at {wav_path}")
        raise
    finally:
        if not keep_wav:
            try:
                os.remove(wav_path)
            except OSError:
                pass


def whisper_translates():
    """Whether the local model itself translates speech into English."""
    out = load_config().get("output_language") or ""
    return STT_BACKEND != "elevenlabs" and out == "en" and SPOKEN_LANGUAGE != "en"


def output_language_for_cleanup():
    """The language the cleanup pass should write in, or None.

    None when no translation is wanted, or when whisper already produced the
    English that was asked for.
    """
    out = load_config().get("output_language") or ""
    if not out or whisper_translates():
        return None
    return out


def is_self(app):
    """Whether a window class is Baatsun's own window."""
    return "baatsun" in (app or "").lower()


def deliver(text, app, title, raw=None, secs=None, usage=None, stt=None,
            context=None, mode=None):
    """Type text into the window, record it, and tell the GUIs."""
    # Enter sends in a chat window, so no newline reaches one, whatever
    # the cleanup model returned. Terminals and editors keep theirs, and
    # insertion makes each one a soft newline instead of an Enter.
    if not baatsun_context.allows_line_breaks(app, title):
        text = " ".join(text.split())
    inserted, how = insert_transcript(
        text, app, soft_newlines=baatsun_context.soft_newlines(app, title))
    if inserted:
        remember_insert(app, text)
    entry = add_history_entry(text, raw, app=app, context=context, secs=secs,
                              usage=usage, stt=stt, mode=mode,
                              surface=baatsun_context.surface(app, title))
    broadcast({"type": "transcript", "entry": entry})
    if inserted:
        # "result" rather than "reason": reasons are problems the GUIs explain,
        # this is the pill's cue to show a brief confirmation.
        broadcast_state("idle", result="pasted")
    else:
        # Saved in history either way, so it can be copied from there.
        broadcast_state("idle", reason="insert_failed", error=how)


def remember_insert(app, text):
    with last_insert_lock:
        last_insert.update(app=app, text=text, ts=time.monotonic())


def scratch_last(app):
    """Take back the last thing typed, if it went into this same window."""
    with last_insert_lock:
        prev = dict(last_insert)
        fresh = (prev["text"] and prev["app"] == app
                 and time.monotonic() - prev["ts"] < SCRATCH_WINDOW_SECONDS)
        if fresh:
            last_insert.update(app=None, text="", ts=0.0)
    if not fresh:
        log("scratch that — nothing recent to remove in this window")
        broadcast_state("idle", reason="nothing_to_scratch")
        return
    ok, err = baatsun_insert.delete_chars(len(prev["text"]))
    log("scratched the last dictation" if ok else f"could not scratch: {err}")
    broadcast_state("idle", reason="scratched" if ok else "insert_failed",
                    **({} if ok else {"error": err}))


def finish_voice_edit(instruction, app, title, secs, stt_usage):
    """Apply a spoken instruction to the selection, or write from it."""
    cfg = load_config()
    api_key = load_api_key()
    if not api_key:
        log("voice edit needs an OpenAI key — none is set")
        broadcast_state("idle", reason="needs_key")
        return
    selection, clipboard = baatsun_insert.copy_selection(app)
    surface = baatsun_context.surface(app, title)
    cleanup_model = cfg.get("cleanup_model") or DEFAULT_CLEANUP_MODEL
    usage = {}
    words = len(instruction.split()) + len((selection or "").split())
    result = baatsun_cleanup.edit(
        instruction, selection, api_key, cleanup_model, log=log,
        surface=surface, usage=usage,
        timeout=baatsun_cleanup.timeout_for(" ".join(["w"] * words), cleanup_model),
        reasoning=cfg.get("cleanup_reasoning") or DEFAULT_CLEANUP_REASONING,
        output_language=cfg.get("output_language") or None)
    if not result:
        log("voice edit produced nothing usable — leaving the text alone")
        broadcast_state("idle", reason="edit_failed")
        return
    log(f"voice edit ({'selection' if selection else 'new text'}, {len(result)} chars)")
    if not baatsun_context.allows_line_breaks(app, title):
        result = " ".join(result.split())
    method = cfg.get("insert_method") or "paste"
    inserted, how = baatsun_insert.insert(
        result, app=app, method=method, log=log,
        soft_newlines=baatsun_context.soft_newlines(app, title),
        restore=clipboard)
    if inserted:
        remember_insert(app, result)
    entry = add_history_entry(result, instruction, app=app,
                              context=baatsun_context.classify(app, title),
                              secs=secs, usage=usage or None, stt=stt_usage,
                              mode="edit" if selection else "compose",
                              surface=surface, force_raw=True)
    broadcast({"type": "transcript", "entry": entry})
    broadcast_state("idle", **({"result": "edited"} if inserted
                               else {"reason": "insert_failed", "error": how}))


def finish_notes(text, app, title, secs, stt_usage, heard=None):
    """Turn a long dictation into structured notes and keep them in History.

    Typed into the focused window too, unless that window is Baatsun itself —
    where notes started from the Dictate page would otherwise land.
    """
    cfg = load_config()
    api_key = load_api_key()
    usage = {}
    result = None
    if api_key:
        cleanup_model = cfg.get("cleanup_model") or DEFAULT_CLEANUP_MODEL
        result = baatsun_cleanup.notes(
            text, api_key, cleanup_model, log=log, usage=usage,
            timeout=baatsun_cleanup.timeout_for(text, cleanup_model),
            reasoning=cfg.get("cleanup_reasoning") or DEFAULT_CLEANUP_REASONING,
            vocabulary=cfg.get("vocabulary") or "",
            output_language=output_language_for_cleanup())
    else:
        log("notes need an OpenAI key to be organised — keeping the raw transcript")
    notes_text = result or text
    inserted, how = True, ""
    if not is_self(app):
        inserted, how = insert_transcript(
            notes_text, app, soft_newlines=baatsun_context.soft_newlines(app, title))
        if inserted:
            remember_insert(app, notes_text)
    entry = add_history_entry(notes_text, heard or text,
                              app=None if is_self(app) else app,
                              secs=secs, usage=usage or None, stt=stt_usage,
                              mode="notes")
    broadcast({"type": "transcript", "entry": entry})
    if not inserted:
        broadcast_state("idle", reason="insert_failed", error=how)
    else:
        broadcast_state("idle", reason=None if result else ("needs_key" if not api_key else "notes_failed"),
                        result="notes")


def handle_notes():
    """Start a notes recording, or stop whatever recording is running."""
    with state_lock:
        if state["recording"]:
            pending = stop_recording()
        elif state["finishing"]:
            return "busy"
        else:
            return "started" if start_recording(NOTES) else "mic_failed"
    start_finishing(pending)
    return "stopped"


def note_window(app, title):
    """Remember a focused window for the Apps page (not Baatsun's own)."""
    if not app or is_self(app):
        return
    match = baatsun_context.suggest_match(app, title)
    if not match:
        return
    with recent_windows_lock:
        recent_windows[:] = [w for w in recent_windows if w["match"] != match]
        # The full title stays in memory only, to classify the window the way
        # a dictation into it would be; the Apps page is sent the match.
        recent_windows.insert(0, {"app": app, "match": match, "title": title})
        del recent_windows[RECENT_WINDOWS_LIMIT:]


def windows_payload():
    """Recent windows with how each is classified right now."""
    with recent_windows_lock:
        windows = list(recent_windows)
    return [{
        "app": w["app"],
        "match": w["match"],
        "surface": baatsun_context.surface(w["app"], w["title"]),
        "ruled": baatsun_context.rule_surface(w["app"], w["title"]) is not None,
    } for w in windows]


def insert_transcript(text, app=None, soft_newlines=False):
    """Paste or type text into the focused window. Returns (ok, how/error)."""
    method = load_config().get("insert_method") or "paste"
    ok, how = baatsun_insert.insert(text, app=app, method=method, log=log,
                                    soft_newlines=soft_newlines)
    if ok:
        log(f"inserted ({how})")
    else:
        log(f"could not insert the transcript: {how} — it is in History. "
            "With ydotool 1.x, ydotoold must be running "
            "(systemctl --user enable --now baatsun-ydotoold)")
    return ok, how


def abandon_recording():
    """Stop an in-flight recording without transcribing it, for shutdown.

    Nothing else will ever come along to end this recording, so without it we
    leave an orphaned pw-record holding the microphone and a stray wav in
    /tmp. Discarding rather than transcribing is deliberate: we're on our way
    out and there'd be no window left to type into.

    Waits on the finish thread, so a transcription already under way finishes
    and gets typed first rather than being cut off mid-word. That used to fall
    out of taking state_lock, back when the transcription held it; now that it
    runs unlocked, the wait has to be explicit.
    """
    with state_lock:
        cancel_recording_timer()
        proc = state["proc"]
        wav_path = state["wav_path"]
        capture = state["capture"]
        state["recording"] = False
        state["proc"] = None
        state["wav_path"] = None
        state["capture"] = None
        thread = finish_thread

    if thread is not None and thread.is_alive():
        log("waiting for the transcription in flight to finish typing")
        thread.join(timeout=FINISH_JOIN_SECONDS)
        if thread.is_alive():
            log("transcription did not finish in time — exiting anyway")

    if capture is not None:
        capture.abort()

    if proc is not None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log("discarded in-flight recording")

    if wav_path is not None:
        try:
            os.remove(wav_path)
        except OSError:
            pass


def handle_toggle():
    with state_lock:
        if state["recording"]:
            pending = stop_recording()
        elif state["finishing"]:
            # The previous dictation is still being transcribed and typed.
            # Starting another now would race two transcripts into the same
            # window; the caller gets told rather than silently ignored.
            return "busy"
        else:
            return "started" if start_recording() else "mic_failed"
    start_finishing(pending)
    return "stopped"


def find_keyboard_devices(skip=()):
    devices = []
    for path in list_devices():
        if path in skip:
            continue
        try:
            dev = InputDevice(path)
        except OSError:
            continue
        caps = dev.capabilities().get(ecodes.EV_KEY, [])
        if is_injector(dev):
            # Closed like any other device we don't want, but never watched:
            # see INJECTOR_DEVICE_NAMES.
            close_device(dev)
        elif ecodes.KEY_A in caps and ecodes.KEY_LEFTCTRL in caps:
            devices.append(dev)
        else:
            # Every mouse, lid switch and power button gets opened by this
            # scan, and the scan now repeats forever. Closing the ones we don't
            # want keeps the daemon from running itself out of file
            # descriptors.
            close_device(dev)
    return devices


def is_injector(dev):
    """Is this one of our own synthetic keyboards rather than a real one?

    Reading the name can itself fail on a node that vanished between the
    listing and here — which is exactly what ydotool's device does — so a
    device we can't identify is treated as an injector. Refusing to watch a
    keyboard we couldn't name is the safe direction: the rescan is two seconds
    away and will pick it up properly if it was real and is still there.
    """
    try:
        name = (dev.name or "").lower()
    except OSError:
        return True
    return any(tag in name for tag in INJECTOR_DEVICE_NAMES)


def close_device(dev):
    try:
        dev.close()
    except OSError:
        pass


def refresh_chord():
    """Re-check whether the chord is held and fire the edge. Callers hold state_lock.

    Keys are tracked per keyboard but tested as one pool, so half a chord on
    the laptop keyboard and half on an external one still counts.

    Returns whatever the edge handler produced — a pending transcription for
    the caller to start once it has dropped state_lock, or None.
    """
    held = set()
    for keys in hotkey_state["pressed"].values():
        held |= keys

    now_held = bool(held & PRIMARY_KEYS) and bool(held & SECONDARY_KEYS)
    command = bool(held & COMMAND_KEYS)
    if now_held and not hotkey_state["held"]:
        hotkey_state["held"] = True
        hotkey_state["press_time"] = time.monotonic()
        return on_hotkey_press(COMMAND if command else DICTATE)
    if now_held and command and state["recording"] and state["mode"] == DICTATE:
        # The modifier joined after the chord: still a voice edit.
        state["mode"] = COMMAND
        broadcast_state("listening", mode=COMMAND, surface=state.get("surface"))
        log("voice edit — the modifier joined mid-recording")
    elif not now_held and hotkey_state["held"]:
        hotkey_state["held"] = False
        return on_hotkey_release()
    return None


def on_key_event(path, code, value):
    """value: 1=press, 0=release, 2=autorepeat (ignored)."""
    if value == 2:
        return
    with state_lock:
        pressed = hotkey_state["pressed"].setdefault(path, set())
        if value == 1:
            pressed.add(code)
        else:
            pressed.discard(code)
        pending = refresh_chord()
    # Outside the lock, and on its own thread: this watcher has to get straight
    # back to read_loop() for the next key event.
    start_finishing(pending)


def forget_device(path):
    """Drop the keys a vanished keyboard was holding.

    Unplug a keyboard mid-chord and its release events never arrive. Left in
    place those phantom keys hold the chord down forever: a hold-mode recording
    would run until the MAX_RECORDING_SECONDS backstop, and every later press
    would do nothing because the chord never looked released. Dropping them
    re-checks the chord, so an unplug ends a held recording the way letting go
    would.
    """
    pending = None
    with state_lock:
        if hotkey_state["pressed"].pop(path, None):
            pending = refresh_chord()
    start_finishing(pending)


def on_hotkey_press(mode=DICTATE):
    """The chord was just formed. Callers already hold state_lock.

    Returns a pending transcription for the caller to hand to
    start_finishing() after dropping the lock, or None.

    Calls the start/stop pair directly rather than going through
    handle_toggle(), which takes the state_lock we are already inside —
    threading.Lock is not reentrant, so that would deadlock the keyboard thread
    and leave the hotkey dead until the daemon restarts.
    """
    # The previous dictation is still transcribing and typing. This is where
    # the hotkey used to simply block on state_lock; ignoring the press keeps
    # the old behaviour ("nothing happens during transcription") without
    # parking the keyboard thread to get it.
    if state["finishing"]:
        return None

    if ACTIVATION == "hold":
        # A recording started from the pill or baatsun-toggle is already
        # running; this press joins it, and the release will end it.
        if not state["recording"]:
            start_recording(mode)
        return None

    # Clear the latch before deciding anything. If the MAX_RECORDING_SECONDS
    # watchdog ended a latched recording, this flag would otherwise still be
    # set and would swallow the release of the next press, leaving a
    # hold-style recording that never ends.
    hotkey_state["latched"] = False

    # toggle and hybrid both stop on the press that follows a running
    # recording. Under hybrid that recording can only ever be a latched one: a
    # held recording ends on its own release, so nothing is still running by
    # the time a later press arrives.
    if state["recording"]:
        return stop_recording()
    start_recording(mode)
    return None


def on_hotkey_release():
    """The chord was just broken. Callers already hold state_lock.

    Returns a pending transcription, or None — see on_hotkey_press.
    """
    if ACTIVATION == "hold":
        # Safe when nothing is running: stop_recording() returns None the
        # moment it sees no subprocess, which is what a press swallowed by
        # state["finishing"] leaves behind.
        return stop_recording()

    # Toggle ignores releases entirely; so does hybrid once a tap has latched,
    # and when the press already stopped the recording.
    if ACTIVATION != "hybrid" or hotkey_state["latched"] or not state["recording"]:
        return None

    # This is the release that decides which gesture the user made. Held long
    # enough to be deliberate, so end it here like push-to-talk. Otherwise it
    # was a tap: leave the recording up for the next press to stop.
    if time.monotonic() - hotkey_state["press_time"] >= TAP_SECONDS:
        return stop_recording()
    hotkey_state["latched"] = True
    log("tap — recording latched, press the hotkey again to stop")
    return None


def watch_device(dev):
    log(f"watching keyboard: {dev.path} ({dev.name})")
    try:
        for event in dev.read_loop():
            if event.type == ecodes.EV_KEY:
                on_key_event(dev.path, event.code, event.value)
    except OSError as e:
        log(f"lost keyboard device {dev.path}: {e}")
    finally:
        with watched_lock:
            watched_paths.discard(dev.path)
        forget_device(dev.path)
        close_device(dev)


def scan_keyboards():
    """Start a watcher for every keyboard that isn't already being watched."""
    with watched_lock:
        skip = set(watched_paths)
    devices = find_keyboard_devices(skip)
    for dev in devices:
        with watched_lock:
            watched_paths.add(dev.path)
        threading.Thread(target=watch_device, args=(dev,), daemon=True).start()
    return devices


def scan_keyboards_forever():
    """Rescan when /dev/input changes, rather than every tick regardless.

    A scan opens every device node it isn't already watching — every mouse, lid
    switch and power button — reads its capability map and closes it again.
    Doing that every two seconds for the life of the daemon is pure idle cost,
    and nothing can be plugged in or pulled out without the directory listing
    changing. One readdir is enough to know.

    The watcher count is in the comparison because a device can also stop being
    watched without the directory changing — a read error on a node that still
    exists ends its thread — and that has to trigger a rescan too.
    """
    seen_nodes = None
    seen_count = None
    while True:
        time.sleep(DEVICE_SCAN_SECONDS)
        try:
            nodes = frozenset(os.listdir(DEVICE_DIR))
        except OSError as e:
            log(f"keyboard rescan failed: {e}")
            seen_nodes = None
            continue
        with watched_lock:
            count = len(watched_paths)
        if nodes == seen_nodes and count == seen_count:
            continue
        try:
            scan_keyboards()
        except OSError as e:
            log(f"keyboard rescan failed: {e}")
            seen_nodes = None
            continue
        seen_nodes = nodes
        with watched_lock:
            seen_count = len(watched_paths)


def start_hotkey_listener():
    log(f"hotkey {config['hotkey']} in {ACTIVATION} mode " + {
        "hold": "(records while held)",
        "toggle": "(press to start, press again to stop)",
        "hybrid": f"(hold to talk; tap under {TAP_SECONDS}s to keep recording "
                  "until the next press)",
    }[ACTIVATION])
    if not scan_keyboards():
        log("no keyboard found yet — still watching for one to be plugged in")
    threading.Thread(target=scan_keyboards_forever, daemon=True).start()


# Periods the cost report breaks spend down by, as (name, seconds back from
# now). "today" is handled separately because it means local midnight, not 24
# hours ago — "what have I spent today" is a calendar question.
COST_PERIODS = (("week", 7 * 86400), ("month", 30 * 86400))


def _period_bucket():
    return {
        "dictations": 0,
        "elevenlabs": {"dictations": 0, "secs": 0.0, "cost": 0.0,
                       "estimated": 0},
        "openai": {"calls": 0, "in": 0, "out": 0, "cost": 0.0, "estimated": 0},
        # Entries written before the backend was recorded per-entry. Counted and
        # reported, never billed: we genuinely do not know which backend
        # produced them, and inventing a number for them would put a wrong
        # figure on a spend total, which is the one place a guess is worst.
        "unattributed": 0,
        "total": 0.0,
    }


def _add_entry_cost(bucket, entry):
    bucket["dictations"] += 1

    stt = entry.get("stt") or {}
    backend = stt.get("backend")
    if backend == "elevenlabs":
        eleven = bucket["elevenlabs"]
        eleven["dictations"] += 1
        cost = stt.get("cost")
        if not isinstance(cost, (int, float)):
            # The call happened but its duration wasn't recorded. Fall back to
            # the entry's own mic-open time, which is the same quantity from a
            # less exact source, and mark the figure as estimated.
            cost = baatsun_stt.cost_of(entry.get("secs") or 0,
                                       stt.get("keyterms") or 0)
            eleven["estimated"] += 1
        eleven["secs"] += stt.get("secs") or entry.get("secs") or 0.0
        eleven["cost"] += cost
    elif backend != "local":
        bucket["unattributed"] += 1

    usage = entry.get("usage") or {}
    openai = bucket["openai"]
    cost = usage.get("cost")
    if isinstance(cost, (int, float)):
        openai["calls"] += 1
        openai["in"] += usage.get("in") or 0
        openai["out"] += usage.get("out") or 0
        openai["cost"] += cost
    elif entry.get("raw"):
        # Cleanup ran (it left a pre-cleanup text behind) but predates usage
        # being recorded. Estimated the same way the Home page does it, so the
        # two never disagree — see electron/src/renderer/lib/stats.js.
        model = load_config().get("cleanup_model") or DEFAULT_CLEANUP_MODEL
        prompt = baatsun_cleanup.estimate_tokens(entry.get("raw"))
        completion = baatsun_cleanup.estimate_tokens(entry.get("text"))
        openai["calls"] += 1
        openai["in"] += prompt
        openai["out"] += completion
        openai["cost"] += baatsun_cleanup.cost_of(model, prompt, completion) or 0.0
        openai["estimated"] += 1

    bucket["total"] = bucket["elevenlabs"]["cost"] + bucket["openai"]["cost"]


def compute_cost(entries, now=None):
    """What the two APIs have cost, over the transcripts still in history.

    Deliberately computed from history rather than from a separate ledger, so
    it can never disagree with what the History page shows — but that also means
    it reports a *window*, not a lifetime: history is capped at HISTORY_LIMIT
    and "clear" wipes it. The window block says so explicitly rather than
    letting a caller read the total as all-time spend.
    """
    now = time.time() if now is None else now
    local = time.localtime(now)
    midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday,
                            0, 0, 0, 0, 0, -1))

    periods = {name: _period_bucket() for name, _ in COST_PERIODS}
    periods["today"] = _period_bucket()
    periods["all"] = _period_bucket()

    timestamps = []
    for entry in entries:
        _add_entry_cost(periods["all"], entry)
        ts = entry.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        timestamps.append(ts)
        if ts >= midnight:
            _add_entry_cost(periods["today"], entry)
        for name, window in COST_PERIODS:
            if ts >= now - window:
                _add_entry_cost(periods[name], entry)

    return {
        "generated": now,
        "window": {
            "entries": len(entries),
            "since": min(timestamps) if timestamps else None,
            "limit": HISTORY_LIMIT,
            # At the cap, the oldest dictations have already been dropped, so
            # "all" is a floor on lifetime spend rather than the whole of it.
            "at_limit": len(entries) >= HISTORY_LIMIT,
        },
        "periods": periods,
        # Echoed so a caller never has to hardcode a second copy of the prices,
        # and so a report carries the rates it was computed with.
        "rates": {
            "elevenlabs": {
                "model": baatsun_stt.MODEL_ID,
                "per_hour": baatsun_stt.PRICE_PER_HOUR,
                "keyterms_per_hour": baatsun_stt.KEYTERMS_PRICE_PER_HOUR,
            },
            "openai": {
                "model": load_config().get("cleanup_model")
                or DEFAULT_CLEANUP_MODEL,
                "per_million": baatsun_cleanup.PRICING,
            },
        },
        "backend": STT_BACKEND,
    }


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        # Generous because "focus" carries a window title, which can be long;
        # every other command is a word and an integer.
        data = self.request.recv(MAX_COMMAND_BYTES).decode("utf-8", "replace").strip()
        command, _, arg = data.partition(" ")
        if command == "focus":
            arg = self._await_focus_payload(arg)
        if command == "toggle":
            result = handle_toggle()
            self._reply(result.encode())
        elif command == "notes":
            self._reply(handle_notes().encode())
        elif command == "windows":
            self._reply(json.dumps(windows_payload()).encode() + b"\n")
        elif command == "refocus":
            # After a rule changes, re-announce the focused window so the
            # GUI's "where it lands" reflects it without a focus change.
            broadcast(focus_event())
            self._reply(b"ok")
        elif command == "status":
            self._reply(("recording" if state["recording"] else "idle").encode())
        elif command == "history":
            with history_lock:
                payload = json.dumps(history)
            self._reply(payload.encode() + b"\n")
        elif command == "cost":
            # Snapshot the list under the lock and total it outside: this walks
            # every entry several times over, and the history lock is also held
            # by the path that writes a finished dictation.
            with history_lock:
                entries = list(history)
            payload = json.dumps(compute_cost(entries))
            self._reply(payload.encode() + b"\n")
        elif command == "clear":
            clear_history()
            broadcast({"type": "history_cleared"})
            self._reply(b"ok")
        elif command == "delete":
            self.handle_delete(arg)
        elif command == "retype":
            self.handle_retype(arg)
        elif command == "focus":
            self.handle_focus(arg)
        elif command == "subscribe":
            self.handle_subscribe()
        else:
            self._reply(b"unknown command")

    def _await_focus_payload(self, arg):
        """Wait for the rest of a focus payload that arrived in pieces.

        The protocol has no terminator — every client writes a bare command and
        then waits for the reply — so there is nothing to read *until*, and one
        recv() is right for every command except this one. "focus" is the only
        one whose argument is unbounded: it carries a window title, and a
        stream socket is free to split that across reads. When it does, the
        JSON fails to parse and the report is dropped, and this is the input
        that decides whether a transcript gets rewritten before it is typed.

        So: keep reading while what we have is not yet valid JSON. Costs a
        round of parsing in the normal case, where the first recv already had
        all of it, and nothing else.
        """
        deadline = time.monotonic() + FOCUS_READ_TIMEOUT
        while True:
            try:
                json.loads(arg)
                return arg
            except ValueError:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0 or len(arg) >= MAX_COMMAND_BYTES:
                return arg  # handle_focus reports bad_focus
            try:
                self.request.settimeout(remaining)
                chunk = self.request.recv(MAX_COMMAND_BYTES)
            except OSError:
                return arg
            finally:
                self.request.settimeout(None)
            if not chunk:
                return arg
            arg += chunk.decode("utf-8", "replace")

    def handle_focus(self, arg):
        """Record which window has focus, reported by the GNOME extension.

        Malformed input is dropped rather than raised on: this arrives from
        another process on every window switch, and a bad line should never
        take down the thread that also serves the hotkey.
        """
        try:
            payload = json.loads(arg)
            app = str(payload.get("app") or "")
            title = str(payload.get("title") or "")
        except (ValueError, AttributeError):
            self._reply(b"bad_focus")
            return
        # Only announce a real change. The extension reports on every title
        # change, which in a browser is every keystroke in the address bar.
        with focus_lock:
            changed = (focus["app"], focus["title"]) != (app, title)
            focus["app"] = app
            focus["title"] = title
        if changed:
            note_window(app, title)
            broadcast(focus_event())
        self._reply(b"ok")

    def _reply(self, payload):
        """Answer a fire-and-forget notification, tolerating a gone client.

        The GNOME extension writes "focus ..." and closes without waiting for
        an answer — correct for a notification, but it means our reply races
        the close and usually loses. Without this guard every window switch
        logs a BrokenPipeError traceback from socketserver.
        """
        try:
            self.request.sendall(payload)
        except OSError:
            pass

    def handle_delete(self, arg):
        try:
            entry_id = int(arg)
        except ValueError:
            self._reply(b"bad_id")
            return
        if delete_history_entry(entry_id):
            broadcast({"type": "deleted", "id": entry_id})
            self._reply(b"ok")
        else:
            self._reply(b"not_found")

    def handle_retype(self, arg):
        try:
            entry_id = int(arg)
        except ValueError:
            self._reply(b"bad_id")
            return
        entry = find_history_entry(entry_id)
        if entry is None:
            self._reply(b"not_found")
            return
        with focus_lock:
            app = focus["app"]
        ok, _how = insert_transcript(entry["text"], app)
        self._reply(b"ok" if ok else b"insert_failed")

    def handle_subscribe(self):
        # Bound how long a send to this client can block, because broadcast()
        # sends to every subscriber under one lock and on the transcription
        # path. A GUI that stops reading — hung renderer, stopped process —
        # fills its buffer, and without this the daemon would wait on it
        # forever.
        #
        # SO_SNDTIMEO rather than settimeout(): the same socket's recv() below
        # has to stay blocking for the life of the subscription, and
        # settimeout() would apply to both. A send that times out raises
        # OSError, which broadcast() already handles by dropping the client.
        self.request.setsockopt(
            socket.SOL_SOCKET, socket.SO_SNDTIMEO,
            struct.pack("ll", SUBSCRIBER_SEND_TIMEOUT, 0))

        # Bring the newcomer up to date before it starts waiting for changes.
        # Done under subscribers_lock, together with joining the list, so no
        # broadcast can land between the snapshot and the send — otherwise a
        # "listening" sent in that gap would be overwritten by a stale "idle".
        # A failure here means the client vanished between connecting and
        # now, which the read loop below is about to notice anyway.
        focus_payload = focus_event()
        with subscribers_lock:
            subscribers.append(self.request)
            count = len(subscribers)
            try:
                with current_state_lock:
                    opening = dict(current_state)
                for event in (opening, focus_payload):
                    self.request.sendall((json.dumps(event) + "\n").encode())
            except OSError:
                pass
        log(f"gui subscriber connected ({count} total)")

        try:
            # Block until the client disconnects; broadcast() pushes events
            # to self.request directly from other threads in the meantime.
            while self.request.recv(1):
                pass
        except OSError:
            pass
        finally:
            with subscribers_lock:
                if self.request in subscribers:
                    subscribers.remove(self.request)
            log(f"gui subscriber disconnected ({len(subscribers)} total)")


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main():
    global model

    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    load_history()
    log(f"loaded {len(history)} history entries from {HISTORY_PATH}")

    if STT_BACKEND == "elevenlabs":
        # The import is inside the branch, not just the load: faster_whisper
        # pulls in ctranslate2 and costs ~50 MB before a single weight is read,
        # and the whole point of this backend is not to pay for a model it
        # will never call.
        log("transcribing with ElevenLabs — the local model is not loaded")
        log(f"long dictations are sent in {baatsun_stt.CHUNK_SECONDS}s slices "
            "while you talk, so a 5–10 minute brainstorm is not one 30s timeout")
        log("your audio leaves this machine on every dictation; "
            f"switch 'stt_backend' in {CONFIG_PATH} back to 'local' to stop that")
    else:
        from faster_whisper import WhisperModel

        # faster-whisper fetches the model into ~/.cache/huggingface on first
        # use; after that this is a local load.
        # Every core but one (capped): ctranslate2's own default is 4 threads,
        # which leaves most of a modern CPU idle while you wait.
        threads = max(1, min(8, (os.cpu_count() or 4) - 1))
        log(f"loading model {MODEL_SIZE} ({COMPUTE_TYPE}, cpu, {threads} threads"
            f"{', Hinglish' if HINGLISH else ''})...")
        try:
            model = WhisperModel(MODEL_SIZE, device="cpu",
                                 compute_type=COMPUTE_TYPE, cpu_threads=threads)
        except Exception as exc:
            # Starting anyway would leave a daemon whose hotkey silently does
            # nothing. Exit instead, with the reason on one readable line above
            # the traceback; systemd retries and gives up at its start limit.
            log(f"could not load the dictation model {MODEL_SIZE}: {exc}")
            log("dictation can't run without it — check your network and "
                f"restart, or point 'model_override' in {CONFIG_PATH} at a "
                "local model")
            raise
        log("model loaded")
        # Warm up once so the first real dictation doesn't also pay for
        # ctranslate2's lazy initialisation.
        try:
            import numpy as np
            list(model.transcribe(np.zeros(16000, dtype=np.float32),
                                  language=WHISPER_LANGUAGE, beam_size=1,
                                  without_timestamps=True)[0])
        except Exception as exc:  # noqa: BLE001 — warm-up is best effort
            log(f"warm-up skipped: {exc}")

    start_hotkey_listener()

    server = Server(SOCKET_PATH, Handler)
    os.chmod(SOCKET_PATH, 0o600)
    log(f"listening on {SOCKET_PATH}")

    stopping = threading.Event()

    def shutdown(_signum, _frame):
        # Only set a flag. A signal handler runs in the main thread, so calling
        # server.shutdown() here would deadlock: it blocks until the serve loop
        # exits, and if that loop is the thread executing this handler it never
        # gets back to notice the request. systemd then has to SIGKILL us. So
        # the serve loop gets its own thread and the main thread does the
        # stopping, below.
        log("shutting down")
        stopping.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    serve_thread = threading.Thread(target=server.serve_forever, name="serve")
    serve_thread.start()

    # Waiting with a timeout rather than indefinitely: a plain wait() is
    # interruptible by signals on the main thread, but the periodic wakeup makes
    # that guarantee something we don't have to rely on.
    while not stopping.wait(0.5):
        pass

    server.shutdown()
    serve_thread.join()
    server.server_close()
    abandon_recording()
    try:
        os.remove(SOCKET_PATH)
    except OSError:
        pass
    log("stopped")


if __name__ == "__main__":
    main()
