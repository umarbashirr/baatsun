"""Shared config for the daemon and everything that reads its settings —
stdlib only, so it can be imported from the venv or from system Python
unmodified.

The application window is an Electron app and does not import this module; it
mirrors it in electron/src/main/store.js, which reads and writes the same files
with the same shapes and the same permissions. DEFAULT_CONFIG below and DEFAULTS
there are the same dictionary, and when one changes so must the other.

Settings changed in that window are written here; baatsun.py reads them at
startup. An env var of the same name (BAATSUN_MODEL, BAATSUN_COMPUTE_TYPE,
BAATSUN_STT_BACKEND) still overrides the config file, for anyone who prefers to
pin it in systemd/baatsun.service instead of using the Settings page.
"""
import json
import os

CONFIG_DIR = os.path.expanduser("~/.config/baatsun")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
API_KEY_PATH = os.path.join(CONFIG_DIR, "openai.key")
STT_KEY_PATH = os.path.join(CONFIG_DIR, "elevenlabs.key")

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

# Where transcription happens. "local" is faster-whisper on this CPU and is the
# default, because it is the only one of the two that keeps the promise on the
# tin: with it, no audio ever leaves the machine.
#
# "elevenlabs" sends the wav to Scribe v2 instead. It measures ~2.2% WER against
# small.en's ~6-9%, and not loading the local model frees about 570 MB of RSS —
# but it needs a network round trip per dictation, costs about $0.27 an hour of
# speech, and means your audio leaves this computer. That is a real trade, not
# an upgrade, which is why it is opt-in and named plainly in Settings.
STT_BACKEND_CHOICES = ["local", "elevenlabs"]
# "grammar" and "natural" are both offered in Settings. Cleanup now uses one
# editor prompt for either; the distinction is kept so existing configs load.
CLEANUP_STRENGTH_CHOICES = ["grammar", "natural"]

DEFAULT_CONFIG = {
    # What the app window greets you by. Empty means "work it out from the
    # system", which on most machines means the login name -- a handle, not a
    # name, and not something to greet anyone by if they'd rather it didn't.
    # Read only by the GUI; the daemon never looks at it.
    "display_name": "",
    # Empty means DEFAULT_MODEL, which is what almost everyone wants. Set this
    # to another faster-whisper model name ("distil-large-v3.5", "base.en"…), a
    # HuggingFace CT2 repo id, or a local directory to use something else.
    "model_override": "",
    "compute_type": "int8",
    # "local" or "elevenlabs" — see STT_BACKEND_CHOICES. Local by default so an
    # upgrade never starts sending audio off the machine on its own.
    "stt_backend": "local",
    "hotkey": "ctrl+super",
    # "hold" records only while the hotkey is down. "toggle" starts on one
    # press and stops on the next, so your hands are free for the length of a
    # long dictation. "hybrid" is both, told apart by how long the chord is
    # down: tap it and the recording stays up until you press again, hold it
    # and it ends on release like push-to-talk always has.
    #
    # Hold stays the default because it is the only one of the three where an
    # accidental brush of the chord ends itself. In the other two it can leave a
    # recording running until you notice the pill has gone red.
    "activation": "hold",
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
    # The speaker mixes Hindi discourse words into English speech. The model is
    # English-only, so those arrive garbled ("ki" as "K", "hamare" as "Hummer");
    # this tells the cleanup pass to recognise and render them. Distinct from
    # the Hinglish *transcription* this project used to ship: the audio is still
    # decoded as English, and the output is still English, not romanized Hindi.
    "cleanup_strength": "grammar",
    "hinglish": False,
    # Group long dictations into short paragraphs for readability. Only ever
    # applied where Enter starts a new line — never in a chat window, where it
    # would send the message in pieces.
    "line_breaks": True,
}

COMPUTE_TYPE_CHOICES = ["int8", "int8_float16", "float16", "float32"]
# Offered in the Settings page beside DEFAULT_MODEL. Not exhaustive — anything
# faster-whisper resolves still works if written into model_override by hand;
# these are the ones worth clicking. Ordered by size.
MODEL_CHOICES = ["base.en", "distil-small.en", "medium.en", "distil-large-v3.5"]
ACTIVATION_CHOICES = ["hold", "toggle", "hybrid"]
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


