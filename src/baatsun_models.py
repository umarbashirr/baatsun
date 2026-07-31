"""Fetches the Hinglish model from GitHub Releases on first use.

Stdlib only, like baatsun_config — no new dependency in the packaged venv.

The English profile uses a stock faster-whisper model name (`base.en`), which
faster-whisper downloads itself. The Hinglish profile can't work that way: it
needs a CTranslate2 build of Oriserve/Whisper-Hindi2Hinglish-Swift, which only
exists as a converted artifact we publish ourselves. We host it as a release
asset and hand faster-whisper a local directory instead of a model name.
"""
import hashlib
import os
import shutil
import tarfile
import tempfile
import urllib.request

CACHE_DIR = os.path.expanduser("~/.cache/baatsun/models")

HINGLISH_NAME = "hinglish-swift-ct2"
HINGLISH_URL = (
    "https://github.com/umarbashirr/baatsun/releases/download/"
    "models-v1/hinglish-swift-ct2.tar.gz"
)
HINGLISH_SHA256 = "d66338496d46ff39c8e22f582987aabc1e9a0b10df7e40261ba8d0f0d000e764"
# Presence of this file marks an extraction as complete; anything less means a
# previous run died partway and the directory should be thrown away.
MARKER = "model.bin"


def _log(message):
    print(f"[baatsun] {message}", flush=True)


def _download(url, dest, log):
    """Stream url to dest, returning the hex sha256 of what was written."""
    digest = hashlib.sha256()
    with urllib.request.urlopen(url) as response, open(dest, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        read = 0
        next_report = 10
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            read += len(chunk)
            if total:
                percent = read * 100 // total
                if percent >= next_report:
                    log(f"  downloading... {percent}% ({read >> 20} / {total >> 20} MiB)")
                    next_report = percent - (percent % 10) + 10
    return digest.hexdigest()


def ensure_hinglish_model(log=_log):
    """Return a local path to the Hinglish CT2 model, downloading it if needed.

    Raises on download or checksum failure — the caller decides whether that is
    fatal. The extraction is staged in a temp directory and moved into place
    only once complete, so an interrupted run can't leave a half-model behind
    that looks valid on the next start.
    """
    target = os.path.join(CACHE_DIR, HINGLISH_NAME)
    if os.path.exists(os.path.join(target, MARKER)):
        return target

    if os.path.isdir(target):
        log(f"discarding incomplete model at {target}")
        shutil.rmtree(target, ignore_errors=True)

    os.makedirs(CACHE_DIR, exist_ok=True)
    log(f"fetching Hinglish model (~62 MiB) from {HINGLISH_URL}")

    staging = tempfile.mkdtemp(prefix=".hinglish-", dir=CACHE_DIR)
    archive = os.path.join(staging, "model.tar.gz")
    try:
        actual = _download(HINGLISH_URL, archive, log)
        if actual != HINGLISH_SHA256:
            raise ValueError(
                f"checksum mismatch for {HINGLISH_URL}: "
                f"expected {HINGLISH_SHA256}, got {actual}"
            )
        log("  checksum verified, extracting...")

        with tarfile.open(archive, "r:gz") as tar:
            _safe_extract(tar, staging)

        extracted = os.path.join(staging, HINGLISH_NAME)
        if not os.path.exists(os.path.join(extracted, MARKER)):
            raise ValueError(f"archive did not contain {HINGLISH_NAME}/{MARKER}")

        os.replace(extracted, target)
        log(f"  Hinglish model ready at {target}")
        return target
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _safe_extract(tar, path):
    """Extract, refusing members that would escape the destination directory.

    We publish this archive ourselves, but it arrives over the network and a
    tarball is trusted input only until it isn't.
    """
    base = os.path.realpath(path)
    for member in tar.getmembers():
        if member.issym() or member.islnk():
            raise ValueError(f"refusing link member in archive: {member.name}")
        destination = os.path.realpath(os.path.join(path, member.name))
        if destination != base and not destination.startswith(base + os.sep):
            raise ValueError(f"refusing path outside archive root: {member.name}")
    tar.extractall(path)
