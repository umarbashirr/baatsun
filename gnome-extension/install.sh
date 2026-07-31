#!/usr/bin/env bash
# Install the Baatsun Pill GNOME Shell extension into the current user's
# extension directory and enable it. For working from a git checkout — the
# .deb ships the same files to /usr/share/gnome-shell/extensions instead.
#
#   gnome-extension/install.sh

set -euo pipefail

UUID="baatsun@umarbashirr.github.io"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/$UUID" && pwd)"
DEST="$HOME/.local/share/gnome-shell/extensions/$UUID"

if ! command -v gnome-extensions >/dev/null 2>&1; then
    echo "baatsun: 'gnome-extensions' not found — this doesn't look like GNOME." >&2
    echo "On other Wayland compositors, run 'baatsun-pill' instead (needs gtk4-layer-shell)." >&2
    exit 1
fi

mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
cp -r "$SRC" "$DEST"
echo "baatsun: installed the pill extension to $DEST"

# On Wayland the Shell can't be restarted in place, so a newly-copied
# extension isn't visible to it until the next login. Enabling still works:
# it writes the gsettings key the Shell reads at startup.
if gnome-extensions enable "$UUID" 2>/dev/null; then
    echo "baatsun: extension enabled."
else
    ENABLED="$(gsettings get org.gnome.shell enabled-extensions)"
    if [[ "$ENABLED" != *"$UUID"* ]]; then
        gsettings set org.gnome.shell enabled-extensions \
            "$(python3 -c "
import ast, sys
current = ast.literal_eval(sys.argv[1])
current.append(sys.argv[2])
print(current)
" "$ENABLED" "$UUID")"
    fi
    echo "baatsun: extension queued to enable."
fi

if [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
    echo ""
    echo "Log out and back in for the pill to appear (Wayland can't reload the Shell in place)."
else
    echo ""
    echo "Press Alt+F2, type 'r', and hit Enter to reload the Shell."
fi
