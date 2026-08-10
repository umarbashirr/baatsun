#!/usr/bin/env python3
"""baatsun-gui: the Baatsun application window.

A GTK4/libadwaita app that connects to the daemon's unix socket, fetches past
transcripts, and stays subscribed for live updates. Runs under the desktop
session's system Python (needs PyGObject + libadwaita), not the daemon's venv —
launch it with plain `python3`, not `venv/bin/python3`.

Single-instance: GApplication's own D-Bus activation means running this a
second time (e.g. from the tray icon) just re-presents the existing window
instead of starting a new process.

Four places, in an Adw.NavigationSplitView:

    Dictate    the live page — record control, level meter, and the focused
               window, which is what decides whether the next thing you say is
               cleaned up or typed exactly as transcribed
    History    past transcripts, grouped by day, filterable
    Words      the vocabulary the decoder is biased toward
    Settings   everything in ~/.config/baatsun/config.json worth exposing

Deliberately no Gtk.DrawingArea anywhere in here. Cairo drawing from Python
needs the python3-gi-cairo foreign-struct converter, which is a separate
package and not guaranteed present; a draw func would raise on every frame and
paint nothing. The level meter is five boxes with an animated height instead,
which needs no dependency beyond what the rest of this window already uses.
"""
import json
import math
import os
import random
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

import baatsun_cleanup  # noqa: E402
import baatsun_config  # noqa: E402

SOCKET_PATH = f"/run/user/{os.getuid()}/baatsun.sock"
APP_ID = "com.baatsun.Baatsun"
RECONNECT_SECONDS = 3

PAGES = [
    ("home", "Home", "go-home-symbolic"),
    ("dictate", "Dictate", "audio-input-microphone-symbolic"),
    ("history", "History", "document-open-recent-symbolic"),
    ("words", "Words", "view-list-bullet-symbolic"),
    ("settings", "Settings", "emblem-system-symbolic"),
]

CSS = b"""
.record-orb {
  min-width: 96px;
  min-height: 96px;
}
.record-orb.recording {
  background: #c43434;
  color: #ffffff;
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
.daemon-dot.up {
  color: @success_color;
}
.daemon-dot.down {
  color: @warning_color;
}
.transcript-text {
  font-size: 1.05rem;
}

/* Home ------------------------------------------------------------------ */

.greeting {
  font-size: 1.9rem;
  font-weight: 800;
}
.quote-text {
  font-size: 1.15rem;
  font-style: italic;
}
.stat-number {
  font-size: 1.8rem;
  font-weight: 800;
}
.stat-tile {
  padding: 14px;
}
.quote-card {
  padding: 16px;
}
/* One series, so one hue, and it is the theme's own accent -- which means
   light and dark are each chosen by libadwaita rather than one being an
   automatic flip of the other. Data-ends are rounded and the baseline end is
   square, so every bar is anchored to the same line. */
.chart-bar {
  background-color: @accent_bg_color;
  border-radius: 4px 4px 0 0;
}
/* A day with nothing in it still gets a mark, or the axis reads as ragged
   rather than as a real zero. */
.chart-bar.empty {
  background-color: alpha(currentColor, 0.13);
}
.chart-bar.today {
  background-color: @accent_color;
}
"""


# ------------------------------------------------------------------- socket


def send_command(command):
    """Send a one-shot command and return the raw reply bytes (or None)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(SOCKET_PATH)
            sock.sendall(command.encode())
            chunks = []
            try:
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
            except socket.timeout:
                pass
            return b"".join(chunks)
    except OSError:
        return None


def fire_and_forget(command):
    threading.Thread(target=send_command, args=(command,), daemon=True).start()


# ------------------------------------------------------------------ helpers


def format_clock(ts):
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def day_heading(ts):
    try:
        day = datetime.fromtimestamp(ts).date()
    except (TypeError, ValueError, OSError):
        return "Earlier"
    today = datetime.now().date()
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    if today - day < timedelta(days=7):
        return day.strftime("%A")
    return day.strftime("%-d %B %Y")


def esc(text):
    """Adw rows read Pango markup, so anything user-shaped has to be escaped."""
    return GLib.markup_escape_text(text or "")


def flat_button(icon, tooltip, on_click=None):
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip,
                        valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    if on_click:
        button.connect("clicked", on_click)
    return button


def pretty_app(app):
    """A window class as a person would name it.

    The Shell reports WM_CLASS, which for anything packaged as a Flatpak or a
    GNOME app is a reverse-DNS id — "org.gnome.Nautilus", "com.baatsun.Baatsun".
    The last component is the name, and lowercase single-word classes
    ("firefox") only need a capital.
    """
    if not app:
        return ""
    name = app.rsplit(".", 1)[-1] or app
    return name[:1].upper() + name[1:] if name.islower() else name


def split_vocabulary(text):
    """config stores one comma-separated string; the Words page wants a list."""
    return [term.strip() for term in (text or "").split(",") if term.strip()]


# ------------------------------------------------------------------- quotes

# Deliberately only quotations with settled attributions. The famous ones about
# writing are a minefield of misattribution — "write drunk, edit sober" is not
# Hemingway, and "if you can't explain it simply" is not Einstein — so neither
# is here, however well they would have fitted.
QUOTES = [
    ("The difference between the almost right word and the right word is "
     "really a large matter — 'tis the difference between the lightning-bug "
     "and the lightning.", "Mark Twain"),
    ("I have made this longer than usual because I have not had time to make "
     "it shorter.", "Blaise Pascal"),
    ("The limits of my language mean the limits of my world.",
     "Ludwig Wittgenstein"),
    ("Easy reading is damned hard writing.", "Nathaniel Hawthorne"),
    ("A word after a word after a word is power.", "Margaret Atwood"),
    ("Brevity is the soul of wit.", "William Shakespeare"),
    ("Language is the dress of thought.", "Samuel Johnson"),
    ("Don't tell me the moon is shining; show me the glint of light on broken "
     "glass.", "Anton Chekhov"),
    ("Speech is the mirror of the soul; as a man speaks, so is he.",
     "Publilius Syrus"),
    ("Proper words in proper places make the true definition of a style.",
     "Jonathan Swift"),
    ("Think like a wise man but communicate in the language of the people.",
     "W. B. Yeats"),
    ("There is no greater agony than bearing an untold story inside you.",
     "Maya Angelou"),
    ("Words are, of course, the most powerful drug used by mankind.",
     "Rudyard Kipling"),
    ("Speak clearly, if you speak at all; carve every word before you let it "
     "fall.", "Oliver Wendell Holmes Sr."),
]


def greeting_for(hour, name):
    part = ("Good morning" if 5 <= hour < 12
            else "Good afternoon" if 12 <= hour < 18
            else "Good evening" if 18 <= hour < 22
            else "Still up")
    return f"{part}, {name}" if name else part


def greeting_name(cfg=None):
    """A name to greet by, in order of how likely it is to be the real one.

    1. What the user typed in Settings. Used exactly as written -- if someone
       has gone to the trouble of typing their name, shortening it is not an
       improvement.
    2. The GECOS real name, which is what GNOME Settings > Users writes.
    3. The login name, capitalised.

    2 and 3 are trimmed to a first word, because GECOS is conventionally
    "Umar Bashir Rather,,," and "Good morning, Umar Bashir Rather,,," is not a
    greeting. get_real_name() answers the literal string "Unknown" when it has
    nothing, and on many machines answers the login name, which is a handle
    rather than a name -- hence the field in Settings.
    """
    chosen = ((cfg or baatsun_config.load_config()).get("display_name")
              or "").strip()
    if chosen:
        return chosen

    derived = (GLib.get_real_name() or "").strip()
    if not derived or derived.lower() == "unknown":
        derived = (GLib.get_user_name() or "").strip()
    if not derived or derived.lower() == "unknown":
        return ""
    first = derived.split()[0].rstrip(",")
    return first[:1].upper() + first[1:] if first.islower() else first


# -------------------------------------------------------------- level meter


class LevelMeter(Gtk.Box):
    """Five bars that jump while a recording is open.

    The same five bars, with the same random walk, as the pill's — so the
    window and the always-on-top indicator read as one instrument rather than
    two designs that happen to ship together.

    Not reading the microphone. The daemon records straight to a file with
    pw-record and broadcasts no levels, so this says "capturing", not
    "capturing *this* loudly".
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


