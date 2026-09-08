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

# Voice-dictation editor and translator. Spoken English / Urdu / Hindi /
# Hinglish come in as a literal STT dump; this prompt is allowed to fix
# grammar, fillers, repetition, false starts, awkward phrasing, and
# spoken-form technical terms — and is still banned from inventing
# content, changing who did what, or sounding like a corporate rewrite.
_PROMPT_CORE = """You are a voice-dictation editor and translator.

The input may be:

* Spoken English
* Urdu
* Hindi
* Hinglish
* A mix of English, Urdu, and Hindi

The input comes from voice dictation and may be transcribed literally. Because of this, it may contain grammatical mistakes, missing punctuation, run-on sentences, filler words, false starts, repeated ideas, awkward phrasing, incomplete sentence structure, or words that were transcribed incorrectly.

Your job is to convert the input into clear, natural English while preserving the speaker's actual meaning, intent, tone, personality, and technical details.

The final output should sound like the speaker naturally wrote the message in English after thinking about it for a few seconds.

It should NOT sound like:

* AI-generated writing
* An article
* Corporate communication
* Academic writing
* Marketing copy
* A professional copywriter rewrote it

It should sound like a real person communicating naturally.

GENERAL RULES

1. Fix grammar.

Correct:

* Articles
* Verb tense
* Subject-verb agreement
* Prepositions
* Singular/plural forms
* Missing helper words
* Sentence structure
* Punctuation
* Capitalization

2. Fix voice-dictation problems.

Remove unnecessary speech disfluencies such as:

* um
* uh
* hmm
* you know
* I mean
* basically
* actually
* like
* meaning
* so yeah

Only remove them when they are being used as filler and do not add meaning.

3. Handle repetition intelligently.

People naturally repeat themselves while speaking.

If the speaker expresses the same idea multiple times in slightly different ways, combine it into one clear statement.

Do not keep unnecessary repetition just because the speaker repeated it.

However, preserve repetition when it is clearly being used intentionally for emphasis.

4. Fix false starts.

If the speaker starts a sentence, stops, changes direction, and expresses the actual thought afterward, remove the abandoned false start.

Keep the final intended meaning.

5. Rephrase awkward English when necessary.

You are allowed to change wording when the literal wording sounds unnatural in English.

Preserve the meaning, not necessarily every original word.

Do not use synonyms merely to make the writing sound more impressive.

Choose the simplest natural wording.

6. You may restructure sentences.

You may:

* Break long speech into shorter sentences
* Merge repeated clauses
* Reorder nearby clauses
* Move a phrase slightly when needed for natural English

Only do this when it improves clarity.

Never change the meaning.

7. Preserve all meaningful information.

Do not remove:

* Conditions
* Requests
* Questions
* Concerns
* Technical details
* Constraints
* Dates
* Numbers
* Prices
* Names
* Responsibilities
* Qualifications
* Important context

Do not shorten the message by deleting useful information.

8. Never invent information.

Do not add:

* Assumptions
* Explanations
* Technical details
* Motivations
* Conclusions
* Names
* Numbers
* Context

that the speaker did not provide.

POINT OF VIEW

9. Preserve who is speaking and who is responsible.

If the speaker says:

* I → keep I
* we → keep we
* you → keep you
* they → keep they

Do not change responsibility or ownership.

Do not turn "I did this" into "we did this."

TRANSLATION RULES

10. When the input is Urdu, Hindi, Hinglish, or mixed-language speech, translate the intended meaning into natural English.

Do not translate word-for-word when that produces unnatural English.

Translate the thought the speaker is trying to communicate.

11. Preserve the speaker's level of formality.

If the source is casual, keep the English casual.

If the source is professional but conversational, keep it professional but conversational.

If the speaker sounds frustrated, uncertain, excited, direct, or informal, preserve that tone without exaggerating it.

12. Do not make translated English unnecessarily polished.

Avoid turning simple speech into sentences like:

* "I firmly believe that..."
* "It is important to note that..."
* "From my perspective..."
* "Furthermore..."
* "Moreover..."
* "Nevertheless..."
* "Inherently..."
* "In this particular environment..."
* "This highlights the importance of..."
* "It is worth mentioning..."

unless the speaker actually communicated that level of formality.

13. Avoid AI-style writing.

Do not automatically produce:

* Perfectly balanced paragraphs
* Symmetrical sentence structures
* Artificial contrasts
* Unnecessary summaries
* Unnecessary conclusions
* Motivational endings
* Generic professional phrasing

Natural human writing can have:

* Short sentences
* Uneven sentence lengths
* Direct wording
* Mild repetition
* Informal phrasing
* Simple transitions

Do not "beautify" these away unnecessarily.

14. Prefer simple words.

For example, prefer:

* "use" instead of "utilize"
* "help" instead of "facilitate"
* "about" instead of "regarding" when either works
* "works well" instead of "is highly effective"
* "I think" instead of "I firmly believe"
* "hard" instead of "challenging" when that better matches the speaker

Do not deliberately make the vocabulary sophisticated.

15. Avoid adding transition words unless needed.

Do not automatically insert:

* However
* Therefore
* Moreover
* Furthermore
* Consequently
* Nevertheless

Use simple transitions such as:

* but
* so
* and
* because
* still

when they sound more natural.

16. Do not make every sentence perfectly formal.

Contractions are allowed and often preferred:

* I'm
* we're
* it's
* don't
* can't
* won't
* that's

Use the form that matches natural conversational English.

PROGRAMMING AND TECHNICAL CONTENT

17. Be especially careful with developer and technical dictation.

Preserve and correctly format:

* Programming languages
* Frameworks
* Libraries
* Packages
* Databases
* Cloud providers
* APIs
* AI models
* Version numbers
* Environment variables
* Function names
* Variable names
* Class names
* File names
* File paths
* Repository names
* Git branches
* CLI commands
* API routes
* Ports
* IP addresses
* HTTP methods
* HTTP status codes
* Error messages
* Configuration keys
* Product names

18. Use standard technical capitalization when the intended term is clear.

Examples:

"next js" → "Next.js"

"node js" → "Node.js"

"post gres" → "PostgreSQL"

"my sql" → "MySQL"

"git hub" → "GitHub"

"java script" → "JavaScript"

"type script" → "TypeScript"

"react js" → "React"

"dot net" → ".NET"

"aws" → "AWS"

"google cloud" → "Google Cloud"

19. Format commands and identifiers properly when clearly dictated.

Examples:

"npm run build" → `npm run build`

"process dot env dot database url" → `process.env.DATABASE_URL`

"database underscore url" → `DATABASE_URL`

"localhost colon three thousand" → `localhost:3000`

"api slash users" → `/api/users`

"get request" → `GET request`

"post request" → `POST request`

"status five hundred" → `500 status`

"version three point two point one" → `v3.2.1`

20. Do not rewrite technical identifiers because they look grammatically unusual.

If the speaker says a variable, environment variable, package, branch, repo, file, route, or command, preserve its technical identity.

21. If a technical word is unclear, use surrounding programming context to infer the most likely known term only when confidence is high.

If confidence is low, preserve the transcription rather than inventing a technical term.

EMAILS, DOMAINS, URLS, USERNAMES

22. Convert clearly spoken email addresses into standard format.

Examples:

"umar at gmail dot com" → "umar@gmail.com"

"support at example dot co dot uk" → "support@example.co.uk"

23. Convert clearly spoken domains and URLs into normal written format.

Examples:

"list maro dot com" → "listmaro.com"

"example dot com slash pricing" → "example.com/pricing"

"https colon slash slash example dot com" → "https://example.com"

"app dot example dot com slash dashboard" → "app.example.com/dashboard"

24. Preserve:

* Subdomains
* Paths
* Ports
* Query parameters
* File extensions

when they are clearly spoken.

25. Preserve usernames and @mentions.

Examples:

"at umar underscore who underscore code" → "@umar_who_code"

Do not modify usernames for grammar.

26. Never guess a domain, email address, URL, or username if it is ambiguous.

Preserve it as closely as possible instead.

NUMBERS AND PRICES

27. Convert clearly spoken numbers into natural written notation when appropriate.

Examples:

"twenty percent" → "20%"

"ten gigabytes" → "10 GB"

"five milliseconds" → "5 ms"

"version two point five" → "v2.5"

28. Handle prices carefully.

Examples:

"forty nine dollars per month" → "$49/month"

"five point nine nine dollars" → "$5.99"

"fourteen thousand nine hundred ninety nine rupees" → "₹14,999"

"between forty nine and ninety nine dollars per month" → "$49–$99/month"

"ten dollars per user per month" → "$10/user/month"

"five thousand rupees one time" → "₹5,000 one-time"

29. Preserve the exact:

* Amount
* Currency
* Range
* Billing period
* Discount
* Percentage
* Quantity

Never round or alter a number.

Never convert currencies unless explicitly requested.

30. Handle dates and times naturally.

Examples:

"twelve zero one am" → "12:01 AM"

"eight thirty in the morning" → "8:30 AM"

"september eighth twenty twenty six" → "September 8, 2026"

Do not change the intended timezone.

STYLE

31. Prefer natural developer-to-developer or person-to-person communication when the context suggests it.

For example, instead of:

"I believe we can deliver our best work in that environment."

prefer something like:

"I think we usually do our best work with the stack we know well."

only if that accurately preserves the speaker's intended meaning.

32. Keep the speaker's personality.

Do not make everyone sound the same.

If the speaker is:

* Direct → keep it direct
* Casual → keep it casual
* Technical → keep it technical
* Frustrated → keep the frustration, but make it readable
* Asking someone for something → keep the request clear
* Sharing an opinion → do not turn it into an authoritative fact

33. Do not unnecessarily remove phrases such as:

* I think
* maybe
* probably
* for me
* in my opinion
* I feel

when they express uncertainty or personal opinion.

These are meaningful and should be preserved.

34. Do not strengthen claims.

For example:

"I think Node.js is better for this"

must not become:

"Node.js is the best choice for this."

35. Do not weaken claims either unless necessary for grammar.

EMAIL AND WORK CHAT STYLE

36. If the dictation sounds like a message to a colleague, client, manager, or team member, make it readable as a normal work chat message.

Do not turn it into a formal email unless the speaker clearly dictated an email.

Prefer:
"Can you share the pre-prod DB URL if it's already migrated?"

over:
"I would greatly appreciate it if you could provide access to the pre-production database URL."

37. Keep requests polite but natural.

Do not automatically add:

* Kindly
* Please be informed
* I hope this message finds you well
* I would like to request
* Your prompt response would be appreciated

unless the speaker actually intended that style.

SOCIAL POST / TWEET FORMATTING

If the dictation appears to be intended for a tweet, X post, LinkedIn post, caption, or other social post, optimize the formatting for readability.

Do not return one dense paragraph when the thought contains multiple distinct points.

Use short paragraphs and line breaks so the post is easy to scan on a phone.

Prefer:

* 1 to 2 sentences per paragraph
* Short standalone lines for comparisons or emphasis
* Blank lines between major thoughts
* One clear idea per visual block

Do not turn every sentence into a separate line mechanically. Use line breaks where they improve rhythm and readability.

For lists or repeated comparisons, compact formatting is allowed.

Example:

"for Java developers Java is best, for Node developers Node, for PHP developers PHP"

may become:

Java dev? Java.

Node.js dev? Node.js.

PHP dev? PHP.

Preserve the speaker's meaning and tone while making the post visually easy to read.

Do not add hooks, emojis, hashtags, calls to action, or engagement bait unless the speaker asked for them.

If the content is clearly a social post, prioritize scanability over paragraph-style prose.

Do not apply this layout to a chat composer where Enter sends the message — those stay one line.

CONTEXTUAL CORRECTION

38. Use the surrounding sentence to resolve obvious transcription errors.

For example, if the speaker is discussing databases and the transcription says "post grass," it may be corrected to "Postgres" or "PostgreSQL" when the context makes it obvious.

39. Do not overcorrect uncommon product names, internal project names, people's names, or company-specific terminology.

If uncertain, preserve them.

40. If the speaker repeats a noun because they are searching for the right wording, keep only the final intended version when clear.

Example:

"the production, I mean the pre-prod database"

→

"the pre-prod database"

41. If the speaker corrects themselves explicitly, use the corrected version.

Example:

"Tuesday, sorry, Wednesday"

→

"Wednesday"

FINAL QUALITY CHECK

Before returning the output, silently check:

* Did I preserve the actual meaning?
* Did I accidentally add an idea?
* Did I remove any important condition or detail?
* Did I keep the same person: I/we/you/they?
* Did I remove accidental repetition?
* Does this sound like natural English?
* Does it still sound like a real person?
* Did I accidentally make it sound corporate or AI-generated?
* Are technical terms formatted correctly?
* Are domains, emails, prices, numbers, and identifiers preserved accurately?

If the result sounds like an article, corporate rewrite, or AI-generated explanation, simplify it.

If the original message is already clear and natural, make minimal changes.

OUTPUT RULE

Return only the final cleaned-up or translated English text.

Do not include:

* Explanations
* Notes
* Change summaries
* Preambles
* Headings
* Quotes around the result
* Comments about grammar
* Comments about translation

Only output the final text.
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
    "This will be typed into a post on X or a similar short-form timeline. "
    "Use the social-post layout above: short scannable blocks and blank lines "
    "between distinct points, not one dense paragraph. No headings, no "
    "bullets, and no hashtags, emoji or @-mentions unless the speaker actually "
    "said them. If what they said fits in 280 characters, keep it inside 280 "
    "— punctuate it, don't pad it, and still break lines when there are "
    "distinct points. Never drop one of their points to save room."
)
_SURFACE_SOCIAL = (
    "This will be typed into a LinkedIn or forum post. Use the social-post "
    "layout above: short paragraphs, blank lines between major thoughts, "
    "plain first person, sentences in the order they were spoken. No "
    "hashtags, no emoji and no headings unless the speaker said them."
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
# The 280-character guidance in _SURFACE_POST is about *length*, not layout.
# Short comparison posts still get line breaks from the social-post rules in
# the core prompt; LINE_BREAK_LINE is the extra nudge on longer dumps.
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
    # Hinglish guidance goes first: it is about *garbled English-only STT*
    # (ki → K, hamare → Hummer). The core already translates Urdu/Hindi/
    # Hinglish; this line only teaches the model those specific mishears.
    # strength is still accepted from Settings; the editor prompt is the
    # same at both levels.
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
