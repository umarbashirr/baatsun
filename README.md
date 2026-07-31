# Baatsun

*बात + सुन — "talk" + "listen".*

A voice dictation tool for Linux: **hold Ctrl+Super (Windows key) anywhere**,
speak, **release** — it transcribes locally (faster-whisper, CPU) and types
the text into whatever window/input box is focused (via ydotool). True
push-to-talk, not toggle. Runs fully offline; nothing you say leaves your
machine.

## Architecture

```
src/baatsun.py (background daemon, systemd --user service)
   │
   ├─ reads /dev/input/eventN directly (evdev) for every keyboard device
   │      Ctrl+Super pressed  → pw-record starts capturing audio
   │      Ctrl+Super released → stop capture → faster-whisper transcribe
   │                            → ydotool type → append to transcript history
   │
   └─ unix socket at /run/user/$UID/baatsun.sock, multiple commands:
        toggle     — start/stop recording, sent by bin/baatsun-toggle and
                     baatsun_gui.py's record button
        status     — "recording" or "idle"
        history    — one-shot JSON dump of all past transcripts
        subscribe  — hold the connection open, stream newline-delimited
                     JSON events (state changes, new/deleted transcripts)
                     as they happen — this is what the GUI/tray apps use
        clear      — wipe all transcript history
        delete <id> — remove a single transcript by id
        retype <id> — ydotool-type a past transcript into the focused
                     window again

src/baatsun_config.py (stdlib only — shared by both Python interpreters below)
   Reads/writes ~/.config/baatsun/config.json: model size, compute type,
   hotkey combo. baatsun.py reads it at startup (env vars still override,
   for anyone pinning values in systemd/baatsun.service); baatsun_gui.py's
   Settings panel writes it and restarts the daemon to apply.

src/baatsun_gui.py (GTK4 + libadwaita, system Python — needs PyGObject)
   The full app window: record/stop button, live state in the header
   ("Listening…"/"Transcribing…"), a search box that filters history live,
   and per-transcript copy/retype/delete actions. Fetches `history` on
   connect, then stays subscribed for live updates. A gear icon opens
   Settings (model/compute-type/hotkey — writes baatsun_config and runs
   `systemctl --user restart baatsun.service`). Closing the window hides
   it rather than quitting, so the tray icon can re-present it instantly.

src/baatsun_tray.py (GTK3 + AppIndicator, separate process, system Python)
   Tray/status icon whose glyph reflects daemon state (idle/listening/
   transcribing) via the same subscribe stream. Menu: show history,
   toggle recording, quit. Runs as its own process because AppIndicator
   only speaks GTK3's Gtk.Menu, and GTK3 and GTK4 typelibs can't be
   loaded in the same Python process — "Show History" launches
   baatsun_gui.py as a subprocess, which is a no-op re-present rather than
   a second window if it's already running (GApplication D-Bus activation).
```

Transcript history used to only ever appear as a `notify-send` popup that
vanished after ~1.5s. It's now persisted to
`~/.local/share/baatsun/history.json` and shown in `baatsun_gui.py`'s window.
The daemon no longer sends any desktop notifications at all — state
(listening/transcribing/idle) is only broadcast over the unix socket, which
the tray icon glyph and the GUI header subtitle already reflect live.

Why this shape: GNOME on Wayland has no API for an app to grab a global
hotkey itself, and a GNOME custom keyboard shortcut only fires on key
*press*, never on release — no good for hold-to-talk. Reading the keyboard
directly via `evdev` (`/dev/input/eventN`) sits below the compositor, so it
sees real press/release events regardless of desktop environment, and
requires no shortcut to be registered anywhere. It's a passive read (no
`EVIOCGRAB`), so normal typing and GNOME's own shortcuts are unaffected.
Wayland also blocks synthetic key injection into arbitrary windows for
security, so text entry goes through `ydotool`, which writes directly to
`/dev/uinput` (kernel level) instead of going through the compositor.

## One-time setup

Already done: `sudo apt install -y python3.12-venv python3-pip ydotool`,
venv created at `venv/`, `faster-whisper` + `numpy` installed into it.

Two things are still needed, both require `sudo`:

### 1. Let ydotool write to /dev/uinput without root

This ydotool build (0.1.8) talks to `/dev/uinput` directly — there's no
`ydotoold` daemon in this version, so it needs the device to be group-writable
instead of running everything as root.

```bash
sudo cp systemd/60-ydotool.rules /etc/udev/rules.d/60-ydotool.rules
sudo udevadm control --reload-rules
sudo udevadm trigger /sys/class/misc/uinput  # or just reboot
sudo usermod -aG input "$USER"
```

**You must log out and back in** (group membership only applies to new
login sessions) before `ydotool type` will work without sudo.

Verify after re-login:

```bash
groups   # should list "input"
ydotool type "hello"   # click into any text field first
```

### 2. Install and enable the daemon as a systemd user service

```bash
mkdir -p ~/.config/systemd/user
cp systemd/baatsun.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now baatsun.service
```

Check it's alive and see the model load log:

```bash
systemctl --user status baatsun.service
journalctl --user -u baatsun.service -f
```

