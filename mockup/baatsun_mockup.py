#!/usr/bin/env python3
"""A runnable mockup of the full Baatsun application window.

This is a *design artifact*, not shipped code. It talks to nothing: no daemon,
no socket, no config file, no OpenAI. Every list here is made up, and none of
the controls persist anything — the Apply button in Settings is a no-op, and
the record button plays a scripted dictation instead of opening a microphone.
Run it, click around, and say what's wrong with it.

    python3 mockup/baatsun_mockup.py

It is written against the real GTK4/libadwaita toolkit rather than drawn in an
image editor, so what you see is exactly what the shipped app would look like
on this machine — same widgets, same metrics, same theme, light or dark.

The shape it proposes: an Adw.NavigationSplitView with four places, instead of
today's single history list with a modal settings dialog.

    Dictate    the live page — record control, level meter, what window has
               focus and therefore whether cleanup will run on what you say
    History    what exists today, grouped by day and filterable
    Words      vocabulary as a managed list, not one comma-separated field
    Settings   a page rather than a modal, with room for the model settings
               that are currently config-file-only

NOT YET BACKED BY REAL DATA. Three things below need the daemon to record or
report something it doesn't today. They are in the mockup because the question
is whether they're worth having; if they are, they're small daemon changes.

    1. The focused-window card on Dictate. The daemon already *receives*
       focus reports from the Shell extension and already classifies them
       (baatsun_context.classify), but it never tells the GUI. Needs one more
       broadcast event.
    2. The per-entry app badge in History ("Firefox", "Code"). History entries
       currently store id/text/raw/ts and nothing about where they were typed.
       Needs two more fields at append time.
    3. Duration/word counts anywhere. Not stored at all.

Everything else on screen maps to something that already exists.
"""
import math
import random
import sys
import time
from datetime import datetime, timedelta

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

APP_ID = "com.baatsun.Baatsun.Mockup"

# ---------------------------------------------------------------- fake data

_NOW = time.time()


def _ago(**kwargs):
    return (datetime.fromtimestamp(_NOW) - timedelta(**kwargs)).timestamp()


# `raw` is set only where the cleanup pass changed something, mirroring how the
# daemon actually stores it. `app` is the invented field (see note 2 above).
FAKE_HISTORY = [
    {
        "id": 1, "ts": _ago(minutes=3), "app": "Firefox", "context": "prose",
        "text": "I've been building a dictation tool for Linux that runs "
                "whisper locally, and the hardest part turned out to be "
                "everything except the transcription.",
        "raw": "so i've been building a dictation tool for linux that runs "
               "whisper locally and um the hardest part turned out to be "
               "everything except the transcription",
    },
    {
        "id": 2, "ts": _ago(minutes=18), "app": "Code", "context": "developer",
        "text": "refactor the state machine so hover and daemon state both go "
                "through one refresh function",
        "raw": None,
    },
    {
        "id": 3, "ts": _ago(hours=2), "app": "Slack", "context": "prose",
        "text": "Sorry for the delay — I was heads-down on the pill rewrite. "
                "Should have something to show this afternoon.",
        "raw": "sorry for the delay i was heads down on the pill rewrite "
               "should have something to show this afternoon",
    },
    {
        "id": 4, "ts": _ago(hours=5), "app": "Terminal", "context": "developer",
        "text": "git rebase interactive on to main and squash the last three "
                "commits",
        "raw": None,
    },
    {
        "id": 5, "ts": _ago(days=1, hours=1), "app": "Firefox",
        "context": "prose",
        "text": "The thing nobody tells you about dictation is that the model "
                "is the easy half. Getting the text into the right window, at "
                "the right moment, without stealing focus, is the whole game.",
        "raw": "the thing nobody tells you about dictation is that the model "
               "is the easy half getting the text into the right window at the "
               "right moment without stealing focus is the whole game",
    },
    {
        "id": 6, "ts": _ago(days=1, hours=3), "app": "Code",
        "context": "developer",
        "text": "add a breakpoint so the sidebar collapses under six forty",
        "raw": None,
    },
    {
        "id": 7, "ts": _ago(days=3), "app": "Slack", "context": "prose",
        "text": "Shipping the pill redesign today. One dark pill that opens "
                "instead of four states that change colour.",
        "raw": None,
    },
]

