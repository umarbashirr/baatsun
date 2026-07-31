# Baatsun

*बात + सुन — "talk" + "listen".*

A voice dictation tool for Linux: **hold Ctrl+Super (Windows key) anywhere**,
speak, **release** — it transcribes locally (faster-whisper, CPU) and types
the text into whatever window or input box is focused (via `ydotool`). True
push-to-talk, not toggle. Runs fully offline; nothing you say ever leaves
your machine.

## Features

- **Push-to-talk, not toggle** — hold the hotkey, speak, release. No mode to
  forget you're in.
- **Fully offline** — transcription runs locally on CPU via
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper). No audio or
  text is sent anywhere.
- **Works anywhere** — types directly into whatever window has focus, so it
  works in any app, not just ones with dictation support built in.
- **No shortcut registration needed** — reads the keyboard directly below the
  compositor, so it works the same way on any desktop environment.
- **Tray icon + history window** — see live recording state at a glance, and
  browse/search/copy/retype past transcripts.
- **English or Hinglish** — dictate in English, or in Hinglish (Hindi and
  English mixed together), which is typed out in Roman script:
  "mujhe ek coffee chahiye".
- **Configurable** — language and hotkey combo are adjustable from the
  Settings panel; model size and compute type are tunable via config file or
  env var.

## Requirements

- Linux with [PipeWire](https://pipewire.org/) for audio capture (default on
  most modern distros, including Ubuntu 22.10+).
