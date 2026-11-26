#!/usr/bin/env bash
# Command-line installer for baatsun. Downloads a release .deb from GitHub,
# verifies it, installs it with apt, and starts the dictation service for you.
#
#   curl -fsSL https://raw.githubusercontent.com/umarbashirr/baatsun/main/install.sh | bash
#
# Run it as yourself, not with sudo: it asks for sudo only for the apt step,
# and it needs to be you to start your own (per-user) baatsun service.
#
# Options (when piping, pass them after `bash -s --`):
#   --version vX.Y.Z   install that release instead of the latest
#   --deb FILE         install a .deb you already have (FILE.sha256 is
#                      checked if it sits next to it)
#   --no-start         install only; don't enable or start the service
#   --uninstall        stop the service and remove the package (keeps your
#                      settings and history)
#   -h, --help         show this help
#
#   curl -fsSL https://raw.githubusercontent.com/umarbashirr/baatsun/main/install.sh | bash -s -- --version v0.11.0
#   ./install.sh --deb ~/Downloads/baatsun_0.11.0_amd64.deb
#
# Source: https://github.com/umarbashirr/baatsun

set -euo pipefail

REPO="umarbashirr/baatsun"
API="https://api.github.com/repos/${REPO}/releases"

VERSION=""
LOCAL_DEB=""
START=1
UNINSTALL=0

say() { printf 'baatsun: %s\n' "$*"; }
die() { printf 'baatsun: %s\n' "$*" >&2; exit 1; }

# A heredoc rather than reading this file's header: piped into bash, $0 is
# "bash" and there is no file to read.
usage() {
    cat <<'USAGE'
Install baatsun from a GitHub release (Ubuntu/Debian, amd64).

  curl -fsSL https://raw.githubusercontent.com/umarbashirr/baatsun/main/install.sh | bash
  curl -fsSL .../install.sh | bash -s -- [options]
  ./install.sh [options]

Options:
  --version vX.Y.Z   install that release instead of the latest
  --deb FILE         install a .deb you already have (FILE.sha256 is checked
                     if it sits next to it)
  --no-start         install only; don't enable or start the service
  --uninstall        stop the service and remove the package (keeps your
                     settings and history)
  -h, --help         show this help

Run it as yourself, not with sudo: it asks for sudo only when installing.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --version) [ $# -ge 2 ] || die "--version needs a value, e.g. v0.11.0"
                   VERSION="$2"; shift 2 ;;
        --version=*) VERSION="${1#*=}"; shift ;;
        --deb) [ $# -ge 2 ] || die "--deb needs a file"
               LOCAL_DEB="$2"; shift 2 ;;
        --deb=*) LOCAL_DEB="${1#*=}"; shift ;;
        --no-start) START=0; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
done
case "$VERSION" in ""|v*) ;; *) VERSION="v$VERSION" ;; esac

# ---------------------------------------------------------------- who is this for

# The person the desktop pieces are for. Usually whoever ran the script; if
# they ran it with sudo anyway (the old instructions said to), the user behind
# the sudo.
if [ "$(id -u)" -eq 0 ]; then
    TARGET_USER="${SUDO_USER:-}"
    if [ -z "$TARGET_USER" ] && [ -n "${PKEXEC_UID:-}" ]; then
        TARGET_USER="$(getent passwd "$PKEXEC_UID" | cut -d: -f1)"
    fi
    SUDO=""
else
    TARGET_USER="$(id -un)"
    command -v sudo >/dev/null 2>&1 || die "sudo is needed to install the package."
    SUDO="sudo"
fi
TARGET_UID=""
if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ]; then
    TARGET_UID="$(id -u "$TARGET_USER")"
else
    TARGET_USER=""
fi

# Run systemctl --user for the target user, from this script or from under sudo.
user_systemctl() {
    [ -n "$TARGET_UID" ] || return 1
    local rt="/run/user/$TARGET_UID"
    [ -d "$rt" ] || return 1
    if [ "$(id -u)" -eq "$TARGET_UID" ]; then
        XDG_RUNTIME_DIR="$rt" systemctl --user "$@"
    else
        sudo -u "$TARGET_USER" XDG_RUNTIME_DIR="$rt" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=$rt/bus" systemctl --user "$@"
    fi
}

# ---------------------------------------------------------------- uninstall

if [ "$UNINSTALL" -eq 1 ]; then
    say "stopping the service..."
    user_systemctl disable --now baatsun baatsun-ydotoold >/dev/null 2>&1 || true
    say "removing the package..."
    $SUDO apt-get remove -y baatsun
    say "removed. Your settings and history are still in ~/.config/baatsun and"
    say "~/.local/share/baatsun; delete those too for a clean slate."
    exit 0
fi

# ---------------------------------------------------------------- checks

command -v apt-get >/dev/null 2>&1 || {
    die "this installer supports apt-based distros (Ubuntu/Debian) only.
See https://github.com/${REPO}#build-from-source to install from source."
}
ARCH="$(dpkg --print-architecture)"
[ "$ARCH" = "amd64" ] || die "the release is built for amd64 (64-bit Intel/AMD); this machine is $ARCH.
See https://github.com/${REPO}#build-from-source to build it here."
for cmd in curl python3 sha256sum; do
    command -v "$cmd" >/dev/null 2>&1 || die "'$cmd' is required but not installed."