FAKE_VOCAB = ["Baatsun", "Claude", "ydotool", "libadwaita", "faster-whisper",
              "PipeWire", "Umar", "wlr-layer-shell"]

# Cycled by the "Show another" button on Dictate, to demonstrate that the card
# is what tells you *why* a transcript was cleaned or left verbatim.
FAKE_FOCUS = [
    ("Firefox", "Share something — LinkedIn", "prose"),
    ("Code", "extension.js — baatsun", "developer"),
    ("Slack", "baatsun — general", "prose"),
    ("Terminal", "xclore@thinkpad: ~/projects/baatsun", "developer"),
]

SCRIPTED_DICTATION = (
    "This is what a dictation looks like when it lands — the pill opens, the "
    "bars move, and the text arrives in whatever window had focus."
)

CSS = b"""
.record-orb {
  min-width: 96px;
  min-height: 96px;
}
.record-orb.recording {
  background: #c43434;
  color: #ffffff;
}
.state-caption {
  font-size: 1.1rem;
}
.context-badge {
  font-size: 0.8rem;
  font-weight: bold;
  padding: 2px 9px;
  border-radius: 999px;
}
.context-badge.prose {
  background: alpha(@accent_bg_color, 0.22);
  color: @accent_color;
}
.context-badge.developer {
  background: alpha(currentColor, 0.10);
}
.meter-bar {
  background-color: alpha(currentColor, 0.22);
  border-radius: 3px;
}
.meter-bar.live {
  background-color: @accent_bg_color;
}
.daemon-dot {
  font-size: 0.7rem;
  color: @success_color;
}
.transcript-text {
  font-size: 1.05rem;
}
"""