# ------------------------------------------------------------------ insights


# Typing speed the "time saved" tile measures against. A composition speed, not
# a copy-typing speed: nobody drafts prose at their typing-test number.
TYPING_WPM = 40
# Fallback speaking rate, used only for entries recorded before the daemon
# started storing the real duration.
SPEAKING_WPM = 150
ACTIVITY_DAYS = 14


def compute_stats(entries):
    """Everything the Home page shows, from the history the daemon already has."""
    today = datetime.now().date()
    counts = {today - timedelta(days=n): 0 for n in range(ACTIVITY_DAYS)}

    words = cleaned = spoken_secs = 0
    per_app = {}
    days_seen = set()

    for entry in entries:
        text = entry.get("text") or ""
        count = len(text.split())
        words += count
        if entry.get("raw"):
            cleaned += 1
        spoken_secs += entry.get("secs") or (count / SPEAKING_WPM * 60)
        if entry.get("app"):
            name = pretty_app(entry["app"])
            per_app[name] = per_app.get(name, 0) + 1
        try:
            day = datetime.fromtimestamp(entry["ts"]).date()
        except (KeyError, TypeError, ValueError, OSError):
            continue
        days_seen.add(day)
        if day in counts:
            counts[day] += 1

    # Consecutive days ending today, or ending yesterday if today is still
    # empty -- a streak shouldn't look broken at breakfast.
    streak = 0
    cursor = today if today in days_seen else today - timedelta(days=1)
    while cursor in days_seen:
        streak += 1
        cursor -= timedelta(days=1)

    typing_secs = words / TYPING_WPM * 60
    return {
        "total": len(entries),
        "words": words,
        "today": counts.get(today, 0),
        "streak": streak,
        "cleaned": cleaned,
        "spoken_secs": spoken_secs,
        "saved_secs": max(0.0, typing_secs - spoken_secs),
        "top_apps": sorted(per_app.items(), key=lambda kv: kv[1], reverse=True),
        "activity": [(day, counts[day]) for day in sorted(counts)],
        **compute_spend(entries),
    }


def compute_spend(entries):
    """What the OpenAI cleanup pass has cost.

    Dictations carry the token counts OpenAI itself reported, but only since
    those started being recorded. Older ones are estimated from the text on
    each side of the call plus the system prompt — which is the bulk of the
    input, and has to be rebuilt from the *current* settings because the ones
    in force at the time were never stored. Estimated cost is totalled
    separately so the UI can say how much of the figure is inferred rather than
    billed.

    The estimate is a floor. A cleanup call that changed nothing left no trace
    in history at all, because the pre-cleanup text is only kept when it
    differs, so those old calls can't be counted. Entries written from now on
    carry their usage whether or not the text changed.
    """
    cfg = baatsun_config.load_config()
    model = cfg.get("cleanup_model") or baatsun_config.DEFAULT_CLEANUP_MODEL
    system_tokens = baatsun_cleanup.estimate_tokens(
        baatsun_cleanup.build_system_prompt(
            cfg.get("vocabulary") or "",
            line_breaks=bool(cfg.get("line_breaks", True)),
            hinglish=bool(cfg.get("hinglish")),
            strength=cfg.get("cleanup_strength") or baatsun_cleanup.GRAMMAR))
    month_start = datetime.now().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()

    total = estimated = month = 0.0
    calls = tokens_in = tokens_out = 0
    unpriced = False

    for entry in entries:
        usage = entry.get("usage")
        if usage:
            cost = usage.get("cost")
            went_in = usage.get("in") or 0
            came_out = usage.get("out") or 0
            inferred = False
        elif entry.get("raw"):
            went_in = system_tokens + baatsun_cleanup.estimate_tokens(entry["raw"])
            came_out = baatsun_cleanup.estimate_tokens(entry.get("text"))
            cost = baatsun_cleanup.cost_of(model, went_in, came_out)
            inferred = True
        else:
            continue

        calls += 1
        tokens_in += went_in
        tokens_out += came_out
        # A model with no price on file. It still made a call and still spent
        # tokens, both of which are counted; only its cost is unknown.
        if cost is None:
            unpriced = True
            continue
        total += cost
        if inferred:
            estimated += cost
        if (entry.get("ts") or 0) >= month_start:
            month += cost

    return {
        "spend_total": total,
        "spend_estimated": estimated,
        "spend_month": month,
        "spend_calls": calls,
        "spend_unpriced": unpriced,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }


def human_duration(seconds):
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def describe_spend(stats):
    """The tooltip behind the spend tile: what the figure covers, and how much
    of it is inferred rather than billed."""
    calls = stats["spend_calls"]
    if not calls:
        return ("Nothing spent yet. Only the optional cleanup pass costs "
                "anything — transcription runs on this machine and is free.")

    parts = [
        f"{calls} cleanup call{'' if calls == 1 else 's'} to OpenAI: "
        f"{human_count(stats['tokens_in'])} tokens in, "
        f"{human_count(stats['tokens_out'])} out.\n"
        f"{human_money(stats['spend_month'])} of it this month.",
    ]
    if stats["spend_estimated"]:
        parts.append(
            f"About {human_money(stats['spend_estimated'])} of the total is "
            "estimated from the stored text — those dictations were cleaned up "
            "before token usage was recorded, and a cleanup that changed "
            "nothing left no trace to count at all, so treat the figure as a "
            "floor. Newer dictations use the counts OpenAI reports.")
    if stats["spend_unpriced"]:
        parts.append("Some calls used a model with no price on file. Their "
                     "tokens are counted, but their cost isn't.")
    return "\n\n".join(parts)


