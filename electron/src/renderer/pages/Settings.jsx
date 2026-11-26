import React, { useEffect, useMemo, useState } from 'react'
import { Card, Button, Field, Select, Toggle, TextInput, Section, Badge } from '../components/ui.jsx'
import { useTimer } from '../lib/useTimer.js'

// Changing any of these means the daemon has to come back up: they are read
// once at startup, and stt_backend in particular decides whether the local
// model is loaded into memory at all. Everything else is re-read per dictation.
// compute_type and hinglish both decide which model is loaded.
const RESTART_KEYS = [
  'hotkey',
  'activation',
  'model_override',
  'stt_backend',
  'compute_type',
  'hinglish',
]

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
const STRENGTH_LABELS = { grammar: 'Proofread only', natural: 'Proofread + light grammar' }
const BEAM_LABELS = { 1: 'Fastest (least accurate)', 3: 'Balanced', 5: 'Most accurate' }
const INSERT_LABELS = {
  paste: 'Paste (instant, restores your clipboard)',
  type: 'Type key by key (slower)',
}

const opts = (values, labels) =>
  values.map((v) => ({ value: v, label: labels?.[v] || v }))

/**
 * A key box with its own Test button.
 *
 * Tests what is currently typed rather than what is saved, so a key can be
 * checked before committing to it — and neither probe costs anything, because
 * both hit a listing endpoint rather than doing real work.
 */
function KeyField({ label, hint, provider, status, value, onChange, placeholder }) {
  const [result, setResult] = useState(null)
  const [testing, setTesting] = useState(false)
  // value is null while the saved key is untouched, '' once it is marked for
  // removal, and the new key otherwise. The saved key itself is never here.
  const typed = value || ''
  const removing = value === ''
  const hasSaved = status?.set && !removing

  const test = async () => {
    setTesting(true)
    setResult(null)
    try {
      setResult(await window.baatsun.verifyKey(provider, typed.trim()))
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
        ) : status?.fromEnv ? (
          'Set by an environment variable, which overrides anything saved here'
        ) : (
          hint
        )
      }
    >
      <div className="flex gap-2">
        <TextInput
          type="password"
          value={typed}
          onChange={(e) => {
            onChange(e.target.value === '' ? null : e.target.value)
            setResult(null)
          }}
          placeholder={
            removing ? 'Will be removed on save' : hasSaved ? `Saved (…${status.hint})` : placeholder
          }
          className="w-[190px] font-mono"
        />
        <Button size="md" onClick={test} disabled={testing || (!typed.trim() && !hasSaved)}>
          {testing ? '…' : 'Test'}
        </Button>
        {status?.set && !status.fromEnv && !typed && (
          <Button size="md" variant="ghost" onClick={() => onChange(removing ? null : '')}>
            {removing ? 'Keep' : 'Remove'}
          </Button>
        )}
      </div>
    </Field>
  )
}

