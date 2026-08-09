#!/usr/bin/env python3
"""baatsun-pill: a thin always-on-top pill showing dictation state.

This is the *fallback* implementation, for compositors that speak
wlr-layer-shell (sway, Hyprland, KDE Plasma, wayfire, ...). On GNOME use the
Shell extension in gnome-extension/ instead: mutter implements neither
wlr-layer-shell nor client-side window positioning, so this file cannot work
there — there is no way for a GTK window to pin itself near the bottom centre
above other windows.

Layer-shell is what makes the window behave like chrome rather than an app:
it is anchored to the bottom of the output, sits on the overlay layer (above
fullscreen windows), reserves no space, and — the part that matters most for
baatsun — is created with keyboard mode NONE and an empty input region, so it
can never take focus or a click away from whatever the user is dictating into.

There is one dark pill and it is always the same dark pill. It never changes
colour; it only opens and closes, and everything else happens inside it:

    idle          a 6px-tall bar, empty
    listening     opens to hold a row of white level bars
    transcribing  opens to hold a white spinner
    offline       the closed bar, receded (no daemon on the socket)

Keeping one shape and one colour is deliberate. This thing lives over
arbitrary content, so it should be a single predictable object that opens and
closes rather than a patch of screen that changes colour — and a dark pill
stays legible over both a white document and a dark terminal, where a tinted
translucent bar does not.

There is no hover button here, unlike the GNOME extension; see
make_click_through() for why.
"""
import json
import math
import os
import random
import socket
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell
except ValueError:
    LayerShell = None

import cairo  # noqa: E402
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

SOCKET_PATH = f"/run/user/{os.getuid()}/baatsun.sock"

# Closed: a bar, thin enough to read as an indicator and nothing else.
REST_WIDTH = 68.0
REST_HEIGHT = 6.0
# Open: tall enough for the spinner plus 2px of breathing room top and bottom,
# and narrower, so opening reads as the bar gathering itself up rather than
# just getting fatter.
ACTIVE_WIDTH = 52.0
ACTIVE_HEIGHT = 16.0

# The window is deliberately larger than the pill so the drop shadow has room
# to bleed; everything outside the pill is transparent.
SURFACE_WIDTH = 92
SURFACE_HEIGHT = 48
# Gap from the bottom of the screen to the closed bar. The surface is taller
# than the bar and the bar is centred in it, so the anchor margin has to back
# off by half that difference or the pill floats higher than this number says.
# The pill grows about its own centre, so an open pill straddles this line.
PILL_BOTTOM_MARGIN = 64
BOTTOM_MARGIN = round(PILL_BOTTOM_MARGIN - (SURFACE_HEIGHT - REST_HEIGHT) / 2)

# How fast the pill opens and closes, and how fast its contents fade, both as
# exponential rates rather than durations: framerate-independent, and they need
# no tweening state beyond the target itself.
MORPH_RATE = 14.0
FADE_RATE = 18.0

# The level bars: 5*3 + 4*4 = 31 wide, centred in the open pill.
BAR_COUNT = 5
BAR_WIDTH = 3.0
BAR_SPACING = 4.0
BAR_MAX_HEIGHT = 11.0
BAR_MIN_HEIGHT = 3.0
BAR_RADIUS = 1.5

# Every bar walks to a fresh random height on its own clock, which is what
# makes the row read as a level meter rather than a synchronised pattern.
# Nothing here is driven by the actual microphone — the daemon records straight
# to a file and broadcasts no levels — so this says "capturing", not
# "capturing *this* loudly".
HOP_MIN_INTERVAL = 0.11
HOP_MAX_INTERVAL = 0.23
# How much shorter the outermost bars run than the centre ones, which is what
# gives the row a crest instead of a flat wall of noise.
HOP_EDGE_FALLOFF = 0.45
HOP_MIN_PEAK = 0.15  # as a fraction of the bar's own allowance
# How fast a bar closes on its target height. High enough that a hop lands
# before the next one is rolled, low enough that the row still looks damped.
BAR_APPROACH = 20.0