def format_clock(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def day_heading(ts):
    day = datetime.fromtimestamp(ts).date()
    today = datetime.fromtimestamp(_NOW).date()
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return datetime.fromtimestamp(ts).strftime("%A %-d %B")


def esc(text):
    """Adw rows read Pango markup, so anything user-shaped has to be escaped."""
    return GLib.markup_escape_text(text)


def flat_button(icon, tooltip, on_click=None):
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip,
                        valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    if on_click:
        button.connect("clicked", on_click)
    return button


# ------------------------------------------------------------- level meter


class LevelMeter(Gtk.Box):
    """The pill's level meter, at desk size.

    Deliberately the same five bars with the same random walk, so the window
    and the pill read as one instrument rather than two designs that happen to
    ship together. Like the pill's, it is not reading a microphone — the daemon
    records straight to a file and broadcasts no levels.

    Built from plain widgets rather than a Gtk.DrawingArea on purpose. Cairo
    drawing from Python needs the python3-gi-cairo foreign-struct converter,
    which is a separate package and is NOT installed on this machine; a draw
    func would raise on every frame and paint nothing. Five boxes with an
    animated height need no dependency at all.
    """

    COUNT = 5
    BAR_WIDTH = 6
    SPACING = 9
    MAX_HEIGHT = 44
    MIN_HEIGHT = 6
    APPROACH = 20.0

    def __init__(self):
        super().__init__(spacing=self.SPACING, halign=Gtk.Align.CENTER,
                         valign=Gtk.Align.CENTER)
        self.set_size_request(-1, self.MAX_HEIGHT + 4)
        self.active = False

        self._bars = []
        for _ in range(self.COUNT):
            bar = Gtk.Box(valign=Gtk.Align.CENTER)
            bar.add_css_class("meter-bar")
            bar.set_size_request(self.BAR_WIDTH, self.MIN_HEIGHT)
            self.append(bar)
            self._bars.append(bar)

        self._heights = [float(self.MIN_HEIGHT)] * self.COUNT
        self._targets = list(self._heights)
        self._due = [0.0] * self.COUNT
        self._last = None
        self.add_tick_callback(self._tick)

    def _tick(self, _widget, clock):
        now = clock.get_frame_time() / 1_000_000.0
        dt = 0.016 if self._last is None else min(now - self._last, 0.1)
        self._last = now

        centre = (self.COUNT - 1) / 2.0
        approach = 1.0 - math.exp(-dt * self.APPROACH)
        span = self.MAX_HEIGHT - self.MIN_HEIGHT

        for i, bar in enumerate(self._bars):
            if not self.active:
                self._targets[i] = self.MIN_HEIGHT
            elif now >= self._due[i]:
                self._due[i] = now + random.uniform(0.11, 0.23)
                # Bars nearer the middle run taller, so the row has a centre
                # rather than looking like uniform static.
                allowance = 1.0 - 0.45 * abs(i - centre) / centre
                reach = 0.15 + 0.85 * random.random()
                self._targets[i] = self.MIN_HEIGHT + span * allowance * reach

            self._heights[i] += (self._targets[i] - self._heights[i]) * approach
            bar.set_size_request(self.BAR_WIDTH, round(self._heights[i]))

        return GLib.SOURCE_CONTINUE

    def set_active(self, active):
        self.active = active
        for bar in self._bars:
            if active:
                bar.add_css_class("live")
            else:
                bar.remove_css_class("live")


# -------------------------------------------------------------- the pages


class DictatePage(Adw.Bin):
    """The page the app opens on: what is happening, right now.

    This is the part that does not exist today, and the reason the window
    deserves a sidebar. Today the app can only tell you what you already said.
    """

    def __init__(self, window):
        super().__init__()
        self._window = window
        self._focus_index = 0

        page = Adw.PreferencesPage()

        orb_group = Adw.PreferencesGroup()
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                         margin_top=18, margin_bottom=6,
                         halign=Gtk.Align.CENTER)

        self.orb = Gtk.Button(halign=Gtk.Align.CENTER,
                              tooltip_text="Start dictating")
        self.orb.add_css_class("circular")
        self.orb.add_css_class("suggested-action")
        self.orb.add_css_class("record-orb")
        self.orb.set_child(Gtk.Image(icon_name="audio-input-microphone-symbolic",
                                     pixel_size=36))
        self.orb.connect("clicked", self._on_orb)
        column.append(self.orb)

        # The same three-way vocabulary the pill uses — nothing, bars,
        # spinner — so the window and the indicator are saying the same thing
        # in the same order. A Stack keeps the height reserved, so the page
        # doesn't jump every time a dictation starts.
        self.meter = LevelMeter()
        spinner = Gtk.Spinner(spinning=True, width_request=32,
                              height_request=32, halign=Gtk.Align.CENTER,
                              valign=Gtk.Align.CENTER)
        blank = Gtk.Box()
        blank.set_size_request(120, LevelMeter.MAX_HEIGHT + 4)

        self.activity = Gtk.Stack(halign=Gtk.Align.CENTER)
        self.activity.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.activity.add_named(blank, "idle")
        self.activity.add_named(self.meter, "listening")
        self.activity.add_named(spinner, "transcribing")
        column.append(self.activity)

        self.state_label = Gtk.Label(label="Ready")
        self.state_label.add_css_class("title-2")
        column.append(self.state_label)

        self.hint_label = Gtk.Label(label="Hold Ctrl+Super and speak")
        self.hint_label.add_css_class("dim-label")
        column.append(self.hint_label)

        orb_group.add(column)
        page.add(orb_group)

        # Why this is here: the classifier decides whether what you are about
        # to say gets cleaned up or typed verbatim, and today that decision is
        # completely invisible until you read the result.
        self.focus_group = Adw.PreferencesGroup(
            title="Focused window",
            description="Where the next transcript will be typed, and how it "
                        "will be treated when it gets there.",
        )
        self.focus_row = Adw.ActionRow()
        self.focus_row.set_subtitle_lines(2)
        self.badge = Gtk.Label(valign=Gtk.Align.CENTER)
        self.badge.add_css_class("context-badge")
        self.focus_row.add_suffix(self.badge)
        self.focus_row.add_suffix(
            flat_button("view-refresh-symbolic",
                        "Show another example window", self._cycle_focus))
        self.focus_group.add(self.focus_row)
        page.add(self.focus_group)
        self._apply_focus()

        self.last_group = Adw.PreferencesGroup(title="Last transcript")
        self.last_row = Adw.ActionRow()
        self.last_row.set_title_lines(0)
        self.last_row.add_suffix(
            flat_button("edit-copy-symbolic", "Copy",
                        lambda *_a: window.toast("Copied")))
        self.last_row.add_suffix(
            flat_button("edit-redo-symbolic", "Type again into focused window",
                        lambda *_a: window.toast("Typed again")))
        self.last_group.add(self.last_row)
        page.add(self.last_group)
        self.set_last(FAKE_HISTORY[0])

        self.set_child(page)

    def set_last(self, entry):
        self.last_row.set_title(esc(entry["text"]))
        badge = " · cleaned up" if entry.get("raw") else " · verbatim"
        self.last_row.set_subtitle(
            f"{format_clock(entry['ts'])} · {esc(entry['app'])}{badge}")

    def _cycle_focus(self, *_args):
        self._focus_index = (self._focus_index + 1) % len(FAKE_FOCUS)
        self._apply_focus()

    def _apply_focus(self):
        app, title, context = FAKE_FOCUS[self._focus_index]
        self.focus_row.set_title(esc(app))
        cleaned = context == "prose"
        self.focus_row.set_subtitle(
            esc(title) + "\n" + ("Cleanup will run on this one"
                                 if cleaned else "Typed exactly as transcribed"))
        self.badge.set_label("Prose" if cleaned else "Developer")
        self.badge.remove_css_class("prose")
        self.badge.remove_css_class("developer")
        self.badge.add_css_class(context)

    # The whole dictation loop, scripted, so every state can be seen without a
    # microphone: press once and it listens, transcribes, and lands the text.
    def _on_orb(self, *_args):
        if self._window.state == "idle":
            self._window.set_state("listening")
        elif self._window.state == "listening":
            self._window.set_state("transcribing")
            GLib.timeout_add(1600, self._finish)

    def _finish(self):
        entry = {
            "id": 1000 + len(FAKE_HISTORY), "ts": time.time(),
            "app": FAKE_FOCUS[self._focus_index][0],
            "context": FAKE_FOCUS[self._focus_index][2],
            "text": SCRIPTED_DICTATION,
            "raw": SCRIPTED_DICTATION.lower() if
            FAKE_FOCUS[self._focus_index][2] == "prose" else None,
        }
        self._window.add_transcript(entry)
        self._window.set_state("idle")
        return GLib.SOURCE_REMOVE

    def apply_state(self, state):
        self.meter.set_active(state == "listening")
        self.activity.set_visible_child_name(
            state if state in ("listening", "transcribing") else "idle")
        self.state_label.set_label({
            "listening": "Listening…",
            "transcribing": "Transcribing…",
        }.get(state, "Ready"))
        self.hint_label.set_label({
            "listening": "Press again, or release Ctrl+Super, to stop",
            "transcribing": "Running whisper locally",
        }.get(state, "Hold Ctrl+Super and speak"))

        recording = state == "listening"
        self.orb.set_sensitive(state != "transcribing")
        if recording:
            self.orb.add_css_class("recording")
        else:
            self.orb.remove_css_class("recording")
        self.orb.get_child().set_from_icon_name(
            "media-playback-stop-symbolic" if recording
            else "audio-input-microphone-symbolic")


