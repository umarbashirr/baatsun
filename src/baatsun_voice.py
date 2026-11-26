"""What a transcript asks for when it isn't simply text to type.

Three things live here, all deterministic and all applied before the cleanup
model sees anything:

* corrections: "heard X, meant Y" pairs you teach from History, replaced in
  every transcript. Unlike the vocabulary list (a hint the decoder may ignore),
  these always apply, and they work with cleanup switched off.
* snippets: a spoken trigger ("my signature") that types saved text verbatim.
* commands: "scratch that" removes the last dictation from the window.

Only a whole utterance is ever read as a snippet or a command. "Please scratch
that line from the report" is a sentence to type, not an instruction, and a
dictation tool that fires commands from the middle of your prose can't be
trusted with it.

Stdlib only.
"""
import re

SCRATCH = "scratch"

# Whole-utterance phrasings of "remove what you just typed".
_SCRATCH_PHRASES = {
    "scratch that", "scratch it", "delete that", "undo that", "remove that",
    "erase that", "never mind delete that",
}
# Said before a snippet name, and optional: "insert my signature" and "my
# signature" both work.
_SNIPPET_VERBS = ("insert", "paste", "type", "add", "put")


def normalise(text):
    """Lowercase words only, for matching spoken phrases.

    Whisper punctuates and capitalises unpredictably ("Scratch that." vs
    "scratch that"), so matching is done on bare words.
    """
    return " ".join(re.findall(r"[\w']+", (text or "").lower()))


def apply_corrections(text, corrections):
    """Replace every taught mishearing, as whole words, case-insensitively.

    Longest first, so a correction for "ground mainly" wins over one for
    "ground" when both are taught.
    """
    pairs = []
    for item in corrections or []:
        if not isinstance(item, dict):
            continue
        heard = str(item.get("heard") or "").strip()
        correct = str(item.get("correct") or "").strip()
        if heard and correct and heard.lower() != correct.lower():
            pairs.append((heard, correct))
    for heard, correct in sorted(pairs, key=lambda p: -len(p[0])):
        words = [re.escape(w) for w in heard.split()]
        pattern = r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)"
        text = re.sub(pattern, lambda _m, c=correct: c, text, flags=re.IGNORECASE)
    return text


def match_snippet(text, snippets):
    """The saved text for a spoken snippet trigger, or None."""
    said = normalise(text)
    if not said:
        return None
    candidates = {said}
    for verb in _SNIPPET_VERBS:
        if said.startswith(verb + " "):
            candidates.add(said[len(verb) + 1:])
    for item in snippets or []:
        if not isinstance(item, dict):
            continue
        trigger = normalise(item.get("trigger"))
        body = item.get("text")
        if trigger and isinstance(body, str) and body and trigger in candidates:
            return body
    return None


def match_command(text):
    """SCRATCH when the whole utterance is a scratch command, else None."""
    said = normalise(text)
    if said in _SCRATCH_PHRASES:
        return SCRATCH
    return None
