#!/usr/bin/env bash
# One-line installer for baatsun: downloads the latest .deb release from
# GitHub and installs it with apt, no git clone required.
#
#   curl -fsSL https://raw.githubusercontent.com/umarbashirr/baatsun/main/install.sh | sudo bash
#
# Source: https://github.com/umarbashirr/baatsun

set -euo pipefail

REPO="umarbashirr/baatsun"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"

if [ "$(id -u)" -ne 0 ]; then
    echo "baatsun: this installer needs root to install a .deb package. Run:" >&2
    echo "  curl -fsSL https://raw.githubusercontent.com/${REPO}/main/install.sh | sudo bash" >&2
    exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
    echo "baatsun: this installer only supports apt-based distros (Ubuntu/Debian)." >&2
    echo "See https://github.com/${REPO}#installation for manual install steps." >&2
    exit 1
fi

for cmd in curl python3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "baatsun: '$cmd' is required but not found." >&2
        exit 1
    fi
done

echo "baatsun: looking up the latest release..."
RELEASE_JSON="$(curl -fsSL "$API_URL")"

DEB_URL="$(printf '%s' "$RELEASE_JSON" | python3 -c '
import json, sys
data = json.load(sys.stdin)
for asset in data.get("assets", []):
    name = asset.get("name", "")
    if name.endswith(".deb"):
        print(asset["browser_download_url"])
        break
')"

if [ -z "$DEB_URL" ]; then
    echo "baatsun: no .deb asset found on the latest GitHub release." >&2
    echo "See https://github.com/${REPO}/releases" >&2
    exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
DEB_PATH="$TMP_DIR/$(basename "$DEB_URL")"

echo "baatsun: downloading $(basename "$DEB_URL")..."
curl -fsSL -o "$DEB_PATH" "$DEB_URL"

echo "baatsun: installing (this also builds the transcription venv, needs network access)..."
apt-get update -qq
apt-get install -y "$DEB_PATH"