class HistoryPage(Adw.Bin):
    """Today's list, grouped by day and filterable."""

    FILTERS = ["Everything", "Cleaned up", "Verbatim"]

    def __init__(self, window):
        super().__init__()
        self._window = window
        self._query = ""
        self._filter = 0
        self._revealed = set()

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        controls = Gtk.Box(spacing=6, margin_start=12, margin_end=12,
                           margin_top=6, margin_bottom=6)
        self.search = Gtk.SearchEntry(placeholder_text="Search transcripts",
                                      hexpand=True)
        self.search.connect("search-changed", self._on_search)
        controls.append(self.search)

        self.filter_drop = Gtk.DropDown.new_from_strings(self.FILTERS)
        self.filter_drop.set_tooltip_text("Show only cleaned or verbatim")
        self.filter_drop.connect("notify::selected", self._on_filter)
        controls.append(self.filter_drop)
        self.box.append(controls)

        self.list_page = Adw.PreferencesPage()
        scroller = Gtk.ScrolledWindow(vexpand=True, child=self.list_page)

        self.empty = Adw.StatusPage(
            title="Nothing matches",
            description="No transcript contains that.",
            icon_name="system-search-symbolic",
        )

        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(scroller, "list")
        self.stack.add_named(self.empty, "empty")
        self.box.append(self.stack)
        self.set_child(self.box)

        self.rebuild()

    def _on_search(self, entry):
        self._query = entry.get_text().strip().lower()
        self.rebuild()

    def _on_filter(self, drop, _pspec):
        self._filter = drop.get_selected()
        self.rebuild()

    def _matches(self, entry):
        if self._filter == 1 and not entry.get("raw"):
            return False
        if self._filter == 2 and entry.get("raw"):
            return False
        return not self._query or self._query in entry["text"].lower()

    def rebuild(self):
        child = self.list_page.get_first_child()
        # PreferencesPage has no "remove everything"; rebuild the whole page
        # rather than track groups, which is what the real one will do too
        # until the list is big enough to need a Gtk.ListView.
        self.list_page = Adw.PreferencesPage()
        scroller = self.stack.get_child_by_name("list")
        scroller.set_child(self.list_page)
        del child

        entries = [e for e in FAKE_HISTORY if self._matches(e)]
        entries.sort(key=lambda e: e["ts"], reverse=True)

        if not entries:
            self.stack.set_visible_child_name("empty")
            return
        self.stack.set_visible_child_name("list")

        group = None
        heading = None
        for entry in entries:
            if day_heading(entry["ts"]) != heading:
                heading = day_heading(entry["ts"])
                group = Adw.PreferencesGroup(title=heading)
                self.list_page.add(group)
            group.add(self._row(entry))

    def _row(self, entry):
        revealed = entry["id"] in self._revealed
        row = Adw.ActionRow()
        row.set_title_lines(0)
        row.set_subtitle_lines(1)
        row.set_title(esc(entry["raw"] if revealed else entry["text"]))
        row.add_css_class("transcript-text")

        marks = [format_clock(entry["ts"]), esc(entry["app"])]
        marks.append("before cleanup" if revealed else
                     ("cleaned up" if entry.get("raw") else "verbatim"))
        row.set_subtitle(" · ".join(marks))

        # Offered only where cleanup changed something, so the button's very
        # presence is the signal that this one was rewritten.
        if entry.get("raw"):
            row.add_suffix(flat_button(
                "view-conceal-symbolic" if revealed else "view-reveal-symbolic",
                "Show what you actually said, before cleanup",
                lambda *_a, e=entry: self._toggle_raw(e)))
        row.add_suffix(flat_button("edit-copy-symbolic", "Copy",
                                   lambda *_a: self._window.toast("Copied")))
        row.add_suffix(flat_button(
            "edit-redo-symbolic", "Type again into focused window",
            lambda *_a: self._window.toast("Typed again")))
        row.add_suffix(flat_button("user-trash-symbolic", "Delete",
                                   lambda *_a, e=entry: self._delete(e)))
        return row

    def _toggle_raw(self, entry):
        self._revealed ^= {entry["id"]}
        self.rebuild()

    def _delete(self, entry):
        FAKE_HISTORY.remove(entry)
        self.rebuild()
        self._window.toast("Transcript deleted")


