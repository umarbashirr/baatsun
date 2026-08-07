"""Optional polish pass over a transcript, via the OpenAI chat completions API.

Stdlib only (urllib), so this adds no dependency to the packaged venv and the
GUI can import it to run the "Test key" button.

Only the transcript text is ever sent. The audio never leaves this machine —
that is the point of doing transcription locally and only the cleanup remotely,
and it is why this module takes a string rather than a wav path.

Every failure path returns None, and the daemon types the raw transcript when
it gets one. A dictation tool that loses what you said because a network call
timed out would be worse than one that never cleaned up at all.
"""
import json
import urllib.error
import urllib.request

API_URL = "https://api.openai.com/v1/chat/completions"
# Deliberately tight. The daemon calls this while holding state_lock, the same
# lock the hotkey handler and shutdown path need, so this timeout is the worst
# case for how long a stop can stall. A short dictation comes back in about a
# second; anything past a few is a network problem, and giving up early to type
# the raw transcript is the better answer than making the user wait.
TIMEOUT = 6

# A proofreader, not an editor — at either strength. An early version of this
# prompt asked for well-written prose and got it, by rewriting the speaker's
# sentences into someone else's: it merged clauses, swapped "shaped" for
# "developed", and at one point flipped "I" to "you". The bans on merging,
# reordering and tightening below are what hold that line, and they are why
# raising the strength stays safe — it widens which *words* may be corrected,
# never whether the sentences may be rearranged.
_PROMPT_HEAD = (
    "You proofread voice dictation. The input is spoken English, transcribed "
    "literally, so it runs on and lacks punctuation.\n"
    "Fix what is grammatically wrong: missing or incorrect articles, verb "
    "tense and agreement, prepositions, singular/plural, and missing helper "
    "words. Add correct punctuation and capitalisation, and break run-on speech "
    "into sentences.\n"
    "Remove only disfluencies: um, uh, and abandoned false starts.\n"
)

# What separates the two levels is not how hard it tries, but which category of
# change it is allowed to make. "grammar" may only fix what is wrong; "natural"
# may additionally fix what is unidiomatic. Neither may restructure — that is
# the clause that keeps the speaker's meaning and shape intact, and it is
# repeated in both rather than shared, because it is the load-bearing one.
_PRESERVE_GRAMMAR = (
    "Preserve the speaker's exact wording everywhere else. Do NOT substitute "
    "synonyms. Do NOT reorder or merge clauses. Do NOT tighten, shorten or "
    "restructure. Do NOT add or remove any idea. If a phrase is grammatical but "
    "plain or repetitive, leave it exactly as it is — plainness is not an error "
    "to be corrected.\n"
)
_PRESERVE_NATURAL = (
    "ALSO fix unidiomatic phrasing: where wording is understandable but not how "
    "a native speaker would put it, replace just that phrase with the natural "
    "equivalent (for example 'take leverage from AI' becomes 'leverage AI').\n"
    "Everything else is preserved. Do NOT merge, split, reorder or delete any "
    "sentence. Do NOT tighten or shorten. Do NOT add or remove any idea. Keep "
    "the speaker's structure and their points exactly. Change wording only "
    "where it is wrong or unnatural, never where it is merely plain — do not "
    "swap a word for a fancier one, and do not reword to avoid repetition.\n"
)
_PROMPT_TAIL = (
    "Never change who a sentence is about: if they said 'I', keep 'I'.\n"
    "Return only the corrected text, with no preamble, quotes, or commentary."
)

GRAMMAR, NATURAL = "grammar", "natural"

SYSTEM_PROMPT = _PROMPT_HEAD + _PRESERVE_GRAMMAR + _PROMPT_TAIL

HINGLISH_LINE = (
    "The speaker is an Indian English speaker who mixes Hindi discourse words "
    "into English while dictating. The transcriber is English-only, so those "
    "words arrive garbled — 'ki' (that) as 'K', 'hamare'/'humara' (our) as "
    "'Hummer', and similar. Recognise them and render the sentence in natural "
    "English. Do not output Hindi or romanized Hindi.\n"
)