SPINNER_SIZE = 12.0
SPINNER_PERIOD = 0.9  # seconds per turn
SPINNER_WIDTH = 1.8   # stroke
SPINNER_ARC = 1.9     # radians of bright head, of a 2*pi ring

RECONNECT_INTERVAL = 3

PILL_FILL = (0.11, 0.11, 0.12, 0.94)
PILL_BORDER = (1.0, 1.0, 1.0, 0.16)
PILL_FILL_OFFLINE = (0.11, 0.11, 0.12, 0.4)
PILL_BORDER_OFFLINE = (1.0, 1.0, 1.0, 0.06)

WHITE = (1.0, 1.0, 1.0)
SPINNER_TRACK_ALPHA = 0.22
SPINNER_HEAD_ALPHA = 0.95

# St renders the extension's `box-shadow` properly; cairo has no cheap blur,
# so this approximates one with concentric copies. Kept tight and pushed
# downward — spread any wider and the falloff stops reading as a shadow and
# starts reading as a halo around the pill.
SHADOW_LAYERS = 8
SHADOW_SPREAD = 6.0
SHADOW_OFFSET_Y = 2.0


def rounded_rect(cr, x, y, width, height, radius):
    """A rounded rectangle, clamping the radius to what the box can hold."""
    radius = min(radius, width / 2.0, height / 2.0)
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


class Bar:
    """One level bar: a current height, and the height it is heading for."""

    __slots__ = ("height", "target", "reroll_at")

    def __init__(self):
        self.height = BAR_MIN_HEIGHT
        self.target = BAR_MIN_HEIGHT
        self.reroll_at = 0.0