class WordsPage(Adw.Bin):
    """Vocabulary, promoted out of a single comma-separated Settings field.

    Same data the daemon already uses — it goes to whisper as an initial_prompt
    and is repeated to the cleanup model — just editable one term at a time
    instead of as one long string you have to re-read to change.
    """

    def __init__(self, window):
        super().__init__()
        self._window = window

        self.page = Adw.PreferencesPage()
        self.set_child(self.page)
        self.rebuild()

    def rebuild(self):
        page = Adw.PreferencesPage()

        add_group = Adw.PreferencesGroup(
            title="Names to get right",
            description="Your name, your products, the tools you talk about — "
                        "anything the transcriber mishears. These are fed to "
                        "whisper as it decodes, so they work even with cleanup "
                        "switched off.",
        )
        self.entry = Adw.EntryRow(title="Add a name or term")
        add = Gtk.Button(icon_name="list-add-symbolic",
                         valign=Gtk.Align.CENTER, tooltip_text="Add")
        add.add_css_class("flat")
        add.connect("clicked", self._on_add)
        self.entry.add_suffix(add)
        self.entry.connect("entry-activated", self._on_add)
        add_group.add(self.entry)
        page.add(add_group)

        list_group = Adw.PreferencesGroup(
            title=f"{len(FAKE_VOCAB)} terms",
            description="Kept short on purpose: a long list dilutes the hint "
                        "and starts pulling words into transcripts that were "
                        "never said.",
        )
        for term in FAKE_VOCAB:
            row = Adw.ActionRow(title=esc(term))
            row.add_suffix(flat_button(
                "user-trash-symbolic", "Remove",
                lambda *_a, t=term: self._remove(t)))
            list_group.add(row)
        page.add(list_group)

        self.set_child(page)
        self.page = page

    def _on_add(self, *_args):
        term = self.entry.get_text().strip()
        if not term:
            return
        if term not in FAKE_VOCAB:
            FAKE_VOCAB.append(term)
        self.rebuild()
        self._window.toast(f"Added “{term}”")

    def _remove(self, term):
        FAKE_VOCAB.remove(term)
        self.rebuild()
        self._window.toast(f"Removed “{term}”")