done

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# ---------------------------------------------------------------- get the .deb

if [ -n "$LOCAL_DEB" ]; then
    [ -f "$LOCAL_DEB" ] || die "no such file: $LOCAL_DEB"
    DEB_NAME="$(basename "$LOCAL_DEB")"
    DEB_PATH="$TMP_DIR/$DEB_NAME"
    cp "$LOCAL_DEB" "$DEB_PATH"
    if [ -f "$LOCAL_DEB.sha256" ]; then
        cp "$LOCAL_DEB.sha256" "$DEB_PATH.sha256"
    else
        say "warning — no $DEB_NAME.sha256 next to it, so it can't be verified." >&2
    fi
else
    if [ -n "$VERSION" ]; then
        say "looking up release $VERSION..."
        RELEASE_URL="$API/tags/$VERSION"
    else
        say "looking up the latest release..."
        RELEASE_URL="$API/latest"
    fi
    RELEASE_JSON="$(curl -fsSL "$RELEASE_URL")" \
        || die "could not read $RELEASE_URL (does that release exist?)"

    # Prefer the amd64 package; older releases were named _all.
    ASSETS="$(printf '%s' "$RELEASE_JSON" | python3 -c '
import json, sys
data = json.load(sys.stdin)
assets = {a.get("name", ""): a.get("browser_download_url", "") for a in data.get("assets", [])}
debs = sorted((n for n in assets if n.endswith(".deb")),
              key=lambda n: (not n.endswith("_amd64.deb"), n))
deb = debs[0] if debs else ""
print(data.get("tag_name", ""))
print(assets.get(deb, ""))
print(assets.get(deb + ".sha256", ""))
')"
    TAG="$(printf '%s\n' "$ASSETS" | sed -n 1p)"
    DEB_URL="$(printf '%s\n' "$ASSETS" | sed -n 2p)"
    SUMS_URL="$(printf '%s\n' "$ASSETS" | sed -n 3p)"
    [ -n "$DEB_URL" ] || die "release ${TAG:-$VERSION} has no .deb attached.
See https://github.com/${REPO}/releases"

    DEB_NAME="$(basename "$DEB_URL")"
    DEB_PATH="$TMP_DIR/$DEB_NAME"
    say "downloading $DEB_NAME ($TAG)..."
    curl -fL --progress-bar -o "$DEB_PATH" "$DEB_URL"
    if [ -n "$SUMS_URL" ]; then
        curl -fsSL -o "$DEB_PATH.sha256" "$SUMS_URL"
    else
        say "warning — release $TAG publishes no .sha256, so the download can't be verified." >&2
    fi
fi

# What is about to be installed runs a root postinst, so check it is the file
# the release says it is. HTTPS proves who served the bytes, not that they are
# the ones that were built.
if [ -f "$DEB_PATH.sha256" ]; then
    say "verifying checksum..."
    (cd "$TMP_DIR" && sha256sum -c "$DEB_NAME.sha256" >/dev/null 2>&1) \
        || die "CHECKSUM MISMATCH for $DEB_NAME — refusing to install.
Report this at https://github.com/${REPO}/issues"
fi

# ---------------------------------------------------------------- install

NEEDS_RELOGIN=0
if [ -n "$TARGET_USER" ] && ! id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx input; then
    NEEDS_RELOGIN=1
fi

say "installing (needs sudo; also downloads the speech-recognition packages, a few minutes)..."
# apt runs as _apt and must be able to read the file.
chmod 755 "$TMP_DIR"
chmod 644 "$DEB_PATH"
$SUDO apt-get update -qq
# python3-dev explicitly as well as via Depends: releases before it was a
# dependency still need it to compile evdev.
$SUDO env SUDO_USER="${TARGET_USER:-}" apt-get install -y python3-dev "$DEB_PATH"

# ---------------------------------------------------------------- start

STARTED=0
if [ "$START" -eq 1 ] && [ -n "$TARGET_USER" ]; then
    if user_systemctl daemon-reload >/dev/null 2>&1 \
            && user_systemctl enable baatsun >/dev/null 2>&1 \
            && user_systemctl restart baatsun >/dev/null 2>&1; then
        STARTED=1
    fi
fi

echo
say "installed."
STEP=1
if [ "$NEEDS_RELOGIN" -eq 1 ]; then
    echo "  $STEP. Log out and back in — you were just added to the 'input' group,"
    echo "     which the hotkey and typing need. (One time only.)"
    STEP=$((STEP + 1))
fi
if [ "$STARTED" -eq 1 ]; then
    echo "  $STEP. The service is running. The first start downloads the speech model"
    echo "     (~250 MB); watch for \"model loaded\" with:"
    echo "       journalctl --user -u baatsun -f"
else
    echo "  $STEP. Start the service:  systemctl --user enable --now baatsun"
fi
STEP=$((STEP + 1))
echo "  $STEP. Open the app: search 'Baatsun' in your launcher, or run baatsun-gui."
echo "     Then hold Ctrl+Super anywhere, speak, and let go."
echo
echo "  Uninstall:  curl -fsSL https://raw.githubusercontent.com/${REPO}/main/install.sh | bash -s -- --uninstall"