def human_money(amount):
    """Dollars, at whatever precision keeps a real cost from reading as zero.

    Cleanup costs a small fraction of a cent per dictation, so two decimal
    places would show "$0.00" for months of use and make the feature look
    broken. The number of places shrinks as the amount grows.
    """
    if amount <= 0:
        return "$0"
    if amount < 0.01:
        return f"${amount:.4f}"
    if amount < 1:
        return f"${amount:.3f}"
    if amount < 100:
        return f"${amount:.2f}"
    return f"${round(amount):,}"


def human_count(value):
    if value < 1000:
        return str(value)
    if value < 100_000:
        return f"{value / 1000:.1f}k".replace(".0k", "k")
    return f"{round(value / 1000)}k"


class StatTile(Gtk.Box):
    """A hero number. Not a chart -- one value has no shape worth plotting."""

    def __init__(self, label, tooltip=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                         hexpand=True)
        self.add_css_class("card")
        self.add_css_class("stat-tile")

        self.value = Gtk.Label(label="0", xalign=0)
        self.value.add_css_class("stat-number")
        caption = Gtk.Label(label=label, xalign=0)
        caption.add_css_class("caption")
        caption.add_css_class("dim-label")
        caption.set_wrap(True)
        self.append(self.value)
        self.append(caption)
        if tooltip:
            self.set_tooltip_text(tooltip)

    def set_value(self, text):
        self.value.set_label(text)


class ActivityChart(Gtk.Box):
    """Dictations per day for the last fortnight.

    One series, so one hue and no legend -- the section title names it. Built
    from boxes rather than a drawing area for the same reason as the level
    meter: cairo from Python needs a package that may not be installed.
    """

    HEIGHT = 88
    BAR_WIDTH = 18
    STUB = 3  # what a zero-count day still shows, so the axis stays readable

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.columns = Gtk.Box(spacing=6, valign=Gtk.Align.END, homogeneous=True)
        self.columns.set_size_request(-1, self.HEIGHT)
        self.append(self.columns)
        self.labels = Gtk.Box(spacing=6, homogeneous=True)
        self.append(self.labels)
        self._bars = []

    def set_days(self, activity):
        for box in (self.columns, self.labels):
            child = box.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                box.remove(child)
                child = nxt

        peak = max((count for _day, count in activity), default=0)
        today = datetime.now().date()
        # Only the busiest day is labelled. A number over every bar is noise,
        # and the rest are one hover away.
        best = max(range(len(activity)), key=lambda i: activity[i][1]) \
            if peak else -1

        for index, (day, count) in enumerate(activity):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                             valign=Gtk.Align.END)
            tag = Gtk.Label(label=str(count) if index == best else "")
            tag.add_css_class("caption")
            tag.add_css_class("dim-label")
            column.append(tag)

            bar = Gtk.Box(valign=Gtk.Align.END, halign=Gtk.Align.CENTER)
            bar.add_css_class("chart-bar")
            if count == 0:
                bar.add_css_class("empty")
            elif day == today:
                bar.add_css_class("today")
            height = self.STUB if not peak or count == 0 else max(
                self.STUB, round(count / peak * (self.HEIGHT - 22)))
            bar.set_size_request(self.BAR_WIDTH, height)
            bar.set_tooltip_text(
                f"{day.strftime('%a %-d %b')} \u00b7 "
                f"{count} dictation{'' if count == 1 else 's'}")
            column.append(bar)
            self.columns.append(column)

            initial = Gtk.Label(label=day.strftime("%a")[0])
            initial.add_css_class("caption")
            initial.add_css_class("dim-label")
            self.labels.append(initial)


