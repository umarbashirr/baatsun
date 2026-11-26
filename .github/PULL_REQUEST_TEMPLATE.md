## What and why

<!-- What changes, and the reason for it. Link the issue: "Fixes #123". -->

## How I tested it

<!-- Distro, desktop, X11 or Wayland, and what you actually dictated into. -->

- [ ] `python3 -m py_compile src/*.py`
- [ ] `cd electron && npm run build` (if `electron/` changed)
- [ ] `packaging/build-deb.sh` builds and installs (if packaging changed)
- [ ] Dictated with the change running and checked the daemon log

## Checklist

- [ ] Nothing new leaves the machine by default. Any new network use is opt-in and documented.
- [ ] No transcripts, keystrokes or keys are added to logs.
- [ ] README updated if behaviour or settings changed.
- [ ] `packaging/requirements.txt` regenerated with hashes (if Python deps changed).
