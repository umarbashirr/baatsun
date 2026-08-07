# Baatsun

*बात + सुन — "talk" + "listen".*

A voice dictation tool for Linux: **hold Ctrl+Super (Windows key) anywhere**,
speak, **release** — it transcribes locally (faster-whisper, CPU) and types
the text into whatever window or input box is focused (via `ydotool`). True
push-to-talk, not toggle. Transcription runs fully offline; nothing you say
ever leaves your machine unless you opt in to the OpenAI cleanup pass, which
sends the transcript text only.

## Features

- **Push-to-talk, not toggle** — hold the hotkey, speak, release. No mode to
  forget you're in.
- **Offline transcription** — runs locally on CPU via
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Your audio is
  never sent anywhere, and by default neither is the text.
- **Works anywhere** — types directly into whatever window has focus, so it
  works in any app, not just ones with dictation support built in.
- **No shortcut registration needed** — reads the keyboard directly below the
  compositor, so it works the same way on any desktop environment.
- **Always-on-top pill** — a thin bar pinned to the bottom centre of the
  screen, above every window (including fullscreen ones), that never steals
  focus: dim at rest, red and breathing while listening, blue and sweeping
  while transcribing.
- **Tray icon + history window** — see live recording state at a glance, and
  browse/search/copy/retype past transcripts.
- **Accurate English** — whisper `small.en`, int8 on CPU: the smallest model
  that gets every word right and punctuates properly, at ~1.6s per dictation.
- **Optional cleanup pass** — tidies punctuation and filler words via OpenAI
  `gpt-4o-mini`, but only when the focused window is prose (LinkedIn, X, Slack).
  Terminals and editors are always typed verbatim. Your audio never leaves the
  machine; only the transcript text is sent, and only if you enable it.
- **Configurable** — hotkey combo is adjustable from the Settings panel; the
  model and compute type are tunable via config file or env var.

## Requirements

- Linux with [PipeWire](https://pipewire.org/) for audio capture (default on
  most modern distros, including Ubuntu 22.10+).
