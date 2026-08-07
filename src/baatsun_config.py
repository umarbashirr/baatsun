"""Shared config for baatsun.py (venv Python) and baatsun_gui.py (system
Python) — stdlib only, so both interpreters can import it unmodified.

Settings changed via the GUI are written here; baatsun.py reads them at
startup. An env var of the same name (BAATSUN_MODEL, BAATSUN_COMPUTE_TYPE)
still overrides the config file, for anyone who prefers to pin it in
systemd/baatsun.service instead of using the Settings panel.
"""
import json
import os

CONFIG_DIR = os.path.expanduser("~/.config/baatsun")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
API_KEY_PATH = os.path.join(CONFIG_DIR, "openai.key")

# whisper small.en, chosen by measurement rather than by size. On this repo's
# test clips it is the smallest model that gets every word right and emits real
# sentence punctuation; base.en (the pre-Hinglish default) drops and substitutes
# words, and distil-small.en swallowed a whole clause. Going bigger buys little:
# distil-large-v3.5 is marginally more accurate but ~3x slower per dictation,
# and whisper's encoder always runs on a padded 30s window, so that cost lands
# on every utterance no matter how short. faster-whisper knows this name and
# downloads it itself on first use (~250 MB).
DEFAULT_MODEL = "small.en"

# Transcripts are cleaned up by OpenAI when the focused window looks like prose
# (a browser on LinkedIn/X, a chat app) rather than a developer context. Only
# the transcript text is sent — never the audio, which stays on this machine.
DEFAULT_CLEANUP_MODEL = "gpt-4o-mini"
CLEANUP_SCOPE_CHOICES = ["prose", "all"]

DEFAULT_CONFIG = {
    # Empty means DEFAULT_MODEL, which is what almost everyone wants. Set this
    # to another faster-whisper model name ("distil-large-v3.5", "base.en"…), a
    # HuggingFace CT2 repo id, or a local directory to use something else.
    "model_override": "",
    "compute_type": "int8",
    "hotkey": "ctrl+super",
    # Off until an API key is entered in Settings; without one there is nothing
    # to call and every transcript is typed exactly as transcribed.
    "cleanup_enabled": False,
    "cleanup_model": DEFAULT_CLEANUP_MODEL,
    # "prose" cleans only what the focused window says is prose; "all" cleans
    # everything, including what you dictate into a terminal or editor.
    "cleanup_scope": "prose",
    # Comma-separated names whisper reliably mishears — your own name, the
    # products you talk about, the tools you use. Fed to the decoder as an
    # initial_prompt so they're transcribed correctly in the first place, and
    # repeated to the cleanup model so it can fix any that still slip through.
    # Correcting them at the decoder matters more: a proofreader asked to fix
    # "Cloud Code" has to guess it was wrong, and mostly doesn't.
    "vocabulary": "",
    # Group long dictations into short paragraphs for readability. Only ever
    # applied where Enter starts a new line — never in a chat window, where it
    # would send the message in pieces.
    "line_breaks": True,
}

COMPUTE_TYPE_CHOICES = ["int8", "int8_float16", "float16", "float32"]
# label -> (primary key group, secondary key group), resolved to evdev
# keycodes by baatsun.py (which is the only side that has evdev installed).
HOTKEY_CHOICES = ["ctrl+super", "ctrl+alt", "alt+super", "ctrl+shift"]

# Baatsun dictates English. Pinning the language rather than letting whisper
# detect it saves a detection pass on every recording and stops a mumbled first
# word from sending the decode into another language.
WHISPER_LANGUAGE = "en"


def resolve_model(cfg):
    """Return the model spec to load: the override if set, else DEFAULT_MODEL."""
    return cfg.get("model_override") or DEFAULT_MODEL


def load_api_key():
    """Return the OpenAI API key, or "" if none is set.

    OPENAI_API_KEY wins if it's in the environment, so an existing shell or
    systemd setup keeps working without retyping it into Settings.
    """
    from_env = os.environ.get("OPENAI_API_KEY", "").strip()
    if from_env:
        return from_env
    try:
        with open(API_KEY_PATH) as f:
            return f.read().strip()
    except OSError:
        return ""


def save_api_key(key):
    """Write the API key 0600, or delete the file when given an empty string.

    Deliberately not a key in config.json: that file is rewritten wholesale by
    the Settings panel, gets read by two interpreters, and is the first thing
    anyone pastes into a bug report. A separate file can be locked down on its
    own and stays out of that blast radius.
    """
    os.makedirs(CONFIG_DIR, exist_ok=True)
    key = (key or "").strip()
    if not key:
        try:
            os.remove(API_KEY_PATH)
        except OSError:
            pass
        return
    tmp_path = API_KEY_PATH + ".tmp"
    # Create with 0600 from the outset rather than chmod-ing afterwards, which
    # would leave the key briefly world-readable.
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    os.replace(tmp_path, API_KEY_PATH)


def cleanup_ready(cfg, api_key=None):
    """True when a cleanup pass should be attempted at all."""
    if not cfg.get("cleanup_enabled"):
        return False
    return bool(api_key if api_key is not None else load_api_key())


def safe_index(choices, value, default_index=0):
    """Index of value in choices, or default_index if it isn't there.

    Guards the GUI's ComboRow setup against a hand-edited config holding an
    off-list value, which would otherwise raise ValueError and stop the
    Settings window opening at all.
    """
    try:
        return choices.index(value)
    except ValueError:
        return default_index


def load_config():
    """Read the config file over the defaults, dropping keys we no longer use.

    The drop matters on upgrade. Older configs carry "language",
    "hinglish_model" and a "model" key from the versions that had an
    English/Hinglish switch; honouring any of those would pin an upgraded
    install to a model it no longer wants. Unknown keys go, so `model_override`
    is only ever set by someone who meant to set it.
    """
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH) as f:
            stored = json.load(f)
    except (FileNotFoundError, ValueError):
        return cfg
    if isinstance(stored, dict):
        cfg.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG})
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)
