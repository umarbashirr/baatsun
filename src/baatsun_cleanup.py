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

# Stdlib-only itself, and sitting in this same directory, so importing it costs
# this module nothing it didn't already have. What it provides is the surface
# names — email, chat, post — that SURFACE_LINES below is keyed by.
import baatsun_context

API_URL = "https://api.openai.com/v1/chat/completions"
# Floor for a short clip. A 10-minute brainstorm is ~1500 words and needs more
# than 6s for gpt-4o-mini to lay it out; timeout_for() scales up, and the
# daemon passes that in. verify_key() keeps this floor so a Test click stays
# snappy. Giving up still types the raw transcript.
MIN_TIMEOUT = 6
MAX_TIMEOUT = 90
TIMEOUT = MIN_TIMEOUT

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

# USD per million tokens, (input, output). Hardcoded because there is no
# pricing endpoint to read, so this is a snapshot that will drift when OpenAI
# changes its prices. A model that isn't listed reports no cost at all rather
# than a guessed one — a wrong number on a spend figure is worse than a blank.
PRICING = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
}


def cost_of(model, prompt_tokens, completion_tokens):
    """USD for one call, or None if this model has no price on file."""
    price = PRICING.get(model)
    if price is None:
        return None
    return (prompt_tokens * price[0] + completion_tokens * price[1]) / 1_000_000


def estimate_tokens(text):
    """Rough token count for calls made before usage was recorded.

    Four characters per token is the usual approximation for English. Only ever
    used to put a figure on old history; anything built on it is marked as an
    estimate in the UI, and live calls always use the counts OpenAI reports.
    """
    return max(1, round(len(text or "") / 4))

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

# One per surface baatsun_context can name, appended so the layout matches
# where the text is going: an email gets its greeting on its own line, a chat
# message stays one line, a post on X stays one block.
#
# Every one of these is about *layout and register* — never content. None of
# them may introduce a word the speaker didn't say, which is why each one ends
# by naming what it must not add. That is the same line the proofreading rules
# above hold, and it is the reason this can be turned on for everybody rather
# than hidden behind a switch: knowing you are in Gmail changes where the line
# breaks go, not what the message says.
_SURFACE_EMAIL = (
    "This will be typed into an email, so lay it out as the body of one. If the "
    "speaker opened with a greeting, put it on its own line with a blank line "
    "after it; if they closed with a sign-off or their name, put that on its "
    "own line too. Use plain, complete sentences — no chat shorthand. Do NOT "
    "add a greeting, a sign-off, a signature or a subject line: if they did not "
    "say it, it does not appear."
)
_SURFACE_CHAT = (
    "This will be typed into a chat message. Keep it to a single line with no "
    "line breaks at all — Enter sends here, so a paragraph break would post it "
    "in pieces. Keep the conversational register the speaker used: do not make "
    "it more formal, and do not add a greeting, a closing or an emoji."
)
_SURFACE_POST = (
    "This will be typed into a post on X or a similar short-form timeline. No "
    "headings, no bullets, and no hashtags, emoji or @-mentions unless the "
    "speaker actually said them. If what they said fits in 280 characters, keep "
    "it inside 280 — punctuate it, don't pad it. Never drop one of their points "
    "to save room."
)
_SURFACE_SOCIAL = (
    "This will be typed into a LinkedIn or forum post, so lay it out to be "
    "read in a feed: short paragraphs, plain first person, sentences in the "
    "order they were spoken. No hashtags, no emoji and no headings unless the "
    "speaker said them."
)
_SURFACE_DOCS = (
    "This will be typed into a document, a note or an article editor, so lay it "
    "out as written prose: full sentences in paragraphs, no chat shorthand. Do "
    "not add a title, headings or bullets that were not spoken."
)
SURFACE_LINES = {
    baatsun_context.EMAIL: _SURFACE_EMAIL,
    baatsun_context.CHAT: _SURFACE_CHAT,
    baatsun_context.POST: _SURFACE_POST,
    baatsun_context.SOCIAL: _SURFACE_SOCIAL,
    baatsun_context.DOCS: _SURFACE_DOCS,
}

# Surfaces whose layout is paragraphs. Only chat is left out, and for a reason
# that is not stylistic: Enter sends there, so a paragraph break would post the
# message in pieces. Everywhere a break is survivable, a long dictation gets
# one, because a wall of text is the thing people actually complain about.
#
# A post on X belongs in here, briefly did not, and that was a regression: a
# 200-word post came back as one block where it used to be three paragraphs.
# The 280-character guidance in _SURFACE_POST is about *length*, not layout —
# and since LINE_BREAK_LINE only applies from LINE_BREAK_MIN_WORDS up, a post
# short enough to be one breath still comes back as one block on its own.
PARAGRAPH_SURFACES = frozenset({
    baatsun_context.EMAIL, baatsun_context.POST, baatsun_context.SOCIAL,
    baatsun_context.DOCS,
})

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


def wants_paragraphs(surface=None):
    """Whether this surface is laid out in paragraphs at all.

    None — an old caller, or a window nothing could be worked out about — keeps
    the pre-surface behaviour of paragraphing whatever the window will take.
    """
    return surface is None or surface in PARAGRAPH_SURFACES


def build_system_prompt(vocabulary="", line_breaks=False, hinglish=False,
                       strength=GRAMMAR, surface=None):
    # Hinglish guidance goes first: it changes how the input should be *read*,
    # which the proofreading rules below then apply to.
    preserve = _PRESERVE_NATURAL if strength == NATURAL else _PRESERVE_GRAMMAR
    prompt = ((HINGLISH_LINE if hinglish else "")
              + _PROMPT_HEAD + preserve + _PROMPT_TAIL)
    if vocabulary:
        prompt += "\n" + VOCABULARY_LINE.format(vocabulary=vocabulary)
    # After the preservation rules and before the paragraphing, because it is
    # narrower than the first and wider than the second.
    if surface in SURFACE_LINES:
        prompt += "\n" + SURFACE_LINES[surface]
    if line_breaks and wants_paragraphs(surface):
        prompt += "\n" + LINE_BREAK_LINE
    return prompt


def timeout_for(text):
    """Seconds to wait for a cleanup request of `text`.

    gpt-4o-mini is fast on a sentence and slow on a 10-minute dump. ~30 words
    per extra second on top of the 6s floor, capped so a hung network cannot
    park the finish thread for minutes.
    """
    words = len((text or "").split())
    return max(MIN_TIMEOUT, min(MAX_TIMEOUT, 6 + words // 30))


def clean(text, api_key, model="gpt-4o-mini", log=None, vocabulary="",
          line_breaks=False, hinglish=False, strength=GRAMMAR, usage=None,
          surface=None, timeout=None):
    """Return the cleaned transcript, or None if it couldn't be produced.

    None is not an error the caller needs to handle beyond falling back to the
    raw text — it already means "type what they actually said".

    A dict passed as usage is filled in with the call's token counts and cost.
    It is filled in as soon as OpenAI answers, before the checks below decide
    whether the answer is usable: a response we reject still cost money, and a
    spend figure that quietly omitted it would understate the bill.
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
                surface,
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

    if timeout is None:
        timeout = TIMEOUT
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
        if usage is not None:
            _record_usage(usage, model, body.get("usage") or {})
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


def _record_usage(usage, model, reported):
    prompt = reported.get("prompt_tokens") or 0
    completion = reported.get("completion_tokens") or 0
    usage.update({
        "model": model,
        "in": prompt,
        "out": completion,
        "cost": cost_of(model, prompt, completion),
    })


def _report(log, message):
    if log:
        log(message)