The first startup takes a few seconds while faster-whisper downloads the
`base.en` model (cached under `~/.cache/huggingface` afterwards).

No keyboard shortcut needs to be registered anywhere — the daemon watches
the keyboard directly.

### 3. Install the tray icon's GObject bindings

`src/baatsun_gui.py` (GTK4 + libadwaita) runs on this system's Python as-is —
those bindings were already present. `src/baatsun_tray.py` additionally needs
the AppIndicator typelib, which wasn't installed:

```bash
sudo apt install -y gir1.2-ayatanaappindicator3-0.1
```

On GNOME/Wayland, tray icons also need the **"AppIndicator and
KStatusNotifierItem Support"** Shell extension — already enabled on Ubuntu's
default GNOME session (`ubuntu-appindicators@ubuntu.com`); if you're on a
different GNOME setup and don't see the icon appear, install/enable that
extension first.

### 4. (Optional) autostart the tray icon on login

```bash
mkdir -p ~/.config/autostart
cp autostart/baatsun-tray.desktop ~/.config/autostart/
```

Without this, launch it manually with `bin/baatsun-tray` (or run
`bin/baatsun-gui` directly to just open the history window, no tray icon).

### 5. (Optional) add "Baatsun" to the GNOME app grid

```bash
mkdir -p ~/.local/share/applications
cp desktop/baatsun-gui.desktop ~/.local/share/applications/
```

Makes `bin/baatsun-gui` launchable from Activities/search like a normal
installed app, in addition to the tray icon and running it from a terminal.

## Using it

1. Click into any text field.
2. Hold **Ctrl+Super** → recording starts (tray icon glyph switches to a
   record dot, and the GUI header shows "Listening…", if either is open).
3. Speak, while still holding both keys.
4. Release either key → "Transcribing…" then the text is typed in and
   appended to the history window.

`bin/baatsun-toggle` still exists as a manual/scriptable alternative (sends a
toggle command over the daemon's unix socket) — useful for testing without
touching the keyboard, but not needed for day-to-day use.

### The app window

- **Tray icon** (`bin/baatsun-tray`, or autostarted per step 4 above) — click
  it → "Show History" opens the window; the icon itself changes glyph for
  idle/listening/transcribing so you get feedback without opening anything.
- **Window only** (`bin/baatsun-gui`, or the "Baatsun" entry in the app
  grid per step 5) — skip the tray icon and open the window directly;
  closing it hides rather than quits, so re-opening it (from the tray, app
  grid, or a terminal) re-presents the same window instead of starting a
  second one.

Inside the window:

- The record button in the header starts/stops recording — an on-screen
  alternative to holding Ctrl+Super — and the header subtitle shows
  "Listening…"/"Transcribing…" live.
- The search box filters history as you type.
- Each transcript row has copy / retype (re-runs `ydotool type` into
  whatever's currently focused) / delete buttons.
- The gear icon opens **Settings** — model size, compute type, and hotkey
  combo (Ctrl+Super / Ctrl+Alt / Alt+Super / Ctrl+Shift). Applying restarts
  the daemon (`systemctl --user restart baatsun.service`) to pick it up,
  which takes a few seconds while the model reloads.

History persists to `~/.local/share/baatsun/history.json` across daemon
restarts; the toolbar's clear-history button wipes it.

## Config

The Settings panel in `baatsun-gui` is the normal way to change these — it
writes `~/.config/baatsun/config.json` and restarts the daemon for you.
Defaults live in `src/baatsun_config.py`:

- **model** — faster-whisper size: `tiny.en` / `base.en` (default) /
  `small.en` (better accuracy, more latency) / `medium.en`.
- **compute_type** — `int8` (default, fastest on CPU) / `int8_float16` /
  `float16` / `float32`.
- **hotkey** — `ctrl+super` (default) / `ctrl+alt` / `alt+super` /
  `ctrl+shift`.

For scripted/headless setups, `BAATSUN_MODEL` and `BAATSUN_COMPUTE_TYPE` env
vars in `systemd/baatsun.service` still override the config file (there's no
env var for hotkey — use the Settings panel or edit config.json directly).

## Roadmap / known limitations

- Swapping in a cloud STT API (OpenAI/Deepgram) only touches
  `stop_recording_and_transcribe()` in `src/baatsun.py` — the daemon/hotkey/
  ydotool plumbing stays the same.
- The hotkey combo is configurable (Settings panel or
  `~/.config/baatsun/config.json`) but limited to four curated pairs
  (`baatsun_config.HOTKEY_CHOICES`) rather than an arbitrary key — capturing
  an arbitrary combo would need a "press your new hotkey" UI flow in
  baatsun_gui.py that doesn't exist yet.
- Reading raw evdev means the daemon sees every keystroke typed anywhere,
  not just the hotkey — it only acts on Ctrl/Super state, but this is worth
  being explicit about (with yourself and later with any other user) as a
  privacy/trust consideration, since it's effectively keylogger-capable code
  even though it isn't logging anything today.

## Author

Umar Bashir Rather — mail.umarbashir@gmail.com

## License

[MIT](LICENSE)
