#!/usr/bin/env python3
"""Background voice-dictation daemon.

Listens on a unix socket for a "toggle" command (sent by bin/baatsun-toggle,
which is bound to a GNOME keyboard shortcut). First toggle starts recording
audio via pw-record; second toggle stops it, transcribes with a local
faster-whisper model, and types the result into the focused window via
ydotool.
"""
import json
import os
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

from evdev import InputDevice, ecodes, list_devices

import baatsun_cleanup
import baatsun_context
import baatsun_stt
from baatsun_config import (
    ACTIVATION_CHOICES,
    CONFIG_PATH,
    WHISPER_LANGUAGE,
    cleanup_ready,
    load_api_key,
    load_config,
    load_stt_api_key,
    resolve_model,
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
SOCKET_PATH = f"/run/user/{os.getuid()}/baatsun.sock"
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
}
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
watched_lock = threading.Lock()
watched_paths = set()

# Hybrid mode's dividing line between a tap and a hold. Below it, releasing the
# chord leaves the recording running until the next press; above it, the release
# ends the recording the way push-to-talk always has. Set so that a deliberate
# tap lands well under and the shortest useful held dictation — "yes", "no", a
# file name — lands well over.
TAP_SECONDS = 0.4

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
    os.makedirs(HISTORY_DIR, exist_ok=True)
    tmp_path = HISTORY_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(history, f)
    os.replace(tmp_path, HISTORY_PATH)


def add_history_entry(text, raw=None, app=None, context=None, secs=None,
                      usage=None):
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
        if raw is not None and raw != text:
            entry["raw"] = raw
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
        "cleanup": bool(cleanup_ready(cfg)
                        and baatsun_context.should_clean(cfg, app, title)),
    }


def log(msg):
    print(f"[baatsun] {msg}", file=sys.stderr, flush=True)


def start_recording():
    fd, wav_path = tempfile.mkstemp(prefix="baatsun-", suffix=".wav")
    os.close(fd)
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
    state["recording"] = True
    state["proc"] = proc
    state["wav_path"] = wav_path
    state["started"] = time.monotonic()
    timer = threading.Timer(MAX_RECORDING_SECONDS, on_recording_timeout, args=(wav_path,))
    timer.daemon = True
    state["timer"] = timer
    timer.start()
    broadcast_state("listening")
    log(f"recording started -> {wav_path}")


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
        stop_recording_and_transcribe()


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
    usage = {}
    cleaned = baatsun_cleanup.clean(
        text, api_key, cfg.get("cleanup_model") or "gpt-4o-mini", log=log,
        vocabulary=cfg.get("vocabulary") or "",
        line_breaks=(cfg.get("line_breaks", True)
                     and baatsun_context.allows_line_breaks(app, title)),
        hinglish=bool(cfg.get("hinglish")),
        strength=cfg.get("cleanup_strength") or "grammar",
        usage=usage,
    )
    # Empty when the request never got as far as an answer, which is a
    # different thing from a call that cost nothing.
    usage = usage or None
    if cleaned is None:
        return text, usage
    if cleaned != text:
        log(f"cleaned: {cleaned!r}")
    return cleaned, usage


