import React, { useEffect, useState } from 'react'
import { Card, Button, Badge, Section } from '../components/ui.jsx'
import { prettyApp } from '../lib/stats.js'

// Keyed by the state names the daemon actually broadcasts — "listening", not
// "recording" (see broadcast_state in baatsun.py, and the same three names in
// the pill, the tray and the GNOME extension). Getting this wrong is silent:
// an unrecognised state falls through to COPY.idle, so the page just claims to
// be ready for the whole time it is recording.
const COPY = {
  idle: { title: 'Ready', body: 'Hold your hotkey anywhere and speak.' },
  listening: { title: 'Listening', body: 'Let go of the hotkey when you are done.' },
  transcribing: { title: 'Transcribing', body: 'Turning what you said into text…' },
}

const REASONS = {
  too_short: 'That one was too short to transcribe.',
  empty: 'Nothing recognisable in that recording.',
  failed: 'Transcription failed — the recording was kept on disk.',
  insert_failed: 'Could not paste into the window — the text is in History. Is ydotoold running?',
  mic_failed: 'Could not open the microphone — is PipeWire (pw-record) installed?',
  scratched: 'Removed the last dictation.',
  nothing_to_scratch: 'Nothing recent to remove in that window.',
  needs_key: 'That needs an OpenAI key — add one in Settings.',
  edit_failed: 'The voice edit didn’t come back, so nothing was changed.',
  notes_failed: 'Couldn’t organise the notes, so the plain transcript was kept.',
}

const MODE_COPY = {
  command: { title: 'Listening for an edit', body: 'Say what to do with the selected text, then let go.' },
  notes: { title: 'Taking notes', body: 'Talk as long as you like. Press Stop when you’re done.' },
}

// Replies to "toggle" that mean nothing happened.
const TOGGLE_ERRORS = {
  busy: 'Still finishing the last dictation — try again in a moment.',
  mic_failed: REASONS.mic_failed,
}

// The surface names baatsun_context.surface() returns, in the words a person
// would use for them. "code" is left out on purpose: it is the one surface
// where nothing is reshaped, and the badge says so in those terms instead.
const SURFACES = {
  email: 'Email layout',
  chat: 'Chat message',
  post: 'Post',
  social: 'Feed post',
  docs: 'Document',
}

/**
 * The recording orb.
 *
 * The pill's idea, at window scale: one shape that opens rather than a control
 * that changes colour to mean something new. Rings expand while recording so
 * the state is legible from across a desk.
 */
function Orb({ state, onToggle, disabled }) {
  const recording = state === 'listening'
  const transcribing = state === 'transcribing'

  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={transcribing || disabled}
      aria-label={recording ? 'Stop recording' : 'Start recording'}
      className="no-drag group relative grid h-[132px] w-[132px] place-items-center disabled:cursor-wait"
    >
      {recording && (
        <>
          <span className="animate-live absolute inset-0 rounded-full bg-live/8" />
          <span
            className="animate-live absolute inset-[14px] rounded-full bg-live/12"
            style={{ animationDelay: '0.3s' }}
          />
        </>
      )}
      <span
        className={`relative grid h-[92px] w-[92px] place-items-center rounded-full border transition-all duration-300 ${
          recording
            ? 'border-live/50 bg-live/12 scale-105'
            : transcribing
              ? 'border-green/40 bg-mint'
              : 'border-sand-2 bg-sand group-hover:border-sand-2 group-hover:bg-sand'
        }`}
      >
        {transcribing ? (
          <svg className="h-7 w-7 animate-spin text-green" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" opacity="0.2" />
            <path
              d="M21 12a9 9 0 0 0-9-9"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </svg>
        ) : (
          <svg
            className={`h-8 w-8 transition-colors ${recording ? 'text-live' : 'text-muted'}`}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 15a3.5 3.5 0 0 0 3.5-3.5v-5a3.5 3.5 0 1 0-7 0v5A3.5 3.5 0 0 0 12 15Z" />
            <path d="M19 11.5a7 7 0 0 1-14 0M12 18.5V22" />
          </svg>
        )}
      </span>
    </button>
  )
}