export default function Settings() {
  const [initial, setInitial] = useState(null)
  const [cfg, setCfg] = useState(null)
  const [choices, setChoices] = useState({})
  const [defaultModel, setDefaultModel] = useState('small.en')
  const [multilingualModel, setMultilingualModel] = useState('small')
  const [keys, setKeys] = useState({})
  // null = unchanged; see KeyField.
  const [openaiKey, setOpenaiKey] = useState(null)
  const [sttKey, setSttKey] = useState(null)
  const [loadError, setLoadError] = useState('')
  const [toast, setToast] = useState('')
  const [saving, setSaving] = useState(false)
  // Both toast sites share one timer, so a restart toast can't be cleared
  // early by a save toast's leftover timeout.
  const clearToastLater = useTimer()

  const load = () =>
    window.baatsun
      .loadConfig()
      .then((payload) => {
        setCfg(payload.config)
        setInitial(payload.config)
        setChoices(payload.choices)
        setDefaultModel(payload.defaultModel)
        setMultilingualModel(payload.multilingualModel || 'small')
        setKeys(payload.keys || {})
        setOpenaiKey(null)
        setSttKey(null)
        setLoadError('')
      })
      .catch((err) => setLoadError(err.message))

  useEffect(() => {
    load()
  }, [])

  const keysEdited = openaiKey !== null || sttKey !== null
  const dirty = useMemo(
    () =>
      initial && cfg && (JSON.stringify(initial) !== JSON.stringify(cfg) || keysEdited),
    [initial, cfg, keysEdited],
  )
  const needsRestart = useMemo(
    () =>
      initial &&
      cfg &&
      (RESTART_KEYS.some((k) => initial[k] !== cfg[k]) ||
        // The daemon only picks the cloud backend at startup, and falls back
        // to local when it has no key — so a new key needs a restart.
        (sttKey !== null && cfg.stt_backend === 'elevenlabs')),
    [initial, cfg, sttKey],
  )

  if (!cfg) {
    return (
      <p className="text-[13px] text-ink-500">
        {loadError ? `Could not load settings: ${loadError}` : 'Loading settings…'}
      </p>
    )
  }

  const set = (key) => (value) => setCfg((prev) => ({ ...prev, [key]: value }))

  const save = async () => {
    setSaving(true)
    try {
      try {
        await window.baatsun.saveConfig({
          config: cfg,
          openaiKey: openaiKey === null ? undefined : openaiKey,
          sttKey: sttKey === null ? undefined : sttKey,
        })
      } catch (err) {
        setToast(`Could not save: ${err.message}`)
        return
      }
      // Saved from here on, whatever the restart does.
      const restart = needsRestart
      await load()
      if (restart) {
        try {
          await window.baatsun.restartDaemon()
          setToast('Saved — daemon restarted')
        } catch (err) {
          setToast(`Saved, but the daemon did not restart: ${err.message}`)
        }
      } else {
        setToast('Saved — in effect from your next dictation')
      }
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
                ? 'More accurate and frees ~690 MB of memory, but your audio is sent to ElevenLabs. Long dictations are sent in 20s slices while you talk.'
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
              status={keys.elevenlabs}
              value={sttKey}
              onChange={setSttKey}
              placeholder="sk_…"
            />
          )}

          <Field
            label="Language"
            hint={
              cfg.hinglish
                ? 'Uses the multilingual model, so Hindi/Urdu words are heard rather than guessed. Output is English.'
                : 'English-only model: fastest and most accurate for pure English.'
            }
          >
            <Select
              value={cfg.hinglish ? 'hinglish' : 'en'}
              onChange={(v) => set('hinglish')(v === 'hinglish')}
              options={[
                { value: 'en', label: 'English' },
                { value: 'hinglish', label: 'English + Hinglish' },
              ]}
            />
          </Field>

          {!cloud && (
            <Field label="Accuracy" hint="More accurate settings take a little longer per dictation">
              <Select
                value={String(cfg.beam_size)}
                onChange={(v) => set('beam_size')(Number(v))}
                options={(choices.beam_size || [1, 3, 5]).map((v) => ({
                  value: String(v),
                  label: BEAM_LABELS[v] || String(v),
                }))}
              />
            </Field>
          )}

          <Field label="Insert text by" hint="Paste also works for any script and any length">
            <Select
              value={cfg.insert_method}
              onChange={set('insert_method')}
              options={opts(choices.insert_method || [], INSERT_LABELS)}
            />
          </Field>

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
                {
                  value: '',
                  label: `${cfg.hinglish ? multilingualModel : defaultModel} (recommended)`,
                },
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
        description="Fixes punctuation, capitalisation, filler words and your own spoken corrections (“3, no, 4” → “4”), keeping your wording. Only the transcribed text is sent — your audio never leaves this machine."
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
                hint="Neither level rephrases what you said"
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
              <KeyField
                label="OpenAI API key"
                hint="Stored 0600 in its own file, never in config.json"
                provider="openai"
                status={keys.openai}
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
                <Button
                  variant="ghost"
                  onClick={() => {
                    setCfg(initial)
                    setOpenaiKey(null)
                    setSttKey(null)
                  }}
                  disabled={saving}
                >
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