- Python 3.10+.
- [`ydotool`](https://github.com/ouija/ydotool) for typing into the focused
  window (works under both X11 and Wayland).
- GTK4 + libadwaita and PyGObject for the app window; GTK3 + AppIndicator
  bindings for the tray icon (optional — see below).
- For the pill: on GNOME, the bundled GNOME Shell extension (no extra
  packages); elsewhere, [gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell)
  (optional — see below).
- Root/sudo access for one-time device permission setup (see below) — the
  daemon itself runs as your normal user afterwards.

Developed and tested on Ubuntu/GNOME; the core daemon doesn't depend on
GNOME specifically. The pill needs either GNOME (via the bundled Shell
extension) or a Wayland compositor that implements `wlr-layer-shell`
(sway, Hyprland, etc.) — plain X11 window managers can't host it. The tray
icon depends on your desktop supporting the
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
        focus <json> — record which window has focus ({app, title}), sent by
                     the GNOME extension on every focus/title change; decides
                     whether a transcript is cleaned up or typed verbatim

src/baatsun_config.py (stdlib only — shared by both Python interpreters below)
   Reads/writes ~/.config/baatsun/config.json: model override, compute type,
   hotkey combo. baatsun.py reads it at startup (env vars still override, for
   anyone pinning values in systemd/baatsun.service); baatsun_gui.py's Settings
   panel writes the hotkey and restarts the daemon to apply, preserving the
   model/compute-type keys it doesn't expose. resolve_model() returns the
   override if one is set and DEFAULT_MODEL otherwise; either way it's a name
   faster-whisper resolves and downloads itself, so this module stays
   stdlib-only and the GUI's system Python can import it too. load_config()
   drops keys that aren't in DEFAULT_CONFIG, which is what retires the
   `language`/`hinglish_model`/`model` keys from the versions that had a
   Hinglish mode, instead of letting an old one quietly pin the model.

src/baatsun_context.py (stdlib only)
   Maps the focused window (class + title, reported by the GNOME extension) to
   "developer" or "prose", deciding whether a transcript gets the cleanup pass.
   Defaults to "developer" for anything unrecognised — cleaning a post that
   didn't need it costs a re-read, but rewriting a coding prompt destroys the
   specifics that made it work, so the safe direction is verbatim.

src/baatsun_cleanup.py (stdlib only — urllib, no new venv dependency)
   The optional OpenAI polish pass over a transcript. Takes a string, never a
   wav: the audio stays on this machine by construction. Every failure path
   returns None and the daemon types the raw transcript, so a dead network can
   never cost you a dictation.

src/baatsun_gui.py (GTK4 + libadwaita, system Python — needs PyGObject)
   The full app window: record/stop button, live state in the header
   ("Listening…"/"Transcribing…"), a search box that filters history live,
   and per-transcript copy/retype/delete actions. Fetches `history` on
   connect, then stays subscribed for live updates. A gear icon opens
   Settings (hotkey, cleanup toggle/scope, and the OpenAI API key — writes
   baatsun_config and runs `systemctl --user restart baatsun.service`; the key
   goes to its own 0600 file, not config.json). Closing the window hides
   it rather than quitting, so the tray icon can re-present it instantly.

src/baatsun_tray.py (GTK3 + AppIndicator, separate process, system Python)
   Tray/status icon whose glyph reflects daemon state (idle/listening/
   transcribing) via the same subscribe stream. Menu: show history,
   toggle recording, quit. Runs as its own process because AppIndicator
   only speaks GTK3's Gtk.Menu, and GTK3 and GTK4 typelibs can't be
   loaded in the same Python process — "Show History" launches
   baatsun_gui.py as a subprocess, which is a no-op re-present rather than
   a second window if it's already running (GApplication D-Bus activation).

src/baatsun_pill.py (GTK4 + gtk4-layer-shell, non-GNOME fallback)
   The bottom-centre pill for wlr-layer-shell compositors (sway, Hyprland,
   ...): an undecorated GTK4 window anchored to the bottom of the output on
   the overlay layer, keyboard mode NONE and an empty input region so it can
   never take focus or eat a click. Cairo-draws the pill itself (rounded
   rect, glow, breathing/sweep animation) driven off the frame clock, fed by
   the same subscribe stream as the tray. Not usable on GNOME: mutter
   implements neither wlr-layer-shell nor client-side window positioning.

gnome-extension/baatsun@umarbashirr.github.io/ (GJS, runs inside GNOME Shell)
   The pill's GNOME implementation — a Clutter actor added to the Shell's
   own chrome via Main.layoutManager.addChrome(), which is the only way to
   sit above every window (including fullscreen ones) and stay
   focus-transparent on GNOME/Wayland. Reads the same unix socket directly
   via GJS's Gio bindings; no extra IPC. bin/baatsun-pill enables this
   extension on GNOME instead of launching baatsun_pill.py. It also reports the
   focused window's class and title to the daemon on every focus (and title)
   change — on Wayland the Shell is the only thing that can see this, and it's
   what lets baatsun_context tell a coding prompt from a LinkedIn post.
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

Then skip ahead to [Usage](#usage). The pill autostarts on your next login
(on GNOME this enables the bundled Shell extension; elsewhere it launches
`baatsun-pill` if `gtk4-layer-shell` is installed — see
[The pill](#the-pill) if it doesn't appear). The tray icon is installed but
no longer autostarts by default; run `baatsun-tray` if you want it too.
"Baatsun" shows up in your app launcher either way.

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
ln -sf "$(pwd)"/bin/baatsun-{gui,tray,toggle,pill} ~/.local/bin/
```

The `ln -sf` step puts `baatsun-gui`/`baatsun-tray`/`baatsun-toggle`/
`baatsun-pill` on your `PATH` (assuming `~/.local/bin` is on it, the default
on Ubuntu/GNOME) so the desktop entries in steps 5 and 6 below can find them.

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

The first startup downloads the dictation model (~250 MB, cached under
`~/.cache/huggingface` afterwards) and the hotkey won't respond until that
lands — watch the log for `model loaded`. Later starts take a couple of
seconds.

No keyboard shortcut needs to be registered anywhere — the daemon watches
the keyboard directly.

#### 4. Install the app window, tray icon, and pill dependencies

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

For the pill:

- **On GNOME**, install the bundled Shell extension:
  ```bash
  gnome-extension/install.sh
  ```
  then log out and back in (Wayland can't reload the Shell in place). No
  extra packages needed.
- **On sway/Hyprland/other `wlr-layer-shell` compositors**, install
  `gtk4-layer-shell` — package names vary by distro:
  ```bash
  sudo apt install gir1.2-gtk4layershell-1.0   # Ubuntu 25.04+/Debian 13+
  sudo pacman -S gtk4-layer-shell               # Arch
  sudo dnf install gtk4-layer-shell             # Fedora
  ```
  Not in Ubuntu 24.04's archive; on older Ubuntu, build it from source or
  skip the pill and use the tray icon instead.
- **On GNOME/Xorg or a plain X11 window manager**, there's no supported way
  to host the pill — skip it and use the tray icon/window instead.

#### 5. (Optional) autostart the pill and/or tray icon on login

```bash
mkdir -p ~/.config/autostart
cp autostart/baatsun-pill.desktop ~/.config/autostart/
cp autostart/baatsun-tray.desktop ~/.config/autostart/   # optional, tray icon
```

`baatsun-pill` at login detects GNOME automatically and enables the Shell
extension instead of opening a window there. Without this entry, bring up
the pill manually with `baatsun-pill`, or the tray/window with
`baatsun-tray`/`baatsun-gui`.

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

### The pill

A thin bar pinned to the bottom centre of the screen, above every window
(including fullscreen ones): dim grey at rest, red and breathing while
listening, blue with a sweeping highlight while transcribing, and barely
visible if the daemon isn't reachable. It's the fastest way to confirm the
hotkey registered — no window to open, and it can't steal your keyboard
focus, so the transcript still lands wherever you were typing.

It autostarts on login (see [Installation](#installation)). Where it comes
from depends on your desktop:

- **GNOME** — a bundled GNOME Shell extension. `baatsun-pill` just makes
  sure it's enabled; there's no separate window or process to manage.
- **sway/Hyprland/other `wlr-layer-shell` compositors** — `baatsun-pill`
  opens a GTK4 window via `gtk4-layer-shell`.
- **X11, or a Wayland compositor without layer-shell support** — not
  available; use the tray icon or app window instead.

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
- The gear icon opens **Settings** — the hotkey combo (Ctrl+Super / Ctrl+Alt /
  Alt+Super / Ctrl+Shift). Applying restarts the daemon
  (`systemctl --user restart baatsun.service`) to pick it up, which takes a
  few seconds while the model reloads.

History persists to `~/.local/share/baatsun/history.json` across daemon
restarts; the toolbar's clear-history button wipes it.

## Configuration

All settings live in `~/.config/baatsun/config.json`; defaults are defined in
`src/baatsun_config.py`.

### Hotkey — Settings panel

- **hotkey** — `ctrl+super` (default) / `ctrl+alt` / `alt+super` /
  `ctrl+shift`.

The gear icon in `baatsun-gui` is the normal way to change this. It writes the
config file and restarts the daemon for you, leaving the values below
untouched.

### The model

Baatsun dictates English with whisper `small.en`, picked by measuring the
alternatives on this machine rather than by reaching for the biggest one.
Whisper's encoder always runs on a padded 30-second window, so latency is set
by model size and lands on *every* dictation however short it was:

| model | latency | on a clean 11s test clip |
|---|---|---|
| Hinglish-Swift (the old default) | 0.67s | substitutes a word, no punctuation |
| `base.en` | 0.75s | substitutes two words |
| **`small.en`** | **1.64s** | **every word right, punctuated** |
| `distil-small.en` | 1.51s | dropped an entire clause — avoid |
| `distil-medium.en` | 3.94s | correct |
| `distil-large-v3.5` | 5.12s | exact, best punctuation |

`small.en` is the first rung that gets the words right, and going further up
costs 3-6x the latency for a marginal gain. `distil-large-v3.5` is the sensible
`model_override` if you want the best available and don't mind the wait.

faster-whisper knows the name and downloads it on first use (~250 MB) into
`~/.cache/huggingface`. The hotkey won't respond until that lands — watch
`journalctl --user -u baatsun.service -f`, which prints `model loaded` when the
daemon is ready. Later starts load from that cache in about a second.

Whisper is decoded with the language pinned to `en` rather than auto-detected,
which skips a detection pass on every recording and stops a mumbled first word
from sending the decode off into another language.

### Model override and compute type — config file or env var

Neither is exposed in the Settings panel; the defaults suit most machines, and
the wrong compute type mostly just makes transcription slower. Change them if
you need to:

- **model_override** — empty by default, meaning `small.en` above. Set it to
  another faster-whisper model name, a HuggingFace CT2 repo id, or a local
  directory. `distil-large-v3.5` (~1.4 GB) is the step up; `base.en` (~150 MB)
  the step down. See the latency table above before choosing.
- **compute_type** — `int8` (default, fastest on CPU) / `int8_float16` /
  `float16` / `float32`.

Either edit `~/.config/baatsun/config.json` directly and restart the daemon
(`systemctl --user restart baatsun.service`), or set the `BAATSUN_MODEL` and
`BAATSUN_COMPUTE_TYPE` env vars, which override the config file. Set those by
editing `systemd/baatsun.service` before step 3 if you built from source, or
with `systemctl --user edit baatsun.service` (adds a drop-in override, so it
survives package upgrades) if you used the `.deb`.

Whichever model you name, faster-whisper downloads it on the next daemon start
and caches it under `~/.cache/huggingface`.

Upgrading from a version that had the English/Hinglish switch: the old
`language`, `model` and `hinglish_model` keys in your config are ignored and
dropped the next time settings are saved. Nothing to do by hand. The retired
Hinglish model in `~/.cache/baatsun/models/` is no longer used and can be
deleted.

### Cleanup with OpenAI — Settings panel

Off by default. When enabled, a transcript is passed through OpenAI
`gpt-4o-mini` before being typed, to fix punctuation, capitalisation and filler
words ("um", "you know") and to break run-on speech into sentences. It's told to
preserve your wording and to leave technical terms, identifiers and file paths
exactly as dictated.

**Only the transcript text is sent. The audio never leaves your machine** —
that's the point of transcribing locally and only polishing remotely.

Enable it in the gear icon → **Cleanup with OpenAI**:

- **Clean up transcripts** — the on/off switch.
- **Apply to** — *Prose windows only* (default) or *Everything I dictate*.
- **OpenAI API key** — stored in `~/.config/baatsun/openai.key` mode 0600, never
  in `config.json`. `OPENAI_API_KEY` in the environment takes precedence if set.
- **Test** — round-trips one short request and reports whether the key works.

#### How "prose windows only" decides

The bundled GNOME Shell extension reports the focused window's class and title
to the daemon (on Wayland nothing outside the Shell can see this), and
`src/baatsun_context.py` maps it to `developer` or `prose`:

| focused window | verdict |
|---|---|
| terminals, VS Code, JetBrains IDEs, editors | developer — typed verbatim |
| browser on LinkedIn / X / Reddit / Gmail | prose — cleaned |
| browser on GitHub / localhost / Jira / CI | developer — typed verbatim |
| Slack, Discord, Telegram, mail clients | prose — cleaned |
| **anything unrecognised, or no extension** | **developer — typed verbatim** |

That default is deliberately asymmetric. Cleaning a post that didn't need it
costs you a re-read; "cleaning" a coding prompt rewrites the specifics that made
it work. So an unknown window, a missing focus report, or a non-GNOME desktop
all fall through to typing exactly what you said.

Every failure path — no key, bad key, network down, timeout, implausible
response — types the raw transcript instead. A dictation is never lost to a
failed API call.

#### Cost

Cleanup is billed per token, and dictation is short, so this is cheap: roughly
**$0.03/month** at 2 minutes of speech a day, **~$1.50/month** at four hours a
day. Transcription itself is free — it runs on your CPU.

## Privacy

Baatsun runs entirely offline by default — audio never leaves your machine, and
nothing is logged beyond the transcript history you can see and clear yourself
in the app window.

The one exception is opt-in and off unless you turn it on: enabling **Cleanup
with OpenAI** sends the *transcript text* (never the audio) to OpenAI for the
windows classified as prose. Leave it off to keep the tool fully offline.

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
- **Pill doesn't appear on GNOME** — check it's enabled:
  `gnome-extensions list --enabled | grep baatsun`. If it's not, run
  `gnome-extension/install.sh` again and log out and back in — Wayland can't
  reload the Shell in place, so an extension enabled or copied into place
  mid-session won't draw anything until the next login.
- **Pill doesn't appear on sway/Hyprland/etc.** — run `baatsun-pill` from a
  terminal and check the output; the most common cause is `gtk4-layer-shell`
  not being installed (see step 4 above). On a plain X11 session or a
  Wayland compositor without `wlr-layer-shell`, the pill isn't available at
  all — use the tray icon instead.
- **Transcription is slower than you'd like** — set `model_override` to
  `base.en` (see [Configuration](#configuration)); it's about 2x quicker than
  `small.en` but does substitute words.
- **Nothing happens on a fresh install** — the first run downloads the ~250 MB
  model and the hotkey stays unresponsive until it lands. Check
  `journalctl --user -u baatsun.service -f`; you should see `model loaded` once
  it's ready.
- **Daemon won't start, log says it couldn't download the model** — fix the
  network and `systemctl --user restart baatsun.service`. If a previous attempt
  left a partial download behind, delete
  `~/.cache/huggingface/hub/models--Systran--faster-whisper-small.en` to
  retry from scratch. To run fully offline, set `model_override` to a local
  model directory.

## Roadmap / known limitations

- Swapping in a cloud STT API (OpenAI/Deepgram) only touches
  `stop_recording_and_transcribe()` in `src/baatsun.py` — the daemon/hotkey/
  ydotool plumbing stays the same.
- The hotkey combo is configurable (Settings panel or
  `~/.config/baatsun/config.json`) but limited to four curated pairs
  (`baatsun_config.HOTKEY_CHOICES`) rather than an arbitrary key — capturing
  an arbitrary combo would need a "press your new hotkey" UI flow in
  `baatsun_gui.py` that doesn't exist yet.
- English only. The default model is an English-only distillation and there's no
  language setting; another language means pointing `model_override` at a
  multilingual model and changing `WHISPER_LANGUAGE` in
  `src/baatsun_config.py`.
- Accuracy is bought with CPU. `small.en` is ~2.4x slower per dictation than
  the whisper-base-sized model this used to ship — `model_override` is the way
  back down if that matters more than word accuracy.
- Any language that writes in a non-Latin script (Hindi in Devanagari, say)
  needs more than a different model: `ydotool type` can only produce US-layout
  keycodes, so the typing path in `stop_recording_and_transcribe()` would have
  to be replaced with a clipboard-and-paste approach first.

## Contributing

Issues and pull requests are welcome. If you're proposing a larger change
(e.g. a new backend, a different capture mechanism), please open an issue
first to discuss the approach — see the Architecture section above for how
the pieces fit together.

## Author

Umar Bashir Rather

## License

[MIT](LICENSE)