class HomePage(Adw.Bin):
    """Where the app opens: who you are, something worth reading, and what
    you have actually been doing with it."""

    def __init__(self, window):
        super().__init__()
        self._window = window
        self._quote = None

        page = Adw.PreferencesPage()

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                         margin_top=8, margin_bottom=4)
        self.greeting = Gtk.Label(xalign=0)
        self.greeting.add_css_class("greeting")
        self.greeting.set_wrap(True)
        header.append(self.greeting)
        self.subgreeting = Gtk.Label(xalign=0)
        self.subgreeting.add_css_class("dim-label")
        self.subgreeting.set_wrap(True)
        header.append(self.subgreeting)
        header_group = Adw.PreferencesGroup()
        header_group.add(header)
        page.add(header_group)

        quote_group = Adw.PreferencesGroup()
        quote_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        quote_box.add_css_class("card")
        quote_box.add_css_class("quote-card")
        text_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                              hexpand=True)
        self.quote_label = Gtk.Label(xalign=0, wrap=True)
        self.quote_label.add_css_class("quote-text")
        self.quote_author = Gtk.Label(xalign=0)
        self.quote_author.add_css_class("dim-label")
        self.quote_author.add_css_class("caption")
        text_column.append(self.quote_label)
        text_column.append(self.quote_author)
        quote_box.append(text_column)
        quote_box.append(flat_button("view-refresh-symbolic", "Another quote",
                                     lambda *_a: self.shuffle_quote()))
        quote_group.add(quote_box)
        page.add(quote_group)

        stats_group = Adw.PreferencesGroup(title="At a glance")
        self.tile_total = StatTile("Dictations")
        self.tile_words = StatTile("Words dictated")
        self.tile_saved = StatTile(
            "Time saved",
            f"Estimated: what these words would have taken to type at "
            f"{TYPING_WPM} wpm, less the time the microphone was actually "
            f"open. Older entries have no recorded duration and are estimated "
            f"at {SPEAKING_WPM} wpm of speech.")
        self.tile_streak = StatTile("Day streak")
        self.tile_spoken = StatTile(
            "Time spoken",
            f"How long the microphone has been open across every dictation. "
            f"Older entries have no recorded duration and are estimated at "
            f"{SPEAKING_WPM} wpm of speech.")
        self.tile_spend = StatTile("OpenAI spend")
        # Six across is too narrow to read on a half-width window, so they wrap
        # into two rows of three rather than one row that squeezes.
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for group in ((self.tile_total, self.tile_words, self.tile_saved),
                      (self.tile_streak, self.tile_spoken, self.tile_spend)):
            row = Gtk.Box(spacing=12, homogeneous=True)
            for tile in group:
                row.append(tile)
            rows.append(row)
        stats_group.add(rows)
        page.add(stats_group)

        chart_group = Adw.PreferencesGroup(
            title="Last 14 days",
            description="Dictations per day.")
        self.chart = ActivityChart()
        chart_group.add(self.chart)
        page.add(chart_group)

        # Its own group, because Adw.PreferencesGroup lays its rows out after
        # any plain widgets it holds regardless of the order they were added --
        # in one group the list would sit above the chart it belongs to.
        #
        # Only the busiest bar is labelled, so without this the other thirteen
        # values would be reachable by hover alone: no good on a keyboard, and
        # no good to a screen reader.
        table_group = Adw.PreferencesGroup()
        self.table = Adw.ExpanderRow(
            title="Show as a list",
            subtitle="Every day's count, without hovering")
        self._table_rows = []
        table_group.add(self.table)
        page.add(table_group)

        self.apps_group = Adw.PreferencesGroup(
            title="Where you dictate",
            description="Recorded from the focused window at the moment the "
                        "text was typed.")
        page.add(self.apps_group)
        self._app_rows = []

        self.set_child(page)
        self.shuffle_quote()
        self.set_entries([])

    # -- content

    def refresh_greeting(self, stats):
        self.greeting.set_label(
            greeting_for(datetime.now().hour, greeting_name()))
        if not stats["total"]:
            self.subgreeting.set_label(
                "Nothing dictated yet. Hold the hotkey and say something.")
        elif stats["today"]:
            self.subgreeting.set_label(
                f"{stats['today']} dictation"
                f"{'' if stats['today'] == 1 else 's'} today"
                + (f" \u00b7 {stats['streak']} day streak"
                   if stats["streak"] > 1 else ""))
        else:
            self.subgreeting.set_label("Nothing yet today.")

    def shuffle_quote(self):
        choices = [q for q in QUOTES if q != self._quote] or QUOTES
        self._quote = random.choice(choices)
        text, author = self._quote
        self.quote_label.set_label(f"\u201c{text}\u201d")
        self.quote_author.set_label(author)

    def _fill_table(self, activity):
        for row in self._table_rows:
            self.table.remove(row)
        self._table_rows = []
        for day, count in reversed(activity):
            row = Adw.ActionRow(title=day.strftime("%A %-d %B"))
            value = Gtk.Label(label=str(count), valign=Gtk.Align.CENTER)
            value.add_css_class("dim-label" if count == 0 else "heading")
            row.add_suffix(value)
            self.table.add_row(row)
            self._table_rows.append(row)

    def set_entries(self, entries):
        stats = compute_stats(entries)
        self.refresh_greeting(stats)

        self.tile_total.set_value(human_count(stats["total"]))
        self.tile_words.set_value(human_count(stats["words"]))
        self.tile_saved.set_value(human_duration(stats["saved_secs"]))
        self.tile_streak.set_value(str(stats["streak"]))
        self.tile_spoken.set_value(human_duration(stats["spoken_secs"]))
        self.tile_spend.set_value(human_money(stats["spend_total"]))
        self.tile_spend.set_tooltip_text(describe_spend(stats))
        self.chart.set_days(stats["activity"])
        self._fill_table(stats["activity"])

        for row in self._app_rows:
            self.apps_group.remove(row)
        self._app_rows = []

        top = stats["top_apps"][:5]
        if not top:
            # Every entry predates the daemon recording this, which is a fact
            # about the data rather than a fact about the user.
            row = Adw.ActionRow(
                title="Not recorded yet",
                subtitle="Transcripts from now on will remember which window "
                         "they were typed into.")
            self.apps_group.add(row)
            self._app_rows.append(row)
            return

        for name, count in top:
            share = count / stats["total"] if stats["total"] else 0
            row = Adw.ActionRow(
                title=esc(name),
                subtitle=f"{count} dictation{'' if count == 1 else 's'}"
                         f" \u00b7 {round(share * 100)}%")
            self.apps_group.add(row)
            self._app_rows.append(row)


# -------------------------------------------------------------------- pages


class DictatePage(Adw.Bin):
    """What is happening right now, and where the next transcript will land."""

    def __init__(self, window):
        super().__init__()
        self._window = window
        self._last_entry = None

        page = Adw.PreferencesPage()

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                         margin_top=18, margin_bottom=6,
                         halign=Gtk.Align.CENTER)

        self.orb = Gtk.Button(halign=Gtk.Align.CENTER)
        self.orb.add_css_class("circular")
        self.orb.add_css_class("suggested-action")
        self.orb.add_css_class("record-orb")
        self.orb.set_child(Gtk.Image(icon_name="audio-input-microphone-symbolic",
                                     pixel_size=36))
        self.orb.connect("clicked", lambda *_a: fire_and_forget("toggle"))
        column.append(self.orb)

        # The same three-way vocabulary as the pill — nothing, bars, spinner —
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

        self.hint_label = Gtk.Label(label="")
        self.hint_label.add_css_class("dim-label")
        self.hint_label.set_wrap(True)
        self.hint_label.set_justify(Gtk.Justification.CENTER)
        column.append(self.hint_label)

        orb_group = Adw.PreferencesGroup()
        orb_group.add(column)
        page.add(orb_group)

        # baatsun_context decides whether what you are about to say gets
        # cleaned up or typed verbatim, and without this that decision is
        # invisible until you read the result and notice it was rewritten.
        self.focus_group = Adw.PreferencesGroup(
            title="Focused window",
            description="Where the next transcript will be typed, and how it "
                        "will be treated when it gets there.",
        )
        self.focus_row = Adw.ActionRow(title="Nothing focused")
        self.focus_row.set_subtitle_lines(2)
        self.badge = Gtk.Label(valign=Gtk.Align.CENTER, visible=False)
        self.badge.add_css_class("context-badge")
        self.focus_row.add_suffix(self.badge)
        self.focus_group.add(self.focus_row)
        page.add(self.focus_group)

        self.last_group = Adw.PreferencesGroup(title="Last transcript")
        self.last_row = Adw.ActionRow(title="Nothing yet")
        # Capped for the same reason as the History rows: one long dictation
        # would otherwise push the rest of the page off screen.
        self.last_row.set_title_lines(4)
        self.last_copy = flat_button("edit-copy-symbolic", "Copy",
                                     self._on_copy)
        self.last_retype = flat_button(
            "edit-redo-symbolic", "Type again into focused window",
            self._on_retype)
        self.last_row.add_suffix(self.last_copy)
        self.last_row.add_suffix(self.last_retype)
        self.last_group.add(self.last_row)
        page.add(self.last_group)

        self.set_child(page)
        self.set_last(None)
        self.set_focus_window(None)

    # -- content

    def set_last(self, entry):
        self._last_entry = entry
        has = entry is not None
        self.last_copy.set_visible(has)
        self.last_retype.set_visible(has)
        if not has:
            self.last_row.set_title("Nothing yet")
            self.last_row.set_subtitle(
                "Your most recent transcript will appear here.")
            return
        self.last_row.set_title(esc(entry.get("text", "")))
        self.last_row.set_subtitle(describe_entry(entry))

    def set_focus_window(self, event):
        """Reflect a focus event from the daemon, or say we can't see one."""
        if not event or not (event.get("app") or event.get("title")):
            self.focus_row.set_title("Nothing focused")
            # Only the GNOME extension reports focus. Everywhere else this
            # stays empty, and baatsun_context reads that as "developer",
            # which is the safe direction — so say so rather than look broken.
            self.focus_row.set_subtitle(
                "No window reported. Transcripts are typed exactly as "
                "transcribed.")
            self.badge.set_visible(False)
            return

        context = event.get("context") or "developer"
        self.focus_row.set_title(
            esc(pretty_app(event.get("app")) or "Unknown window"))
        self.focus_row.set_subtitle(
            esc(event.get("title") or "") + "\n"
            + ("Cleanup will run on this one" if event.get("cleanup")
               else "Typed exactly as transcribed"))
        self.badge.set_label("Prose" if context == "prose" else "Developer")
        self.badge.remove_css_class("prose")
        self.badge.remove_css_class("developer")
        self.badge.add_css_class(context)
        self.badge.set_visible(True)

    # -- state

    def apply_state(self, state, connected):
        self.meter.set_active(state == "listening")
        self.activity.set_visible_child_name(
            state if state in ("listening", "transcribing") else "idle")

        if not connected:
            self.state_label.set_label("Daemon not running")
            self.hint_label.set_label(
                "Start it with: systemctl --user start baatsun.service")
        else:
            self.state_label.set_label({
                "listening": "Listening…",
                "transcribing": "Transcribing…",
            }.get(state, "Ready"))
            hotkey = baatsun_config.load_config().get("hotkey", "ctrl+super")
            pretty = "+".join(part.capitalize() for part in hotkey.split("+"))
            self.hint_label.set_label({
                "listening": f"Press again, or release {pretty}, to stop",
                "transcribing": "Running whisper locally",
            }.get(state, f"Hold {pretty} and speak"))

        recording = state == "listening"
        self.orb.set_sensitive(connected and state != "transcribing")
        self.orb.set_tooltip_text(
            "Stop recording" if recording else "Start dictating")
        if recording:
            self.orb.add_css_class("recording")
        else:
            self.orb.remove_css_class("recording")
        self.orb.get_child().set_from_icon_name(
            "media-playback-stop-symbolic" if recording
            else "audio-input-microphone-symbolic")

    # -- actions

    def _on_copy(self, *_args):
        if self._last_entry:
            self.get_clipboard().set(self._last_entry.get("text", ""))
            self._window.toast("Copied")

    def _on_retype(self, *_args):
        if self._last_entry:
            fire_and_forget(f"retype {self._last_entry['id']}")
            self._window.toast("Typed again")