class Pill(Gtk.DrawingArea):
    """Draws the pill and runs its animation off the frame clock."""

    def __init__(self):
        super().__init__()
        self.set_draw_func(self._draw)

        self.state = "offline"
        self._phase_start = time.monotonic()
        self._bars = [Bar() for _ in range(BAR_COUNT)]

        # Smoothed geometry and content opacities, so a state change eases
        # rather than jumps.
        self._width = REST_WIDTH
        self._height = REST_HEIGHT
        self._bars_alpha = 0.0
        self._spinner_alpha = 0.0
        self._spin = 0.0
        self._last_frame = None

        self.add_tick_callback(self._tick)

    def set_state(self, state):
        if state == self.state:
            return
        self.state = state
        self._phase_start = time.monotonic()
        # Re-roll immediately rather than at each bar's own next deadline, so
        # the meter starts moving on the frame the state lands.
        for bar in self._bars:
            bar.reroll_at = 0.0

    # ------------------------------------------------------------- animation

    def _tick(self, _widget, frame_clock):
        now = frame_clock.get_frame_time() / 1_000_000.0
        dt = 0.016 if self._last_frame is None else min(now - self._last_frame, 0.1)
        self._last_frame = now

        listening = self.state == "listening"
        transcribing = self.state == "transcribing"
        open_ = listening or transcribing

        morph = 1.0 - math.exp(-dt * MORPH_RATE)
        self._width += ((ACTIVE_WIDTH if open_ else REST_WIDTH) - self._width) * morph
        self._height += (
            (ACTIVE_HEIGHT if open_ else REST_HEIGHT) - self._height) * morph

        fade = 1.0 - math.exp(-dt * FADE_RATE)
        self._bars_alpha += ((1.0 if listening else 0.0) - self._bars_alpha) * fade
        self._spinner_alpha += (
            (1.0 if transcribing else 0.0) - self._spinner_alpha) * fade
        self._spin = (self._spin + dt / SPINNER_PERIOD) % 1.0

        elapsed = time.monotonic() - self._phase_start
        approach = 1.0 - math.exp(-dt * BAR_APPROACH)
        for i, bar in enumerate(self._bars):
            bar.target = self._bar_target(i, elapsed) if listening else BAR_MIN_HEIGHT
            bar.height += (bar.target - bar.height) * approach

        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _bar_target(self, index, elapsed):
        bar = self._bars[index]
        if elapsed < bar.reroll_at:
            return bar.target

        bar.reroll_at = elapsed + random.uniform(HOP_MIN_INTERVAL, HOP_MAX_INTERVAL)
        # Bars nearer the middle are allowed to run taller, so the row has a
        # centre rather than looking like uniform static.
        centre = (BAR_COUNT - 1) / 2.0
        allowance = 1.0 - HOP_EDGE_FALLOFF * abs(index - centre) / centre
        reach = HOP_MIN_PEAK + (1.0 - HOP_MIN_PEAK) * random.random()
        return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT) * allowance * reach

    # --------------------------------------------------------------- drawing

    def _draw(self, _area, cr, width, height, *_args):
        cr.set_operator(cairo.Operator.SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.Operator.OVER)

        x = (width - self._width) / 2.0
        y = (height - self._height) / 2.0
        radius = self._height / 2.0

        offline = self.state == "offline"
        if not offline:
            self._draw_shadow(cr, x, y, radius)

        rounded_rect(cr, x, y, self._width, self._height, radius)
        cr.set_source_rgba(*(PILL_FILL_OFFLINE if offline else PILL_FILL))
        cr.fill()

        # Inset by half the line width so the 1px stroke lands inside the pill
        # rather than straddling its edge and looking like a 2px blur.
        rounded_rect(cr, x + 0.5, y + 0.5, self._width - 1, self._height - 1,
                     max(radius - 0.5, 0.0))
        cr.set_line_width(1.0)
        cr.set_source_rgba(*(PILL_BORDER_OFFLINE if offline else PILL_BORDER))
        cr.stroke()

        # Everything inside is clipped to the pill, so an opening pill reveals
        # its contents rather than letting them stick out of a 6px bar.
        cr.save()
        rounded_rect(cr, x, y, self._width, self._height, radius)
        cr.clip()
        if self._bars_alpha > 0.01:
            self._draw_bars(cr, x, y)
        if self._spinner_alpha > 0.01:
            self._draw_spinner(cr, x, y)
        cr.restore()

    def _draw_shadow(self, cr, x, y, radius):
        """Cheap blur-free drop shadow: concentric copies, each fainter."""
        for i in range(SHADOW_LAYERS, 0, -1):
            spread = SHADOW_SPREAD * i / SHADOW_LAYERS
            falloff = (1.0 - i / (SHADOW_LAYERS + 1.0)) ** 2
            rounded_rect(
                cr,
                x - spread,
                y - spread + SHADOW_OFFSET_Y,
                self._width + spread * 2,
                self._height + spread * 2,
                radius + spread,
            )
            cr.set_source_rgba(0, 0, 0, falloff * 0.07)
            cr.fill()

    def _draw_bars(self, cr, pill_x, pill_y):
        cr.set_source_rgba(*WHITE, self._bars_alpha)

        row_width = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_SPACING
        x = pill_x + (self._width - row_width) / 2.0
        middle = pill_y + self._height / 2.0

        for bar in self._bars:
            rounded_rect(cr, x, middle - bar.height / 2.0,
                         BAR_WIDTH, bar.height, BAR_RADIUS)
            cr.fill()
            x += BAR_WIDTH + BAR_SPACING

    def _draw_spinner(self, cr, pill_x, pill_y):
        """A faint full ring with a bright head riding it.

        The ring is what keeps the shape readable at 12px, where a lone arc
        reads as a smudge rather than as something turning.
        """
        cx = pill_x + self._width / 2.0
        cy = pill_y + self._height / 2.0
        radius = SPINNER_SIZE / 2.0 - SPINNER_WIDTH / 2.0
        head = -math.pi / 2 + self._spin * 2 * math.pi

        cr.save()
        cr.set_line_width(SPINNER_WIDTH)
        cr.set_line_cap(cairo.LineCap.ROUND)

        cr.set_source_rgba(*WHITE, SPINNER_TRACK_ALPHA * self._spinner_alpha)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        cr.set_source_rgba(*WHITE, SPINNER_HEAD_ALPHA * self._spinner_alpha)
        cr.arc(cx, cy, radius, head, head + SPINNER_ARC)
        cr.stroke()
        cr.restore()


