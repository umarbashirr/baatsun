/* Baatsun Pill — a dictation-state indicator that lives inside GNOME Shell.
 *
 * Why an extension rather than a window: on GNOME/Wayland, mutter implements
 * neither wlr-layer-shell nor client-side window positioning, so no ordinary
 * GTK window can pin itself to the bottom centre and stay above other
 * windows. A Clutter actor added to the Shell's chrome can, and — the reason
 * this matters most for baatsun — it can never take keyboard focus, so the
 * daemon's ydotool typing still lands in whatever the user was actually
 * focused on.
 *
 * State comes from the daemon's own unix socket (the same "subscribe" stream
 * the tray and history window use), read here with Gio. No extra IPC.
 */

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Graphene from 'gi://Graphene';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const PILL_WIDTH = 100;
const PILL_HEIGHT = 5;
const BOTTOM_MARGIN = 7;

// The sweep segment that travels along the pill while transcribing.
const SWEEP_WIDTH = 34;
const SWEEP_DURATION = 900;

const BREATH_DURATION = 620;
const RECONNECT_INTERVAL = 3;

// Scale factors applied to the idle pill, so the allocation never changes and
// we never have to re-centre mid-animation.
const LISTENING_SCALE_X = 1.45;
const LISTENING_SCALE_Y = 1.3;
const BREATH_SCALE_X = 1.52;