def describe_entry(entry):
    """The line under a transcript: when, where, and how it was treated."""
    marks = [format_clock(entry.get("ts"))]
    if entry.get("app"):
        marks.append(esc(pretty_app(entry["app"])))
    marks.append("cleaned up" if entry.get("raw") else "verbatim")
    return " · ".join(m for m in marks if m)


class HistoryPage(Adw.Bin):
    FILTERS = ["Everything", "Cleaned up", "Verbatim"]

    def __init__(self, window):
        super().__init__()
        self._window = window
        self._entries = []
        self._query = ""
        self._filter = 0
        self._revealed = set()
        self._expanded = set()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

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
        box.append(controls)

        self.scroller = Gtk.ScrolledWindow(vexpand=True)
        self.empty = Adw.StatusPage(
            title="No transcripts yet",
            description="Hold the hotkey and speak, or press record on the "
                        "Dictate page.",
            icon_name="audio-input-microphone-symbolic",
        )
        self.no_match = Adw.StatusPage(
            title="Nothing matches",
            description="No transcript contains that.",
            icon_name="system-search-symbolic",
        )

        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self.scroller, "list")
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(self.no_match, "no-match")
        box.append(self.stack)
        self.set_child(box)

        self.rebuild()

    @property
    def entries(self):
        return self._entries

    def set_entries(self, entries):
        self._entries = list(entries)
        self.rebuild()

    def add_entry(self, entry):
        self._entries.append(entry)
        self.rebuild()

    def remove_entry(self, entry_id):
        self._entries = [e for e in self._entries if e.get("id") != entry_id]
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
        return not self._query or self._query in entry.get("text", "").lower()

    def rebuild(self):
        if not self._entries:
            self.stack.set_visible_child_name("empty")
            self.scroller.set_child(None)
            return

        shown = sorted((e for e in self._entries if self._matches(e)),
                       key=lambda e: e.get("ts") or 0, reverse=True)
        if not shown:
            self.stack.set_visible_child_name("no-match")
            self.scroller.set_child(None)
            return

        # Rebuilt wholesale rather than diffed. History is capped at 500
        # entries by the daemon, and at that size this is imperceptible; a
        # Gtk.ListView with a model is the answer if the cap ever lifts.
        page = Adw.PreferencesPage()
        group = None
        heading = None
        for entry in shown:
            if day_heading(entry.get("ts")) != heading:
                heading = day_heading(entry.get("ts"))
                group = Adw.PreferencesGroup(title=heading)
                page.add(group)
            group.add(self._row(entry))

        self.scroller.set_child(page)
        self.stack.set_visible_child_name("list")

    # A fifteen-minute dictation is one entry and thousands of words. Left
    # uncapped it becomes a single screen-high row that pushes every other
    # transcript — and its own buttons — out of view.
    COLLAPSED_LINES = 4

    def _row(self, entry):
        revealed = entry.get("id") in self._revealed
        expanded = entry.get("id") in self._expanded

        row = Adw.ActionRow()
        row.set_title_lines(0 if expanded else self.COLLAPSED_LINES)
        row.set_subtitle_lines(1)
        row.add_css_class("transcript-text")
        row.set_title(esc(entry["raw"] if revealed else entry.get("text", "")))
        row.set_subtitle("before cleanup" if revealed else describe_entry(entry))
        row.set_activatable(True)
        row.connect("activated", lambda *_a, e=entry: self._toggle_expanded(e))

        # Top-aligned as one box rather than four centred suffixes: on a tall
        # row, centred buttons float halfway down a wall of text.
        actions = Gtk.Box(spacing=0, valign=Gtk.Align.START,
                          margin_top=4, margin_bottom=4)
        # Offered only where cleanup changed something, so the button's very
        # presence is the signal that this one was rewritten.
        if entry.get("raw"):
            actions.append(flat_button(
                "view-conceal-symbolic" if revealed else "view-reveal-symbolic",
                "Show what you actually said, before cleanup",
                lambda *_a, e=entry: self._toggle_raw(e)))
        actions.append(flat_button(
            "edit-copy-symbolic", "Copy",
            lambda *_a, e=entry, r=revealed: self._copy(e, r)))
        actions.append(flat_button(
            "edit-redo-symbolic", "Type again into focused window",
            lambda *_a, e=entry: self._retype(e)))
        actions.append(flat_button(
            "user-trash-symbolic", "Delete",
            lambda *_a, e=entry: self._delete(e)))
        row.add_suffix(actions)
        return row

    def _toggle_expanded(self, entry):
        self._expanded ^= {entry["id"]}
        self.rebuild()

    def _toggle_raw(self, entry):
        self._revealed ^= {entry["id"]}
        self.rebuild()

    def _copy(self, entry, revealed):
        """Copy whichever version is on screen, not always the cleaned one."""
        self.get_clipboard().set(
            entry["raw"] if revealed else entry.get("text", ""))
        self._window.toast("Copied")

    def _retype(self, entry):
        fire_and_forget(f"retype {entry['id']}")
        self._window.toast("Typed again")

    def _delete(self, entry):
        fire_and_forget(f"delete {entry['id']}")
        self.remove_entry(entry["id"])

    def clear_all(self):
        fire_and_forget("clear")
        self.set_entries([])
        self._window.home.set_entries([])
        self._window.toast("History cleared")


