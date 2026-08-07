#!/usr/bin/env python3
"""baatsun-gui: full history + control window for the baatsun dictation daemon.

A GTK4/libadwaita app that connects to the daemon's unix socket, fetches
past transcripts, and stays subscribed for live updates. Runs under the
desktop session's system Python (needs PyGObject + libadwaita), not the
daemon's venv — launch it with plain `python3`, not `venv/bin/python3`.

Single-instance: GApplication's own D-Bus activation means running this a
second time (e.g. from the tray icon) just re-presents the existing window
instead of starting a new process.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

import baatsun_config  # noqa: E402

SOCKET_PATH = f"/run/user/{os.getuid()}/baatsun.sock"
APP_ID = "com.baatsun.Baatsun"
RECONNECT_SECONDS = 3

STATE_LABELS = {"listening": "Listening…", "transcribing": "Transcribing…"}
STATE_ICONS = {
    "listening": "media-playback-stop-symbolic",
    "transcribing": "content-loading-symbolic",
}
IDLE_ICON = "media-record-symbolic"


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


def format_time(ts):
    try:
        return datetime.fromtimestamp(ts).strftime("%a %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


class HistoryRow(Gtk.ListBoxRow):
    def __init__(self, entry, on_retype, on_delete):
        super().__init__()
        self.entry_id = entry.get("id")
        self.entry_text = entry.get("text", "")
        # Present only when the cleanup pass actually changed something.
        self.raw_text = entry.get("raw")
        self._showing_raw = False
        self.set_activatable(False)

        outer = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_top=8,
            margin_bottom=8,
            margin_start=12,
            margin_end=6,
        )

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        self.text_label = Gtk.Label(label=self.entry_text, xalign=0, wrap=True, selectable=True)
        self.time_label = Gtk.Label(label=format_time(entry.get("ts")), xalign=0)
        self.time_label.add_css_class("caption")
        self.time_label.add_css_class("dim-label")
        self._stamp = format_time(entry.get("ts"))
        text_box.append(self.text_label)
        text_box.append(self.time_label)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, valign=Gtk.Align.START)
        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy")
        copy_btn.add_css_class("flat")
        copy_btn.connect("clicked", self._on_copy)
        # Only offered where there is a difference to look at, so its presence
        # is itself the signal that cleanup changed this one.
        if self.raw_text:
            self.raw_btn = Gtk.Button(
                icon_name="view-reveal-symbolic",
                tooltip_text="Show what you actually said, before cleanup")
            self.raw_btn.add_css_class("flat")
            self.raw_btn.connect("clicked", self._on_toggle_raw)
            actions.append(self.raw_btn)
        retype_btn = Gtk.Button(icon_name="edit-redo-symbolic", tooltip_text="Type again into focused window")
        retype_btn.add_css_class("flat")
        retype_btn.connect("clicked", lambda *_a: on_retype(self.entry_id))
        delete_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete")
        delete_btn.add_css_class("flat")
        delete_btn.connect("clicked", lambda *_a: on_delete(self.entry_id))
        actions.append(copy_btn)
        actions.append(retype_btn)
        actions.append(delete_btn)

        outer.append(text_box)
        outer.append(actions)
        self.set_child(outer)

    def _on_copy(self, *_args):
        """Copy whichever version is on screen, not always the cleaned one."""
        self.get_clipboard().set(
            self.raw_text if self._showing_raw else self.entry_text)

    def _on_toggle_raw(self, *_args):
        self._showing_raw = not self._showing_raw
        self.text_label.set_label(
            self.raw_text if self._showing_raw else self.entry_text)
        self.time_label.set_label(
            f"{self._stamp}  ·  before cleanup" if self._showing_raw
            else self._stamp)
        self.raw_btn.set_icon_name(
            "view-conceal-symbolic" if self._showing_raw else "view-reveal-symbolic")


class SettingsWindow(Adw.PreferencesWindow):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True)
        self.set_title("Voice Settings")
        self.set_default_size(420, 300)

        cfg = baatsun_config.load_config()

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Recording",
            description=(
                "Applying a change restarts the baatsun daemon (a few seconds "
                "while the model reloads)."
            ),
        )

        self.vocab_row = Adw.EntryRow(title="Names to get right")
        self.vocab_row.set_text(cfg.get("vocabulary") or "")
        self.vocab_row.set_tooltip_text(
            "Comma-separated. Your name, your products, the tools you talk "
            "about — anything the transcriber mishears. Applied while "
            "transcribing, so it works even with cleanup off."
        )
        group.add(self.vocab_row)

        self.hotkey_row = Adw.ComboRow(title="Hotkey")
        self.hotkey_row.set_model(Gtk.StringList.new(baatsun_config.HOTKEY_CHOICES))
        self.hotkey_row.set_selected(
            baatsun_config.safe_index(baatsun_config.HOTKEY_CHOICES, cfg.get("hotkey"))
        )
        group.add(self.hotkey_row)

        page.add(group)
        page.add(self._build_cleanup_group(cfg))

        button_group = Adw.PreferencesGroup()
        apply_button = Gtk.Button(label="Apply & Restart Daemon")
        apply_button.add_css_class("suggested-action")
        apply_button.set_halign(Gtk.Align.END)
        apply_button.connect("clicked", self.on_apply)
        button_group.add(apply_button)
        page.add(button_group)

        self.add(page)

    def _build_cleanup_group(self, cfg):
        group = Adw.PreferencesGroup(
            title="Cleanup with OpenAI",
            description=(
                "Tidies punctuation, capitalisation and filler words before "
                "typing. Only the transcribed text is sent — your audio never "
                "leaves this machine. Costs a fraction of a cent per dictation."
            ),
        )

        self.cleanup_row = Adw.SwitchRow(
            title="Clean up transcripts",
            subtitle="Off until an API key is saved below",
        )
        self.cleanup_row.set_active(bool(cfg.get("cleanup_enabled")))
        group.add(self.cleanup_row)

        self.scope_row = Adw.ComboRow(
            title="Apply to",
            subtitle="Prose only: terminals and editors stay verbatim",
        )
        self.scope_row.set_model(Gtk.StringList.new(
            ["Prose windows only", "Everything I dictate"]))
        self.scope_row.set_selected(baatsun_config.safe_index(
            baatsun_config.CLEANUP_SCOPE_CHOICES, cfg.get("cleanup_scope")))
        group.add(self.scope_row)

        self.strength_row = Adw.ComboRow(
            title="Correction level",
            subtitle="Natural also fixes phrasing a native speaker wouldn't use",
        )
        self.strength_row.set_model(Gtk.StringList.new(
            ["Grammar and punctuation only", "Natural English"]))
        self.strength_row.set_selected(baatsun_config.safe_index(
            baatsun_config.CLEANUP_STRENGTH_CHOICES, cfg.get("cleanup_strength")))
        group.add(self.strength_row)

        self.breaks_row = Adw.SwitchRow(
            title="Break long text into paragraphs",
            subtitle="Never in chat apps, where Enter would send the message",
        )
        self.breaks_row.set_active(bool(cfg.get("line_breaks", True)))
        group.add(self.breaks_row)

        self.hinglish_row = Adw.SwitchRow(
            title="I mix Hindi words into my speech",
            subtitle="Renders garbled Hindi ('K', 'Hummer') as English",
        )
        self.hinglish_row.set_active(bool(cfg.get("hinglish")))
        group.add(self.hinglish_row)

        # PasswordEntryRow so the key isn't left on screen; it is stored 0600 in
        # its own file, never in config.json.
        self.key_row = Adw.PasswordEntryRow(title="OpenAI API key")
        self.key_row.set_text(baatsun_config.load_api_key())
        group.add(self.key_row)

        self.key_status = Adw.ActionRow(title="Test key")
        test_button = Gtk.Button(label="Test")
        test_button.set_valign(Gtk.Align.CENTER)
        test_button.connect("clicked", self.on_test_key)
        self.key_status.add_suffix(test_button)
        self.key_status.set_subtitle("Sends one short request to check the key")
        group.add(self.key_status)

        return group

    def on_test_key(self, button):
        key = self.key_row.get_text().strip()
        model = baatsun_config.load_config().get("cleanup_model") \
            or baatsun_config.DEFAULT_CLEANUP_MODEL
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

    def on_apply(self, *_args):
        # Merge into the existing config rather than rebuilding it:
        # model_override and compute_type are not editable here, but the config
        # file may still carry them, and they must survive a save.
        cfg = baatsun_config.load_config()
        cfg["hotkey"] = baatsun_config.HOTKEY_CHOICES[self.hotkey_row.get_selected()]
        cfg["vocabulary"] = self.vocab_row.get_text().strip()
        cfg["cleanup_enabled"] = self.cleanup_row.get_active()
        cfg["cleanup_scope"] = \
            baatsun_config.CLEANUP_SCOPE_CHOICES[self.scope_row.get_selected()]
        cfg["line_breaks"] = self.breaks_row.get_active()
        cfg["hinglish"] = self.hinglish_row.get_active()
        cfg["cleanup_strength"] = \
            baatsun_config.CLEANUP_STRENGTH_CHOICES[self.strength_row.get_selected()]
        baatsun_config.save_config(cfg)
        baatsun_config.save_api_key(self.key_row.get_text())
        threading.Thread(target=self._restart_daemon, daemon=True).start()
        self.close()

    @staticmethod
    def _restart_daemon():
        subprocess.run(["systemctl", "--user", "restart", "baatsun.service"], check=False)


class BaatsunWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Baatsun")
        self.set_default_size(440, 600)

        self._entries = []
        self._search_text = ""

        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        self.status_label = Adw.WindowTitle(title="Baatsun", subtitle="")
        header.set_title_widget(self.status_label)

        self.record_button = Gtk.Button(icon_name=IDLE_ICON)
        self.record_button.set_tooltip_text("Start recording (or hold Ctrl+Super)")
        self.record_button.connect("clicked", self.on_record_clicked)
        header.pack_start(self.record_button)

        settings_button = Gtk.Button(icon_name="emblem-system-symbolic")
        settings_button.set_tooltip_text("Settings")
        settings_button.connect("clicked", self.on_settings_clicked)
        header.pack_end(settings_button)

        clear_button = Gtk.Button(icon_name="edit-clear-all-symbolic")
        clear_button.set_tooltip_text("Clear history")
        clear_button.connect("clicked", self.on_clear_clicked)
        header.pack_end(clear_button)

        toolbar_view.add_top_bar(header)

        search_bar_box = Gtk.Box(margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search transcripts")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self.on_search_changed)
        search_bar_box.append(self.search_entry)
        toolbar_view.add_top_bar(search_bar_box)

        self.empty_page = Adw.StatusPage(
            title="No transcripts yet",
            description="Hold Ctrl+Super and speak, or press the record button above.",
            icon_name="audio-input-microphone-symbolic",
        )

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_margin_top(6)
        self.listbox.set_margin_bottom(12)
        self.listbox.set_margin_start(12)
        self.listbox.set_margin_end(12)
        self.listbox.set_filter_func(self._filter_func)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_child(self.listbox)

        self.stack = Gtk.Stack()
        self.stack.add_named(self.empty_page, "empty")
        self.stack.add_named(scroller, "list")
        toolbar_view.set_content(self.stack)

        self.set_content(toolbar_view)
        self.connect("close-request", self.on_close_request)

    def on_close_request(self, *_args):
        # Hide instead of quitting, so the app keeps running for the tray
        # icon to re-present later without restarting the process.
        self.hide()
        return True

    def on_clear_clicked(self, *_args):
        fire_and_forget("clear")
        self._entries = []
        self._rebuild()

    def on_record_clicked(self, *_args):
        fire_and_forget("toggle")

    def on_settings_clicked(self, *_args):
        SettingsWindow(self).present()

    def on_search_changed(self, entry):
        self._search_text = entry.get_text().strip().lower()
        self.listbox.invalidate_filter()

    def request_retype(self, entry_id):
        fire_and_forget(f"retype {entry_id}")

    def request_delete(self, entry_id):
        fire_and_forget(f"delete {entry_id}")

    def _filter_func(self, row):
        if not self._search_text:
            return True
        return self._search_text in row.entry_text.lower()

    def set_connection_status(self, connected):
        self.record_button.set_sensitive(connected)
        if not connected:
            self.status_label.set_subtitle("Daemon not running")

    def set_state(self, state):
        self.record_button.set_icon_name(STATE_ICONS.get(state, IDLE_ICON))
        self.record_button.set_sensitive(state != "transcribing")
        self.status_label.set_subtitle(STATE_LABELS.get(state, ""))

    def set_history(self, entries):
        self._entries = list(entries)
        self._rebuild()

    def add_entry(self, entry):
        self._entries.append(entry)
        self._rebuild()

    def remove_entry(self, entry_id):
        self._entries = [e for e in self._entries if e.get("id") != entry_id]
        self._rebuild()

    def _rebuild(self):
        child = self.listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.listbox.remove(child)
            child = nxt
        for entry in reversed(self._entries):
            self.listbox.append(HistoryRow(entry, self.request_retype, self.request_delete))
        self.stack.set_visible_child_name("list" if self._entries else "empty")


class BaatsunApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None
        self._listener_started = False

    def do_activate(self):
        if self.window is None:
            self.window = BaatsunWindow(self)
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
                GLib.idle_add(self.window.set_connection_status, True)
                GLib.idle_add(self.window.set_state, "idle")
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
        except OSError:
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


def main():
    app = BaatsunApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
