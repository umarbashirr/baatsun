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
}

/**
 * The recording orb.
 *
 * The pill's idea, at window scale: one shape that opens rather than a control
 * that changes colour to mean something new. Rings expand while recording so
 * the state is legible from across a desk.
 */
function Orb({ state, onToggle }) {
  const recording = state === 'listening'
  const transcribing = state === 'transcribing'

  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={transcribing}
      aria-label={recording ? 'Stop recording' : 'Start recording'}
      className="no-drag group relative grid h-[132px] w-[132px] place-items-center disabled:cursor-wait"
    >
      {recording && (
        <>
          <span className="animate-live absolute inset-0 rounded-full bg-live-500/10" />
          <span
            className="animate-live absolute inset-[14px] rounded-full bg-live-500/15"
            style={{ animationDelay: '0.3s' }}
          />
        </>
      )}
      <span
        className={`relative grid h-[92px] w-[92px] place-items-center rounded-full border transition-all duration-300 ${
          recording
            ? 'border-live-500/50 bg-live-500/15 scale-105'
            : transcribing
              ? 'border-saffron-500/50 bg-saffron-500/12'
              : 'border-ink-600 bg-ink-800 group-hover:border-ink-500 group-hover:bg-ink-750'
        }`}
      >
        {transcribing ? (
          <svg className="h-7 w-7 animate-spin text-saffron-400" viewBox="0 0 24 24" fill="none">
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
            className={`h-8 w-8 transition-colors ${recording ? 'text-live-500' : 'text-ink-300'}`}
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
  const copy = COPY[current] || COPY.idle
  const reason = daemon.state.reason ? REASONS[daemon.state.reason] : ''

  // The newest transcript is the last one the daemon appended.
  const latest = daemon.entries.length ? daemon.entries[daemon.entries.length - 1] : null

  useEffect(() => {
    // Any state change means the daemon accepted our last toggle, or acted on
    // the hotkey — either way the button is live again.
    setBusy(false)
  }, [daemon.state])

  const toggle = async () => {
    setBusy(true)
    setError('')
    try {
      await daemon.toggle()
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-[720px]">
      <div className="flex flex-col items-center py-6">
        <Orb state={current} onToggle={toggle} />

        <h2 className="mt-6 text-[19px] font-semibold tracking-tight text-ink-50">
          {copy.title}
        </h2>
        <p className="mt-1.5 text-center text-[13.5px] text-ink-400">{copy.body}</p>

        {reason && (
          <p className="mt-3 text-[12.5px] text-ink-500">{reason}</p>
        )}
        {error && <p className="mt-3 text-[12.5px] text-live-500">{error}</p>}

        <Button
          variant={current === 'listening' ? 'danger' : 'default'}
          size="md"
          className="mt-5"
          onClick={toggle}
          disabled={busy || current === 'transcribing' || !daemon.connected}
        >
          {current === 'listening' ? 'Stop and transcribe' : 'Start dictating'}
        </Button>
      </div>

      <Section title="Where the next transcript lands">
        <Card className="flex items-center justify-between px-4 py-3.5">
          {daemon.focus.app ? (
            <>
              <div className="min-w-0">
                <p className="text-[13.5px] font-medium text-ink-100">
                  {prettyApp(daemon.focus.app)}
                </p>
                <p className="truncate text-[12.5px] text-ink-400" title={daemon.focus.title}>
                  {daemon.focus.title || 'No window title'}
                </p>
              </div>
              <Badge tone="neutral">focused</Badge>
            </>
          ) : (
            <p className="text-[13px] text-ink-400">
              No focused window reported yet — it updates as you switch windows.
            </p>
          )}
        </Card>
      </Section>

      {latest && (
        <Section
          title="Last transcript"
          action={
            <Button size="sm" variant="ghost" onClick={() => daemon.retype(latest.id)}>
              Type it again
            </Button>
          }
        >
          <Card className="px-4 py-3.5">
            <p className="selectable text-[13.5px] leading-relaxed text-ink-200">
              {latest.text}
            </p>
            {latest.raw && (
              <details className="mt-3 border-t border-ink-700/60 pt-2.5">
                <summary className="cursor-pointer text-[12px] text-ink-500 hover:text-ink-300">
                  Before cleanup
                </summary>
                <p className="selectable mt-2 text-[12.5px] leading-relaxed text-ink-400">
                  {latest.raw}
                </p>
              </details>
            )}
          </Card>
        </Section>
      )}
    </div>
  )
}
