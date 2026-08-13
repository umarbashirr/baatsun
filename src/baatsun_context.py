"""Decides what kind of place the text you just dictated is about to land in.

Stdlib only, like baatsun_config — the GUI's system Python imports it to show
you how the current window is being classified.

The signal is the focused window, reported by the GNOME Shell extension (see
gnome-extension/) as a window class plus a title. That is a deterministic fact
about where the text is about to land, which beats asking a model to guess from
the transcript: it costs nothing, adds no latency, and can't be talked out of
its answer by the words you happened to say.

There are two answers here, and they are not the same question. surface() names
the platform — an email, a chat message, a post on X — and is what shapes the
layout the cleanup pass produces. classify() collapses that to the older
DEVELOPER/PROSE pair, which is what decides whether the transcript is rewritten
at all; it is what history entries record and what Settings' "Apply to" means.

The default is DEVELOPER, and that asymmetry is the whole point. Cleaning a
LinkedIn post that didn't need it costs you a re-read; "cleaning" a coding
prompt rewrites the specifics that made it work. So an unrecognised window, a
missing report, or a non-GNOME desktop with no extension all fall through to
typing exactly what you said.
"""
import re

DEVELOPER = "developer"
PROSE = "prose"

# The surfaces. Each one is a place with its own conventions about layout —
# where a line break belongs, whether there is a greeting, how long a thing is
# expected to be — and baatsun_cleanup keeps one instruction per surface.
CODE = "code"        # terminals, editors, GitHub, anywhere verbatim wins
EMAIL = "email"      # mail clients and webmail
CHAT = "chat"        # WhatsApp, Slack, Telegram: one message, Enter sends
POST = "post"        # X and the other short-form timelines
SOCIAL = "social"    # LinkedIn, Reddit: a feed post, but a long one
DOCS = "docs"        # documents, notes, articles

# Matched against the window class, lowercased. Terminals and editors: anything
# you dictate here is a command, a prompt, or code.
DEVELOPER_APPS = {
    "alacritty", "kitty", "wezterm", "foot", "contour", "rio",
    "gnome-terminal-server", "org.gnome.terminal", "konsole", "xterm",
    "terminator", "tilix", "urxvt", "st", "ghostty", "warp",
    "code", "code-insiders", "vscodium", "cursor", "windsurf", "zed",
    "sublime_text", "gvim", "emacs", "neovide", "android studio",
}
# JetBrains ships a window class per IDE (jetbrains-pycharm, jetbrains-idea…).
DEVELOPER_APP_PATTERNS = (re.compile(r"^jetbrains-"),)

# Chat apps: prose, but Enter SENDS the message rather than starting a new
# line, so a paragraph break typed here would fire the message off mid-thought
# and split it into fragments. Proofread these; never break lines in them.
CHAT_APPS = {
    "slack", "discord", "org.telegram.desktop", "telegram", "signal",
    "whatsapp", "element", "teams", "messenger",
}

# Mail clients. Dictation here is a message to a person, laid out as one.
MAIL_APPS = {
    "thunderbird", "betterbird", "geary", "org.gnome.evolution", "evolution",
    "mailspring", "superhuman", "proton-mail", "protonmail-desktop",
}

# Editors for prose rather than code: documents, notes, drafts.
NOTE_APPS = {"notion", "obsidian", "logseq", "joplin", "standard notes"}

# Chat and mail clients: dictation here is someone-facing prose.
PROSE_APPS = CHAT_APPS | MAIL_APPS | NOTE_APPS

# Sites where Enter submits rather than newlines, same hazard as CHAT_APPS.
# Broader than the chat services named in CHAT_SITE_NAMES below, and kept apart
# from them for that reason: the bare word "chat" is a fine reason to withhold
# line breaks from a page, and a terrible reason to call it a chat service —
# it appears in the title of every ChatGPT tab, and those must stay verbatim.
CHAT_SITES = re.compile(
    r"slack|discord|whatsapp|messenger|teams|chat",
    re.IGNORECASE,
)

# A browser is whichever page it's on, so the title decides. Browser titles are
# "<page title> — <Browser>" or "<page title> - <Browser>", which is enough.
# Both the plain and reverse-DNS-tail forms, since _app_names reduces
# "com.google.Chrome" to "chrome" and "org.mozilla.firefox" to "firefox".
BROWSER_APPS = {
    "firefox", "firefox-esr", "librewolf", "zen", "google-chrome",
    "google-chrome-unstable", "chromium", "chromium-browser", "brave-browser",
    "microsoft-edge", "vivaldi-stable", "org.gnome.epiphany", "safari",
    "chrome", "brave", "edge", "vivaldi", "epiphany", "navigator",
}

# The prose surfaces as reached through a browser, one pattern each. PROSE_SITES
# is built from them below rather than maintained alongside them: a site listed
# as prose but matching no surface would be cleaned up with no idea of what it
# was being cleaned up *for*, and that gap is exactly the kind that opens
# quietly when two lists have to be edited together.
MAIL_SITES = (
    r"gmail|outlook|proton\s*mail|mail\.proton|fastmail|zoho\s*mail|"
    r"superhuman|roundcube|mail\.yahoo|yahoo\s*mail|hey\.com"
)
POST_SITES = (
    # Browsers title X as "Home / X" or "Name (@handle) / X", never "x.com", so
    # the bare-letter form has to be matched — anchored on the "/ " and a
    # trailing separator so it can't fire on an ordinary word containing an x.
    r"(?:^|\W)x\.com|/\s*X(?=\s*[-—|]|\s*$)|\bon X:|"
    r"twitter|mastodon|bluesky|bsky|threads"
)
SOCIAL_SITES = r"linkedin|reddit"
DOCS_SITES = (
    # "google docs" as well as the hostname: a Docs tab is titled "Quarterly
    # update - Google Docs" and never carries the URL, so the hostname form
    # alone matched nothing anyone was actually looking at.
    r"medium|substack|notion|docs\.google|google\s*docs|"
    r"wordpress|ghost|hashnode|dev\.to"
)
# Chat services reached through a browser are prose and must be cleaned;
# CHAT_SITES separately stops them getting line breaks. Leaving them out of the
# prose set made WhatsApp Web fall through to "developer" and skip cleanup.
CHAT_SITE_NAMES = (
    r"whatsapp|microsoft teams|teams\.microsoft|google chat|chat\.google|"
    r"messenger|slack|discord"
)

