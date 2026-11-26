# Security policy

Baatsun reads raw keyboard input (evdev), records audio, and injects text into
other windows. A bug in any of those can matter a lot, so security reports are
taken seriously.

## Reporting a vulnerability

**Please don't open a public issue.** Instead, either:

- use GitHub's [private vulnerability reporting](https://github.com/umarbashirr/baatsun/security/advisories/new), or
- email **mail.umarbashir@gmail.com** with "baatsun security" in the subject.

Include what you found, how to reproduce it, which version (`dpkg -s baatsun`
or the commit hash), and the impact you think it has. You'll get an
acknowledgement within a few days. Once a fix ships, you'll be credited in the
release notes unless you'd rather not be.

## Supported versions

Only the latest release gets security fixes. Upgrade by re-running the
installer:

```bash
curl -fsSL https://raw.githubusercontent.com/umarbashirr/baatsun/main/install.sh | bash
```

## What's in scope

Examples of what we'd treat as a vulnerability:

- The daemon logging, storing, or sending keystrokes other than the hotkey
  modifiers.
- Audio or transcripts leaving the machine when both cloud options are off.
- API keys reaching the renderer, `config.json`, logs, or any host other
  than the provider they belong to.
- Another local user reading your history, keys, or the daemon socket, or
  driving the daemon through it.
- Problems in the `.deb` maintainer scripts, `install.sh`, or the AppArmor
  profile that allow privilege escalation, or that let unverified code run
  as root.
- Escaping the app window's CSP or sandbox, for example through markup in a
  transcript.

Not in scope: anything that needs an attacker who can already run code as
your user, or bugs in upstream projects (faster-whisper, Electron, ydotool).
Please report those upstream, though we're glad to hear about them too.

## What the daemon can access

Baatsun needs access to `/dev/input` (via the `input` group) and `/dev/uinput`.
That means it can see every keystroke on the system, not just the hotkey.
That's inherent to working on any desktop without registering a shortcut. The
code only acts on Ctrl/Super state, and the source is public so you can check:
start with `src/baatsun.py`.