def stop_recording_and_transcribe():
    cancel_recording_timer()
    proc = state["proc"]
    wav_path = state["wav_path"]
    started = state["started"]
    state["recording"] = False
    state["proc"] = None
    state["wav_path"] = None
    state["started"] = None
    # Monotonic, so a clock adjustment mid-dictation can't make this negative.
    secs = None if started is None else time.monotonic() - started

    if proc is None or wav_path is None:
        return

    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    broadcast_state("transcribing")

    # A remote transcription that fails takes the dictation with it — there is
    # no raw text to fall back on, the way a failed cleanup still has the
    # transcript. Keeping the wav turns that from "what you said is gone" into
    # "what you said is on disk", which is the difference between an annoyance
    # and a reason to distrust the tool.
    keep_wav = False
    try:
        if os.path.getsize(wav_path) < 1024:
            log("recording too short, skipping")
            broadcast_state("idle", reason="too_short")
            return

        vocabulary = load_config().get("vocabulary") or ""
        if STT_BACKEND == "elevenlabs":
            text = baatsun_stt.transcribe(
                wav_path, load_stt_api_key(),
                language=WHISPER_LANGUAGE, vocabulary=vocabulary, log=log)
            if text is None:
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
            segments, _info = model.transcribe(
                wav_path,
                language=WHISPER_LANGUAGE,
                beam_size=1,
                initial_prompt=vocabulary or None,
            )
            text = "".join(seg.text for seg in segments).strip()

        if not text:
            log("empty transcript")
            broadcast_state("idle", reason="empty")
            return

        log(f"transcript: {text!r}")
        # Read the focused window once and use it for both decisions. Two
        # separate reads could disagree if the user switched windows mid-
        # cleanup, and an entry that says "verbatim" about text that was in
        # fact rewritten is worse than one that is a window out of date.
        with focus_lock:
            app, title = focus["app"], focus["title"]
        context = baatsun_context.classify(app, title)

        raw = text
        text, usage = maybe_clean(text, app, title)
        subprocess.run(["ydotool", "type", "--", text], check=False)
        entry = add_history_entry(text, raw, app=app, context=context,
                                  secs=secs, usage=usage)
        broadcast({"type": "transcript", "entry": entry})
        broadcast_state("idle")
    finally:
        if not keep_wav:
            try:
                os.remove(wav_path)
            except OSError:
                pass


def abandon_recording():
    """Stop an in-flight recording without transcribing it, for shutdown.

    Nothing else will ever come along to end this recording, so without it we
    leave an orphaned pw-record holding the microphone and a stray wav in
    /tmp. Discarding rather than transcribing is deliberate: we're on our way
    out and there'd be no window left to type into.

    Takes state_lock, so a transcription already under way finishes and gets
    typed first rather than being cut off mid-word.
    """
    with state_lock:
        cancel_recording_timer()
        proc = state["proc"]
        wav_path = state["wav_path"]
        state["recording"] = False
        state["proc"] = None
        state["wav_path"] = None

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
            stop_recording_and_transcribe()
            return "stopped"
        else:
            start_recording()
            return "started"


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
        if ecodes.KEY_A in caps and ecodes.KEY_LEFTCTRL in caps:
            devices.append(dev)
        else:
            # Every mouse, lid switch and power button gets opened by this
            # scan, and the scan now repeats forever. Closing the ones we don't
            # want keeps the daemon from running itself out of file
            # descriptors.
            close_device(dev)
    return devices


def close_device(dev):
    try:
        dev.close()
    except OSError:
        pass


def refresh_chord():
    """Re-check whether the chord is held and fire the edge. Callers hold state_lock.

    Keys are tracked per keyboard but tested as one pool, so half a chord on
    the laptop keyboard and half on an external one still counts.
    """
    held = set()
    for keys in hotkey_state["pressed"].values():
        held |= keys

    now_held = bool(held & PRIMARY_KEYS) and bool(held & SECONDARY_KEYS)
    if now_held and not hotkey_state["held"]:
        hotkey_state["held"] = True
        hotkey_state["press_time"] = time.monotonic()
        on_hotkey_press()
    elif not now_held and hotkey_state["held"]:
        hotkey_state["held"] = False
        on_hotkey_release()


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
        refresh_chord()


def forget_device(path):
    """Drop the keys a vanished keyboard was holding.

    Unplug a keyboard mid-chord and its release events never arrive. Left in
    place those phantom keys hold the chord down forever: a hold-mode recording
    would run until the MAX_RECORDING_SECONDS backstop, and every later press
    would do nothing because the chord never looked released. Dropping them
    re-checks the chord, so an unplug ends a held recording the way letting go
    would.
    """
    with state_lock:
        if hotkey_state["pressed"].pop(path, None):
            refresh_chord()


