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
    "compute_type": "int8",
    "hotkey": "ctrl+super",
}

MODEL_CHOICES = ["tiny.en", "base.en", "small.en", "medium.en"]
COMPUTE_TYPE_CHOICES = ["int8", "int8_float16", "float16", "float32"]
# label -> (primary key group, secondary key group), resolved to evdev
# keycodes by baatsun.py (which is the only side that has evdev installed).
HOTKEY_CHOICES = ["ctrl+super", "ctrl+alt", "alt+super", "ctrl+shift"]


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
