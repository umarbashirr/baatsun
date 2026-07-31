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
    "model": "base.en",
    # Empty means "use the managed model" — a CTranslate2 conversion of
    # Oriserve/Whisper-Hindi2Hinglish-Swift (Apache-2.0, whisper-base sized)
    # that baatsun_models.py fetches from GitHub Releases on first use. It
    # emits Hindi romanized into Latin script rather than Devanagari, which
    # matters because ydotool maps characters to US-layout keycodes and would
    # silently drop Devanagari. Set this to a faster-whisper model name, a
    # HuggingFace CT2 repo id, or a local directory to override.
    "hinglish_model": "",
    "compute_type": "int8",
    "hotkey": "ctrl+super",
    "language": "english",
}

MODEL_CHOICES = ["tiny.en", "base.en", "small.en", "medium.en"]
COMPUTE_TYPE_CHOICES = ["int8", "int8_float16", "float16", "float32"]
# label -> (primary key group, secondary key group), resolved to evdev
# keycodes by baatsun.py (which is the only side that has evdev installed).
HOTKEY_CHOICES = ["ctrl+super", "ctrl+alt", "alt+super", "ctrl+shift"]
LANGUAGE_CHOICES = ["english", "hinglish"]
LANGUAGE_LABELS = {
    "english": "English",
    "hinglish": "Hinglish (Roman script)",
}


def resolve_language(cfg):
    """Return (model, whisper_language) for the configured language.

    `model` is None for Hinglish-with-no-override, meaning the caller should
    fetch the managed model via baatsun_models.ensure_hinglish_model(). That
    indirection keeps this module stdlib-only and free of any download logic.

    Both profiles pass "en". The Hinglish model was fine-tuned against the
    <|en|> token — that is precisely what makes it emit Latin script instead of
    Devanagari — so passing "hi" would regress it toward Devanagari output.
    """
    if cfg.get("language") == "hinglish":
        return cfg.get("hinglish_model") or None, "en"
    return cfg.get("model") or DEFAULT_CONFIG["model"], "en"


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
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    except (FileNotFoundError, ValueError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)
