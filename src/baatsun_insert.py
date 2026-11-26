"""Put a finished transcript into the focused window.

Two ways to do it:

* "paste" (the default): put the text on the clipboard and press Ctrl+V
  (Ctrl+Shift+V in a terminal). It is effectively instant whatever the length,
  and because nothing is typed key by key it works for any script — Devanagari,
  Urdu, emoji — which `ydotool type` cannot produce, since it only knows US
  keyboard keycodes. The clipboard you had before is put back afterwards when
  it held text.
* "type": `ydotool type`, one synthetic key press per character. Slower (each
  character is a key down/up pair with a delay between), but it does not touch
  the clipboard. Also the fallback whenever pasting is not possible.

Stdlib only. Every failure is reported back to the caller rather than raised,
so the daemon can tell the user nothing was inserted instead of recording a
dictation as typed when it never appeared.
"""
import os
import shutil
import subprocess
import threading
import time

# Linux input-event keycodes (linux/input-event-codes.h), for ydotool 1.x,
# whose `key` command takes "<code>:<1|0>" pairs rather than key names.
KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_V = 47

# Short, but not zero: some toolkits drop characters when keys arrive with no
# gap at all. ydotool's own default is 12ms per key.
TYPE_KEY_DELAY_MS = 4

# How long to wait after pressing paste before restoring the old clipboard. The
# target app reads the clipboard asynchronously when it handles Ctrl+V; put the
# old contents back too soon and it pastes those instead.
RESTORE_DELAY = 0.6

TERMINAL_APPS = {
    "alacritty", "kitty", "wezterm", "foot", "contour", "rio",
    "gnome-terminal-server", "org.gnome.terminal", "konsole", "xterm",
    "terminator", "tilix", "urxvt", "st", "ghostty", "warp", "ptyxis",
    "org.gnome.console", "kgx", "xfce4-terminal", "lxterminal", "qterminal",
    "terminology", "yakuake", "guake", "tilda", "blackbox",
}

_ydotool_major = None
_ydotool_lock = threading.Lock()


def is_terminal(app):
    app = (app or "").strip().lower()
    if not app:
        return False
    names = {app, *app.split(".")}
    return bool(names & TERMINAL_APPS)


def _run(argv, input_text=None, timeout=30):
    """Run a command; return (ok, error message)."""
    try:
        proc = subprocess.run(
            argv, input=input_text.encode() if input_text is not None else None,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout,
            check=False)
    except FileNotFoundError:
        return False, f"{argv[0]} is not installed"
    except subprocess.TimeoutExpired:
        return False, f"{argv[0]} timed out"
    except OSError as exc:
        return False, f"{argv[0]} failed: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()
        return False, f"{argv[0]} exited {proc.returncode}" + (f": {detail}" if detail else "")
    return True, ""


def ydotool_major():
    """1 for ydotool 1.x (keycode syntax, needs ydotoold), 0 for 0.1.x."""
    global _ydotool_major
    with _ydotool_lock:
        if _ydotool_major is not None:
            return _ydotool_major
        major = None
        try:
            out = subprocess.run(
                ["dpkg-query", "-W", "-f=${Version}", "ydotool"],
                capture_output=True, text=True, timeout=5, check=False).stdout
            if out.strip():
                major = 0 if out.strip().startswith("0.") else 1
        except (OSError, subprocess.TimeoutExpired):
            pass
        if major is None:
            # Not from a .deb. ydotoold only exists from 1.0 on.
            major = 1 if shutil.which("ydotoold") else 0
        _ydotool_major = major
        return major


def _press_paste(terminal):
    if ydotool_major() >= 1:
        if terminal:
            keys = [f"{KEY_LEFTCTRL}:1", f"{KEY_LEFTSHIFT}:1", f"{KEY_V}:1",
                    f"{KEY_V}:0", f"{KEY_LEFTSHIFT}:0", f"{KEY_LEFTCTRL}:0"]
        else:
            keys = [f"{KEY_LEFTCTRL}:1", f"{KEY_V}:1", f"{KEY_V}:0",
                    f"{KEY_LEFTCTRL}:0"]
        return _run(["ydotool", "key", *keys])
    return _run(["ydotool", "key", "ctrl+shift+v" if terminal else "ctrl+v"])


def _clipboard_tools():
    """(copy argv, read argv) for this session, or None if there is no way."""
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        read = ["wl-paste", "--no-newline"] if shutil.which("wl-paste") else None
        return ["wl-copy"], read
    if os.environ.get("DISPLAY"):
        if shutil.which("xclip"):
            return (["xclip", "-selection", "clipboard", "-in"],
                    ["xclip", "-selection", "clipboard", "-out"])
        if shutil.which("xsel"):
            return (["xsel", "--clipboard", "--input"],
                    ["xsel", "--clipboard", "--output"])
    return None


def _read_clipboard(read_argv):
    """The current clipboard if it is text, else None (left alone)."""
    if not read_argv:
        return None
    try:
        if read_argv[0] == "wl-paste":
            types = subprocess.run(["wl-paste", "--list-types"], capture_output=True,
                                   text=True, timeout=2, check=False).stdout
            if not any(t.startswith("text/") or t in ("UTF8_STRING", "STRING", "TEXT")
                       for t in types.split()):
                return None
        out = subprocess.run(read_argv, capture_output=True, timeout=2, check=False)
        if out.returncode != 0:
            return None
        return out.stdout.decode("utf-8")
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return None


def _copy(copy_argv, text):
    # wl-copy and xclip fork a process that keeps serving the selection, so
    # their stdout must not be a pipe we wait on.
    try:
        proc = subprocess.Popen(copy_argv, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        proc.communicate(text.encode(), timeout=5)
    except FileNotFoundError:
        return False, f"{copy_argv[0]} is not installed"
    except subprocess.TimeoutExpired:
        proc.kill()
        return False, f"{copy_argv[0]} timed out"
    except OSError as exc:
        return False, f"{copy_argv[0]} failed: {exc}"
    if proc.returncode != 0:
        return False, f"{copy_argv[0]} exited {proc.returncode}"
    return True, ""


def paste(text, app=None):
    tools = _clipboard_tools()
    if tools is None:
        return False, "no clipboard tool (install wl-clipboard, or xclip on X11)"
    copy_argv, read_argv = tools
    previous = _read_clipboard(read_argv)
    ok, err = _copy(copy_argv, text)
    if not ok:
        return False, err
    # Give the compositor a moment to announce the new selection.
    time.sleep(0.05)
    ok, err = _press_paste(is_terminal(app))
    if not ok:
        return False, err
    if previous is not None and previous != text:
        def restore():
            time.sleep(RESTORE_DELAY)
            _copy(copy_argv, previous)
        threading.Thread(target=restore, name="clipboard-restore", daemon=True).start()
    return True, ""


def type_text(text):
    ok, err = _run(["ydotool", "type", f"--key-delay={TYPE_KEY_DELAY_MS}", "--", text],
                   timeout=300)
    if not ok and "key-delay" in err:
        # An old build without the option: its default delay, but typed.
        ok, err = _run(["ydotool", "type", "--", text], timeout=300)
    return ok, err


def insert(text, app=None, method="paste", log=None):
    """Insert text into the focused window. Returns (ok, how or error)."""
    if not text:
        return True, "nothing"
    if method != "type":
        ok, err = paste(text, app)
        if ok:
            return True, "pasted"
        if log:
            log(f"paste failed ({err}) — typing instead")
    ok, err = type_text(text)
    if ok:
        return True, "typed"
    return False, err
