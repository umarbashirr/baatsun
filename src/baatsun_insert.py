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
KEY_ENTER = 28
KEY_BACKSPACE = 14
KEY_C = 46
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


def _press_copy(terminal):
    if ydotool_major() >= 1:
        mods = [KEY_LEFTCTRL] + ([KEY_LEFTSHIFT] if terminal else [])
        keys = [f"{k}:1" for k in mods] + [f"{KEY_C}:1", f"{KEY_C}:0"]
        keys += [f"{k}:0" for k in reversed(mods)]
        return _run(["ydotool", "key", *keys])
    return _run(["ydotool", "key", "ctrl+shift+c" if terminal else "ctrl+c"])


# Put on the clipboard before pressing copy, so "nothing was selected" can be
# told apart from "the selection happens to equal what was already copied".
_SENTINEL = "\u2063baatsun-no-selection\u2063"


def copy_selection(app=None):
    """The text currently selected in the focused window, or None.

    Returns (selection or None, the clipboard as it was before, or None).
    The caller hands that second value to paste(restore=...) so a voice edit
    leaves your clipboard as it found it.
    """
    tools = _clipboard_tools()
    if tools is None:
        return None, None
    copy_argv, read_argv = tools
    if not read_argv:
        return None, None
    previous = _read_clipboard(read_argv)
    ok, _err = _copy(copy_argv, _SENTINEL)
    if not ok:
        return None, previous
    time.sleep(0.05)
    ok, _err = _press_copy(is_terminal(app))
    if not ok:
        return None, previous
    # The app answers the copy asynchronously; give it a moment, then look.
    selection = None
    for _ in range(6):
        time.sleep(0.05)
        current = _read_clipboard(read_argv)
        if current is not None and current != _SENTINEL:
            selection = current
            break
    if selection is None and previous is not None:
        _copy(copy_argv, previous)
    return (selection if selection and selection.strip() else None), previous


def delete_chars(count):
    """Press Backspace count times, to take back text we just inserted."""
    if count <= 0:
        return True, ""
    if ydotool_major() >= 1:
        # Batched, so a long dictation doesn't build one enormous argv.
        for start in range(0, count, 200):
            n = min(200, count - start)
            ok, err = _run(["ydotool", "key", *([f"{KEY_BACKSPACE}:1", f"{KEY_BACKSPACE}:0"] * n)])
            if not ok:
                return ok, err
        return True, ""
    return _run(["ydotool", "key", "--key-delay", "2", "--repeat", str(count), "backspace"],
                timeout=120)


def paste(text, app=None, restore=None):
    tools = _clipboard_tools()
    if tools is None:
        return False, "no clipboard tool (install wl-clipboard, or xclip on X11)"
    copy_argv, read_argv = tools
    previous = restore if restore is not None else _read_clipboard(read_argv)
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


def _press_shift_enter():
    if ydotool_major() >= 1:
        return _run(["ydotool", "key", f"{KEY_LEFTSHIFT}:1", f"{KEY_ENTER}:1",
                     f"{KEY_ENTER}:0", f"{KEY_LEFTSHIFT}:0"])
    return _run(["ydotool", "key", "shift+enter"])


def type_soft(text):
    """Type text with every line break pressed as Shift+Enter.

    ydotool types "\n" as a bare Enter, which runs a shell command or submits
    a prompt to a coding assistant halfway through. Shift+Enter is a newline
    in those prompts and in editors alike.
    """
    for i, line in enumerate(text.split("\n")):
        if i:
            ok, err = _press_shift_enter()
            if not ok:
                return False, err
        if line:
            ok, err = type_text(line)
            if not ok:
                return False, err
    return True, ""


def insert(text, app=None, method="paste", log=None, soft_newlines=False,
           restore=None):
    """Insert text into the focused window. Returns (ok, how or error).

    soft_newlines: line breaks must not arrive as Enter (terminals, editors).
    Pasting already satisfies that — a terminal's bracketed paste inserts
    newlines without running anything — so a multi-line paste into a terminal
    is tried even when "type" is the chosen method, and typing presses
    Shift+Enter for each break.
    """
    if not text:
        return True, "nothing"
    multiline = "\n" in text
    if method != "type" or (soft_newlines and multiline and is_terminal(app)):
        ok, err = paste(text, app, restore=restore)
        if ok:
            return True, "pasted"
        if log:
            log(f"paste failed ({err}) — typing instead")
    if soft_newlines and multiline:
        ok, err = type_soft(text)
    else:
        ok, err = type_text(text)
    if ok:
        return True, "typed"
    return False, err
