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

# postinst builds the venv from this rather than from an unpinned package list,
# so it has to ship inside the package.
install -m 644 "$REPO_ROOT/packaging/requirements.txt" "$STAGING/opt/baatsun/requirements.txt"

# The application window is an Electron app, so the built renderer, the main
# process and the Electron runtime itself all have to ship. This is what makes
# the package large (~200 MB installed, nearly all of it the runtime) — the
# app's own code is under 300 KB.
ELECTRON_SRC="$REPO_ROOT/electron"
if [ ! -d "$ELECTRON_SRC/node_modules/electron" ]; then
    echo "error: $ELECTRON_SRC/node_modules/electron is missing." >&2
    echo "       Run: (cd $ELECTRON_SRC && npm ci)" >&2
    exit 1
fi

echo "Building the application window ..."
(cd "$ELECTRON_SRC" && npm run build >/dev/null)

mkdir -p "$STAGING/opt/baatsun/electron/node_modules"
cp -r "$ELECTRON_SRC/dist" "$STAGING/opt/baatsun/electron/dist"
cp -r "$ELECTRON_SRC/src/main" "$STAGING/opt/baatsun/electron/src-main-tmp"
mkdir -p "$STAGING/opt/baatsun/electron/src"
mv "$STAGING/opt/baatsun/electron/src-main-tmp" "$STAGING/opt/baatsun/electron/src/main"
cp "$ELECTRON_SRC/package.json" "$STAGING/opt/baatsun/electron/package.json"
# Only electron itself: everything else in node_modules is a build-time
# dependency (vite, tailwind, react) already compiled into dist/.
cp -r "$ELECTRON_SRC/node_modules/electron" \
    "$STAGING/opt/baatsun/electron/node_modules/electron"

install -m 755 "$REPO_ROOT/bin/baatsun-gui" "$STAGING/usr/bin/baatsun-gui"
install -m 755 "$REPO_ROOT/bin/baatsun-tray" "$STAGING/usr/bin/baatsun-tray"
install -m 755 "$REPO_ROOT/bin/baatsun-toggle" "$STAGING/usr/bin/baatsun-toggle"
install -m 755 "$REPO_ROOT/bin/baatsun-pill" "$STAGING/usr/bin/baatsun-pill"

install -m 644 "$REPO_ROOT/packaging/debian/baatsun.service" "$STAGING/usr/lib/systemd/user/baatsun.service"
install -m 644 "$REPO_ROOT/systemd/60-ydotool.rules" "$STAGING/usr/lib/udev/rules.d/60-ydotool.rules"
# Absolute Exec so GNOME's launcher PATH (which often omits ~/.local/bin)
# still finds the window. The checkout copy keeps the unqualified name.
sed -e 's|^Exec=baatsun-gui$|Exec=/usr/bin/baatsun-gui|' \
    "$REPO_ROOT/desktop/baatsun-gui.desktop" \
    > "$STAGING/usr/share/applications/baatsun-gui.desktop"
chmod 644 "$STAGING/usr/share/applications/baatsun-gui.desktop"
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

# Published alongside the .deb so install.sh can verify what it downloaded.
# Written with a bare filename so `sha256sum -c` works from the directory the
# installer downloads into.
(cd "$OUT_DIR" && sha256sum "$PKG_NAME" > "$PKG_NAME.sha256")

echo "Built $OUT_DIR/$PKG_NAME"
echo "       $OUT_DIR/$PKG_NAME.sha256"