export default class BaatsunPillExtension extends Extension {
    enable() {
        this._state = 'offline';
        this._socketPath = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'baatsun.sock']);

        this._pill = new St.Widget({
            style_class: 'baatsun-pill',
            width: PILL_WIDTH,
            height: PILL_HEIGHT,
            reactive: false,
            can_focus: false,
            track_hover: false,
            clip_to_allocation: true,
            pivot_point: new Graphene.Point({x: 0.5, y: 0.5}),
        });

        this._sweep = new St.Widget({
            style_class: 'baatsun-pill-sweep',
            width: SWEEP_WIDTH,
            height: PILL_HEIGHT,
            visible: false,
        });
        this._pill.add_child(this._sweep);

        // affectsInputRegion: false — the pill is purely ambient. It must not
        // swallow clicks aimed at whatever sits underneath it.
        Main.layoutManager.addChrome(this._pill, {
            affectsInputRegion: false,
            affectsStruts: false,
            trackFullscreen: false,
        });

        this._monitorsChangedId = Main.layoutManager.connect(
            'monitors-changed', () => this._reposition());
        this._reposition();

        this._applyState('offline');
        this._connect();
    }

    disable() {
        this._stopAnimations();

        if (this._monitorsChangedId) {
            Main.layoutManager.disconnect(this._monitorsChangedId);
            this._monitorsChangedId = null;
        }
        if (this._reconnectId) {
            GLib.source_remove(this._reconnectId);
            this._reconnectId = null;
        }
        if (this._cancellable) {
            this._cancellable.cancel();
            this._cancellable = null;
        }
        this._closeConnection();

        if (this._pill) {
            Main.layoutManager.removeChrome(this._pill);
            this._pill.destroy();
            this._pill = null;
            this._sweep = null;
        }
    }

    _reposition() {
        if (!this._pill)
            return;

        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;

        // Monitor geometry and actor sizes are both in logical pixels here, so
        // no scale-factor arithmetic is needed.
        this._pill.set_position(
            Math.round(monitor.x + (monitor.width - PILL_WIDTH) / 2),
            Math.round(monitor.y + monitor.height - PILL_HEIGHT - BOTTOM_MARGIN));
    }

    /* ---------------------------------------------------------------- state */

    _applyState(state) {
        if (!this._pill || state === this._state)
            return;

        this._state = state;
        this._stopAnimations();

        for (const cls of ['listening', 'transcribing', 'offline'])
            this._pill.remove_style_class_name(`baatsun-pill-${cls}`);

        switch (state) {
        case 'listening':
            this._pill.add_style_class_name('baatsun-pill-listening');
            this._pill.ease({
                scale_x: LISTENING_SCALE_X,
                scale_y: LISTENING_SCALE_Y,
                opacity: 255,
                duration: 200,
                mode: Clutter.AnimationMode.EASE_OUT_BACK,
                onComplete: () => this._breathe(true),
            });
            break;

        case 'transcribing':
            this._pill.add_style_class_name('baatsun-pill-transcribing');
            this._pill.ease({
                scale_x: LISTENING_SCALE_X,
                scale_y: LISTENING_SCALE_Y,
                opacity: 255,
                duration: 160,
                mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            });
            this._startSweep();
            break;

        case 'offline':
            this._pill.add_style_class_name('baatsun-pill-offline');
            this._easeToRest();
            break;

        default: // idle
            this._easeToRest();
            break;
        }
    }

    _easeToRest() {
        this._pill.ease({
            scale_x: 1,
            scale_y: 1,
            opacity: 255,
            duration: 260,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        });
    }

    /* Recursive ease rather than a repeating timeline: the state can change
     * mid-breath, and _stopAnimations()'s guard flag stops the recursion
     * without leaving a stray timeline attached to the actor. */
    _breathe(expanding) {
        if (!this._pill || this._state !== 'listening')
            return;

        this._breathing = true;
        this._pill.ease({
            scale_x: expanding ? BREATH_SCALE_X : LISTENING_SCALE_X,
            opacity: expanding ? 255 : 170,
            duration: BREATH_DURATION,
            mode: Clutter.AnimationMode.EASE_IN_OUT_SINE,
            onComplete: () => {
                if (this._breathing)
                    this._breathe(!expanding);
            },
        });
    }

    _startSweep() {
        if (!this._sweep)
            return;

        this._sweeping = true;
        this._sweep.show();
        this._sweepOnce();
    }

    _sweepOnce() {
        if (!this._sweep || !this._sweeping)
            return;

        this._sweep.remove_all_transitions();
        this._sweep.translation_x = -SWEEP_WIDTH;
        this._sweep.ease({
            translation_x: PILL_WIDTH,
            duration: SWEEP_DURATION,
            mode: Clutter.AnimationMode.EASE_IN_OUT_QUAD,
            onComplete: () => this._sweepOnce(),
        });
    }

    _stopAnimations() {
        this._breathing = false;
        this._sweeping = false;

        if (this._pill)
            this._pill.remove_all_transitions();
        if (this._sweep) {
            this._sweep.remove_all_transitions();
            this._sweep.hide();
            this._sweep.translation_x = -SWEEP_WIDTH;
        }
    }

    /* --------------------------------------------------------------- socket */

    _connect() {
        this._cancellable = new Gio.Cancellable();

        const client = new Gio.SocketClient();
        client.connect_async(
            Gio.UnixSocketAddress.new(this._socketPath),
            this._cancellable,
            (source, result) => {
                let connection;
                try {
                    connection = client.connect_finish(result);
                } catch (e) {
                    if (!e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        this._scheduleReconnect();
                    return;
                }

                this._connection = connection;
                try {
                    connection.get_output_stream().write_all('subscribe', this._cancellable);
                } catch (e) {
                    this._scheduleReconnect();
                    return;
                }

                this._reader = new Gio.DataInputStream({
                    base_stream: connection.get_input_stream(),
                    close_base_stream: true,
                });
                this._applyState('idle');
                this._readNextEvent();
            });
    }

    _readNextEvent() {
        if (!this._reader)
            return;

        this._reader.read_line_async(GLib.PRIORITY_DEFAULT, this._cancellable,
            (reader, result) => {
                let line;
                try {
                    [line] = reader.read_line_finish_utf8(result);
                } catch (e) {
                    if (!e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        this._scheduleReconnect();
                    return;
                }

                if (line === null) { // daemon went away
                    this._scheduleReconnect();
                    return;
                }

                if (line.length > 0) {
                    try {
                        const event = JSON.parse(line);
                        if (event.type === 'state')
                            this._applyState(event.state ?? 'idle');
                    } catch (e) {
                        // A malformed line is not worth dropping the stream over.
                    }
                }

                this._readNextEvent();
            });
    }

    _closeConnection() {
        this._reader = null;
        if (this._connection) {
            try {
                this._connection.close(null);
            } catch (e) {
                // Already gone; nothing to do.
            }
            this._connection = null;
        }
    }

    _scheduleReconnect() {
        if (this._reconnectId || !this._pill)
            return;

        this._closeConnection();
        this._applyState('offline');

        this._reconnectId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT, RECONNECT_INTERVAL, () => {
                this._reconnectId = null;
                if (this._pill)
                    this._connect();
                return GLib.SOURCE_REMOVE;
            });
    }
}