def on_hotkey_press():
    """The chord was just formed. Callers already hold state_lock.

    Calls the start/stop pair directly rather than going through
    handle_toggle(), which takes the state_lock we are already inside —
    threading.Lock is not reentrant, so that would deadlock the keyboard thread
    and leave the hotkey dead until the daemon restarts.
    """
    if ACTIVATION == "hold":
        start_recording()
        return

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
        stop_recording_and_transcribe()
    else:
        start_recording()


def on_hotkey_release():
    """The chord was just broken. Callers already hold state_lock."""
    if ACTIVATION == "hold":
        stop_recording_and_transcribe()
        return

    # Toggle ignores releases entirely; so does hybrid once a tap has latched,
    # and when the press already stopped the recording.
    if ACTIVATION != "hybrid" or hotkey_state["latched"] or not state["recording"]:
        return

    # This is the release that decides which gesture the user made. Held long
    # enough to be deliberate, so end it here like push-to-talk. Otherwise it
    # was a tap: leave the recording up for the next press to stop.
    if time.monotonic() - hotkey_state["press_time"] >= TAP_SECONDS:
        stop_recording_and_transcribe()
    else:
        hotkey_state["latched"] = True
        log("tap — recording latched, press the hotkey again to stop")


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
    while True:
        time.sleep(DEVICE_SCAN_SECONDS)
        try:
            scan_keyboards()
        except OSError as e:
            log(f"keyboard rescan failed: {e}")


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


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        # Generous because "focus" carries a window title, which can be long;
        # every other command is a word and an integer.
        data = self.request.recv(8192).decode("utf-8", "replace").strip()
        command, _, arg = data.partition(" ")
        if command == "toggle":
            result = handle_toggle()
            self.request.sendall(result.encode())
        elif command == "status":
            self.request.sendall(("recording" if state["recording"] else "idle").encode())
        elif command == "history":
            with history_lock:
                payload = json.dumps(history)
            self.request.sendall(payload.encode() + b"\n")
        elif command == "clear":
            clear_history()
            broadcast({"type": "history_cleared"})
            self.request.sendall(b"ok")
        elif command == "delete":
            self.handle_delete(arg)
        elif command == "retype":
            self.handle_retype(arg)
        elif command == "focus":
            self.handle_focus(arg)
        elif command == "subscribe":
            self.handle_subscribe()
        else:
            self.request.sendall(b"unknown command")

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
            self.request.sendall(b"bad_id")
            return
        if delete_history_entry(entry_id):
            broadcast({"type": "deleted", "id": entry_id})
            self.request.sendall(b"ok")
        else:
            self.request.sendall(b"not_found")

    def handle_retype(self, arg):
        try:
            entry_id = int(arg)
        except ValueError:
            self.request.sendall(b"bad_id")
            return
        entry = find_history_entry(entry_id)
        if entry is None:
            self.request.sendall(b"not_found")
            return
        subprocess.run(["ydotool", "type", "--", entry["text"]], check=False)
        self.request.sendall(b"ok")

    def handle_subscribe(self):
        with subscribers_lock:
            subscribers.append(self.request)
        log(f"gui subscriber connected ({len(subscribers)} total)")

        # Bring the newcomer up to date before it starts waiting for changes.
        # Sent only to this socket: everyone else already knows. A failure here
        # means the client vanished between connecting and now, which the read
        # loop below is about to notice anyway.
        try:
            with current_state_lock:
                opening = dict(current_state)
            for event in (opening, focus_event()):
                self.request.sendall((json.dumps(event) + "\n").encode())
        except OSError:
            pass

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
        log("your audio leaves this machine on every dictation; "
            f"switch 'stt_backend' in {CONFIG_PATH} back to 'local' to stop that")
    else:
        from faster_whisper import WhisperModel

        # faster-whisper fetches the model into ~/.cache/huggingface on first
        # use; after that this is a local load.
        log(f"loading model {MODEL_SIZE} ({COMPUTE_TYPE}, cpu)...")
        try:
            model = WhisperModel(MODEL_SIZE, device="cpu",
                                 compute_type=COMPUTE_TYPE)
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
