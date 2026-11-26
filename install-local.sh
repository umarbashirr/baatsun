#!/usr/bin/env bash
# Wire a git checkout of baatsun into your desktop session, without root.
#
#   ./install-local.sh            # from the root of your clone
#
# Writes, all under your home directory and all pointing at this checkout by
# absolute path:
#   ~/.local/share/applications/baatsun-gui.desktop   (app launcher entry)
#   ~/.config/autostart/baatsun-{pill,tray}.desktop   (start on login)
#   ~/.config/systemd/user/baatsun{,-ydotoold}.service
#
# Absolute paths matter: GNOME's launcher and autostart often run without
# ~/.local/bin on PATH, so an entry that says Exec=baatsun-gui silently does
# nothing when clicked. That was why the window could only be opened from a
# terminal.
#
# Safe to re-run after moving the clone or pulling changes.

set -euo pipefail

CHECKOUT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS="$HOME/.local/share/applications"
AUTOSTART="$HOME/.config/autostart"
UNITS="$HOME/.config/systemd/user"

mkdir -p "$APPS" "$AUTOSTART" "$UNITS"

sed -e "s|^Exec=baatsun-gui\$|Exec=$CHECKOUT/bin/baatsun-gui|" \
    "$CHECKOUT/desktop/baatsun-gui.desktop" > "$APPS/baatsun-gui.desktop"
sed -e "s|^Exec=baatsun-pill\$|Exec=$CHECKOUT/bin/baatsun-pill|" \
    "$CHECKOUT/autostart/baatsun-pill.desktop" > "$AUTOSTART/baatsun-pill.desktop"
sed -e "s|^Exec=baatsun-tray\$|Exec=$CHECKOUT/bin/baatsun-tray|" \
    "$CHECKOUT/autostart/baatsun-tray.desktop" > "$AUTOSTART/baatsun-tray.desktop"

sed -e "s|@CHECKOUT@|$CHECKOUT|g" \
    "$CHECKOUT/systemd/baatsun.service" > "$UNITS/baatsun.service"
cp "$CHECKOUT/systemd/baatsun-ydotoold.service" "$UNITS/baatsun-ydotoold.service"

command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database -q "$APPS" || true

# The app icon, so the launcher shows Baatsun's mark rather than a generic one.
ICONS="$HOME/.local/share/icons/hicolor"
install -D -m 644 "$CHECKOUT/brand/baatsun.svg" "$ICONS/scalable/apps/baatsun.svg"
for size in 16 24 32 48 64 128 256 512; do
    install -D -m 644 "$CHECKOUT/brand/png/baatsun-$size.png" "$ICONS/${size}x${size}/apps/baatsun.png"
done
command -v gtk-update-icon-cache >/dev/null 2>&1 \
    && gtk-update-icon-cache -q -t "$ICONS" >/dev/null 2>&1 || true
systemctl --user daemon-reload

echo "baatsun: launcher, autostart and service now point at $CHECKOUT"
echo "  Start dictation:  systemctl --user enable --now baatsun"
echo "  Open the window:  search 'Baatsun' in your app launcher"
if [ ! -f "$CHECKOUT/electron/dist/index.html" ]; then
    echo "  (build the window first: cd $CHECKOUT/electron && npm install && npm run build)"
fi
