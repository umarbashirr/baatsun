# Contributing to Baatsun

Thanks for helping. Baatsun is a small project with one maintainer, so a clear
issue or a focused pull request goes a long way.

- **Bug?** [Open a bug report](https://github.com/umarbashirr/baatsun/issues/new?template=bug_report.yml).
- **Idea?** [Open a feature request](https://github.com/umarbashirr/baatsun/issues/new?template=feature_request.yml)
  before writing code for anything larger than a small fix.
- **Security problem?** Don't open a public issue. See [SECURITY.md](SECURITY.md).

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## What's especially welcome

- Reports from distros and desktops other than Ubuntu/GNOME. The daemon
  doesn't depend on GNOME, but that's where it has been tested.
- Packaging for other distros (Fedora, Arch/AUR, Flatpak, Nix).
- New transcription backends. Remote ones sit alongside ElevenLabs in
  `src/baatsun_stt.py`, and the daemon picks one by `stt_backend`.
- Fixes to window classification (`src/baatsun_context.py`), which decides
  whether a window gets the cleanup pass and how the text is laid out.
- Docs, troubleshooting entries and clearer error messages.

## How the code is laid out

| Path | What it is |
|---|---|
| `src/baatsun.py` | The daemon: reads the keyboard (evdev), records (PipeWire), transcribes locally with faster-whisper, and serves the app over a Unix socket |
| `src/baatsun_stt.py` | Cloud transcription (ElevenLabs Scribe), standard library only |
| `src/baatsun_cleanup.py` | The optional OpenAI proofreading pass |
| `src/baatsun_context.py` | Works out what kind of window has focus |
| `src/baatsun_insert.py` | Pastes or types the transcript into the focused window |
| `src/baatsun_config.py` | Config defaults and validation (`electron/src/main/store.js` mirrors it) |
| `src/baatsun_pill.py`, `gnome-extension/` | The always-on-top pill (layer-shell, and GNOME Shell) |
| `src/baatsun_tray.py` | Tray icon |
| `electron/` | The app window: Electron main process plus a React and Tailwind renderer |
| `packaging/` | `.deb` build script, Debian maintainer scripts, hash-pinned Python requirements |
| `install.sh` | The `curl … \| bash` installer, served from `main` |
| `docs/` | The landing page at <https://umarbashirr.github.io/baatsun/> |

The [Architecture](README.md#architecture) section of the README covers how
these talk to each other.

## Development setup

Follow [Build from source](README.md#build-from-source) in the README. In short:

```bash
git clone https://github.com/umarbashirr/baatsun.git && cd baatsun
sudo apt install -y python3-venv python3-pip python3-dev ydotool wl-clipboard \
    python3-gi gir1.2-gtk-4.0 gir1.2-ayatanaappindicator3-0.1
python3 -m venv venv && venv/bin/pip install -r packaging/requirements.txt
(cd electron && npm ci && npm run build)
./install-local.sh
systemctl --user enable --now baatsun.service
```

You also need the one-time `/dev/uinput` and `input` group setup in step 2
of the README.

Useful loops while working:

```bash
# daemon: restart after editing src/, then follow its log
systemctl --user restart baatsun.service && journalctl --user -u baatsun.service -f

# app window with hot reload (the daemon must be running)
cd electron && npm run dev

# a .deb, exactly as a release is built
(cd electron && npm ci) && VERSION=0.0.0~dev packaging/build-deb.sh
```

Set `Environment=BAATSUN_LOG_TRANSCRIPTS=1` on the service if you need
transcripts in the journal while debugging. Remove it when you're done.

## Before you open a pull request

There's no automated test suite yet. Check these by hand:

- `python3 -m py_compile src/*.py` passes.
- `cd electron && npm run build` succeeds.
- You've used the feature yourself: held the hotkey, dictated into a real
  window, and watched the daemon log. Say which distro, desktop and session
  type (X11 or Wayland) you tested on.
- If you touched packaging, `packaging/build-deb.sh` builds, and the `.deb`
  installs and starts on a clean machine or VM.
- If you changed a Python dependency, regenerate `packaging/requirements.txt`
  with hashes, using the instructions at the top of that file. Unpinned or
  unhashed dependencies won't be merged: `postinst` installs them as root.
- If behaviour or settings changed, update the README in the same PR.

## Ground rules for changes

These come from the project's privacy promises. They aren't style preferences.

- **Offline stays the default.** Anything that sends data off the machine is
  opt-in, off by default, and says in the UI and README exactly what it sends.
- **Don't log what people say.** The journal gets counts, not transcripts,
  unless `BAATSUN_LOG_TRANSCRIPTS=1` is set.
- **Keys never reach the renderer** and are never written into `config.json`.
  They live in their own `0600` files.
- **The daemon sees every keystroke.** Code that touches `evdev` handling
  gets the closest review. Keep it acting only on modifier state.
- **A dictation is never lost.** Every failure path (network, API, insert)
  falls back to typing the raw text or keeping it in History.

## Style

- Python: standard library first, small functions, and comments that explain
  *why*. Match the surrounding code; there's no formatter config to fight with.
- JavaScript/React: match `electron/src`. The palette and spacing are defined
  in `electron/src/renderer/index.css`.
- Commit messages: an imperative subject line under ~70 characters, then a
  body that explains the reason for the change.
- Keep pull requests focused. One fix or feature per PR is easier to review
  and to revert.

## Releases

Maintainers tag `vX.Y.Z` on `main`. The `.deb` and its `.sha256` are attached
to a GitHub Release, and `install.sh` installs the latest one. See
[`.github/workflows/release.yml`](.github/workflows/release.yml) and
[`packaging/build-deb.sh`](packaging/build-deb.sh).

## License

Baatsun is [MIT licensed](LICENSE). By contributing, you agree that your
contributions are licensed under the same terms.
