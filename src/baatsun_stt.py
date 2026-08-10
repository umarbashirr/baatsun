"""Remote transcription via the ElevenLabs Scribe v2 API.

Stdlib only (urllib), so this adds no dependency to the packaged venv and the
GUI can import it to run the "Test key" button — the same arrangement as
baatsun_cleanup.

This module is the opposite trade from that one. Cleanup sends the transcript
and keeps the audio at home; this sends the audio itself. That is the whole
point of it being off by default and of the Settings copy saying so plainly:
choosing it trades the project's offline promise for accuracy and about 570 MB
of RAM that the local model would otherwise hold.

Every failure path returns None. Unlike cleanup, though, None here is not
recoverable — there is no raw transcript to fall back to, because producing the
transcript *was* the call. The daemon keeps the wav when this returns None so a
network blip costs you a retry rather than the dictation.
"""
import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid

API_URL = "https://api.elevenlabs.io/v1/speech-to-text"
MODEL_ID = "scribe_v2"

# Far longer than cleanup's 6s, because this call cannot be skipped: there is
# no raw text to type if it gives up. A 20s dictation is ~640 KB of wav to
# upload, and Scribe runs at ~60x realtime, so the network dominates. 30s
# covers a slow uplink and still bounds how long a stop can stall.
#
# The daemon calls this while holding state_lock, so this is also the worst
# case for how long the hotkey is unresponsive after you release it. That is a
# real cost of the cloud backend and there is no way around it short of
# restructuring the daemon's locking.
TIMEOUT = 30

# API limits on keyterm prompting, applied here so a long vocabulary list gets
# trimmed to what the endpoint accepts instead of failing the whole request.
MAX_KEYTERMS = 1000
MAX_KEYTERM_CHARS = 50
MAX_KEYTERM_WORDS = 5

# USD per hour of input audio, from elevenlabs.io/pricing/api (checked
# 2026-08-10). Hardcoded for the same reason baatsun_cleanup.PRICING is: there
# is no pricing endpoint to read, so this is a snapshot that will drift when
# ElevenLabs changes its prices, and it is better to have one number to update
# than a figure guessed at the call site.
#
# Keyterm prompting is billed on top of the base rate, and transcribe() sends
# keyterms whenever a vocabulary is set — so most dictations from this app cost
# the sum of the two, not the headline rate. Entity detection is the third
# surcharge on that page; this module never requests it, so it isn't modelled.
PRICE_PER_HOUR = 0.22
KEYTERMS_PRICE_PER_HOUR = 0.05

# Past this many keyterms, ElevenLabs applies a minimum billable duration per
# request. It bites here because dictations are short: with a long vocabulary,
# a four-second "yes, ship it" is billed as twenty. Nothing stops a user pasting
# a list this long into Settings — MAX_KEYTERMS allows 1000 — so the estimate
# models it rather than quietly reporting a fifth of the real cost.
KEYTERMS_MINIMUM_BILLED_SECONDS = 20.0
KEYTERMS_MINIMUM_THRESHOLD = 100


def billed_seconds(seconds, keyterm_count=0):
    """Audio seconds ElevenLabs will actually charge for."""
    seconds = max(0.0, seconds or 0.0)
    if keyterm_count > KEYTERMS_MINIMUM_THRESHOLD:
        return max(seconds, KEYTERMS_MINIMUM_BILLED_SECONDS)
    return seconds


def cost_of(seconds, keyterm_count=0):
    """USD for transcribing `seconds` of audio with `keyterm_count` keyterms.

    Pro-rata on duration: the docs bill on the length of the audio sent, with
    no general per-request minimum — the only minimum is the keyterm one above.
    """
    rate = PRICE_PER_HOUR
    if keyterm_count:
        rate += KEYTERMS_PRICE_PER_HOUR
    return billed_seconds(seconds, keyterm_count) / 3600.0 * rate


def parse_keyterms(vocabulary):
    """Turn the comma-separated vocabulary string into an API keyterms list.

    The same string already feeds whisper's initial_prompt and the cleanup
    prompt, so the user maintains one list and each backend takes it in the
    shape it wants. Terms the endpoint would reject are dropped rather than
    truncated: half a product name biases the model toward the wrong thing.
    """
    terms = []
    for raw in (vocabulary or "").split(","):
        term = raw.strip()
        if not term or len(term) > MAX_KEYTERM_CHARS:
            continue
        if len(term.split()) > MAX_KEYTERM_WORDS:
            continue
        terms.append(term)
    return terms[:MAX_KEYTERMS]