class SettingsPage(Adw.Bin):
    """The current modal, unpacked into a page — plus the settings that are
    config-file-only today (model, compute type) and the daemon controls."""

    def __init__(self, window):
        super().__init__()
        self._window = window

        page = Adw.PreferencesPage()

        recording = Adw.PreferencesGroup(title="Recording")
        hotkey = Adw.ComboRow(title="Hotkey")
        hotkey.set_model(Gtk.StringList.new(
            ["Ctrl+Super", "Ctrl+Alt", "Super+Alt", "Right Ctrl"]))
        recording.add(hotkey)
        behaviour = Adw.ComboRow(
            title="Hotkey behaviour",
            subtitle="Tap-or-hold keeps push-to-talk and frees your hands too",
        )
        behaviour.set_model(Gtk.StringList.new([
            "Record while held",
            "Press to start, press again to stop",
            "Tap to start and stop, hold to talk",
        ]))
        recording.add(behaviour)
        page.add(recording)

        cleanup = Adw.PreferencesGroup(
            title="Cleanup with OpenAI",
            description="Tidies punctuation, capitalisation and filler words "
                        "before typing. Only the transcribed text is sent — "
                        "your audio never leaves this machine.",
        )
        switch = Adw.SwitchRow(title="Clean up transcripts",
                               subtitle="Off until an API key is saved below")
        switch.set_active(True)
        cleanup.add(switch)
        scope = Adw.ComboRow(
            title="Apply to",
            subtitle="Prose only: terminals and editors stay verbatim")
        scope.set_model(Gtk.StringList.new(
            ["Prose windows only", "Everything I dictate"]))
        cleanup.add(scope)
        strength = Adw.ComboRow(
            title="Correction level",
            subtitle="Natural also fixes phrasing a native speaker wouldn't use")
        strength.set_model(Gtk.StringList.new(
            ["Grammar only", "Natural English"]))
        cleanup.add(strength)
        breaks = Adw.SwitchRow(
            title="Break long text into paragraphs",
            subtitle="Never in chat apps, where Enter would send the message")
        breaks.set_active(True)
        cleanup.add(breaks)
        hinglish = Adw.SwitchRow(
            title="I mix Hindi words into my speech",
            subtitle="Renders garbled Hindi (“K”, “Hummer”) as English")
        cleanup.add(hinglish)
        key = Adw.PasswordEntryRow(title="OpenAI API key")
        key.set_text("sk-not-a-real-key")
        cleanup.add(key)
        test = Adw.ActionRow(
            title="Test key",
            subtitle="Sends one short request to check the key")
        test_button = Gtk.Button(label="Test", valign=Gtk.Align.CENTER)
        test_button.connect(
            "clicked", lambda *_a: test.set_subtitle("✓ Key works (gpt-4o-mini)"))
        test.add_suffix(test_button)
        cleanup.add(test)
        page.add(cleanup)

        # Config-file-only today. On a page there is finally room for them.
        transcription = Adw.PreferencesGroup(
            title="Transcription",
            description="Runs locally on CPU. Bigger is not better here — "
                        "small.en was chosen by measurement.")
        model = Adw.ComboRow(title="Model", subtitle="Downloads on first use")
        model.set_model(Gtk.StringList.new(
            ["small.en (recommended)", "base.en", "distil-small.en",
             "distil-large-v3.5"]))
        transcription.add(model)
        compute = Adw.ComboRow(title="Compute type")
        compute.set_model(Gtk.StringList.new(
            ["int8", "int8_float16", "float16", "float32"]))
        transcription.add(compute)
        page.add(transcription)

        daemon = Adw.PreferencesGroup(title="Daemon")
        status = Adw.ActionRow(
            title="baatsun.service",
            subtitle="Running · model loaded · listening for Ctrl+Super")
        restart = Gtk.Button(label="Restart", valign=Gtk.Align.CENTER)
        restart.connect("clicked", lambda *_a: window.toast("Daemon restarted"))
        status.add_suffix(restart)
        daemon.add(status)
        page.add(daemon)

        self.set_child(page)


