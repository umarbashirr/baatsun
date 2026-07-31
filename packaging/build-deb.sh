#!/usr/bin/env bash
# Builds baatsun_<version>_all.deb from the files in this repo.
#
# Usage: packaging/build-deb.sh [version]
#   version defaults to $VERSION, then to git describe, then to 0.0.0-dev

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

VERSION="${1:-${VERSION:-}}"
if [ -z "$VERSION" ]; then
    VERSION="$(cd "$REPO_ROOT" && git describe --tags --always 2>/dev/null | sed 's/^v//')"
fi
VERSION="${VERSION:-0.0.0-dev}"

OUT_DIR="$REPO_ROOT/packaging/dist"
PKG_NAME="baatsun_${VERSION}_all.deb"

echo "Building $PKG_NAME ..."

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

mkdir -p "$STAGING/DEBIAN"
mkdir -p "$STAGING/opt/baatsun/src"
mkdir -p "$STAGING/usr/bin"
mkdir -p "$STAGING/usr/lib/systemd/user"
mkdir -p "$STAGING/usr/lib/udev/rules.d"
mkdir -p "$STAGING/usr/share/applications"
mkdir -p "$STAGING/etc/xdg/autostart"
mkdir -p "$STAGING/usr/share/doc/baatsun"
mkdir -p "$STAGING/usr/share/gnome-shell/extensions"

cp "$REPO_ROOT"/src/*.py "$STAGING/opt/baatsun/src/"

install -m 755 "$REPO_ROOT/bin/baatsun-gui" "$STAGING/usr/bin/baatsun-gui"
install -m 755 "$REPO_ROOT/bin/baatsun-tray" "$STAGING/usr/bin/baatsun-tray"
install -m 755 "$REPO_ROOT/bin/baatsun-toggle" "$STAGING/usr/bin/baatsun-toggle"
install -m 755 "$REPO_ROOT/bin/baatsun-pill" "$STAGING/usr/bin/baatsun-pill"

install -m 644 "$REPO_ROOT/packaging/debian/baatsun.service" "$STAGING/usr/lib/systemd/user/baatsun.service"
install -m 644 "$REPO_ROOT/systemd/60-ydotool.rules" "$STAGING/usr/lib/udev/rules.d/60-ydotool.rules"
install -m 644 "$REPO_ROOT/desktop/baatsun-gui.desktop" "$STAGING/usr/share/applications/baatsun-gui.desktop"
install -m 644 "$REPO_ROOT/autostart/baatsun-pill.desktop" "$STAGING/etc/xdg/autostart/baatsun-pill.desktop"
install -m 644 "$REPO_ROOT/packaging/debian/copyright" "$STAGING/usr/share/doc/baatsun/copyright"

cp -r "$REPO_ROOT/gnome-extension/baatsun@umarbashirr.github.io" \
    "$STAGING/usr/share/gnome-shell/extensions/baatsun@umarbashirr.github.io"

install -m 755 "$REPO_ROOT/packaging/debian/postinst" "$STAGING/DEBIAN/postinst"
install -m 755 "$REPO_ROOT/packaging/debian/postrm" "$STAGING/DEBIAN/postrm"

INSTALLED_SIZE="$(du -sk "$STAGING/opt" "$STAGING/usr" "$STAGING/etc" 2>/dev/null | awk '{sum+=$1} END {print sum}')"
sed -e "s/__VERSION__/$VERSION/" "$REPO_ROOT/packaging/debian/control" > "$STAGING/DEBIAN/control"
echo "Installed-Size: ${INSTALLED_SIZE:-0}" >> "$STAGING/DEBIAN/control"

mkdir -p "$OUT_DIR"
dpkg-deb --root-owner-group --build "$STAGING" "$OUT_DIR/$PKG_NAME"

echo "Built $OUT_DIR/$PKG_NAME"