class StateListener:
    """Background thread feeding daemon state events to the pill."""

    def __init__(self, pill):
        self.pill = pill

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                self._listen_once()
            except OSError:
                pass
            GLib.idle_add(self.pill.set_state, "offline")
            time.sleep(RECONNECT_INTERVAL)

    def _listen_once(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(SOCKET_PATH)
            sock.sendall(b"subscribe")
            GLib.idle_add(self.pill.set_state, "idle")
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

    def _handle_event(self, event):
        if event.get("type") != "state":
            return
        GLib.idle_add(self.pill.set_state, event.get("state", "idle"))


def make_click_through(window):
    """Empty input region: pointer events pass straight through to what's below.

    Without this, a 92x48 invisible patch near the bottom centre of every
    screen would quietly eat clicks.

    This is why the fallback has no hover start/stop button, where the GNOME
    extension does: the button needs a real input region, and giving this
    window one would mean taking clicks on every compositor it runs on, none of
    which are tested here. The hotkey remains the way to toggle.
    """
    surface = window.get_surface()
    if surface is not None:
        surface.set_input_region(cairo.Region())


def build_window(app):
    window = Gtk.ApplicationWindow(application=app)
    window.set_default_size(SURFACE_WIDTH, SURFACE_HEIGHT)
    window.set_decorated(False)
    window.set_resizable(False)
    window.add_css_class("baatsun-pill-window")

    LayerShell.init_for_window(window)
    LayerShell.set_layer(window, LayerShell.Layer.OVERLAY)
    LayerShell.set_anchor(window, LayerShell.Edge.BOTTOM, True)
    LayerShell.set_margin(window, LayerShell.Edge.BOTTOM, BOTTOM_MARGIN)
    # -1: ignore panels' and docks' exclusive zones, so the chip sits over them
    # rather than being pushed up by them.
    LayerShell.set_exclusive_zone(window, -1)
    LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.NONE)

    pill = Pill()
    pill.set_content_width(SURFACE_WIDTH)
    pill.set_content_height(SURFACE_HEIGHT)
    window.set_child(pill)

    window.connect("realize", lambda w: make_click_through(w))

    StateListener(pill).start()
    return window


def apply_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(b".baatsun-pill-window { background: transparent; }")
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def on_activate(app):
    apply_css()
    build_window(app).present()


def main():
    if LayerShell is None:
        print(
            "baatsun-pill: gtk4-layer-shell is not installed, so the pill has no\n"
            "way to anchor itself above other windows.\n"
            "\n"
            "  Debian/Ubuntu 25.04+:  sudo apt install gir1.2-gtk4layershell-1.0\n"
            "  Arch:                  sudo pacman -S gtk4-layer-shell\n"
            "  Fedora:                sudo dnf install gtk4-layer-shell\n"
            "\n"
            "On GNOME, gtk4-layer-shell will not help — mutter does not implement\n"
            "wlr-layer-shell. Install the GNOME Shell extension instead:\n"
            "  https://github.com/umarbashirr/baatsun#the-pill",
            file=sys.stderr,
        )
        return 1

    app = Gtk.Application(application_id="com.github.umarbashirr.baatsun.Pill")
    app.connect("activate", on_activate)
    return app.run([])


if __name__ == "__main__":
    sys.exit(main())
