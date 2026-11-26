/* Baatsun Pill — a dictation-state indicator that lives inside GNOME Shell.
 *
 * Why an extension rather than a window: on GNOME/Wayland, mutter implements
 * neither wlr-layer-shell nor client-side window positioning, so no ordinary
 * GTK window can pin itself near the bottom centre and stay above other
 * windows. A Clutter actor added to the Shell's chrome can, and — the reason
 * this matters most for baatsun — it can never take keyboard focus, so the
 * daemon's ydotool typing still lands in whatever the user was actually
 * focused on.
 *
 * State comes from the daemon's own unix socket (the same "subscribe" stream
 * the tray and history window use), read here with Gio. No extra IPC.
 *
 * One deep-green pill, the app's own colour, that opens and closes; everything
 * else happens inside it:
 *
 *     idle          a small capsule holding five resting dots
 *     hovered       opens to hold a ▶ / ■ button
 *     listening     a coral recording dot, level bars that follow your voice,
 *                   and a tag for where the text will land (Email, Chat…) or
 *                   what the recording is for (Edit, Notes)
 *     transcribing  a spinner and the same tag
 *     finished      a word for a moment: "✓ Pasted", "Too short"
 *
 * Keeping one shape and one colour is deliberate. This thing lives over
 * arbitrary content, so it should be a single predictable object that opens
 * and closes rather than a patch of screen that changes colour — and a dark
 * green stays legible over both a white document and a dark terminal.
 *
 * The pill is also a control. Hovering it opens it into a start/stop button
 * that writes "toggle" back down the same socket, which is what makes a
 * hands-free dictation stoppable without reaching for the keyboard. That is
 * the one thing here that takes pointer input away from the window underneath;
 * everything about the hover box is sized to keep that hole small.
 */

import Cairo from 'gi://cairo';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Graphene from 'gi://Graphene';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

// Closed: a small capsule holding the level bars at rest, which reads as
// "a microphone meter, waiting" rather than as a stray line on the screen.
const REST_WIDTH = 40;
const REST_HEIGHT = 10;
// Open: sized to its contents, with this much padding either side and never
// narrower than a button.
const ACTIVE_HEIGHT = 26;
const ACTIVE_PADDING = 12;
const ACTIVE_MIN_WIDTH = 44;
const ICON_SIZE = 12;
// The resting dots, dimmer than a live bar.
const REST_BARS_OPACITY = 150;
// How long a finished dictation's word stays up.
const MESSAGE_MS = 1600;
// A level older than this means the daemon isn't sending any (an older
// version), so the bars fall back to their own animation.
const LEVEL_STALE_MS = 400;

// The tag: what a recording is for, else where its text will land.
const TAGS = {
    command: 'Edit',
    notes: 'Notes',
    email: 'Email',
    chat: 'Chat',
    post: 'Post',
    social: 'Post',
    docs: 'Doc',
    code: 'Code',
};
// The word shown when a dictation ends, and whether it is a problem.
const RESULTS = {
    pasted: ['✓ Pasted', false],
    edited: ['✓ Edited', false],
    notes: ['✓ Notes saved', false],
    scratched: ['Removed', false],
    too_short: ['Too short', true],
    empty: ['Didn’t catch that', true],
    failed: ['Failed', true],
    insert_failed: ['Not pasted, see History', true],
    mic_failed: ['No microphone', true],
    nothing_to_scratch: ['Nothing to remove', true],
    needs_key: ['Needs an API key', true],
    edit_failed: ['Edit failed', true],
    notes_failed: ['Saved, not organised', true],
};

const MORPH_DURATION = 220;
const CONTENT_FADE = 130;

// Gap from the bottom of the screen to the closed bar, not to the actor that
// carries it. Lifted well clear of the edge so it reads as a deliberate
// indicator rather than a rendering artefact, and so it sits above the dock
// instead of on top of it. The pill grows about its own centre, so an open
// pill straddles this line rather than sitting on it.
const PILL_BOTTOM_MARGIN = 64;

// The pointer target. This is the only part of baatsun that takes clicks away
// from the window underneath, so it is kept barely larger than the widest and
// tallest the pill ever gets — though deliberately much taller than the 6px
// closed bar, which is far too thin to ask anyone to hit.
const HOVER_WIDTH = 64;
const HOVER_HEIGHT = 34;

