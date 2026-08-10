import React, { useEffect, useMemo, useState } from 'react'
import { Card, Button, Field, Select, Toggle, TextInput, Section, Badge } from '../components/ui.jsx'
import { useTimer } from '../lib/useTimer.js'

// Changing any of these means the daemon has to come back up: they are read
// once at startup, and stt_backend in particular decides whether the local
// model is loaded into memory at all. Everything else is re-read per dictation.
const RESTART_KEYS = ['hotkey', 'activation', 'model_override', 'stt_backend']

const HOTKEY_LABELS = {
  'ctrl+super': 'Ctrl + Super',
  'ctrl+alt': 'Ctrl + Alt',
  'alt+super': 'Alt + Super',
  'ctrl+shift': 'Ctrl + Shift',
}
const ACTIVATION_LABELS = {
  hold: 'Hold to talk',
  toggle: 'Press to start, press to stop',
  hybrid: 'Tap or hold',
}
const BACKEND_LABELS = {
  local: 'This machine (offline)',
  elevenlabs: 'ElevenLabs Scribe v2 (cloud)',
}
const SCOPE_LABELS = { prose: 'Prose windows only', all: 'Everything I dictate' }
const STRENGTH_LABELS = { grammar: 'Grammar only', natural: 'Natural English' }

const opts = (values, labels) =>
  values.map((v) => ({ value: v, label: labels?.[v] || v }))

/**
 * A key box with its own Test button.
 *
 * Tests what is currently typed rather than what is saved, so a key can be
 * checked before committing to it — and neither probe costs anything, because
 * both hit a listing endpoint rather than doing real work.
 */
function KeyField({ label, hint, provider, value, onChange, placeholder }) {
  const [result, setResult] = useState(null)
  const [testing, setTesting] = useState(false)

  const test = async () => {
    setTesting(true)
    setResult(null)
    try {
      setResult(await window.baatsun.verifyKey(provider, value.trim()))
    } catch (err) {
      setResult({ ok: false, message: err.message })
    } finally {
      setTesting(false)
    }
  }

  return (
    <Field
      label={label}
      hint={
        result ? (
          <span className={result.ok ? 'text-good-500' : 'text-live-500'}>
            {result.ok ? '✓ ' : '✗ '}
            {result.message}
          </span>
        ) : (
          hint
        )
      }
    >
      <div className="flex gap-2">
        <TextInput
          type="password"
          value={value}
          onChange={(e) => {
            onChange(e.target.value)
            setResult(null)
          }}
          placeholder={placeholder}
          className="w-[190px] font-mono"
        />
        <Button size="md" onClick={test} disabled={testing || !value.trim()}>
          {testing ? '…' : 'Test'}
        </Button>
      </div>
    </Field>
  )
}

