"""Decides whether what you just dictated is code-adjacent or prose.

Stdlib only, like baatsun_config — the GUI's system Python imports it to show
you how the current window is being classified.

The signal is the focused window, reported by the GNOME Shell extension (see
gnome-extension/) as a window class plus a title. That is a deterministic fact
about where the text is about to land, which beats asking a model to guess from
the transcript: it costs nothing, adds no latency, and can't be talked out of
its answer by the words you happened to say.

The default is DEVELOPER, and that asymmetry is the whole point. Cleaning a
LinkedIn post that didn't need it costs you a re-read; "cleaning" a coding
prompt rewrites the specifics that made it work. So an unrecognised window, a
missing report, or a non-GNOME desktop with no extension all fall through to
typing exactly what you said.
"""
import re

DEVELOPER = "developer"
PROSE = "prose"

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

# Chat and mail clients: dictation here is someone-facing prose.
PROSE_APPS = CHAT_APPS | {
    "thunderbird", "geary", "org.gnome.evolution", "evolution",
    "mailspring", "notion", "obsidian", "logseq",
}

# Sites where Enter submits rather than newlines, same hazard as CHAT_APPS.
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
PROSE_SITES = re.compile(
    # Browsers title X as "Home / X" or "Name (@handle) / X", never "x.com", so
    # the bare-letter form has to be matched — anchored on the "/ " and a
    # trailing separator so it can't fire on an ordinary word containing an x.
    r"linkedin|(?:^|\W)x\.com|/\s*X(?=\s*[-—|]|\s*$)|\bon X:|"
    r"twitter|mastodon|bluesky|bsky|threads|reddit|"
    # Chat services reached through a browser are prose and must be cleaned;
    # CHAT_SITES separately stops them getting line breaks. Leaving them out
    # of here made WhatsApp Web fall through to "developer" and skip cleanup.
    r"whatsapp|microsoft teams|teams\.microsoft|google chat|chat\.google|messenger|"
    r"medium|substack|gmail|outlook|slack|discord|notion|docs\.google|"
    r"wordpress|ghost|hashnode|dev\.to",
    re.IGNORECASE,
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
        # an accidental hit lands on DEVELOPER, which is the safe direction.
        names.extend(part for part in app.split(".") if part)
    return names


def classify(app=None, title=None):
    """Return DEVELOPER or PROSE for a focused window.

    app is the window class, title the window title; either may be None when
    nothing reported one, in which case we fall through to DEVELOPER.
    """
    names = _app_names(app)
    app = names[0] if names else ""
    title = title or ""

    if any(n in DEVELOPER_APPS for n in names) or any(
            p.match(n) for n in names for p in DEVELOPER_APP_PATTERNS):
        return DEVELOPER
    if any(n in PROSE_APPS for n in names):
        return PROSE
    if any(n in BROWSER_APPS for n in names):
        # Developer sites win over prose sites: a GitHub issue that happens to
        # mention LinkedIn is still a place where verbatim matters more.
        if DEVELOPER_SITES.search(title):
            return DEVELOPER
        if PROSE_SITES.search(title):
            return PROSE
    return DEVELOPER


def allows_line_breaks(app=None, title=None):
    """Whether a newline typed here starts a line rather than sending.

    ydotool types "\\n" as a real Enter press, and in every chat client Enter
    is send. Breaking a message into paragraphs there would post it in pieces,
    so those windows get proofreading without reformatting.

    Defaults to False for anything unrecognised: a missing paragraph break is a
    cosmetic loss, a prematurely-sent message is not recoverable.
    """
    names = _app_names(app)
    title = title or ""

    if any(n in CHAT_APPS for n in names):
        return False
    if any(n in BROWSER_APPS for n in names):
        return not CHAT_SITES.search(title) and classify(app, title) == PROSE
    # Mail clients, note-takers and the like: Enter is a newline.
    if any(n in PROSE_APPS for n in names):
        return True
    return False


def should_clean(cfg, app=None, title=None):
    """Whether the cleanup pass applies to this window, per cleanup_scope."""
    if cfg.get("cleanup_scope") == "all":
        return True
    return classify(app, title) == PROSE