class WordsPage(Adw.Bin):
    """The vocabulary, one term per row instead of one comma-separated field.

    Saved the moment it changes and with no daemon restart, because the
    transcription path re-reads config on every dictation — the next thing you
    say already knows about it.
    """

    def __init__(self, window):
        super().__init__()
        self._window = window
        self.reload()

    def reload(self):
        self._terms = split_vocabulary(
            baatsun_config.load_config().get("vocabulary"))
        self._render()

    def _render(self):
        page = Adw.PreferencesPage()

        add_group = Adw.PreferencesGroup(
            title="Names to get right",
            description="Your name, your products, the tools you talk about — "
                        "anything the transcriber mishears. These are fed to "
                        "whisper as it decodes, so they work even with cleanup "
                        "switched off, and they apply to your next dictation "
                        "without restarting anything.",
        )
        self.entry = Adw.EntryRow(title="Add a name or term")
        add = flat_button("list-add-symbolic", "Add", self._on_add)
        self.entry.add_suffix(add)
        self.entry.connect("entry-activated", self._on_add)
        add_group.add(self.entry)
        page.add(add_group)

        if self._terms:
            list_group = Adw.PreferencesGroup(
                title=f"{len(self._terms)} term"
                      f"{'' if len(self._terms) == 1 else 's'}",
                description="Kept short on purpose: a long list dilutes the "
                            "hint and starts pulling words into transcripts "
                            "that were never said.",
            )
            for term in self._terms:
                row = Adw.ActionRow(title=esc(term))
                row.add_suffix(flat_button(
                    "user-trash-symbolic", "Remove",
                    lambda *_a, t=term: self._remove(t)))
                list_group.add(row)
            page.add(list_group)
        else:
            empty = Adw.PreferencesGroup()
            empty.add(Adw.ActionRow(
                title="No terms yet",
                subtitle="Add the words whisper keeps getting wrong."))
            page.add(empty)

        self.set_child(page)

    def _save(self):
        cfg = baatsun_config.load_config()
        cfg["vocabulary"] = ", ".join(self._terms)
        baatsun_config.save_config(cfg)

    def _on_add(self, *_args):
        term = self.entry.get_text().strip().strip(",")
        if not term:
            return
        if term in self._terms:
            self._window.toast(f"“{term}” is already there")
            return
        self._terms.append(term)
        self._save()
        self._render()
        self._window.toast(f"Added “{term}”")

    def _remove(self, term):
        self._terms.remove(term)
        self._save()
        self._render()
        self._window.toast(f"Removed “{term}”")