VOCABULARY_LINE = (
    "These names are often misheard by the transcriber; correct them wherever "
    "they appear, however they were spelled: {vocabulary}."
)

# Only ever appended when the target window treats Enter as a newline — see
# baatsun_context.allows_line_breaks. Grouping is explicitly not reordering:
# the paragraph boundaries go between sentences that are already adjacent.
LINE_BREAK_LINE = (
    "Finally, lay it out for readability: group the sentences into short "
    "paragraphs of one to three sentences each, separated by a blank line. "
    "Break where the subject shifts. Keep the sentences in their original "
    "order and do not merge, split or reword any of them — you are only "
    "adding blank lines between sentences that are already next to each other."
)

# Below this, the text is a couple of sentences and paragraphing it would just
# scatter it. Roughly the point where a LinkedIn post starts to look like a wall.
LINE_BREAK_MIN_WORDS = 40


def build_system_prompt(vocabulary="", line_breaks=False, hinglish=False,
                       strength=GRAMMAR):
    # Hinglish guidance goes first: it changes how the input should be *read*,
    # which the proofreading rules below then apply to.
    preserve = _PRESERVE_NATURAL if strength == NATURAL else _PRESERVE_GRAMMAR
    prompt = ((HINGLISH_LINE if hinglish else "")
              + _PROMPT_HEAD + preserve + _PROMPT_TAIL)
    if vocabulary:
        prompt += "\n" + VOCABULARY_LINE.format(vocabulary=vocabulary)
    if line_breaks:
        prompt += "\n" + LINE_BREAK_LINE
    return prompt


def clean(text, api_key, model="gpt-4o-mini", log=None, vocabulary="",
          line_breaks=False, hinglish=False, strength=GRAMMAR):
    """Return the cleaned transcript, or None if it couldn't be produced.

    None is not an error the caller needs to handle beyond falling back to the
    raw text — it already means "type what they actually said".
    """
    if not text or not api_key:
        return None

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": build_system_prompt(
                vocabulary,
                line_breaks and len(text.split()) >= LINE_BREAK_MIN_WORDS,
                hinglish,
                strength,
            )},
            {"role": "user", "content": text},
        ],
        # Deterministic-ish: this is a correction task, not a creative one.
        "temperature": 0.2,
        # Cleaned text is about as long as the input; this is a runaway guard,
        # not a target. 4 tokens per word is generous for English.
        "max_tokens": max(256, len(text.split()) * 4),
    }).encode()

    request = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = json.load(response)
        cleaned = body["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as exc:
        # Read the body: OpenAI puts the actionable part (bad key, quota, model
        # not found) in there, and without it the journal just says "400".
        detail = ""
        try:
            detail = json.load(exc).get("error", {}).get("message", "")
        except Exception:
            pass
        _report(log, f"cleanup failed: HTTP {exc.code} {detail}".strip())
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _report(log, f"cleanup unreachable: {exc}")
        return None
    except (KeyError, IndexError, ValueError) as exc:
        _report(log, f"cleanup returned an unusable response: {exc}")
        return None

    if not cleaned:
        return None
    # Models like to leave a space before a paragraph break. ydotool types that
    # literally, so it becomes a trailing space at the end of a line — invisible
    # here, but real in the post.
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines()).strip()
    # A model that decided to explain itself instead of answering would type a
    # paragraph of apology into whatever you had focused. Length is a crude but
    # effective guard, and falling back to the raw transcript is always safe.
    if len(cleaned) > max(200, len(text) * 3):
        _report(log, "cleanup response implausibly long, using raw transcript")
        return None
    return cleaned


def verify_key(api_key, model="gpt-4o-mini"):
    """Round-trip a trivial request. Returns (ok, message) for the GUI."""
    if not api_key:
        return False, "No API key set."
    result = clean("hello world this is a test", api_key, model)
    if result is None:
        return False, "Key rejected, or OpenAI unreachable. See the daemon log."
    return True, f"Working — {model} responded."


def _report(log, message):
    if log:
        log(message)