# -------------------------------------------------------------- the window


PAGES = [
    ("dictate", "Dictate", "audio-input-microphone-symbolic"),
    ("history", "History", "document-open-recent-symbolic"),
    ("words", "Words", "view-list-bullet-symbolic"),
    ("settings", "Settings", "emblem-system-symbolic"),
]


class MockupWindow(Adw.ApplicationWindow):
    def __init__(self, app, narrow=False):
        super().__init__(application=app, title="Baatsun")
        self.set_default_size(560, 700) if narrow else \
            self.set_default_size(940, 680)
        # Adw.Breakpoint refuses to work without one, and the collapse below
        # is the whole point of the split view.
        self.set_size_request(360, 420)
        self.state = "idle"

        self.dictate = DictatePage(self)
        self.history = HistoryPage(self)
        self.words = WordsPage(self)
        self.settings = SettingsPage(self)
        self._pages = {
            "dictate": self.dictate, "history": self.history,
            "words": self.words, "settings": self.settings,
        }

        self.stack = Gtk.Stack()
        for name, title, _icon in PAGES:
            self.stack.add_named(self._wrap(title, self._pages[name]), name)

        self.split = Adw.NavigationSplitView(
            sidebar=self._build_sidebar(),
            content=Adw.NavigationPage(title="Baatsun", child=self.stack),
        )
        self.split.set_min_sidebar_width(200)
        self.split.set_max_sidebar_width(240)

        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_content(self.toasts)

        # Below this the sidebar becomes a page you navigate back to, which is
        # what makes the window usable at half-screen width.
        breakpoint_ = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 680px"))
        breakpoint_.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint_)

        self.show_page("dictate")
        self.set_state("idle")

    def _build_sidebar(self):
        self.sidebar_list = Gtk.ListBox()
        self.sidebar_list.add_css_class("navigation-sidebar")
        self.sidebar_list.connect("row-selected", self._on_nav)

        for _name, title, icon in PAGES:
            row = Gtk.ListBoxRow()
            line = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8,
                           margin_start=6, margin_end=6)
            line.append(Gtk.Image(icon_name=icon))
            line.append(Gtk.Label(label=title, xalign=0))
            row.set_child(line)
            self.sidebar_list.append(row)
        # Selecting the first row is deliberately left to __init__, after the
        # split view exists — row-selected fires synchronously, and _on_nav
        # reaches for self.split.

        # A window that can be opened while the daemon is down should say so
        # somewhere permanent, not only by disabling a button.
        footer = Gtk.Box(spacing=8, margin_start=14, margin_end=14,
                         margin_top=8, margin_bottom=12)
        dot = Gtk.Label(label="●", valign=Gtk.Align.CENTER)
        dot.add_css_class("daemon-dot")
        footer.append(dot)
        caption = Gtk.Label(label="Daemon running", xalign=0)
        caption.add_css_class("caption")
        caption.add_css_class("dim-label")
        footer.append(caption)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(Gtk.ScrolledWindow(vexpand=True, child=self.sidebar_list))
        body.append(Gtk.Separator())
        body.append(footer)

        view = Adw.ToolbarView(content=body)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Baatsun"))
        view.add_top_bar(header)
        return Adw.NavigationPage(title="Baatsun", child=view)

    def _wrap(self, title, page):
        """Each page carries its own header, so History can own a search bar
        and Dictate can own a record button without either faking it."""
        view = Adw.ToolbarView(content=page)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=title))

        if title == "History":
            menu = Gio.Menu()
            menu.append("Export all…", "app.nothing")
            menu.append("Clear history", "app.nothing")
            header.pack_end(Gtk.MenuButton(icon_name="view-more-symbolic",
                                           menu_model=menu))
        if title == "Settings":
            apply_button = Gtk.Button(label="Apply")
            apply_button.add_css_class("suggested-action")
            apply_button.connect(
                "clicked",
                lambda *_a: self.toast("Saved — restarting daemon"))
            header.pack_end(apply_button)

        view.add_top_bar(header)
        return view

    def _on_nav(self, _listbox, row):
        if row is None:
            return
        self.stack.set_visible_child_name(PAGES[row.get_index()][0])
        if self.split.get_collapsed():
            self.split.set_show_content(True)

    def show_page(self, name):
        index = [p[0] for p in PAGES].index(name)
        self.sidebar_list.select_row(self.sidebar_list.get_row_at_index(index))

    def toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=message, timeout=2))

    def set_state(self, state):
        self.state = state
        self.dictate.apply_state(state)

    def add_transcript(self, entry):
        FAKE_HISTORY.append(entry)
        self.dictate.set_last(entry)
        self.history.rebuild()
        self.toast("Transcript typed into Firefox")


