# Baatsun

*बात + सुन — "talk" + "listen".*

A voice dictation tool for Linux: **hold Ctrl+Super (Windows key) anywhere**,
speak, **release** — it transcribes locally (faster-whisper, CPU) and types
the text into whatever window or input box is focused (via `ydotool`).
Transcription runs fully offline by default; nothing you say ever leaves your
machine unless you opt in — to the OpenAI cleanup pass, which sends the
transcript text only, or to the ElevenLabs transcription backend, which sends
the audio itself.

## Features

- **Push-to-talk, toggle, or both** — hold the hotkey and release when you're
  done; or press once to start and again to stop; or pick tap-or-hold, which
  tells the two apart by how long you held the chord. Hold is the default:
  there's no mode to forget you're in.
- **Offline transcription** — runs locally on CPU via
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Your audio is
  never sent anywhere, and by default neither is the text.
- **Or cloud transcription, if you'd rather** — an optional ElevenLabs Scribe v2
  backend, off unless you turn it on. It is more accurate (~2.2% word error rate
  against `small.en`'s ~6–9%) and skips loading the local model entirely, which
  frees about 690 MB of RAM. In exchange it needs the network on every
  dictation, costs roughly $0.27 per hour of speech, and sends your audio to
  ElevenLabs. Your vocabulary list is passed along as keyterms, so the names it
  gets right locally it gets right there too.
- **Works anywhere** — types directly into whatever window has focus, so it
  works in any app, not just ones with dictation support built in.
- **No shortcut registration needed** — reads the keyboard directly below the
  compositor, so it works the same way on any desktop environment.
- **Always-on-top pill** — a thin dark pill near the bottom centre of the
  screen, above every window (including fullscreen ones), that never steals
  focus. It never changes colour; it opens. A 6px bar at rest, opening to hold
  white level bars while listening and a spinner while transcribing. On GNOME,
  hovering it opens it into a start/stop button, so a hands-free dictation can
  be ended with the mouse.
- **A real app window** — six pages behind a sidebar: Home (a greeting, a
  quote, and what you've actually been doing — dictations, words, time saved,
  streak, time spoken, what the cleanup pass has cost you at OpenAI, and a
  14-day chart), Dictate (live state, level meter, and which
  window the next transcript will land in), History (grouped by day,
  searchable, filterable), Costs (what ElevenLabs and OpenAI have billed,
  today/7 days/30 days/all, with the rates it used and what it could not
  attribute), Words (the vocabulary whisper is biased toward),
  and Settings. Plus a tray icon for live state at a glance.
- **Accurate English** — whisper `small.en`, int8 on CPU: the smallest model
  that gets every word right and punctuates properly, at ~1.6s per dictation.
- **Optional cleanup pass** — tidies punctuation and filler words via OpenAI
  `gpt-4o-mini`, but only when the focused window is prose (LinkedIn, X, Slack).
  Terminals and editors are always typed verbatim. Your audio never leaves the
  machine; only the transcript text is sent, and only if you enable it.
- **Laid out for wherever it lands** — the same cleanup pass knows what kind of
  window it is typing into. Dictate into Gmail and a spoken greeting goes on its
  own line above the body; into WhatsApp and it stays one line, because Enter
  sends there; into X, LinkedIn or a document and a long one becomes short
  paragraphs, while a short post stays the single line it was. It only ever changes
  the shape and the register — it never writes a greeting, a sign-off or a
  hashtag you didn't say.
- **Configurable** — hotkey combo is adjustable from the Settings panel; the
  model and compute type are tunable via config file or env var.

## Requirements

- Linux with [PipeWire](https://pipewire.org/) for audio capture (default on
  most modern distros, including Ubuntu 22.10+).
- Python 3.12+ (the packaged `numpy` pin has no wheels for earlier versions).
- [`ydotool`](https://github.com/ouija/ydotool) for typing into the focused
  window (works under both X11 and Wayland).
- Node.js 20+ and npm, to build the app window (Electron + React).
- PyGObject and GTK3 + AppIndicator bindings for the tray icon (optional —
  see below).
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
                     the app window's record button
        status     — "recording" or "idle"
        history    — one-shot JSON dump of all past transcripts
        cost       — one-shot JSON breakdown of what ElevenLabs and OpenAI
                     have cost, split by today/week/month/all and by vendor.
                     Totalled from the transcripts still in history, so it
                     reports a window rather than lifetime spend — the reply
                     says which in its `window` block. Local transcriptions
                     are counted at zero, not billed at cloud rates
        subscribe  — hold the connection open, stream newline-delimited
                     JSON events (state changes, new/deleted transcripts,
                     focus changes) as they happen — this is what the app
                     window, tray and pill use. The stream opens with the current
                     state and focus, so a client that connects mid-dictation
                     is right immediately instead of at the next change
        clear      — wipe all transcript history
        delete <id> — remove a single transcript by id
        retype <id> — ydotool-type a past transcript into the focused
                     window again
        focus <json> — record which window has focus ({app, title}), sent by
                     the GNOME extension on every focus/title change and on
                     every reconnect; decides whether a transcript is cleaned
                     up or typed verbatim and how it is laid out, and is
                     re-broadcast to subscribers as a `focus` event carrying
                     the daemon's own verdict ({app, title, context, surface,
                     cleanup}) so the app window shows what will actually
                     happen rather than working it out a second time