PROSE_SITES = re.compile(
    "|".join((MAIL_SITES, CHAT_SITE_NAMES, POST_SITES, SOCIAL_SITES,
              DOCS_SITES)),
    re.IGNORECASE,
)
# Ordered, and consulted in this order: the first match names the surface. Mail
# leads because a mailbox is the one place whose *title* routinely quotes other
# people's words — a Gmail tab showing a thread called "Our LinkedIn post" is
# still a mailbox, and answering "social" there would lay a reply out as a feed
# post.
SURFACE_SITES = (
    (EMAIL, re.compile(MAIL_SITES, re.IGNORECASE)),
    (CHAT, re.compile(CHAT_SITE_NAMES, re.IGNORECASE)),
    (POST, re.compile(POST_SITES, re.IGNORECASE)),
    (SOCIAL, re.compile(SOCIAL_SITES, re.IGNORECASE)),
)
# Developer surfaces that live in a browser and must not be rewritten.
DEVELOPER_SITES = re.compile(
    r"github|gitlab|bitbucket|stack\s*overflow|localhost|127\.0\.0\.1|"
    r"jira|linear\.app|codepen|codesandbox|jupyter|colab|grafana|kibana|"
    r"console\.(?:aws|cloud)|vercel|netlify",
    re.IGNORECASE,
)


def _app_names(app):
    """The forms of a window class worth matching against our sets.

    Window classes come in both plain ("kitty") and reverse-DNS
    ("com.mitchellh.ghostty", "org.gnome.Evolution") forms depending on how the
    app ships, and the same program can use either across distributions. So
    match the last dotted component too, rather than listing every vendor
    prefix anyone might publish under.
    """
    app = (app or "").strip().lower()
    if not app:
        return []
    names = [app]
    if "." in app:
        # Every component, not just the last: "com.google.Chrome" identifies
        # itself by its tail ("chrome") but "com.brave.Browser" only by its
        # middle ("brave"), since its tail is the generic word "Browser".
        # Stray components like "com" and "org" match nothing in our sets, and
        # an accidental hit lands on CODE, which is the safe direction.
        names.extend(part for part in app.split(".") if part)
    return names


def surface(app=None, title=None):
    """Name the kind of place a transcript is about to be typed into.

    Returns one of CODE, EMAIL, CHAT, POST, SOCIAL or DOCS. app is the window
    class, title the window title; either may be None when nothing reported
    one, in which case we fall through to CODE and nothing gets rewritten.

    This deliberately answers at the granularity of the *application*, not the
    text box. A window title says "Gmail"; it does not say whether the caret is
    in the compose box or the search field. Dictating a paragraph into Gmail's
    search field is not a thing anyone does, so treating the whole app as its
    dominant use is both what's knowable from here and what's right nearly
    always.
    """
    names = _app_names(app)
    title = title or ""

    if any(n in DEVELOPER_APPS for n in names) or any(
            p.match(n) for n in names for p in DEVELOPER_APP_PATTERNS):
        return CODE
    if any(n in MAIL_APPS for n in names):
        return EMAIL
    if any(n in CHAT_APPS for n in names):
        return CHAT
    if any(n in NOTE_APPS for n in names):
        return DOCS
    if any(n in BROWSER_APPS for n in names):
        # Developer sites win over prose sites: a GitHub issue that happens to
        # mention LinkedIn is still a place where verbatim matters more.
        if DEVELOPER_SITES.search(title):
            return CODE
        if PROSE_SITES.search(title):
            for name, pattern in SURFACE_SITES:
                if pattern.search(title):
                    return name
            # In PROSE_SITES by way of DOCS_SITES, then — a document, a note or
            # a blog editor. Unless the title says "chat" anyway, in which case
            # it is treated as one: this is the case the broader CHAT_SITES
            # covers, and getting it wrong sends a message in fragments.
            return CHAT if CHAT_SITES.search(title) else DOCS
    return CODE


def classify(app=None, title=None):
    """Return DEVELOPER or PROSE for a focused window.

    The older, coarser question, and still the one that decides whether the
    cleanup pass runs at all. Every surface that isn't code is somebody-facing
    prose.
    """
    return DEVELOPER if surface(app, title) == CODE else PROSE


def allows_line_breaks(app=None, title=None):
    """Whether a newline typed here starts a line rather than sending.

    ydotool types "\\n" as a real Enter press, and in every chat client Enter
    is send. Breaking a message into paragraphs there would post it in pieces,
    so those windows get proofreading without reformatting.

    This is the safety question only — whether a line break is *survivable*
    here, not whether one belongs. Whether the layout actually wants paragraphs
    is a matter of the surface's conventions, and baatsun_cleanup decides it.

    Defaults to False for anything unrecognised: a missing paragraph break is a
    cosmetic loss, a prematurely-sent message is not recoverable.
    """
    return surface(app, title) not in (CODE, CHAT)


def should_clean(cfg, app=None, title=None):
    """Whether the cleanup pass applies to this window, per cleanup_scope."""
    if cfg.get("cleanup_scope") == "all":
        return True
    return classify(app, title) == PROSE