// The level bars. Their gap is the stylesheet's `spacing` (St.BoxLayout takes
// it from CSS, not from a property), so the row measures 5*3 + 4*3 = 27.
const BAR_COUNT = 5;
const BAR_WIDTH = 3;
// Bars are allocated at full height and scaled down, so the row never
// re-lays-out mid-animation. BAR_REST is therefore a fraction, not a pixel
// count: 3px of an 11px bar.
const BAR_HEIGHT = 11;
const BAR_REST = 3 / BAR_HEIGHT;

// The daemon broadcasts the real mic level while recording, and the bars
// follow it. These hops are the fallback for a daemon too old to send levels:
// every bar walks to a random height on its own clock.
const HOP_MIN_DURATION = 110;
const HOP_MAX_DURATION = 230;
// How often the meter checks which bars are due a new height. Well under
// HOP_MIN_DURATION so a bar's hop starts close to when it actually falls due.
const HOP_TICK = 40;
// How much shorter the outermost bars run than the centre ones, which is what
// gives the row a crest instead of a flat wall of noise.
const HOP_EDGE_FALLOFF = 0.45;
const HOP_MIN_PEAK = 0.15; // as a fraction of the bar's own allowance

const SPINNER_SIZE = 12;
const SPINNER_PERIOD = 900;
// Hyphenated, the way GObject names it. See _runSpinner().
const SPINNER_PROP = 'rotation-angle-z';
const SPINNER_WIDTH = 1.8;  // stroke
const SPINNER_ARC = 1.9;    // radians of bright head, of a 2π ring

const RECONNECT_INTERVAL = 3;

// See _sendFocus. Well past the longest title that classifies as anything.
const MAX_TITLE_CHARS = 512;