def transcribe(wav_path, api_key, language="en", vocabulary="", log=None,
               seconds=None, usage=None):
    """Return the transcript text, or None if it couldn't be produced.

    language is passed through as language_code. Pinning it rather than letting
    Scribe detect it matches what the local backend does and keeps the output
    English: Scribe v2 can code-switch Hindi/English, but it renders the Hindi
    parts *as Hindi*, which is not what the Hinglish setting asks for — that
    one wants English out, and the cleanup pass is what delivers it.

    A dict passed as usage is filled in with what the call cost, from `seconds`
    of audio. Unlike cleanup, this is a computed figure and not a reported one:
    the endpoint returns no usage block, so the price list above is the only
    source. It is filled in once the response parses and before the empty-text
    check below, because audio that came back with nothing in it was still
    transcribed and still billed.
    """
    if not api_key:
        return None
    try:
        with open(wav_path, "rb") as f:
            audio = f.read()
    except OSError as exc:
        _report(log, f"transcription could not read the recording: {exc}")
        return None
    if not audio:
        return None

    fields = [("model_id", MODEL_ID)]
    if language:
        fields.append(("language_code", language))
    # One part per term, not a JSON array in a single part. Checked against the
    # live endpoint: a JSON array comes back "400 Some keyword contains invalid
    # characters", because the brackets and quotes are read as part of the
    # keyword itself. A comma-joined string is accepted but wrong for the same
    # reason — it arrives as one keyword with commas in it.
    keyterms = parse_keyterms(vocabulary)
    fields += [("keyterms", term) for term in keyterms]

    body, content_type = _encode_multipart(fields, wav_path, audio)
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={"xi-api-key": api_key, "Content-Type": content_type},
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.load(response)
        if usage is not None:
            _record_usage(usage, seconds, len(keyterms))
        text = (payload.get("text") or "").strip()
    except urllib.error.HTTPError as exc:
        # Read the body: ElevenLabs puts the actionable part (bad key, quota,
        # unsupported model) in there, and without it the journal says "422".
        _report(log, f"transcription failed: HTTP {exc.code} {_detail(exc)}".strip())
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _report(log, f"transcription unreachable: {exc}")
        return None
    except ValueError as exc:
        _report(log, f"transcription returned an unusable response: {exc}")
        return None

    if not text:
        _report(log, "transcription returned no text")
        return None
    return text


def verify_key(api_key):
    """Check the key against the account endpoint. Returns (ok, message).

    Deliberately not a transcription round trip: that would need a wav to send
    and would bill for it. /v1/user answers the only question the button asks —
    whether this key is accepted.
    """
    if not api_key:
        return False, "No API key set."
    request = urllib.request.Request(
        "https://api.elevenlabs.io/v1/user", headers={"xi-api-key": api_key})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            json.load(response)
    except urllib.error.HTTPError as exc:
        body = _body(exc)
        # A scoped key is a valid key. This endpoint needs the user_read
        # permission, which dictation never does, and ElevenLabs only reports a
        # missing permission after authenticating the key — so this particular
        # 401 proves the key works. See keycheck.js, which mirrors this.
        if exc.code == 401 and _status_of(body) == "missing_permissions":
            return True, "Working — valid key, scoped to its own permissions."
        if exc.code in (401, 403):
            return False, f"Key rejected by ElevenLabs. {_detail_of(body)}".strip()
        return False, f"HTTP {exc.code} {_detail_of(body)}".strip()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, f"ElevenLabs unreachable: {exc}"
    return True, f"Working — {MODEL_ID} ready."


def _encode_multipart(fields, filename, audio):
    """Build a multipart/form-data body by hand.

    Stdlib has no multipart encoder, and pulling in requests for one would put
    a dependency in the venv that the GUI's system Python doesn't have.

    fields is a list of (name, value) pairs rather than a dict, because
    keyterms repeats its name once per term.
    """
    boundary = uuid.uuid4().hex
    marker = f"--{boundary}".encode()
    parts = []
    for name, value in fields:
        parts += [
            marker,
            f'Content-Disposition: form-data; name="{name}"'.encode(),
            b"",
            str(value).encode(),
        ]
    mime = mimetypes.guess_type(filename)[0] or "audio/wav"
    parts += [
        marker,
        (f'Content-Disposition: form-data; name="file"; '
         f'filename="{os.path.basename(filename)}"').encode(),
        f"Content-Type: {mime}".encode(),
        b"",
        audio,
        f"--{boundary}--".encode(),
        b"",
    ]
    return b"\r\n".join(parts), f"multipart/form-data; boundary={boundary}"


def _record_usage(usage, seconds, keyterm_count):
    # secs is stored alongside cost rather than left to the entry's own "secs"
    # field, because that one is how long the mic was open and this one is what
    # the bill was computed from — the keyterm minimum can make them differ.
    usage.update({
        "backend": "elevenlabs",
        "model": MODEL_ID,
        "secs": round(billed_seconds(seconds, keyterm_count), 1),
        "keyterms": keyterm_count,
        "cost": None if seconds is None else cost_of(seconds, keyterm_count),
    })


def _body(exc):
    """The JSON error body, or {} — read once, since the stream is consumable."""
    try:
        return json.load(exc)
    except Exception:
        return {}


def _detail_of(body):
    detail = body.get("detail")
    if isinstance(detail, dict):
        return detail.get("message") or detail.get("status") or ""
    return str(detail or body.get("message") or "")


def _status_of(body):
    """ElevenLabs' machine-readable reason, e.g. "missing_permissions"."""
    detail = body.get("detail")
    return detail.get("status", "") if isinstance(detail, dict) else ""


def _detail(exc):
    return _detail_of(_body(exc))


def _report(log, message):
    if log:
        log(message)