class SettingsPage(Adw.Bin):
    """Everything in config.json worth exposing, as a page rather than a modal."""

    HOTKEY_LABELS = {
        "ctrl+super": "Ctrl + Super",
        "ctrl+alt": "Ctrl + Alt",
        "alt+super": "Alt + Super",
        "ctrl+shift": "Ctrl + Shift",
    }

    def __init__(self, window):
        super().__init__()
        self._window = window
        self._build()

    def _build(self):
        cfg = baatsun_config.load_config()
        # Remembered so Apply can tell whether the daemon actually has to be
        # restarted. Only the hotkey and activation mode are read once at
        # startup; everything else is re-read per dictation.
        self._restart_keys = (cfg.get("hotkey"), cfg.get("activation"),
                              cfg.get("model_override") or "")

        page = Adw.PreferencesPage()
        page.add(self._profile_group(cfg))

        recording = Adw.PreferencesGroup(title="Recording")
        self.hotkey_row = Adw.ComboRow(title="Hotkey")
        self.hotkey_row.set_model(Gtk.StringList.new([
            self.HOTKEY_LABELS.get(choice, choice)
            for choice in baatsun_config.HOTKEY_CHOICES]))
        self.hotkey_row.set_selected(baatsun_config.safe_index(
            baatsun_config.HOTKEY_CHOICES, cfg.get("hotkey")))
        recording.add(self.hotkey_row)

        self.activation_row = Adw.ComboRow(
            title="Hotkey behaviour",
            subtitle="Tap-or-hold keeps push-to-talk and frees your hands too",
        )
        self.activation_row.set_model(Gtk.StringList.new([
            "Hold to talk",
            "Press to start and stop",
            "Tap or hold",
        ]))
        self.activation_row.set_selected(baatsun_config.safe_index(
            baatsun_config.ACTIVATION_CHOICES, cfg.get("activation")))
        recording.add(self.activation_row)
        page.add(recording)

        page.add(self._cleanup_group(cfg))
        page.add(self._transcription_group(cfg))
        page.add(self._daemon_group())
        self.set_child(page)

    def _profile_group(self, cfg):
        """First on the page on purpose. The greeting is the first thing the
        app says, and being greeted by your machine's login name is the first
        thing anyone wants to change."""
        group = Adw.PreferencesGroup(
            title="You",
            description="Only used to greet you on Home. Never sent anywhere.")
        self.name_row = Adw.EntryRow(title="Your name")
        self.name_row.set_text(cfg.get("display_name") or "")
        # The placeholder shows what the greeting falls back to, so leaving it
        # empty is an informed choice rather than a blank box.
        fallback = greeting_name({"display_name": ""})
        self.name_row.set_tooltip_text(
            f"Leave empty to use what the system knows"
            + (f" ({fallback})" if fallback else ""))
        group.add(self.name_row)
        return group

    def _cleanup_group(self, cfg):
        group = Adw.PreferencesGroup(
            title="Cleanup with OpenAI",
            description="Tidies punctuation, capitalisation and filler words "
                        "before typing. Only the transcribed text is sent — "
                        "your audio never leaves this machine. Costs a "
                        "fraction of a cent per dictation.",
        )

        self.cleanup_row = Adw.SwitchRow(
            title="Clean up transcripts",
            subtitle="Off until an API key is saved below")
        self.cleanup_row.set_active(bool(cfg.get("cleanup_enabled")))
        group.add(self.cleanup_row)

        self.scope_row = Adw.ComboRow(
            title="Apply to",
            subtitle="Prose only: terminals and editors stay verbatim")
        self.scope_row.set_model(Gtk.StringList.new(
            ["Prose windows only", "Everything I dictate"]))
        self.scope_row.set_selected(baatsun_config.safe_index(
            baatsun_config.CLEANUP_SCOPE_CHOICES, cfg.get("cleanup_scope")))
        group.add(self.scope_row)

        self.strength_row = Adw.ComboRow(
            title="Correction level",
            subtitle="Natural also fixes phrasing a native speaker wouldn't use")
        self.strength_row.set_model(Gtk.StringList.new(
            ["Grammar only", "Natural English"]))
        self.strength_row.set_selected(baatsun_config.safe_index(
            baatsun_config.CLEANUP_STRENGTH_CHOICES,
            cfg.get("cleanup_strength")))
        group.add(self.strength_row)

        self.breaks_row = Adw.SwitchRow(
            title="Break long text into paragraphs",
            subtitle="Never in chat apps, where Enter would send the message")
        self.breaks_row.set_active(bool(cfg.get("line_breaks", True)))
        group.add(self.breaks_row)

        self.hinglish_row = Adw.SwitchRow(
            title="I mix Hindi words into my speech",
            subtitle="Renders garbled Hindi (“K”, “Hummer”) as English")
        self.hinglish_row.set_active(bool(cfg.get("hinglish")))
        group.add(self.hinglish_row)

        # PasswordEntryRow so the key isn't left on screen; it is stored 0600
        # in its own file, never in config.json.
        self.key_row = Adw.PasswordEntryRow(title="OpenAI API key")
        self.key_row.set_text(baatsun_config.load_api_key())
        group.add(self.key_row)

        self.key_status = Adw.ActionRow(
            title="Test key",
            subtitle="Sends one short request to check the key")
        test_button = Gtk.Button(label="Test", valign=Gtk.Align.CENTER)
        test_button.connect("clicked", self._on_test_key)
        self.key_status.add_suffix(test_button)
        group.add(self.key_status)
        return group

    def _transcription_group(self, cfg):
        group = Adw.PreferencesGroup(
            title="Transcription",
            description="Runs locally on CPU. Bigger is not better here — "
                        "small.en was chosen by measurement as the smallest "
                        "model that gets every word right.",
        )
        self.model_row = Adw.ComboRow(
            title="Model", subtitle="Downloads on first use")
        self.model_row.set_model(Gtk.StringList.new(
            [f"{baatsun_config.DEFAULT_MODEL} (recommended)"]
            + baatsun_config.MODEL_CHOICES))
        override = cfg.get("model_override") or ""
        self.model_row.set_selected(
            baatsun_config.MODEL_CHOICES.index(override) + 1
            if override in baatsun_config.MODEL_CHOICES else 0)
        group.add(self.model_row)

        self.compute_row = Adw.ComboRow(title="Compute type")
        self.compute_row.set_model(
            Gtk.StringList.new(baatsun_config.COMPUTE_TYPE_CHOICES))
        self.compute_row.set_selected(baatsun_config.safe_index(
            baatsun_config.COMPUTE_TYPE_CHOICES, cfg.get("compute_type")))
        group.add(self.compute_row)
        return group

    def _daemon_group(self):
        group = Adw.PreferencesGroup(title="Daemon")
        self.daemon_row = Adw.ActionRow(title="baatsun.service")
        restart = Gtk.Button(label="Restart", valign=Gtk.Align.CENTER)
        restart.connect("clicked", lambda *_a: self._restart("Restarting…"))
        self.daemon_row.add_suffix(restart)
        group.add(self.daemon_row)
        return group

    def set_connected(self, connected):
        if hasattr(self, "daemon_row"):
            self.daemon_row.set_subtitle(
                "Running · listening for the hotkey" if connected
                else "Not reachable on the socket")

    def _on_test_key(self, button):
        key = self.key_row.get_text().strip()
        model = (baatsun_config.load_config().get("cleanup_model")
                 or baatsun_config.DEFAULT_CLEANUP_MODEL)
        button.set_sensitive(False)
        self.key_status.set_subtitle("Checking…")

        def work():
            import baatsun_cleanup
            ok, message = baatsun_cleanup.verify_key(key, model)
            # Back to the main loop before touching any widget: GTK is not
            # thread-safe and this runs on a worker.
            GLib.idle_add(finish, ok, message)

        def finish(ok, message):
            self.key_status.set_subtitle(("✓ " if ok else "✗ ") + message)
            button.set_sensitive(True)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    def apply(self):
        # Merge into the existing config rather than rebuilding it: keys this
        # page doesn't show must survive a save.
        cfg = baatsun_config.load_config()
        # Kept to tell a name-only save apart from one that changes how
        # dictation behaves -- they deserve different confirmations.
        before = dict(cfg)
        cfg["display_name"] = self.name_row.get_text().strip()
        cfg["hotkey"] = baatsun_config.HOTKEY_CHOICES[
            self.hotkey_row.get_selected()]
        cfg["activation"] = baatsun_config.ACTIVATION_CHOICES[
            self.activation_row.get_selected()]
        cfg["cleanup_enabled"] = self.cleanup_row.get_active()
        cfg["cleanup_scope"] = baatsun_config.CLEANUP_SCOPE_CHOICES[
            self.scope_row.get_selected()]
        cfg["cleanup_strength"] = baatsun_config.CLEANUP_STRENGTH_CHOICES[
            self.strength_row.get_selected()]
        cfg["line_breaks"] = self.breaks_row.get_active()
        cfg["hinglish"] = self.hinglish_row.get_active()

        selected_model = self.model_row.get_selected()
        cfg["model_override"] = ("" if selected_model == 0
                                 else baatsun_config.MODEL_CHOICES[
                                     selected_model - 1])
        cfg["compute_type"] = baatsun_config.COMPUTE_TYPE_CHOICES[
            self.compute_row.get_selected()]

        baatsun_config.save_config(cfg)
        baatsun_config.save_api_key(self.key_row.get_text())

        # Only the hotkey, the activation mode and the model are read once at
        # daemon startup; cleanup settings and the vocabulary are re-read on
        # every dictation. Restarting for a cleanup toggle would cost a model
        # reload — several seconds of not being able to dictate — for nothing.
        current = (cfg["hotkey"], cfg["activation"], cfg["model_override"])
        needs_restart = current != self._restart_keys
        self._restart_keys = current

        # Home is a different page and was built before this save; the name is
        # only read when it renders.
        self._window.home.set_entries(self._window.history.entries)
        dictation_changed = (
            {k: v for k, v in cfg.items() if k != "display_name"}
            != {k: v for k, v in before.items() if k != "display_name"})
        if needs_restart:
            self._restart("Saved — restarting daemon")
        elif dictation_changed:
            self._window.toast("Saved — in effect from your next dictation")
        else:
            # A name-only save changes nothing about dictation, and saying it
            # takes effect from the next one would be nonsense.
            self._window.toast("Saved")

    def _restart(self, message):
        self._window.toast(message)
        threading.Thread(target=self._systemctl, daemon=True).start()

    @staticmethod
    def _systemctl():
        subprocess.run(["systemctl", "--user", "restart", "baatsun.service"],
                       check=False)