export default function Dictate({ daemon }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const current = daemon.state.state || 'idle'
  const mode = daemon.state.mode
  const copy = (current === 'listening' && MODE_COPY[mode]) || COPY[current] || COPY.idle
  const reason = daemon.state.reason ? REASONS[daemon.state.reason] : ''

  // The newest transcript is the last one the daemon appended.
  const latest = daemon.entries.length ? daemon.entries[daemon.entries.length - 1] : null

  useEffect(() => {
    // Any state change means the daemon accepted our last toggle, or acted on
    // the hotkey — either way the button is live again.
    setBusy(false)
  }, [daemon.state])

  const toggle = async () => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const reply = await daemon.toggle()
      if (reply !== 'started' && reply !== 'stopped') {
        // No state change follows a refused toggle, so nothing else would
        // re-enable the button.
        setError(TOGGLE_ERRORS[reply] || `The daemon replied “${reply}”.`)
        setBusy(false)
      }
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  const takeNotes = async () => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const reply = await daemon.notes()
      if (reply !== 'started' && reply !== 'stopped') {
        setError(TOGGLE_ERRORS[reply] || `The daemon replied “${reply}”.`)
        setBusy(false)
      }
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  const retype = async () => {
    setError('')
    try {
      await daemon.retype(latest.id)
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="mx-auto max-w-[720px]">
      <div className="flex flex-col items-center py-6">
        <Orb state={current} onToggle={toggle} disabled={busy || !daemon.connected} />

        <h2 className="mt-6 font-serif text-[34px] leading-tight text-ink">
          {copy.title}
        </h2>
        <p className="mt-1.5 text-center text-[14.5px] text-muted">{copy.body}</p>

        {reason && (
          <p className="mt-3 text-[13.5px] text-faint">{reason}</p>
        )}
        {error && <p className="mt-3 text-[13.5px] text-live">{error}</p>}

        <div className="mt-5 flex gap-2">
          <Button
            variant={current === 'listening' ? 'danger' : 'primary'}
            size="md"
            onClick={toggle}
            disabled={busy || current === 'transcribing' || !daemon.connected}
          >
            {current === 'listening' ? 'Stop and transcribe' : 'Start dictating'}
          </Button>
          {current !== 'listening' && (
            <Button
              size="md"
              onClick={takeNotes}
              disabled={busy || current === 'transcribing' || !daemon.connected}
            >
              Take notes
            </Button>
          )}
        </div>
      </div>

      <Section title="Where the next transcript lands">
        <Card className="flex items-center justify-between px-4 py-3.5">
          {daemon.focus.app ? (
            <>
              <div className="min-w-0">
                <p className="text-[14.5px] font-medium text-ink">
                  {prettyApp(daemon.focus.app)}
                </p>
                <p className="truncate text-[13.5px] text-muted" title={daemon.focus.title}>
                  {daemon.focus.title || 'No window title'}
                </p>
              </div>
              {/* What will happen to the next transcript here, not merely that
                  this window has focus. The daemon works both out with the
                  same classifier the typing path uses, so this cannot drift
                  from what actually lands in the window. */}
              {daemon.focus.cleanup ? (
                <Badge tone="accent">
                  {SURFACES[daemon.focus.surface] || 'Cleaned up'}
                </Badge>
              ) : (
                <Badge tone="neutral">Verbatim</Badge>
              )}
            </>
          ) : (
            <p className="text-[14px] text-muted">
              No focused window reported yet — it updates as you switch windows.
            </p>
          )}
        </Card>
      </Section>

      {latest && (
        <Section
          title="Last transcript"
          action={
            <Button size="sm" variant="ghost" onClick={retype}>
              Insert it again
            </Button>
          }
        >
          <Card className="px-4 py-3.5">
            <p className="selectable whitespace-pre-line text-[14.5px] leading-relaxed text-ink">
              {latest.text}
            </p>
            {latest.raw && (
              <details className="mt-3 border-t border-line pt-2.5">
                <summary className="cursor-pointer text-[13px] text-faint hover:text-muted">
                  Before cleanup
                </summary>
                <p className="selectable mt-2 text-[13.5px] leading-relaxed text-muted">
                  {latest.raw}
                </p>
              </details>
            )}
          </Card>
        </Section>
      )}

      <Section title="More than dictation">
        <Card className="divide-y divide-line">
          {[
            ['Edit by voice', 'Select text, hold your hotkey with Shift (Alt if your hotkey already uses Shift), and say what to do: “make this more polite”, “translate to Hindi”. With nothing selected, it writes what you ask for.'],
            ['Take back the last dictation', 'Say “scratch that” on its own.'],
            ['Layout as you speak', 'Say “new paragraph”, “new line” or “bullet point” (needs cleanup on).'],
            ['Snippets and corrections', 'Set them up on the Words page.'],
            ['Notes', 'Take notes turns a long ramble into a summary, key points and action items. They go to History, and into the focused window if it isn’t Baatsun.'],
          ].map(([title, body]) => (
            <div key={title} className="px-5 py-3.5">
              <p className="text-[14.5px] font-medium text-ink">{title}</p>
              <p className="mt-0.5 text-[13.5px] leading-snug text-muted">{body}</p>
            </div>
          ))}
        </Card>
      </Section>
    </div>
  )
}