export default function Settings() {
  const [initial, setInitial] = useState(null)
  const [cfg, setCfg] = useState(null)
  const [choices, setChoices] = useState({})
  const [defaultModel, setDefaultModel] = useState('small.en')
  const [openaiKey, setOpenaiKey] = useState('')
  const [sttKey, setSttKey] = useState('')
  const [toast, setToast] = useState('')
  const [saving, setSaving] = useState(false)
  // Both toast sites share one timer, so a restart toast can't be cleared
  // early by a save toast's leftover timeout.
  const clearToastLater = useTimer()

  useEffect(() => {
    window.baatsun.loadConfig().then((payload) => {
      setCfg(payload.config)
      setInitial(payload.config)
      setChoices(payload.choices)
      setDefaultModel(payload.defaultModel)
      setOpenaiKey(payload.openaiKey)
      setSttKey(payload.sttKey)
    })
  }, [])

  const dirty = useMemo(
    () => initial && cfg && JSON.stringify(initial) !== JSON.stringify(cfg),
    [initial, cfg],
  )
  const needsRestart = useMemo(
    () => initial && cfg && RESTART_KEYS.some((k) => initial[k] !== cfg[k]),
    [initial, cfg],
  )

  if (!cfg) {
    return <p className="text-[13px] text-ink-500">Loading settings…</p>
  }

  const set = (key) => (value) => setCfg((prev) => ({ ...prev, [key]: value }))

  const save = async () => {
    setSaving(true)
    try {
      await window.baatsun.saveConfig({ config: cfg, openaiKey, sttKey })
      if (needsRestart) {
        await window.baatsun.restartDaemon()
        setToast('Saved — daemon restarted')
      } else {
        setToast('Saved — in effect from your next dictation')
      }
      setInitial(cfg)
    } catch (err) {
      setToast(`Could not save: ${err.message}`)
    } finally {
      setSaving(false)
      clearToastLater(() => setToast(''), 4000)
    }
  }

  const cloud = cfg.stt_backend === 'elevenlabs'

  return (
    <div className="mx-auto max-w-[760px] pb-24">
      <Section title="Profile">
        <Card>
          <Field label="Display name" hint="What the Home page greets you by">
            <TextInput
              value={cfg.display_name}
              onChange={(e) => set('display_name')(e.target.value)}
              placeholder="Leave empty to use your system name"
              className="w-[230px]"
            />
          </Field>
        </Card>
      </Section>

      <Section title="Recording">
        <Card>
          <Field label="Hotkey" hint="Read below the compositor, so it works everywhere">
            <Select
              value={cfg.hotkey}
              onChange={set('hotkey')}
              options={opts(choices.hotkey || [], HOTKEY_LABELS)}
            />
          </Field>
          <Field
            label="Activation"
            hint="Hold is the only one where an accidental brush of the chord ends itself"
          >
            <Select
              value={cfg.activation}
              onChange={set('activation')}
              options={opts(choices.activation || [], ACTIVATION_LABELS)}
            />
          </Field>
        </Card>
      </Section>

      <Section
        title="Transcription"
        description="Where your speech is turned into text. On this machine by default, which is the only setting where your audio never leaves it."
      >
        <Card>
          <Field
            label="Transcribe with"
            hint={
              cloud
                ? 'More accurate and frees ~690 MB of memory, but your audio is sent to ElevenLabs on every dictation'
                : 'Runs on your CPU. Nothing you say leaves this machine.'
            }
          >
            <Select
              value={cfg.stt_backend}
              onChange={set('stt_backend')}
              options={opts(choices.stt_backend || [], BACKEND_LABELS)}
            />
          </Field>

          {cloud && (
            <KeyField
              label="ElevenLabs API key"
              hint="Stored 0600 in its own file, never in config.json"
              provider="elevenlabs"
              value={sttKey}
              onChange={setSttKey}
              placeholder="sk_…"
            />
          )}

          <Field
            label="Local model"
            hint={
              cloud
                ? 'Only used by the offline backend'
                : 'Downloads on first use. Bigger is not better here.'
            }
          >
            <Select
              value={cfg.model_override || ''}
              onChange={set('model_override')}
              options={[
                { value: '', label: `${defaultModel} (recommended)` },
                ...opts(choices.model || []),
              ]}
            />
          </Field>
          <Field label="Compute type">
            <Select
              value={cfg.compute_type}
              onChange={set('compute_type')}
              options={opts(choices.compute_type || [])}
            />
          </Field>
        </Card>

        {cloud && (
          <p className="mt-2.5 flex items-start gap-2 text-[12.5px] leading-relaxed text-ink-400">
            <Badge tone="accent">note</Badge>
            <span>
              Your audio leaves this machine while this is selected. Roughly $0.27 per hour
              of speech, and your Words list is sent along as keyterms.
            </span>
          </p>
        )}
      </Section>

      <Section
        title="Cleanup with OpenAI"
        description="Tidies punctuation, capitalisation and filler words before typing. Only the transcribed text is sent — your audio never leaves this machine."
      >
        <Card>
          <Field label="Clean up transcripts" hint="Off until an API key is saved below">
            <Toggle
              checked={cfg.cleanup_enabled}
              onChange={set('cleanup_enabled')}
              label="Clean up transcripts"
            />
          </Field>

          {cfg.cleanup_enabled && (
            <>
              <Field label="Apply to" hint="Prose only: terminals and editors stay verbatim">
                <Select
                  value={cfg.cleanup_scope}
                  onChange={set('cleanup_scope')}
                  options={opts(choices.cleanup_scope || [], SCOPE_LABELS)}
                />
              </Field>
              <Field
                label="Correction level"
                hint="Natural also fixes phrasing a native speaker wouldn't use"
              >
                <Select
                  value={cfg.cleanup_strength}
                  onChange={set('cleanup_strength')}
                  options={opts(choices.cleanup_strength || [], STRENGTH_LABELS)}
                />
              </Field>
              <Field label="Model">
                <Select
                  value={cfg.cleanup_model}
                  onChange={set('cleanup_model')}
                  options={opts(choices.cleanup_model || [])}
                />
              </Field>
              <Field
                label="Break long text into paragraphs"
                hint="Never in chat apps, where Enter would send the message"
              >
                <Toggle
                  checked={cfg.line_breaks}
                  onChange={set('line_breaks')}
                  label="Break long text into paragraphs"
                />
              </Field>
              <Field
                label="I mix Hindi words into my speech"
                hint="Renders garbled Hindi (“K”, “Hummer”) as English"
              >
                <Toggle
                  checked={cfg.hinglish}
                  onChange={set('hinglish')}
                  label="I mix Hindi words into my speech"
                />
              </Field>
              <KeyField
                label="OpenAI API key"
                hint="Stored 0600 in its own file, never in config.json"
                provider="openai"
                value={openaiKey}
                onChange={setOpenaiKey}
                placeholder="sk-…"
              />
            </>
          )}
        </Card>
      </Section>

      <Section title="Daemon">
        <Card>
          <Field label="baatsun.service" hint="Restart if dictation stops responding">
            <Button
              onClick={async () => {
                try {
                  await window.baatsun.restartDaemon()
                  setToast('Daemon restarted')
                } catch (err) {
                  setToast(err.message)
                }
                clearToastLater(() => setToast(''), 4000)
              }}
            >
              Restart
            </Button>
          </Field>
        </Card>
      </Section>

      {/* Pinned rather than inline: Settings is longer than the window, and a
          save button that scrolls out of reach is a save button people miss. */}
      {(dirty || toast) && (
        <div className="animate-rise fixed bottom-0 left-[196px] right-0 border-t border-ink-700/60 bg-ink-900/95 px-8 py-3 backdrop-blur">
          <div className="mx-auto flex max-w-[760px] items-center justify-between gap-4">
            <p className="text-[12.5px] text-ink-400">
              {toast ||
                (needsRestart
                  ? 'Saving will restart the daemon — dictation pauses for a moment.'
                  : 'Unsaved changes')}
            </p>
            {dirty && (
              <div className="flex gap-2">
                <Button variant="ghost" onClick={() => setCfg(initial)} disabled={saving}>
                  Discard
                </Button>
                <Button variant="primary" onClick={save} disabled={saving}>
                  {saving ? 'Saving…' : 'Save changes'}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