def _load_key(path, env_var):
    """Return a stored API key, or "" if none is set.

    The env var wins if it's in the environment, so an existing shell or
    systemd setup keeps working without retyping it into Settings.
    """
    from_env = os.environ.get(env_var, "").strip()
    if from_env:
        return from_env
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _save_key(path, key):
    """Write an API key 0600, or delete the file when given an empty string.

    Deliberately not a key in config.json: that file is rewritten wholesale by
    the Settings panel, gets read by two interpreters, and is the first thing
    anyone pastes into a bug report. A separate file can be locked down on its
    own and stays out of that blast radius.
    """
    os.makedirs(CONFIG_DIR, exist_ok=True)
    key = (key or "").strip()
    if not key:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    tmp_path = path + ".tmp"
    # Create with 0600 from the outset rather than chmod-ing afterwards, which
    # would leave the key briefly world-readable.
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    os.replace(tmp_path, path)


def load_api_key():
    """The OpenAI key, used by the cleanup pass."""
    return _load_key(API_KEY_PATH, "OPENAI_API_KEY")


def save_api_key(key):
    _save_key(API_KEY_PATH, key)


def load_stt_api_key():
    """The ElevenLabs key, used by the remote transcription backend."""
    return _load_key(STT_KEY_PATH, "ELEVENLABS_API_KEY")


def save_stt_api_key(key):
    _save_key(STT_KEY_PATH, key)


def resolve_stt_backend(cfg):
    """Which backend to transcribe with, falling back to local.

    A cloud backend selected without a key would leave the hotkey doing nothing
    but log a failure on every dictation. Falling back to local keeps dictation
    working; the GUI is what tells you the key is missing.
    """
    backend = cfg.get("stt_backend") or "local"
    if backend not in STT_BACKEND_CHOICES:
        return "local"
    if backend == "elevenlabs" and not load_stt_api_key():
        return "local"
    return backend


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


# The parsed config file, kept only as long as the file itself is unchanged.
# Deliberately caches the *stored* dict rather than the merged result, so
# load_config() still builds a fresh dict per call and a caller that mutates
# what it got back cannot poison this.
_cache = {"key": None, "stored": None}


def _read_stored():
    """The parsed config file, re-read only when it has actually changed.

    load_config() is called far more often than the file changes: once per
    dictation, and once per focus change to classify the focused window — and
    the GNOME extension reports a focus change on every window *title* change,
    which in a browser is every keystroke in the address bar. A stat is enough
    to know whether the read and the parse are needed at all.

    Only ever raced by threads reading the same file, and the worst outcome of
    a race is a redundant read, so no lock.
    """
    try:
        st = os.stat(CONFIG_PATH)
    except OSError:
        _cache["key"] = None
        _cache["stored"] = None
        return None

    # Size and inode alongside mtime, so a restore that puts back an older file
    # with a preserved timestamp is still noticed.
    key = (st.st_mtime_ns, st.st_size, st.st_ino)
    if key == _cache["key"]:
        return _cache["stored"]

    try:
        with open(CONFIG_PATH) as f:
            stored = json.load(f)
    except (OSError, ValueError):
        stored = None
    _cache["key"] = key
    _cache["stored"] = stored
    return stored


def load_config():
    """Read the config file over the defaults, dropping keys we no longer use.

    The drop matters on upgrade. Older configs carry "language",
    "hinglish_model" and a "model" key from the versions that had an
    English/Hinglish switch; honouring any of those would pin an upgraded
    install to a model it no longer wants. Unknown keys go, so `model_override`
    is only ever set by someone who meant to set it.
    """
    cfg = dict(DEFAULT_CONFIG)
    stored = _read_stored()
    if isinstance(stored, dict):
        cfg.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG})
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)
    # The stat key would catch this anyway; clearing it means a load in the
    # same process sees the write immediately rather than depending on it.
    _cache["key"] = None
    _cache["stored"] = None