class MockupApp(Adw.Application):
    def __init__(self, capture_dir=None, narrow=False):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self._capture_dir = capture_dir
        self._narrow = narrow
        # The History menu is decoration in a mockup; give its items somewhere
        # to go so GTK doesn't grey them out and misrepresent the design.
        action = Gio.SimpleAction.new("nothing", None)
        self.add_action(action)

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        window = MockupWindow(self, narrow=self._narrow)
        window.present()
        if self._narrow:
            self.CAPTURES = [
                ("dictate", "idle", "narrow-sidebar"),
                ("dictate", "listening", "narrow-content"),
            ]
        if self._capture_dir:
            GLib.timeout_add(900, self._capture, window)

    # Capture sequence: every page, plus the two live states on Dictate that
    # only exist while something is happening.
    CAPTURES = [
        ("dictate", "idle", "dictate"),
        ("history", None, "history"),
        ("words", None, "words"),
        ("settings", None, "settings"),
        ("dictate", "listening", "dictate-listening"),
        ("dictate", "transcribing", "dictate-transcribing"),
    ]

    def _capture(self, window):
        """Render the app to PNGs out of GTK's own renderer, so the design can
        be looked at without anyone having to run it."""
        self._queue = list(self.CAPTURES)
        return self._capture_next(window)

    def _capture_next(self, window):
        if not self._queue:
            self.quit()
            return GLib.SOURCE_REMOVE
        page, state, filename = self._queue.pop(0)
        window.show_page(page)
        # Collapsed, the split view is a two-page stack; show whichever half
        # this capture is meant to be of.
        if window.split.get_collapsed():
            window.split.set_show_content(filename == "narrow-content")
        if state is not None:
            window.set_state(state)
        # Real time has to pass, not just main-loop iterations: the meter is
        # driven by the frame clock and needs frames to have happened.
        GLib.timeout_add(700, self._snap, window, filename)
        return GLib.SOURCE_REMOVE

    def _snap(self, window, filename):
        import os
        paintable = Gtk.WidgetPaintable.new(window)
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, window.get_width(), window.get_height())
        node = snapshot.to_node()
        if node is None:
            print(f"{filename}: nothing to snapshot", file=sys.stderr)
        else:
            texture = window.get_native().get_renderer().render_texture(
                node, None)
            path = os.path.join(self._capture_dir, f"{filename}.png")
            texture.save_to_png(path)
            print(f"wrote {path}")
        GLib.timeout_add(120, self._capture_next, window)
        return GLib.SOURCE_REMOVE


def main():
    capture_dir = None
    if "--capture" in sys.argv:
        capture_dir = sys.argv[sys.argv.index("--capture") + 1]
    return MockupApp(capture_dir, narrow="--narrow" in sys.argv).run([])


if __name__ == "__main__":
    sys.exit(main())