src/baatsun_config.py (stdlib only — shared by the daemon and the tray)
   Reads/writes ~/.config/baatsun/config.json: model override, compute type,
   hotkey combo. baatsun.py reads it at startup (env vars still override, for
   anyone pinning values in systemd/baatsun.service); the app window's Settings
   page writes the hotkey and restarts the daemon to apply, preserving the
   model/compute-type keys it doesn't expose. resolve_model() returns the
   override if one is set and DEFAULT_MODEL otherwise; either way it's a name
   faster-whisper resolves and downloads itself, so this module stays
   stdlib-only and any interpreter here can import it.
   The app window does not import it — electron/src/main/store.js mirrors it. load_config()
   drops keys that aren't in DEFAULT_CONFIG, which is what retires the
   `language`/`hinglish_model`/`model` keys from the versions that had a
   Hinglish mode, instead of letting an old one quietly pin the model.

src/baatsun_context.py (stdlib only)
   Maps the focused window (class + title, reported by the GNOME extension) to
   a surface — code, email, chat, post, social or docs — and, more coarsely, to
   "developer" or "prose". The coarse answer decides whether a transcript gets
   the cleanup pass at all; the surface decides how the cleaned text is laid
   out. Defaults to code/"developer" for anything unrecognised — cleaning a post
   that didn't need it costs a re-read, but rewriting a coding prompt destroys
   the specifics that made it work, so the safe direction is verbatim.

src/baatsun_cleanup.py (stdlib only — urllib, no new venv dependency)
   The optional OpenAI polish pass over a transcript. Takes a string, never a
   wav: the audio stays on this machine by construction. Every failure path
   returns None and the daemon types the raw transcript, so a dead network can
   never cost you a dictation.

electron/ (the app window — Electron + React, replaces the old GTK window)
   Five pages behind a sidebar, in a frameless window that draws its own
   header. The daemon, the pill and the tray are untouched by this: the pill
   in particular cannot be Electron, because it needs wlr-layer-shell (overlay
   layer, no keyboard focus) to sit above fullscreen windows without stealing
   focus, and Chromium has no way to ask for that.
     src/main/daemon.js   the unix-socket client. request() opens a socket per
               command; subscribe() holds one open for the life of the window
               and reconnects on its own, because the Settings page restarts
               the daemon whenever the hotkey or transcription backend changes.
     src/main/store.js    reads and writes the same files baatsun_config.py
               owns, with the same shapes and the same 0600 key permissions. A
               deliberate mirror, not a second source of truth — DEFAULTS here
               and DEFAULT_CONFIG there are the same dictionary.
     src/main/keycheck.js tests an API key against a listing endpoint, so the
               Test buttons never bill you for pressing them.
     src/main/preload.js  the whole renderer↔Node surface: explicit named
               methods, no generic invoke-any-channel escape hatch, so
               contextIsolation is doing real work.
     Home      greeting, quote, six stat tiles and a 14-day chart, all computed
               from the history the daemon already keeps. lib/stats.js is a
               port of compute_stats/compute_spend — same 40 wpm typing
               baseline, same 150 wpm fallback, same streak rule — so the
               numbers match what the old window said rather than merely being
               defensible.
     Dictate   the recording orb, the state, and the focused-window card: which
               app the next transcript lands in. Reads the daemon's `focus`.
     History   past transcripts grouped by day, searchable, filterable by
               cleaned/verbatim, with copy/retype/delete per row.
     Words     the vocabulary as chips rather than one comma-separated field,
               written back as the string the daemon already reads. Saved on
               edit with no restart.
     Settings  hotkey, activation, transcription backend, cleanup, daemon.
               Saving restarts the daemon only when the hotkey, activation,
               model or backend changed; the backend has to, because it decides
               whether the local model is loaded into memory at all.
   The main process holds one subscription and buffers the last state, focus
   and connection status: the socket is up before React mounts, and on a
   healthy daemon nothing ever repeats those events, so the renderer asks for
   a snapshot instead of waiting to be told.