# ------------------------------------------------------------------- window


class BaatsunWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Baatsun")
        self.set_default_size(940, 680)
        # Adw.Breakpoint refuses to work without one.
        self.set_size_request(360, 420)

        self.state = "idle"
        self.connected = False

        self.home = HomePage(self)
        self.dictate = DictatePage(self)
        self.history = HistoryPage(self)
        self.words = WordsPage(self)
        self.settings = SettingsPage(self)
        self._pages = {"home": self.home, "dictate": self.dictate,
                       "history": self.history, "words": self.words,
                       "settings": self.settings}

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
        narrow = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 680px"))
        narrow.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(narrow)

        self.show_page("home")
        self.set_connection_status(False)
        self.connect("close-request", self.on_close_request)

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
        # Selecting the first row is left to __init__, after the split view
        # exists: row-selected fires synchronously and _on_nav reaches for it.

        footer = Gtk.Box(spacing=8, margin_start=14, margin_end=14,
                         margin_top=8, margin_bottom=12)
        self.daemon_dot = Gtk.Label(label="●", valign=Gtk.Align.CENTER)
        self.daemon_dot.add_css_class("daemon-dot")
        footer.append(self.daemon_dot)
        self.daemon_caption = Gtk.Label(label="Connecting…", xalign=0)
        self.daemon_caption.add_css_class("caption")
        self.daemon_caption.add_css_class("dim-label")
        footer.append(self.daemon_caption)

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
        and Settings can own an Apply button without either faking it."""
        view = Adw.ToolbarView(content=page)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=title))

        if title == "History":
            menu = Gio.Menu()
            menu.append("Clear history", "win.clear-history")
            header.pack_end(Gtk.MenuButton(icon_name="view-more-symbolic",
                                           menu_model=menu,
                                           tooltip_text="More"))
        if title == "Settings":
            apply_button = Gtk.Button(label="Apply")
            apply_button.add_css_class("suggested-action")
            apply_button.connect("clicked", lambda *_a: self.settings.apply())
            header.pack_end(apply_button)

        view.add_top_bar(header)
        return view

    def _on_nav(self, _listbox, row):
        if row is None:
            return
        name = PAGES[row.get_index()][0]
        self.stack.set_visible_child_name(name)
        # Reload from disk on entry: Settings may have rewritten the
        # vocabulary, and the daemon may have been reconfigured elsewhere.
        if name == "words":
            self.words.reload()
        if name == "home":
            # The greeting depends on the time of day, and this window is
            # normally left open for hours.
            self.home.set_entries(self.history.entries)
        if self.split.get_collapsed():
            self.split.set_show_content(True)

    def show_page(self, name):
        index = [p[0] for p in PAGES].index(name)
        self.sidebar_list.select_row(self.sidebar_list.get_row_at_index(index))

    def toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=message, timeout=2))

    def on_close_request(self, *_args):
        # Hide instead of quitting, so the app keeps running for the tray
        # icon to re-present later without restarting the process.
        self.set_visible(False)
        return True

    # -- daemon-driven

    def set_connection_status(self, connected):
        self.connected = connected
        self.daemon_caption.set_label(
            "Daemon running" if connected else "Daemon not running")
        self.daemon_dot.remove_css_class("up")
        self.daemon_dot.remove_css_class("down")
        self.daemon_dot.add_css_class("up" if connected else "down")
        self.settings.set_connected(connected)
        if not connected:
            self.dictate.set_focus_window(None)
        self.dictate.apply_state(self.state, connected)

    def set_state(self, state):
        self.state = state
        self.dictate.apply_state(state, self.connected)

    def set_focus_window(self, event):
        self.dictate.set_focus_window(event)

    def set_history(self, entries):
        self.history.set_entries(entries)
        self.dictate.set_last(entries[-1] if entries else None)
        self.home.set_entries(self.history.entries)

    def add_entry(self, entry):
        self.history.add_entry(entry)
        self.dictate.set_last(entry)
        self.home.set_entries(self.history.entries)

    def remove_entry(self, entry_id):
        self.history.remove_entry(entry_id)
        self.home.set_entries(self.history.entries)


class BaatsunApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None
        self._listener_started = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        if self.window is None:
            self.window = BaatsunWindow(self)
            action = Gio.SimpleAction.new("clear-history", None)
            action.connect("activate",
                           lambda *_a: self.window.history.clear_all())
            self.window.add_action(action)
        if not self._listener_started:
            self._listener_started = True
            threading.Thread(target=self._listen_loop, daemon=True).start()
        self.window.present()

    def _listen_loop(self):
        while True:
            self._connect_and_listen_once()
            time.sleep(RECONNECT_SECONDS)

    def _connect_and_listen_once(self):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(SOCKET_PATH)
                sock.sendall(b"history")
                raw = self._read_line(sock)
                entries = json.loads(raw) if raw else []
                GLib.idle_add(self.window.set_history, entries)
        except (OSError, ValueError):
            GLib.idle_add(self.window.set_connection_status, False)
            return

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(SOCKET_PATH)
                sock.sendall(b"subscribe")
                # The daemon opens the stream with the current state and the
                # focused window, so there is nothing to guess at here.
                GLib.idle_add(self.window.set_connection_status, True)
                buf = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line:
                            self._handle_event(json.loads(line))
        except (OSError, ValueError):
            pass
        finally:
            GLib.idle_add(self.window.set_connection_status, False)

    @staticmethod
    def _read_line(sock):
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        return buf.split(b"\n", 1)[0]

    def _handle_event(self, event):
        etype = event.get("type")
        if etype == "transcript":
            GLib.idle_add(self.window.add_entry, event["entry"])
        elif etype == "history_cleared":
            GLib.idle_add(self.window.set_history, [])
        elif etype == "deleted":
            GLib.idle_add(self.window.remove_entry, event["id"])
        elif etype == "state":
            GLib.idle_add(self.window.set_state, event.get("state"))
        elif etype == "focus":
            GLib.idle_add(self.window.set_focus_window, event)


def main():
    app = BaatsunApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