export default class BaatsunPillExtension extends Extension {
    enable() {
        // null rather than 'offline', so the _applyState('offline') below is a
        // real transition and actually runs. Seeding it with the state we are
        // about to apply would trip that method's no-op guard, leaving the
        // offline styling unset and the hover button looking live — clickable
        // to the eye — before the socket has ever been reached.
        this._state = null;
        this._hovered = false;
        this._shown = null;
        this._mode = null;
        this._surface = null;
        this._flashText = null;
        this._flashBad = false;
        this._flashId = null;
        this._lastLevelAt = 0;
        this._socketPath = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'baatsun.sock']);

        // Not reactive itself: the container below owns the pointer, so
        // nothing in here has to work out whether the pointer is over a bar,
        // the icon, or the padding between them.
        this._pill = new St.Widget({
            style_class: 'baatsun-pill',
            layout_manager: new Clutter.BinLayout(),
            width: REST_WIDTH,
            height: REST_HEIGHT,
            reactive: false,
            can_focus: false,
            track_hover: false,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        // St can't interpolate border-radius, and the pill has to stay a
        // stadium at every height it passes through on the way open. Cheaper
        // to restyle on the frames where the rounded height actually changes
        // than to fake the shape some other way.
        this._pill.connect('notify::height', () => this._roundEnds());
        this._roundEnds();

        // Everything inside the pill is clipped to it, so an opening pill
        // *reveals* its contents rather than letting them stick out of a 6px
        // bar mid-transition. This has to be its own actor: clipping the pill
        // itself would cut off its own drop shadow.
        this._content = new St.Widget({
            layout_manager: new Clutter.BinLayout(),
            clip_to_allocation: true,
        });
        this._pill.add_child(this._content);

        // Everything that can sit in the open pill, in one row. What shows is
        // decided by `visible`, so the row's natural width is what the pill
        // opens to.
        this._row = new St.BoxLayout({
            style_class: 'baatsun-pill-row',
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._content.add_child(this._row);

        this._dot = new St.Widget({
            style_class: 'baatsun-pill-dot',
            width: 6,
            height: 6,
            y_align: Clutter.ActorAlign.CENTER,
            visible: false,
        });
        this._row.add_child(this._dot);

        this._bars = new St.BoxLayout({
            style_class: 'baatsun-pill-bars',
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
            opacity: REST_BARS_OPACITY,
        });
        this._barActors = [];
        for (let i = 0; i < BAR_COUNT; i++) {
            const bar = new St.Widget({
                style_class: 'baatsun-pill-bar',
                width: BAR_WIDTH,
                height: BAR_HEIGHT,
                scale_y: BAR_REST,
                // Scale about the middle, so a bar shrinks symmetrically
                // rather than hanging from its top edge.
                pivot_point: new Graphene.Point({x: 0.5, y: 0.5}),
                y_align: Clutter.ActorAlign.CENTER,
            });
            this._barActors.push(bar);
            this._bars.add_child(bar);
        }
        this._row.add_child(this._bars);

        this._spinner = new St.DrawingArea({
            width: SPINNER_SIZE,
            height: SPINNER_SIZE,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
            pivot_point: new Graphene.Point({x: 0.5, y: 0.5}),
            visible: false,
        });
        this._spinner.connect('repaint', area => this._drawSpinner(area));
        this._row.add_child(this._spinner);

        this._controlIcon = new St.Icon({
            style_class: 'baatsun-pill-icon',
            icon_name: 'media-playback-start-symbolic',
            icon_size: ICON_SIZE,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
            visible: false,
        });
        this._row.add_child(this._controlIcon);

        this._tag = new St.Label({
            style_class: 'baatsun-pill-tag',
            y_align: Clutter.ActorAlign.CENTER,
            visible: false,
        });
        this._row.add_child(this._tag);

        this._message = new St.Label({
            style_class: 'baatsun-pill-message',
            y_align: Clutter.ActorAlign.CENTER,
            visible: false,
        });
        this._row.add_child(this._message);

        // can_focus stays false: this is the whole reason the pill can be
        // clickable at all. Taking keyboard focus would move it off the window
        // being dictated into, which is both where ydotool types and what
        // baatsun_context reads to decide whether to clean the transcript up.
        this._container = new St.Widget({
            layout_manager: new Clutter.BinLayout(),
            width: HOVER_WIDTH,
            height: HOVER_HEIGHT,
            reactive: true,
            track_hover: true,
            can_focus: false,
        });
        this._container.add_child(this._pill);

        this._container.connect('enter-event', () => {
            this._setHovered(true);
            return Clutter.EVENT_PROPAGATE;
        });
        this._container.connect('leave-event', () => {
            this._setHovered(false);
            return Clutter.EVENT_PROPAGATE;
        });
        this._container.connect('button-press-event', (_actor, event) =>
            this._onPress(event));

        // affectsInputRegion: true is what lets the hover control be clicked at
        // all, and it costs a HOVER_WIDTH x HOVER_HEIGHT hole in the click
        // surface of whatever is underneath — hence keeping that box small.
        Main.layoutManager.addChrome(this._container, {
            affectsInputRegion: true,
            affectsStruts: false,
            trackFullscreen: false,
        });

        this._monitorsChangedId = Main.layoutManager.connect(
            'monitors-changed', () => this._reposition());
        this._reposition();

        this._applyState('offline');
        this._connect();

        // Report which window has focus, so the daemon can tell a coding
        // prompt from a LinkedIn post. Only the Shell can see this on Wayland.
        this._focusId = global.display.connect(
            'notify::focus-window', () => this._reportFocus());
        this._reportFocus();
    }

    disable() {
        this._stopAnimations();

        if (this._focusId) {
            global.display.disconnect(this._focusId);
            this._focusId = null;
        }
        if (this._titleId) {
            this._titledWindow?.disconnect(this._titleId);
            this._titleId = null;
            this._titledWindow = null;
        }
        if (this._monitorsChangedId) {
            Main.layoutManager.disconnect(this._monitorsChangedId);
            this._monitorsChangedId = null;
        }
        if (this._reconnectId) {
            GLib.source_remove(this._reconnectId);
            this._reconnectId = null;
        }
        if (this._flashId) {
            GLib.source_remove(this._flashId);
            this._flashId = null;
        }
        if (this._cancellable) {
            this._cancellable.cancel();
            this._cancellable = null;
        }
        this._closeConnection();

        if (this._container) {
            Main.layoutManager.removeChrome(this._container);
            this._container.destroy();
            this._container = null;
            this._pill = null;
            this._content = null;
            this._bars = null;
            this._barActors = [];
            this._spinner = null;
            this._controlIcon = null;
            this._row = null;
            this._dot = null;
            this._tag = null;
            this._message = null;
        }
    }

    _reposition() {
        if (!this._container)
            return;

        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;

        // The pill is centred inside the container, so placing it a fixed gap
        // above the screen edge means backing off by half the container's
        // height plus half the closed bar's — otherwise PILL_BOTTOM_MARGIN
        // would silently mean "gap to the invisible hover box" and the pill
        // would float higher than the number says. Measured against the closed
        // bar because that is the resting state; opening grows about the
        // centre, which puts the extra height half above and half below.
        //
        // Monitor geometry and actor sizes are both in logical pixels here, so
        // no scale-factor arithmetic is needed.
        const bottom = monitor.y + monitor.height - PILL_BOTTOM_MARGIN;
        const width = this._container.width;
        this._container.set_position(
            Math.round(monitor.x + (monitor.width - width) / 2),
            Math.round(bottom - (HOVER_HEIGHT + REST_HEIGHT) / 2));
    }

    /* ---------------------------------------------------------------- hover */

    _setHovered(hovered) {
        if (!this._container || this._hovered === hovered)
            return;

        this._hovered = hovered;
        this._refresh();
    }

    /* Only idle and listening can be acted on. Clicking while transcribing
     * would be worse than useless: the daemon serialises toggles behind the
     * lock it holds for the whole transcription, so the click would sit there
     * and then start a *fresh* recording the moment the transcript landed.
     * Offline has nothing on the socket to answer at all. */
    _canToggle() {
        return this._state === 'idle' || this._state === 'listening';
    }

    _onPress(event) {
        // Right or middle click opens the app window: without a tray icon the
        // pill is the one piece of Baatsun always on screen, so it has to be a
        // way in to Settings and History.
        if (event && event.get_button() !== Clutter.BUTTON_PRIMARY) {
            this._openWindow();
            return Clutter.EVENT_STOP;
        }
        if (this._canToggle())
            this._send('toggle');
        return Clutter.EVENT_STOP;
    }

    _openWindow() {
        // The Shell's PATH often lacks ~/.local/bin, where a from-source
        // install links the launcher, so look in the known places first.
        const candidates = [
            '/usr/bin/baatsun-gui',
            GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', 'baatsun-gui']),
        ];
        const launcher = candidates.find(p => GLib.file_test(p, GLib.FileTest.IS_EXECUTABLE))
            || GLib.find_program_in_path('baatsun-gui');
        if (!launcher) {
            logError(new Error('baatsun-gui not found'), 'Baatsun');
            return;
        }
        try {
            GLib.spawn_async(null, [launcher], null, GLib.SpawnFlags.DEFAULT, null);
        } catch (e) {
            logError(e, 'Baatsun: could not open the window');
        }
    }

    /* ---------------------------------------------------------------- state */

    _applyState(state, event = {}) {
        if (!this._pill)
            return;

        if (state === 'listening' || state === 'transcribing') {
            const tagChanged = this._mode !== (event.mode ?? null) ||
                this._surface !== (event.surface ?? null);
            this._mode = event.mode ?? null;
            this._surface = event.surface ?? null;
            this._clearFlash();
            if (tagChanged && state === this._state) {
                // Same state, new tag: an edit that began as a dictation.
                this._shown = null;
                this._refresh();
                return;
            }
        } else if (state === 'idle') {
            const key = event.reason || event.result;
            if (key && RESULTS[key] && this._state !== null)
                this._flash(...RESULTS[key]);
        }

        if (state === this._state)
            return;

        this._state = state;
        // The only state with any styling of its own. Listening and
        // transcribing are told apart by what is inside the pill, not by
        // anything about the pill.
        if (state === 'offline')
            this._pill.add_style_class_name('baatsun-pill-offline');
        else
            this._pill.remove_style_class_name('baatsun-pill-offline');

        this._refresh();
    }

    /* The one place that decides what the pill looks like. Hover and daemon
     * state both answer the same two questions — how far open, and what is
     * inside — and answering them in two places is how they end up
     * disagreeing: a pointer arriving mid-transcription, or the hotkey pressed
     * while hovering, has to land somewhere coherent.
     *
     * The button only appears when there is something to click. While
     * transcribing, hovering leaves the spinner alone: offering a play glyph
     * that then refuses the click says less than the spinner already does. */
    _refresh() {
        if (!this._pill)
            return;

        let show;
        if (this._hovered && this._canToggle())
            show = 'button';
        else if (this._state === 'listening')
            show = 'listening';
        else if (this._state === 'transcribing')
            show = 'transcribing';
        else if (this._flashText)
            show = 'message';
        else
            show = 'none';

        // Before the guard below: starting a recording with the hotkey while
        // the pointer is already on the pill leaves `show` at 'button' but has
        // to flip the glyph from play to stop anyway.
        this._controlIcon.icon_name = this._state === 'listening'
            ? 'media-playback-stop-symbolic'
            : 'media-playback-start-symbolic';

        const key = `${show}|${this._mode}|${this._surface}|${this._flashText}`;
        if (key === this._shown)
            return;
        this._shown = key;

        const tag = TAGS[this._mode] || TAGS[this._surface] || '';
        this._tag.text = tag;
        if (this._flashText) {
            this._message.text = this._flashText;
            if (this._flashBad)
                this._message.add_style_class_name('baatsun-pill-message-bad');
            else
                this._message.remove_style_class_name('baatsun-pill-message-bad');
        }

        const wants = {
            dot: show === 'listening',
            bars: show === 'listening' || show === 'none',
            spinner: show === 'transcribing',
            icon: show === 'button',
            tag: (show === 'listening' || show === 'transcribing') && tag !== '',
            message: show === 'message',
        };
        this._reveal(this._dot, wants.dot);
        this._reveal(this._bars, wants.bars, show === 'none' ? REST_BARS_OPACITY : 255);
        this._reveal(this._spinner, wants.spinner);
        this._reveal(this._controlIcon, wants.icon);
        this._reveal(this._tag, wants.tag);
        this._reveal(this._message, wants.message);

        this._runDot(wants.dot);
        this._runBars(show === 'listening');
        this._runSpinner(wants.spinner);

        const open = show !== 'none';
        let width = REST_WIDTH;
        if (open) {
            const [, natural] = this._row.get_preferred_width(-1);
            width = Math.max(ACTIVE_MIN_WIDTH, Math.ceil(natural) + ACTIVE_PADDING * 2);
        }
        this._fitContainer(width);
        this._pill.ease({
            width,
            height: open ? ACTIVE_HEIGHT : REST_HEIGHT,
            duration: MORPH_DURATION,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        });
    }

    /* Show or hide one thing in the row. Showing fades it in; hiding is
     * immediate, so the row's width already describes what is about to show. */
    _reveal(actor, visible, opacity = 255) {
        if (!visible) {
            actor.remove_transition('opacity');
            actor.visible = false;
            return;
        }
        if (!actor.visible) {
            actor.opacity = 0;
            actor.visible = true;
        }
        actor.ease({
            opacity,
            duration: CONTENT_FADE,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        });
    }

    /* The hover box tracks the pill, so a wide message is still clickable
     * across its width and the hole it makes in the screen stays small when
     * the pill is closed. Grown before the pill eases open, so it never clips
     * the pill on the way. */
    _fitContainer(pillWidth) {
        const width = Math.max(HOVER_WIDTH, pillWidth + 16);
        if (width === this._container.width)
            return;
        this._container.width = width;
        this._reposition();
    }

    /* A word for a moment after a dictation ends, then back to rest. */
    _flash(text, bad) {
        this._clearFlash();
        this._flashText = text;
        this._flashBad = bad;
        this._flashId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, MESSAGE_MS, () => {
            this._flashId = null;
            this._flashText = null;
            this._refresh();
            return GLib.SOURCE_REMOVE;
        });
    }

    _clearFlash() {
        if (this._flashId) {
            GLib.source_remove(this._flashId);
            this._flashId = null;
        }
        this._flashText = null;
    }

    /* The recording dot breathes while the mic is open. */
    _runDot(on) {
        this._dot.remove_transition('opacity');
        if (!on)
            return;
        this._dot.ease({
            opacity: 150,
            duration: 600,
            mode: Clutter.AnimationMode.EASE_IN_OUT_SINE,
            repeatCount: -1,
            autoReverse: true,
        });
    }

    /* One level from the daemon: every bar follows it, the middle ones
     * reaching furthest, with a little jitter so the row doesn't move as one
     * flat block. */
    _onLevel(level) {
        this._lastLevelAt = GLib.get_monotonic_time() / 1000;
        if (this._state !== 'listening' || !this._barActors.length)
            return;
        const centre = (BAR_COUNT - 1) / 2;
        this._barActors.forEach((bar, i) => {
            // Wider falloff than the fallback hops, so even a steady voice
            // gives the row a crest rather than five equal bars.
            const allowance = 1 - 0.6 * (Math.abs(i - centre) / centre);
            const jitter = 0.6 + Math.random() * 0.4;
            bar.ease({
                scale_y: BAR_REST + (1 - BAR_REST) * Math.min(1, level * allowance * jitter),
                duration: 90,
                mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            });
        });
    }

    /* The stadium shape, held across every height the pill passes through on
     * its way open — St cannot interpolate border-radius, so it is restyled
     * as the height animates. Guarded on the rounded value, which turns a
     * 220ms morph into a handful of restyles rather than one per frame. */
    _roundEnds() {
        const radius = Math.round(this._pill.height / 2);
        if (radius === this._radius)
            return;
        this._radius = radius;
        this._pill.set_style(`border-radius: ${radius}px;`);
    }

    /* ------------------------------------------------------------- the bars */

    /* Driven by one timer rather than by each bar's own onComplete, which is
     * the obvious way to write this and is a trap. ease() only reports through
     * onComplete if it found a transition to attach to, and it creates none
     * when the easing duration resolves to 0 — which is exactly what happens
     * with animations switched off in Accessibility. Then onComplete fires
     * *synchronously*, a bar that re-hops from its own onComplete recurses
     * without ever unwinding, and the "too much recursion" that throws takes
     * the whole refresh down with it. A timer cannot do that, and with
     * animations off it degrades into bars that step rather than glide, which
     * is the right answer for someone who asked for less motion.
     *
     * Each bar keeps its own next-hop deadline, so they drift out of step with
     * each other rather than pulsing in lockstep — that drift is the effect. */
    _runBars(on) {
        if (this._barTimer) {
            GLib.source_remove(this._barTimer);
            this._barTimer = null;
        }
        for (const bar of this._barActors)
            bar.remove_all_transitions();

        if (!on) {
            // Back to rest while fading out, so the row is already level the
            // next time it is revealed rather than resuming mid-jump.
            for (const bar of this._barActors) {
                bar.ease({
                    scale_y: BAR_REST,
                    duration: CONTENT_FADE,
                    mode: Clutter.AnimationMode.EASE_OUT_QUAD,
                });
            }
            return;
        }

        this._barDue = new Array(BAR_COUNT).fill(0);
        this._hopDue();
        this._barTimer = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT, HOP_TICK, () => {
                this._hopDue();
                return GLib.SOURCE_CONTINUE;
            });
    }

    /* Re-target every bar whose last hop has run out, and leave the rest
     * mid-flight. */
    _hopDue() {
        const now = GLib.get_monotonic_time() / 1000;
        // Real levels are arriving; they drive the bars instead.
        if (now - this._lastLevelAt < LEVEL_STALE_MS)
            return;

        this._barActors.forEach((bar, i) => {
            if (now < this._barDue[i])
                return;

            const duration = HOP_MIN_DURATION +
                Math.random() * (HOP_MAX_DURATION - HOP_MIN_DURATION);
            this._barDue[i] = now + duration;

            // Bars nearer the middle are allowed to run taller, so the row has
            // a centre rather than looking like uniform static.
            const centre = (BAR_COUNT - 1) / 2;
            const allowance =
                1 - HOP_EDGE_FALLOFF * (Math.abs(i - centre) / centre);
            const reach = HOP_MIN_PEAK + (1 - HOP_MIN_PEAK) * Math.random();

            bar.ease({
                scale_y: BAR_REST + (1 - BAR_REST) * allowance * reach,
                duration,
                mode: Clutter.AnimationMode.EASE_IN_OUT_SINE,
            });
        });
    }

    /* ---------------------------------------------------------- the spinner */

    /* A plain repeating transition rather than the recursive ease() the bars
     * use, for two reasons.
     *
     * One: ease() cannot drive this property. It canonicalises the name with
     * `p.replace('_', '-', 'g')` (environment.js), and String.replace with a
     * string pattern replaces only the FIRST match — the 'g' argument is
     * silently ignored — so it looks the transition up as `rotation-angle_z`.
     * The property does animate (GObject canonicalises for the setter), but
     * the transition table is keyed `rotation-angle-z`, so ease() finds
     * nothing and calls onComplete *synchronously*. Recursing from there is
     * unbounded synchronous recursion: it throws, and takes the rest of the
     * refresh down with it — leaving the pill open, the bars frozen where they
     * stopped, and neither fade ever run. `scale_y` survives the same code
     * path only because one underscore is all that first replace has to fix.
     *
     * Two: an endless spin is what repeat_count: -1 is for. No per-turn
     * callback means no recursion to get wrong in the first place.
     *
     * Stopping leaves the angle where it is rather than snapping to 0 — the
     * spinner is still on screen for the length of its fade out. */
    _runSpinner(on) {
        this._spinner.remove_transition(SPINNER_PROP);
        if (!on)
            return;

        this._spinner.rotation_angle_z = 0;
        const transition = new Clutter.PropertyTransition({
            property_name: SPINNER_PROP,
            interval: new Clutter.Interval({
                value_type: this._spinner.find_property(SPINNER_PROP).value_type,
            }),
            duration: SPINNER_PERIOD,
            repeat_count: -1,
            progress_mode: Clutter.AnimationMode.LINEAR,
        });
        // Only the target: ClutterPropertyTransition takes the interval's
        // starting value from the property itself, zeroed just above.
        transition.set_to(360);
        this._spinner.add_transition(SPINNER_PROP, transition);
    }

    /* A faint full ring with a bright head riding it — the ring is what keeps
     * the shape readable at 12px, where a lone arc reads as a smudge. */
    _drawSpinner(area) {
        const [width, height] = area.get_surface_size();
        const radius = Math.min(width, height) / 2 - SPINNER_WIDTH / 2;
        const cr = area.get_context();

        cr.translate(width / 2, height / 2);
        cr.setLineWidth(SPINNER_WIDTH);
        cr.setLineCap(Cairo.LineCap.ROUND);

        cr.setSourceRGBA(0.98, 0.97, 0.95, 0.22);
        cr.arc(0, 0, radius, 0, 2 * Math.PI);
        cr.stroke();

        cr.setSourceRGBA(0.98, 0.97, 0.95, 0.95);
        cr.arc(0, 0, radius, -Math.PI / 2, -Math.PI / 2 + SPINNER_ARC);
        cr.stroke();

        cr.$dispose();
    }

    _stopAnimations() {
        if (this._barTimer) {
            GLib.source_remove(this._barTimer);
            this._barTimer = null;
        }
        for (const bar of this._barActors ?? [])
            bar.remove_all_transitions();
        this._pill?.remove_all_transitions();
        this._spinner?.remove_all_transitions();
        this._dot?.remove_all_transitions();
    }

    /* ---------------------------------------------------------------- focus */

    /* A browser's window class never changes but its title does, and the title
     * is what says "LinkedIn" rather than "GitHub". So follow the focused
     * window's title as well as focus itself, re-hooking on each change. */
    _reportFocus() {
        if (this._titleId) {
            this._titledWindow?.disconnect(this._titleId);
            this._titleId = null;
            this._titledWindow = null;
        }

        const win = global.display.focus_window;
        if (win) {
            this._titledWindow = win;
            this._titleId = win.connect('notify::title', () => this._sendFocus());
        }
        this._sendFocus();
    }

    _sendFocus() {
        const win = global.display.focus_window;
        this._send(`focus ${JSON.stringify({
            app: win?.get_wm_class() ?? '',
            // Truncated: baatsun_context matches site names and app names near
            // the front of a title, so nothing past a few hundred characters
            // carries any signal — and an unbounded title is the one thing in
            // this protocol that can be long enough to arrive at the daemon in
            // pieces.
            title: (win?.get_title() ?? '').slice(0, MAX_TITLE_CHARS),
        })}`);
    }

    /* Fire one command down a connection of its own, and don't wait for the
     * answer. Not sent over the subscribe stream: that one is held open for the
     * daemon to push events down, and the daemon's handler reads exactly one
     * command per connection.
     *
     * Failure is silent by design. For focus reports the daemon being down is
     * normal and self-correcting — the next focus change reports again. For a
     * toggle it means the click did nothing, which the pill already says: it
     * only shows the button as live when the socket is connected. */
    _send(command) {
        const client = new Gio.SocketClient();
        client.connect_async(
            Gio.UnixSocketAddress.new(this._socketPath), null,
            (source, result) => {
                try {
                    const connection = client.connect_finish(result);
                    connection.get_output_stream().write_all(command, null);
                    connection.close(null);
                } catch (e) {
                    // Nothing to recover; see above.
                }
            });
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
                // Re-report the focused window on every (re)connect. A daemon
                // that just restarted has no record of it, and nothing else
                // would tell it until the user next switched windows — which
                // leaves the app window's focus card blank, and the first
                // dictation after a restart classified as unknown.
                this._reportFocus();
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
                            this._applyState(event.state ?? 'idle', event);
                        else if (event.type === 'level')
                            this._onLevel(Number(event.v) || 0);
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
