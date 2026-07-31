#!/usr/bin/env python3
"""baatsun-pill: a thin always-on-top pill showing dictation state.

This is the *fallback* implementation, for compositors that speak
wlr-layer-shell (sway, Hyprland, KDE Plasma, wayfire, ...). On GNOME use the
Shell extension in gnome-extension/ instead: mutter implements neither
wlr-layer-shell nor client-side window positioning, so this file cannot work
there — there is no way for a GTK window to pin itself to the bottom centre
above other windows.

Layer-shell is what makes the window behave like chrome rather than an app:
it is anchored to the bottom of the output, sits on the overlay layer (above
fullscreen windows), reserves no space, and — the part that matters most for
baatsun — is created with keyboard mode NONE and an empty input region, so it
can never take focus or a click away from whatever the user is dictating into.

Visual states, matching the daemon's event stream:
    idle          dim grey pill, at rest
    listening     red, expanded, breathing
    transcribing  blue, expanded, with a segment sweeping along it
    offline       barely-there grey (no daemon on the socket)
"""
import json
import math
import os
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

# The window is deliberately larger than the pill so the glow has room to
# bleed; everything outside the pill is transparent.
SURFACE_WIDTH = 190
SURFACE_HEIGHT = 26
BOTTOM_MARGIN = 7

REST_WIDTH = 100.0
REST_HEIGHT = 5.0
ACTIVE_WIDTH = 146.0
ACTIVE_HEIGHT = 6.5

BREATH_PERIOD = 1.24  # seconds for a full expand-and-contract
BREATH_AMPLITUDE = 5.0  # pixels added to ACTIVE_WIDTH at the peak
SWEEP_PERIOD = 0.9
SWEEP_WIDTH = 34.0

GLOW_SPREAD = 8.0  # how far the halo reaches past the pill's edge

RECONNECT_INTERVAL = 3

# (r, g, b) per state, plus the alpha the pill settles at.
COLOR_IDLE = (1.0, 1.0, 1.0, 0.22)
COLOR_LISTENING = (1.0, 0.36, 0.36, 1.0)
COLOR_TRANSCRIBING = (0.36, 0.62, 1.0, 0.38)
COLOR_SWEEP = (0.36, 0.62, 1.0, 1.0)
COLOR_OFFLINE = (1.0, 1.0, 1.0, 0.08)


def rounded_rect(cr, x, y, width, height):
    """A fully-rounded (stadium) rectangle — radius is always height/2."""
    radius = height / 2.0
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, math.pi / 2)
    cr.arc(x + radius, y + radius, radius, math.pi / 2, 3 * math.pi / 2)
    cr.close_path()


class Pill(Gtk.DrawingArea):
    """Draws the pill and runs its animation off the frame clock."""

    def __init__(self):
        super().__init__()
        self.set_draw_func(self._draw)

        self.state = "offline"
        self._phase_start = time.monotonic()

        # Smoothed geometry, so a state change eases rather than jumps.
        self._width = REST_WIDTH
        self._height = REST_HEIGHT
        self._last_frame = None

        self.add_tick_callback(self._tick)

    def set_state(self, state):
        if state == self.state:
            return
        self.state = state
        self._phase_start = time.monotonic()

    def _targets(self):
        if self.state in ("listening", "transcribing"):
            return ACTIVE_WIDTH, ACTIVE_HEIGHT
        return REST_WIDTH, REST_HEIGHT

    def _tick(self, _widget, frame_clock):
        now = frame_clock.get_frame_time() / 1_000_000.0
        dt = 0.016 if self._last_frame is None else min(now - self._last_frame, 0.1)
        self._last_frame = now

        target_width, target_height = self._targets()
        if self.state == "listening":
            elapsed = time.monotonic() - self._phase_start
            # 0..1..0 over BREATH_PERIOD, so the pill swells and settles.
            breath = 0.5 - 0.5 * math.cos(2 * math.pi * elapsed / BREATH_PERIOD)
            target_width += BREATH_AMPLITUDE * breath

        # Exponential approach: framerate-independent and needs no tweening state.
        alpha = 1.0 - math.exp(-dt * 14.0)
        self._width += (target_width - self._width) * alpha
        self._height += (target_height - self._height) * alpha

        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw(self, _area, cr, width, height, *_args):
        cr.set_operator(cairo.Operator.SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.Operator.OVER)

        x = (width - self._width) / 2.0
        y = (height - self._height) / 2.0

        if self.state == "listening":
            elapsed = time.monotonic() - self._phase_start
            breath = 0.5 - 0.5 * math.cos(2 * math.pi * elapsed / BREATH_PERIOD)
            r, g, b, a = COLOR_LISTENING
            # The breath shows in the glow and the width, not the body's alpha —
            # a translucent body just muddies against the halo behind it.
            self._draw_glow(cr, x, y, r, g, b, 0.5 + 0.5 * breath)
            base = (r, g, b, a)
        elif self.state == "transcribing":
            r, g, b, a = COLOR_TRANSCRIBING
            self._draw_glow(cr, x, y, r, g, b, 0.28)
            base = (r, g, b, a)
        elif self.state == "offline":
            base = COLOR_OFFLINE
        else:
            base = COLOR_IDLE

        rounded_rect(cr, x, y, self._width, self._height)
        cr.set_source_rgba(*base)
        cr.fill()

        if self.state == "transcribing":
            self._draw_sweep(cr, x, y)

    def _draw_glow(self, cr, x, y, r, g, b, intensity):
        """Cheap blur-free glow: concentric copies of the pill, each fainter.

        Enough layers that the falloff reads as smooth rather than as visible
        bands, with a quadratic curve so the halo fades fast near the edge.
        """
        layers = 12
        for i in range(layers, 0, -1):
            spread = GLOW_SPREAD * i / layers
            falloff = (1.0 - i / (layers + 1.0)) ** 2
            rounded_rect(
                cr,
                x - spread,
                y - spread,
                self._width + spread * 2,
                self._height + spread * 2,
            )
            cr.set_source_rgba(r, g, b, intensity * falloff * 0.10)
            cr.fill()

    def _draw_sweep(self, cr, x, y):
        elapsed = time.monotonic() - self._phase_start
        travel = (elapsed % SWEEP_PERIOD) / SWEEP_PERIOD
        sweep_x = x - SWEEP_WIDTH + travel * (self._width + SWEEP_WIDTH)

        # Clip to the pill so the segment appears to run inside it.
        cr.save()
        rounded_rect(cr, x, y, self._width, self._height)
        cr.clip()

        gradient = cairo.LinearGradient(sweep_x, 0, sweep_x + SWEEP_WIDTH, 0)
        r, g, b, a = COLOR_SWEEP
        gradient.add_color_stop_rgba(0.0, r, g, b, 0.0)
        gradient.add_color_stop_rgba(0.5, r, g, b, a)
        gradient.add_color_stop_rgba(1.0, r, g, b, 0.0)
        cr.set_source(gradient)
        cr.rectangle(sweep_x, y, SWEEP_WIDTH, self._height)
        cr.fill()
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

    Without this, a 190x26 invisible strip at the bottom centre of every
    screen would quietly eat clicks.
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
    # -1: ignore panels' and docks' exclusive zones, so the pill sits over them
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
