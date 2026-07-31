#!/usr/bin/env python3
"""baatsun-tray: system tray icon for the baatsun dictation daemon.

Shows a mic icon in the tray (via AppIndicator/StatusNotifierItem — needs
the "AppIndicator and KStatusNotifierItem Support" GNOME Shell extension on
Wayland) whose glyph reflects daemon state (idle/listening/transcribing),
plus a menu to open the history window, toggle recording, or quit the tray.

This runs as a separate process from src/baatsun_gui.py deliberately: the
appindicator library only speaks GTK3's Gtk.Menu, and a single Python
process can't load both the GTK3 and GTK4 typelibs. "Show History" launches
baatsun_gui.py as a subprocess; GApplication's single-instance D-Bus
activation means that's a no-op re-present if it's already running, not a
second window.
"""
import json
import os
import socket
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
except ValueError:
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3

from gi.repository import GLib, Gtk  # noqa: E402

SOCKET_PATH = f"/run/user/{os.getuid()}/baatsun.sock"
GUI_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baatsun_gui.py")

ICON_IDLE = "audio-input-microphone-symbolic"
ICON_LISTENING = "media-record-symbolic"
ICON_TRANSCRIBING = "view-refresh-symbolic"
ICON_OFFLINE = "audio-input-microphone-muted-symbolic"


def send_command(command):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(SOCKET_PATH)
            sock.sendall(command.encode())
    except OSError:
        pass


def show_history(*_args):
    subprocess.Popen(
        [sys.executable, GUI_SCRIPT],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def toggle_recording(*_args):
    threading.Thread(target=send_command, args=("toggle",), daemon=True).start()


def quit_tray(*_args):
    Gtk.main_quit()


def build_menu(indicator):
    menu = Gtk.Menu()

    show_item = Gtk.MenuItem(label="Show History")
    show_item.connect("activate", show_history)
    menu.append(show_item)

    toggle_item = Gtk.MenuItem(label="Toggle Recording")
    toggle_item.connect("activate", toggle_recording)
    menu.append(toggle_item)

    menu.append(Gtk.SeparatorMenuItem())

    quit_item = Gtk.MenuItem(label="Quit Tray Icon")
    quit_item.connect("activate", quit_tray)
    menu.append(quit_item)

    menu.show_all()
    indicator.set_menu(menu)


class TrayListener:
    """Background thread: keeps the tray icon glyph in sync with daemon state."""

    def __init__(self, indicator):
        self.indicator = indicator

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        import time

        while True:
            try:
                self._connect_and_listen_once()
            except OSError:
                pass
            GLib.idle_add(self.indicator.set_icon_full, ICON_OFFLINE, "baatsun (offline)")
            time.sleep(3)

    def _connect_and_listen_once(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(SOCKET_PATH)
            sock.sendall(b"subscribe")
            GLib.idle_add(self.indicator.set_icon_full, ICON_IDLE, "baatsun")
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
        state = event.get("state")
        icon = {
            "listening": ICON_LISTENING,
            "transcribing": ICON_TRANSCRIBING,
            "idle": ICON_IDLE,
        }.get(state, ICON_IDLE)
        GLib.idle_add(self.indicator.set_icon_full, icon, f"baatsun ({state})")


def main():
    indicator = AppIndicator3.Indicator.new(
        "baatsun-tray", ICON_IDLE, AppIndicator3.IndicatorCategory.APPLICATION_STATUS
    )
    indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
    build_menu(indicator)

    TrayListener(indicator).start()

    Gtk.main()


if __name__ == "__main__":
    main()
