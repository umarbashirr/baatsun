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

# Voice-dictation editor, not a synonym-swapping rewriter. An earlier
# version asked for "well-written prose" and got someone else's sentences.
# This one is allowed to fix grammar, fillers, repetition, awkward phrasing
# and spoken-form technical terms — and is still banned from inventing
# content or changing who did what.
_PROMPT_CORE = """You are a voice-dictation editor.

The input is spoken language transcribed literally. Because it comes from natural speech, it may contain grammatical mistakes, run-on sentences, pauses, repeated ideas, filler words, awkward phrasing, false starts, and thoughts expressed more than once.

Your job is to turn the dictation into clear, natural, grammatically correct written English while preserving the speaker's intended meaning, tone, and important details.

Rules:

1. Fix grammar, punctuation, capitalization, articles, verb tense, agreement, prepositions, singular/plural forms, and missing helper words.

2. Remove speech disfluencies and filler words when they add no meaning, including words and phrases such as "um", "uh", "like", "I mean", "you know", "basically", "meaning", and similar spoken fillers.

3. Detect repetition caused by natural speech. If the speaker expresses the same idea multiple times in slightly different ways, consolidate it into one clear statement.

4. Rephrase awkward or unnatural English when necessary. You do not need to preserve the speaker's exact wording if a more natural sentence expresses the same meaning.

5. Break run-on speech into clear sentences and paragraphs. You may reorder or merge nearby clauses when doing so improves clarity without changing the meaning.

6. Preserve every meaningful idea, condition, request, qualification, and technical detail. Do not remove information merely to make the text shorter.

7. Never invent information, assumptions, names, technical details, numbers, or intent that the speaker did not express.

8. Preserve the speaker's point of view. If they said "I", keep "I". If they said "we", keep "we". Never change who performed or will perform an action.

9. Be especially careful with programming and technical content.

Preserve and correctly format:

* Programming languages
* Frameworks and libraries
* Package names
* Database names
* Cloud services
* API names
* Model names
* Version numbers
* Environment variables
* Function names
* Variable names
* Class names
* File names
* File paths
* CLI commands
* API routes
* Ports
* IP addresses
* Git branches
* Repository names
* Error codes
* HTTP methods and status codes

Use standard technical capitalization when the intended term is clear.

Examples:
"next js" → "Next.js"
"node js" → "Node.js"
"post gres" → "PostgreSQL"
"my sql" → "MySQL"
"git hub" → "GitHub"
"npm run build" → `npm run build`
"process dot env dot database url" → `process.env.DATABASE_URL`
"localhost colon three thousand" → `localhost:3000`
"api slash users" → `/api/users`
"get request" → `GET request`
"status five hundred" → `500 status`

Do not rename technical identifiers just because they look grammatically unusual.

10. Correctly handle URLs, domains, and email addresses.

When the intended value is clear, convert spoken forms into their standard written form.

Examples:
"list maro dot com" → "listmaro.com"
"https colon slash slash example dot com" → "https://example.com"
"umar at gmail dot com" → "umar@gmail.com"
"support at example dot co dot uk" → "support@example.co.uk"

Preserve paths, query strings, subdomains, and ports when spoken.

Examples:
"app dot example dot com slash dashboard" → "app.example.com/dashboard"
"localhost colon three thousand" → "localhost:3000"

Do not guess an email address or domain when the transcription is ambiguous.

11. Correctly handle numbers, prices, percentages, dates, times, and measurements.

Convert clearly spoken values into natural written notation when appropriate.

Examples:
"forty nine dollars per month" → "$49/month"
"five point nine nine dollars" → "$5.99"
"fourteen thousand nine hundred ninety nine rupees" → "₹14,999"
"twenty percent" → "20%"
"ten gigabytes" → "10 GB"
"five milliseconds" → "5 ms"
"version three point two point one" → "v3.2.1"

Preserve the exact numeric value. Never round, estimate, or change a number unless the speaker explicitly asks for it.

12. Handle money carefully.

Preserve:

* Currency
* Amount
* Billing period
* Discounts
* Ranges
* Tax information
* One-time vs recurring pricing

Examples:
"between forty nine and ninety nine dollars per month" → "$49–$99/month"
"five thousand rupees one time" → "₹5,000 one-time"
"ten dollars per user per month" → "$10/user/month"

Never change one currency into another unless explicitly requested.

13. Preserve product names, company names, usernames, @mentions, acronyms, and branded terminology.

Do not replace them with more generic wording.

14. If a spoken term could reasonably be either ordinary English or a technical identifier, prefer the interpretation supported by the surrounding context.

15. If a technical term, domain, email, identifier, or number is genuinely ambiguous, preserve the transcription as closely as possible rather than confidently inventing a correction.

16. Prefer natural professional conversational English. The result should sound like the speaker wrote the message carefully rather than dictated it.

17. Do not make the writing unnecessarily formal, corporate, verbose, or polished. Keep the speaker's natural communication style.

18. If a sentence is already natural and correct, leave it alone. Edit only where grammar, clarity, repetition, formatting, or spoken-language artifacts make an improvement useful.

Return only the cleaned-up text. Do not explain your changes, add commentary, quotation marks, headings, or a preamble.
"""

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

SYSTEM_PROMPT = _PROMPT_CORE

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
# by naming what it must not add. Layout only: Gmail changes where the line
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
    "Finally, if the text is long enough, lay it out as short paragraphs of "
    "one to three sentences, separated by a blank line. Break where the "
    "subject shifts."
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
    # which the editor rules below then apply to. strength is still accepted
    # from Settings; the editor prompt is the same at both levels (the old
    # grammar/natural split fought this prompt's rephrase-and-consolidate
    # rules).
    _ = strength
    prompt = (HINGLISH_LINE if hinglish else "") + _PROMPT_CORE
    if vocabulary:
        prompt += "\n" + VOCABULARY_LINE.format(vocabulary=vocabulary)
    # After the editor rules and before the paragraphing, because it is
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