src/baatsun_tray.py (GTK3 + AppIndicator, separate process, system Python)
   Tray/status icon whose glyph reflects daemon state (idle/listening/
   transcribing) via the same subscribe stream. Menu: show history,
   toggle recording, quit. Runs as its own process because AppIndicator
   only speaks GTK3's Gtk.Menu, and there is no GTK4 or Wayland-native
   equivalent every desktop implements — "Show History" runs bin/baatsun-gui,
   which is a no-op re-present rather than a second window if one is already
   up (Electron's single instance lock).

src/baatsun_pill.py (GTK4 + gtk4-layer-shell, non-GNOME fallback)
   The pill for wlr-layer-shell compositors (sway, Hyprland, ...): an
   undecorated GTK4 window anchored near the bottom of the output on
   the overlay layer, keyboard mode NONE and an empty input region so it can
   never take focus or eat a click. That empty region is also why this one is
   indicator-only, with no hover button. Cairo-draws the pill itself (the
   open/close morph, drop shadow, level bars and spinner) driven off the frame
   clock, fed by the same subscribe stream as the tray. Not usable on
   GNOME: mutter implements neither wlr-layer-shell nor client-side window
   positioning.

gnome-extension/baatsun@umarbashirr.github.io/ (GJS, runs inside GNOME Shell)
   The pill's GNOME implementation — a Clutter actor added to the Shell's
   own chrome via Main.layoutManager.addChrome(), which is the only way to
   sit above every window (including fullscreen ones) and stay
   focus-transparent on GNOME/Wayland. Reads the same unix socket directly
   via GJS's Gio bindings; no extra IPC, and writes "toggle" back down it when
   the hover button is clicked. The actor is reactive but can_focus: false, so
   clicking it never moves keyboard focus off the window being dictated into —
   which is both where ydotool types and what baatsun_context classifies.
   bin/baatsun-pill enables this
   extension on GNOME instead of launching baatsun_pill.py. It also reports the
   focused window's class and title to the daemon on every focus (and title)
   change — on Wayland the Shell is the only thing that can see this, and it's
   what lets baatsun_context tell a coding prompt from a LinkedIn post.
```

Transcript history is persisted to `~/.local/share/baatsun/history.json` and
shown in the app window. The daemon doesn't send any desktop
notifications — state (listening/transcribing/idle) is only broadcast over
the unix socket, which the tray icon glyph and the app window's
header reflect live.

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

This pulls in all system dependencies (`ydotool`, GTK4, PipeWire, `python3-dev`)
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

sudo apt install -y python3-venv python3-pip python3-dev ydotool

python3 -m venv venv
venv/bin/pip install -r packaging/requirements.txt

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

The app window is an Electron app. Build it once:

```bash
cd electron && npm install && npm run build
```

The tray and the pill are still GTK and need PyGObject on your system Python:

```bash
sudo apt install -y python3-gi gir1.2-gtk-4.0
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

### Hotkey behaviour — for long dictations

Holding a two-key chord is fine for a sentence and tiring for a blog post.
**Hotkey behaviour** in Settings offers three ways to drive the same chord:

| Setting | Config value | Behaviour |
| --- | --- | --- |
| Record while held | `hold` | Records while the chord is down. The default. |
| Press to start, press again to stop | `toggle` | One press starts, the next stops. Releasing does nothing. |
| Tap to start and stop, hold to talk | `hybrid` | Both, told apart by duration. |

**Tap-or-hold** is the one to pick if you don't want to choose. Hold the chord
and it behaves exactly as it always has — speak, release, done. Tap it instead
(under 0.4s, `TAP_SECONDS` in `src/baatsun.py`) and the recording stays up with
your hands free until you press again. Short dictations keep push-to-talk; long
ones don't need you holding anything.

Two things to know before leaving `hold`:

- **Nothing ends the recording but you.** In hold mode an accidental brush of
  the chord starts and immediately ends a recording that gets discarded as too
  short. In the other two it can open one that runs until you notice. Watch the
  pill — if it's open with bars jumping in it, you're still recording.
- **There's a 15-minute cap**, after which the recording stops and transcribes
  itself. It exists so a start you didn't notice can't fill `/tmp` overnight
  and then hand whisper an eight-hour file. No real dictation should reach it.

### The pill

One dark pill near the bottom centre of the screen, above every window
(including fullscreen ones). It never changes colour and it is never a
different object — it opens and closes, and everything happens inside it:

| state | the pill |
|---|---|
| idle | a 68x6 bar, empty |
| listening | opens to 52x16 to hold a row of white level bars |
| transcribing | opens to hold a white spinner |
| offline | the closed bar, receded, if the daemon isn't reachable |

It's the fastest way to confirm the hotkey registered — no window to open, and
it can't steal your keyboard focus, so the transcript still lands wherever you
were typing.

The bars aren't reading your microphone. The daemon records straight to a file
and broadcasts no levels, so the meter says "capturing", not "capturing *this*
loudly".

**On GNOME, it's also a button.** Hover it and it opens the same way, this time
onto a white ▶ when idle or ■ while recording — the mouse equivalent of the
hotkey, and the natural way to stop a hands-free dictation. The background
stays the same black throughout; nothing about the pill turns a different
colour. Hovering while a transcript is being produced leaves the spinner alone,
because a click then would only queue up and start a fresh recording the moment
the text landed. Hovering is the only thing that takes pointer input away from
the window underneath, so the target is kept to barely more than the pill
itself — though it is deliberately taller than the 6px closed bar, which is far
too thin to ask anyone to hit.

The `wlr-layer-shell` fallback has no hover button — its window is created with
an empty input region, which is exactly what stops it eating clicks on
compositors this project can't test against. Use the hotkey there.

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
- **activation** — `hold` (default) records only while the chord is down;
  `toggle` starts on one press and stops on the next; `hybrid` does both,
  splitting on how long the chord was held. See
  [Hotkey behaviour](#hotkey-behaviour--for-long-dictations).

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

#### How it lays the text out for the platform

The same window report answers a second, finer question: what kind of place is
this? A message to a colleague is not shaped like a post, and neither is shaped
like an email. Whenever the cleanup pass runs, it is told which surface the text
is heading for, and lays it out accordingly:

| focused window | surface | what changes |
|---|---|---|
| Gmail, Outlook, Proton Mail, Thunderbird | email | a spoken greeting and sign-off each get their own line; the body becomes paragraphs |
| WhatsApp, Slack, Telegram, Discord, Teams | chat | one line, no breaks at all — Enter sends here — and the spoken register is kept |
| X, Mastodon, Bluesky, Threads | post | short paragraphs once it is long enough to need them; a short post stays one block, and what fits in 280 characters stays inside 280 |
| LinkedIn, Reddit | social | short paragraphs, plain first person, no hashtags |
| Google Docs, Notion, Obsidian, Medium, Substack | docs | written prose in paragraphs |
| terminals, editors, GitHub, anything unrecognised | code | nothing — typed exactly as transcribed |

What it does *not* do is write anything for you. It never adds a greeting, a
sign-off, a signature, a subject line, a hashtag or an emoji you didn't speak,
and it never drops one of your points to fit a length. Those are the same
preservation rules the proofreading pass already holds; the surface only
governs layout and register. If you want a message composed rather than laid
out, this isn't that, by design.

Two consequences worth knowing. It works at the granularity of the application,
not the text box: a window title says "Gmail", never whether your caret is in
the compose box or the search field, so the whole app is treated as its dominant
use. And it only applies where the cleanup pass runs at all — with cleanup off,
or in a window classified as developer under *Prose windows only*, the raw
transcript is typed and none of this happens.

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

There are two exceptions, both opt-in and both off unless you turn them on:

- **Cleanup with OpenAI** sends the *transcript text* (never the audio) to
  OpenAI, for the windows classified as prose.
- **Transcribe with ElevenLabs** sends the *audio itself* — the recorded wav for
  every dictation, whatever the window. This is the bigger of the two, and it is
  the only setting in Baatsun that takes your voice off this machine.

Leave both off to keep the tool fully offline. Each has its own key file
(`~/.config/baatsun/openai.key`, `~/.config/baatsun/elevenlabs.key`, both 0600),
and neither key is ever written into `config.json`.

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
- **`apt install` fails building `evdev` / `Python.h: No such file or directory`**
  — the daemon's virtualenv compiles `evdev` from source (PyPI has no Linux
  wheel). Install headers and finish configuring the package:
  `sudo apt install -y python3-dev && sudo dpkg --configure -a`.
  The `.deb` now depends on `python3-dev` so a fresh install pulls it in.

## Roadmap / known limitations

- Swapping in a cloud STT API (OpenAI/Deepgram) only touches
  `stop_recording_and_transcribe()` in `src/baatsun.py` — the daemon/hotkey/
  ydotool plumbing stays the same.
- The hotkey combo is configurable (Settings panel or
  `~/.config/baatsun/config.json`) but limited to four curated pairs
  (`baatsun_config.HOTKEY_CHOICES`) rather than an arbitrary key — capturing
  an arbitrary combo would need a "press your new hotkey" flow in the app
  window that doesn't exist yet.
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