- Python 3.10+.
- [`ydotool`](https://github.com/ouija/ydotool) for typing into the focused
  window (works under both X11 and Wayland).
- GTK4 + libadwaita and PyGObject for the app window; GTK3 + AppIndicator
  bindings for the tray icon (optional — see below).
- Root/sudo access for one-time device permission setup (see below) — the
  daemon itself runs as your normal user afterwards.

Developed and tested on Ubuntu/GNOME; the core daemon doesn't depend on
GNOME specifically; the tray icon depends on your desktop supporting the
[AppIndicator](https://github.com/AyatanaIndicators) protocol (GNOME needs an
extension for this — see step 3 below).

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
   Reads/writes ~/.config/baatsun/config.json: language, model size, compute
   type, hotkey combo. baatsun.py reads it at startup (env vars still
   override, for anyone pinning values in systemd/baatsun.service);
   baatsun_gui.py's Settings panel writes language + hotkey and restarts the
   daemon to apply, preserving the model/compute-type keys it doesn't expose.
   resolve_language() maps the language profile to (model, whisper language
   token) so that pairing lives in exactly one place; it returns None for the
   model when the managed Hinglish model should be used.

src/baatsun_models.py (stdlib only)
   Fetches the Hinglish model on first use. The English profile just names a
   stock model and lets faster-whisper download it, but the Hinglish profile
   needs a CTranslate2 conversion that only exists as an artifact we publish
   ourselves — so ensure_hinglish_model() downloads the tarball from this
   repo's models-v1 release, checks it against a pinned sha256, extracts via a
   staging directory (so an interrupted run can't leave a half-model that
   looks valid), and hands baatsun.py a local path. Cached under
   ~/.cache/baatsun/models/.

src/baatsun_gui.py (GTK4 + libadwaita, system Python — needs PyGObject)
   The full app window: record/stop button, live state in the header
   ("Listening…"/"Transcribing…"), a search box that filters history live,
   and per-transcript copy/retype/delete actions. Fetches `history` on
   connect, then stays subscribed for live updates. A gear icon opens
   Settings (language + hotkey — writes baatsun_config and runs
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

Transcript history is persisted to `~/.local/share/baatsun/history.json` and
shown in `baatsun_gui.py`'s window. The daemon doesn't send any desktop
notifications — state (listening/transcribing/idle) is only broadcast over
the unix socket, which the tray icon glyph and the GUI header subtitle
reflect live.

**Why this shape:** GNOME on Wayland has no API for an app to grab a global
hotkey itself, and a GNOME custom keyboard shortcut only fires on key
*press*, never on release — no good for hold-to-talk. Reading the keyboard
directly via `evdev` (`/dev/input/eventN`) sits below the compositor, so it
sees real press/release events regardless of desktop environment, and
requires no shortcut to be registered anywhere. It's a passive read (no
`EVIOCGRAB`), so normal typing and your desktop's own shortcuts are
unaffected. Wayland also blocks synthetic key injection into arbitrary
windows for security, so text entry goes through `ydotool`, which writes
directly to `/dev/uinput` (kernel level) instead of going through the
compositor.

## Installation

### Quick install (Ubuntu/Debian)

No clone needed — this downloads the latest `.deb` from
[Releases](https://github.com/umarbashirr/baatsun/releases) and installs it
with `apt`:

```bash
curl -fsSL https://raw.githubusercontent.com/umarbashirr/baatsun/main/install.sh | sudo bash
```

This pulls in all system dependencies (`ydotool`, GTK4/libadwaita, PipeWire)
automatically via `apt`, builds the `faster-whisper`/`numpy` virtualenv under
`/opt/baatsun`, activates the ydotool udev rule, and adds you to the `input`
group. Watch the output at the end for next steps — typically:

1. Log out and back in (one-time, so the new `input` group membership takes
   effect).
2. `systemctl --user enable --now baatsun`

Then skip ahead to [Usage](#usage). The tray icon autostarts on your next
login (or run `baatsun-tray` now), and "Baatsun" shows up in your app
launcher.

Prefer to grab the file yourself instead of piping a script into `sudo`?
Download the `.deb` from the
[Releases page](https://github.com/umarbashirr/baatsun/releases) and run
`sudo apt install ./baatsun_*_all.deb`.

### Build from source

Only needed if you're hacking on baatsun itself — the quick install above is
the recommended path for normal use.

#### 1. Clone and install dependencies

```bash
git clone https://github.com/umarbashirr/baatsun.git
cd baatsun

sudo apt install -y python3-venv python3-pip ydotool

python3 -m venv venv
venv/bin/pip install faster-whisper numpy

mkdir -p ~/.local/bin
ln -sf "$(pwd)"/bin/baatsun-{gui,tray,toggle} ~/.local/bin/
```

The `ln -sf` step puts `baatsun-gui`/`baatsun-tray`/`baatsun-toggle` on your
`PATH` (assuming `~/.local/bin` is on it, the default on Ubuntu/GNOME) so the
desktop entries in steps 5 and 6 below can find them.

#### 2. Let ydotool write to /dev/uinput without root

This build of `ydotool` talks to `/dev/uinput` directly — there's no
`ydotoold` daemon involved, so the device needs to be group-writable instead
of running everything as root.

```bash
sudo cp systemd/60-ydotool.rules /etc/udev/rules.d/60-ydotool.rules
sudo udevadm control --reload-rules
sudo udevadm trigger /sys/class/misc/uinput  # or just reboot
sudo usermod -aG input "$USER"
```

**You must log out and back in** (group membership only applies to new login
sessions) before `ydotool type` will work without sudo.

Verify after re-login:

```bash
groups   # should list "input"
ydotool type "hello"   # click into any text field first
```

#### 3. Install and enable the daemon as a systemd user service

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

#### 4. Install the app window and tray icon dependencies

`src/baatsun_gui.py` (GTK4 + libadwaita) needs PyGObject and the GTK4/
libadwaita typelibs on your system Python:

```bash
sudo apt install -y python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
```

`src/baatsun_tray.py` additionally needs the AppIndicator typelib:

```bash
sudo apt install -y gir1.2-ayatanaappindicator3-0.1
```

On GNOME, tray icons also need the **"AppIndicator and KStatusNotifierItem
Support"** Shell extension — already enabled on Ubuntu's default GNOME
session (`ubuntu-appindicators@ubuntu.com`); on other GNOME setups, install
and enable that extension first, or skip the tray icon and just use
`baatsun-gui` directly.

#### 5. (Optional) autostart the tray icon on login

```bash
mkdir -p ~/.config/autostart
cp autostart/baatsun-tray.desktop ~/.config/autostart/
```

Without this, launch it manually with `baatsun-tray` (or run `baatsun-gui`
directly to just open the history window, no tray icon).

#### 6. (Optional) add "Baatsun" to your app launcher

```bash
mkdir -p ~/.local/share/applications
cp desktop/baatsun-gui.desktop ~/.local/share/applications/
```

Makes `baatsun-gui` launchable from your app launcher like a normal installed
app, in addition to the tray icon and running it from a terminal.

## Usage

1. Click into any text field.
2. Hold **Ctrl+Super** → recording starts (tray icon glyph switches to a
   record dot, and the GUI header shows "Listening…", if either is open).
3. Speak, while still holding both keys.
4. Release either key → "Transcribing…" then the text is typed in and
   appended to the history window.

`baatsun-toggle` also exists as a manual/scriptable alternative (sends a
toggle command over the daemon's unix socket) — useful for testing without
touching the keyboard, but not needed for day-to-day use.

### The app window

- **Tray icon** (`baatsun-tray`, or autostarted per step 5 above) — click
  it → "Show History" opens the window; the icon itself changes glyph for
  idle/listening/transcribing so you get feedback without opening anything.
- **Window only** (`baatsun-gui`, or the "Baatsun" entry in your app
  launcher per step 6) — skip the tray icon and open the window directly;
  closing it hides rather than quits, so re-opening it (from the tray, app
  launcher, or a terminal) re-presents the same window instead of starting a
  second one.

Inside the window:

- The record button in the header starts/stops recording — an on-screen
  alternative to holding Ctrl+Super — and the header subtitle shows
  "Listening…"/"Transcribing…" live.
- The search box filters history as you type.
- Each transcript row has copy / retype (re-runs `ydotool type` into
  whatever's currently focused) / delete buttons.
- The gear icon opens **Settings** — the dictation language (English /
  Hinglish) and the hotkey combo (Ctrl+Super / Ctrl+Alt / Alt+Super /
  Ctrl+Shift). Applying restarts the daemon
  (`systemctl --user restart baatsun.service`) to pick it up, which takes a
  few seconds while the model reloads — longer the first time you pick
  Hinglish, since the model (~62 MB) downloads then.

History persists to `~/.local/share/baatsun/history.json` across daemon
restarts; the toolbar's clear-history button wipes it.

## Configuration

All settings live in `~/.config/baatsun/config.json`; defaults are defined in
`src/baatsun_config.py`.

### Language and hotkey — Settings panel

- **language** — `english` (default) / `hinglish`.
- **hotkey** — `ctrl+super` (default) / `ctrl+alt` / `alt+super` /
  `ctrl+shift`.

The gear icon in `baatsun-gui` is the normal way to change these. It writes
the config file and restarts the daemon for you, leaving the values below
untouched.

**Hinglish** is for Hindi and English mixed together in one sentence, the way
it's actually spoken — "mujhe ek coffee chahiye", "meeting kal schedule kar
do". Output is **Roman script, not Devanagari**: you get `mujhe ek coffee
chahiye`, not `मुझे एक कॉफ़ी चाहिए`. That's deliberate — `ydotool` types by
mapping characters to US-layout keycodes, and Devanagari has no keycodes to
map to, so it would be silently dropped.

Picking Hinglish switches the daemon to a different model — a CTranslate2
build of
[`Oriserve/Whisper-Hindi2Hinglish-Swift`](https://huggingface.co/Oriserve/Whisper-Hindi2Hinglish-Swift)
(Apache-2.0), fine-tuned to emit romanized Hinglish. Being whisper-base sized,
it keeps roughly the same transcription latency as the English default.

The first time you select Hinglish, the daemon downloads that model (~62 MB)
from this repo's [`models-v1`](https://github.com/umarbashirr/baatsun/releases/tag/models-v1)
release into `~/.cache/baatsun/models/`, verifying it against a pinned
sha256. The hotkey won't respond until it finishes — watch
`journalctl --user -u baatsun.service -f` for progress. If the download fails
the daemon logs the reason and falls back to English rather than dying.

- **hinglish_model** — empty by default, meaning "use the managed model
  above". Set it to a faster-whisper model name, a HuggingFace CT2 repo id, or
  a local directory to override. Accuracy on the default is decent but below
  the English model; if you need better and can accept much slower
  transcription on CPU, point this at a CT2 conversion of Oriserve's larger
  `Apex` or `Prime` models.

### Model and compute type — config file or env var

These aren't exposed in the Settings panel; `base.en`/`int8` suits most
machines, and the wrong combination mostly just makes transcription slower.
Change them if you need to:

- **model** — faster-whisper size: `tiny.en` / `base.en` (default) /
  `small.en` (better accuracy, more latency) / `medium.en`.
- **compute_type** — `int8` (default, fastest on CPU) / `int8_float16` /
  `float16` / `float32`.

Either edit `~/.config/baatsun/config.json` directly and restart the daemon
(`systemctl --user restart baatsun.service`), or set the `BAATSUN_MODEL` and
`BAATSUN_COMPUTE_TYPE` env vars, which override the config file. Set those by
editing `systemd/baatsun.service` before step 3 if you built from source, or
with `systemctl --user edit baatsun.service` (adds a drop-in override, so it
survives package upgrades) if you used the `.deb`.

Switching to a model you haven't used before downloads it on the next daemon
start (~150 MB for `base.en`, more for the larger sizes), cached under
`~/.cache/huggingface`.

## Privacy

Baatsun runs entirely offline — audio never leaves your machine, and nothing
is logged beyond the transcript history you can see and clear yourself in
the app window.

One thing worth being explicit about: reading raw evdev means the daemon
sees every keystroke typed anywhere on your system, not just the hotkey. It
only *acts* on Ctrl/Super state and doesn't log anything else, but this is
effectively keylogger-capable code, so review `src/baatsun.py` yourself
before trusting it with anything sensitive.

## Troubleshooting

- **`ydotool type` does nothing / permission denied** — you likely haven't
  logged out and back in since being added to the `input` group (step 2
  above), or the udev rule didn't apply. Check `groups` includes `input`.
- **Hotkey doesn't trigger recording** — confirm the daemon is running
  (`systemctl --user status baatsun.service`) and check
  `journalctl --user -u baatsun.service -f` while pressing the hotkey for
  errors reading `/dev/input/eventN` (you may need to be in the `input`
  group for this too).
- **Tray icon doesn't appear** — on GNOME, make sure the AppIndicator Shell
  extension is enabled (step 4 above); on other desktops, confirm your
  status bar supports the AppIndicator/KStatusNotifierItem protocol.
- **Transcription is slow or inaccurate** — try a different `model`/
  `compute_type` combination (see [Configuration](#configuration)); smaller
  models are faster but less accurate, larger models are the reverse.
- **Nothing happens after switching to Hinglish** — the first run downloads a
  ~62 MB model and the hotkey stays unresponsive until it lands. Check
  `journalctl --user -u baatsun.service -f`; you should see download progress,
  then `model loaded`. If the download failed it says so and falls back to
  English; delete `~/.cache/baatsun/models/` to retry from scratch.
- **Hinglish transcribes pure English badly** — expected. The Hinglish model
  is tuned for Hindi-dominant speech; switch back to English in Settings when
  you're dictating only English.

## Roadmap / known limitations

- Swapping in a cloud STT API (OpenAI/Deepgram) only touches
  `stop_recording_and_transcribe()` in `src/baatsun.py` — the daemon/hotkey/
  ydotool plumbing stays the same.
- The hotkey combo is configurable (Settings panel or
  `~/.config/baatsun/config.json`) but limited to four curated pairs
  (`baatsun_config.HOTKEY_CHOICES`) rather than an arbitrary key — capturing
  an arbitrary combo would need a "press your new hotkey" UI flow in
  `baatsun_gui.py` that doesn't exist yet.
- Only English and Hinglish are offered. Adding a language that writes in a
  non-Latin script (Hindi in Devanagari, say) needs more than a new entry in
  `LANGUAGE_CHOICES`: `ydotool type` can only produce US-layout keycodes, so
  the typing path in `stop_recording_and_transcribe()` would have to be
  replaced with a clipboard-and-paste approach first.
- Switching language restarts the daemon and reloads the model, so it isn't
  instant — there's no per-recording language switch.

## Contributing

Issues and pull requests are welcome. If you're proposing a larger change
(e.g. a new backend, a different capture mechanism), please open an issue
first to discuss the approach — see the Architecture section above for how
the pieces fit together.

## Author

Umar Bashir Rather

## License

[MIT](LICENSE)
