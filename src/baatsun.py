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
from baatsun_config import (
    CONFIG_PATH,
    WHISPER_LANGUAGE,
    load_api_key,
    load_config,
    resolve_model,
)

config = load_config()

MODEL_SIZE = os.environ.get("BAATSUN_MODEL") or resolve_model(config)
COMPUTE_TYPE = os.environ.get("BAATSUN_COMPUTE_TYPE") or config["compute_type"]
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

HISTORY_DIR = os.path.expanduser("~/.local/share/baatsun")
HISTORY_PATH = os.path.join(HISTORY_DIR, "history.json")
HISTORY_LIMIT = 500

state_lock = threading.Lock()
state = {
    "recording": False,
    "proc": None,       # pw-record subprocess
    "wav_path": None,
}
hotkey_state = {
    "pressed": set(),   # keycodes currently held, across all keyboards
    "held": False,      # whether the ctrl+meta combo is currently active
}

# Last window the GNOME Shell extension told us had focus, used to decide
# whether a transcript is prose worth cleaning up or a coding prompt that must
# be typed verbatim. Stays empty on desktops without the extension, which
# baatsun_context reads as "developer" — the safe direction. Guarded by its own
# lock: it is written from socket threads and read mid-transcription.
focus_lock = threading.Lock()
focus = {"app": "", "title": ""}

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


def add_history_entry(text):
    global next_entry_id
    with history_lock:
        entry = {"id": next_entry_id, "text": text, "ts": time.time()}
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
    broadcast({"type": "state", "state": "listening"})
    log(f"recording started -> {wav_path}")


def maybe_clean(text):
    """Return text polished by OpenAI, or the original if that doesn't apply.

    Config is re-read per dictation rather than cached at startup so toggling
    cleanup in Settings takes effect immediately — the Settings panel restarts
    the daemon for the hotkey's sake, but this shouldn't depend on that.
    """
    cfg = load_config()
    if not cfg.get("cleanup_enabled"):
        return text

    api_key = load_api_key()
    if not api_key:
        log("cleanup is on but no API key is set — typing the raw transcript")
        return text

    with focus_lock:
        app, title = focus["app"], focus["title"]

    if not baatsun_context.should_clean(cfg, app, title):
        log(f"context {baatsun_context.classify(app, title)} ({app or 'unknown'}) "
            "— typing the raw transcript")
        return text

    # Deliberately no new state here: the pill, tray and GUI all treat an
    # unrecognised state as idle, so announcing "cleaning" would drop the pill
    # to rest and re-enable the record button while the request is still in
    # flight. Staying "transcribing" keeps the sweep running, which is what a
    # user waiting on text actually needs to see.
    cleaned = baatsun_cleanup.clean(
        text, api_key, cfg.get("cleanup_model") or "gpt-4o-mini", log=log,
        vocabulary=cfg.get("vocabulary") or "",
        line_breaks=(cfg.get("line_breaks", True)
                     and baatsun_context.allows_line_breaks(app, title)),
        hinglish=bool(cfg.get("hinglish")),
    )
    if cleaned is None:
        return text
    if cleaned != text:
        log(f"cleaned: {cleaned!r}")
    return cleaned


def stop_recording_and_transcribe():
    proc = state["proc"]
    wav_path = state["wav_path"]
    state["recording"] = False
    state["proc"] = None
    state["wav_path"] = None

    if proc is None or wav_path is None:
        return

    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    broadcast({"type": "state", "state": "transcribing"})

    try:
        if os.path.getsize(wav_path) < 1024:
            log("recording too short, skipping")
            broadcast({"type": "state", "state": "idle", "reason": "too_short"})
            return

        # initial_prompt biases the decoder toward names it would otherwise
        # mangle ("Claude" heard as "cloud"). Cheaper and far more reliable
        # than asking the cleanup model to spot the mistake afterwards, and it
        # works even with cleanup switched off.
        segments, _info = model.transcribe(
            wav_path,
            language=WHISPER_LANGUAGE,
            beam_size=1,
            initial_prompt=load_config().get("vocabulary") or None,
        )
        text = "".join(seg.text for seg in segments).strip()

        if not text:
            log("empty transcript")
            broadcast({"type": "state", "state": "idle", "reason": "empty"})
            return

        log(f"transcript: {text!r}")
        text = maybe_clean(text)
        subprocess.run(["ydotool", "type", "--", text], check=False)
        entry = add_history_entry(text)
        broadcast({"type": "transcript", "entry": entry})
        broadcast({"type": "state", "state": "idle"})
    finally:
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


def find_keyboard_devices():
    devices = []
    for path in list_devices():
        try:
            dev = InputDevice(path)
        except OSError:
            continue
        caps = dev.capabilities().get(ecodes.EV_KEY, [])
        if ecodes.KEY_A in caps and ecodes.KEY_LEFTCTRL in caps:
            devices.append(dev)
    return devices


def on_key_event(code, value):
    """value: 1=press, 0=release, 2=autorepeat (ignored)."""
    if value == 2:
        return
    with state_lock:
        pressed = hotkey_state["pressed"]
        if value == 1:
            pressed.add(code)
        else:
            pressed.discard(code)

        now_held = bool(pressed & PRIMARY_KEYS) and bool(pressed & SECONDARY_KEYS)
        if now_held and not hotkey_state["held"]:
            hotkey_state["held"] = True
            start_recording()
        elif not now_held and hotkey_state["held"]:
            hotkey_state["held"] = False
            stop_recording_and_transcribe()


def watch_device(dev):
    log(f"watching keyboard: {dev.path} ({dev.name})")
    try:
        for event in dev.read_loop():
            if event.type == ecodes.EV_KEY:
                on_key_event(event.code, event.value)
    except OSError as e:
        log(f"lost keyboard device {dev.path}: {e}")


def start_hotkey_listener():
    devices = find_keyboard_devices()
    if not devices:
        log("WARNING: no keyboard device found for hotkey listening")
        return
    for dev in devices:
        threading.Thread(target=watch_device, args=(dev,), daemon=True).start()


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
        with focus_lock:
            focus["app"] = app
            focus["title"] = title
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
    from faster_whisper import WhisperModel

    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    load_history()
    log(f"loaded {len(history)} history entries from {HISTORY_PATH}")

    # faster-whisper fetches the model into ~/.cache/huggingface on first use;
    # after that this is a local load.
    log(f"loading model {MODEL_SIZE} ({COMPUTE_TYPE}, cpu)...")
    try:
        model = WhisperModel(MODEL_SIZE, device="cpu", compute_type=COMPUTE_TYPE)
    except Exception as exc:
        # Starting anyway would leave a daemon whose hotkey silently does
        # nothing. Exit instead, with the reason on one readable line above
        # the traceback; systemd retries and gives up at its start limit.
        log(f"could not load the dictation model {MODEL_SIZE}: {exc}")
        log("dictation can't run without it — check your network and restart, "
            f"or point 'model_override' in {CONFIG_PATH} at a local model")
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
