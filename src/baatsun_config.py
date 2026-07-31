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

DEFAULT_CONFIG = {
    # Empty means "use the managed model", which is the whole story for almost
    # everyone: a CTranslate2 conversion of
    # Oriserve/Whisper-Hindi2Hinglish-Swift (Apache-2.0, whisper-base sized)
    # that baatsun_models.py fetches from GitHub Releases on first use. One
    # model covers English and Hinglish, so there is nothing to switch between
    # and no second download. It emits Hindi romanized into Latin script rather
    # than Devanagari, which matters because ydotool maps characters to
    # US-layout keycodes and would silently drop Devanagari.
    #
    # Set this to a faster-whisper model name, a HuggingFace CT2 repo id, or a
    # local directory to use something else instead.
    "model_override": "",
    "compute_type": "int8",
    "hotkey": "ctrl+super",
}

COMPUTE_TYPE_CHOICES = ["int8", "int8_float16", "float16", "float32"]
# label -> (primary key group, secondary key group), resolved to evdev
# keycodes by baatsun.py (which is the only side that has evdev installed).
HOTKEY_CHOICES = ["ctrl+super", "ctrl+alt", "alt+super", "ctrl+shift"]

# Whisper decodes against the <|en|> token for both English and Hinglish. For
# Hinglish that is not a compromise but the point: the fine-tune was trained
# against <|en|>, and that is what makes it emit Latin script. Passing "hi"
# would regress it toward Devanagari.
WHISPER_LANGUAGE = "en"


def resolve_model(cfg):
    """Return the model spec to load, or None to mean "the managed model".

    None makes the caller fetch it via baatsun_models.ensure_model().
    That indirection keeps this module stdlib-only and free of download logic,
    so the GUI (system Python) can import it as cheaply as the daemon can.
    """
    return cfg.get("model_override") or None


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

    The drop matters on upgrade. Configs written before the single-model change
    carry "language", "hinglish_model" and a "model": "base.en" that used to be
    the English default — honouring that last one would silently pin an
    upgraded install to an English-only model, which is exactly the split this
    version removed. Unknown keys go, so `model_override` is only ever set by
    someone who meant to set it.
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
