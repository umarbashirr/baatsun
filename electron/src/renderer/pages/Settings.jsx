import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Card, Button, Field, Select, Toggle, TextInput, Section, Badge } from '../components/ui.jsx'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from '../components/dialog.jsx'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogAction,
  AlertDialogCancel,
} from '../components/alert-dialog.jsx'

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

// Each choice is { label, hint?, keys? }. The label is what the closed control
// shows, so it stays short; the hint explains the choice in the open list.
const HOTKEY_LABELS = {
  'ctrl+super': { label: 'Ctrl + Super', keys: ['Ctrl', 'Super'], hint: 'The default' },
  'ctrl+alt': { label: 'Ctrl + Alt', keys: ['Ctrl', 'Alt'] },
  'alt+super': { label: 'Alt + Super', keys: ['Alt', 'Super'] },
  'ctrl+shift': { label: 'Ctrl + Shift', keys: ['Ctrl', 'Shift'] },
}
const ACTIVATION_LABELS = {
  hold: { label: 'Hold to talk', hint: 'Speak while the keys are held, let go to finish' },
  toggle: { label: 'Press to start and stop', hint: 'Press once to start, again to finish' },
  hybrid: { label: 'Tap or hold', hint: 'A quick tap toggles; holding works like hold to talk' },
}
const BACKEND_LABELS = {
  local: { label: 'This machine', hint: 'Offline. Your audio never leaves the computer' },
  elevenlabs: { label: 'ElevenLabs Scribe v2', hint: 'Cloud. More accurate, but sends your audio' },
}
const SCOPE_LABELS = {
  prose: { label: 'Prose windows only', hint: 'Terminals and code editors stay verbatim' },
  all: { label: 'Everything I dictate', hint: 'Including terminals and editors' },
}
const STRENGTH_LABELS = {
  grammar: { label: 'Proofread only', hint: 'Punctuation, fillers and stutters' },
  natural: { label: 'Proofread + light grammar', hint: 'Also fixes small grammar slips' },
}
const BEAM_LABELS = {
  1: { label: 'Fastest', hint: 'Least accurate' },
  3: { label: 'Balanced' },
  5: { label: 'Most accurate', hint: 'A little slower per dictation' },
}
const CLEANUP_MODEL_LABELS = {
  'gpt-6-luna': { label: 'GPT-6 Luna', hint: 'Recommended. Fast, inexpensive, and it reasons' },
  'gpt-4o-mini': { label: 'GPT-4o mini', hint: 'Older and quickest; no reasoning' },
  'gpt-4.1-mini': { label: 'GPT-4.1 mini', hint: 'Older; no reasoning' },
  'gpt-4.1-nano': { label: 'GPT-4.1 nano', hint: 'Older and cheapest; no reasoning' },
  'gpt-4o': { label: 'GPT-4o', hint: 'Older and much pricier' },
  'gpt-4.1': { label: 'GPT-4.1', hint: 'Older and much pricier' },
}
// How hard a reasoning model thinks before correcting. Proofreading doesn't
// need much; higher is slower and bills the thinking as output tokens.
const REASONING_LABELS = {
  none: { label: 'None', hint: 'Fastest. Answers without thinking first' },
  low: { label: 'Low', hint: 'The default. Quick, with a little thought' },
  medium: { label: 'Medium', hint: 'More thought, a few seconds slower' },
  high: { label: 'High', hint: 'Slowest. Rarely needed for dictation' },
}
const isReasoningModel = (m) => /^(gpt-6|gpt-5|o1|o3|o4)/.test(m || '')

const INSERT_LABELS = {
  paste: { label: 'Paste', hint: 'Instant, and your clipboard is put back' },
  type: { label: 'Type key by key', hint: 'Slower, especially for long text' },
}

// A spinner that flashes for a frame and turns into a tick reads as a glitch.
// Hold the working state long enough to be seen; real work is never slowed.
const atLeast = (promise, ms = 450) =>
  Promise.all([promise, new Promise((r) => setTimeout(r, ms))]).then(([v]) => v)

function StepIcon({ status }) {
  if (status === 'working') {
    return <span className="h-[18px] w-[18px] animate-spin rounded-full border-2 border-green/20 border-t-green" />
  }
  if (status === 'done' || status === 'failed') {
    return (
      <span
        className={`grid h-[20px] w-[20px] place-items-center rounded-full ${
          status === 'done' ? 'bg-green text-cream' : 'bg-live text-white'
        }`}
      >
        <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d={status === 'done' ? 'M20 6 9 17l-5-5' : 'M18 6 6 18M6 6l12 12'} />
        </svg>
      </span>
    )
  }
  return <span className="mx-[5px] h-2.5 w-2.5 rounded-full border-2 border-sand-2" />
}

/**
 * The installed version, and a way to look for a newer one without waiting
 * for the next launch. Finding one opens the same dialog the launch check does.
 */
function UpdatesSection({ updater }) {
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')
  const info = updater?.info

  const check = async () => {
    setChecking(true)
    setError('')
    try {
      const result = await updater.check()
      if (result?.available) updater.open()
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setChecking(false)
    }
  }

  let hint = 'Baatsun checks for a new version each time it opens'
  if (error) hint = error
  else if (info?.supported === false) hint = 'Updates are only available for the .deb install'
  else if (info?.available) hint = `Version ${info.latest} is available`
  else if (info?.supported) hint = 'You’re on the latest version'

  return (
    <Section title="Updates">
      <Card>
        <Field label={info?.current ? `Baatsun ${info.current}` : 'Baatsun'} hint={hint}>
          {info?.available ? (
            <Button variant="primary" onClick={() => updater.open()}>
              Update to {info.latest}
            </Button>
          ) : (
            <Button onClick={check} disabled={checking || info?.supported === false}>
              {checking ? 'Checking…' : 'Check for updates'}
            </Button>
          )}
        </Field>
      </Card>
    </Section>
  )
}

const COPY = {
  save: {
    working: (restart) => ({
      title: 'Saving your settings',
      body: restart
        ? 'Then the daemon restarts to pick them up. Dictation pauses for a moment.'
        : 'This only takes a moment.',
    }),
    done: (restart) => ({
      title: 'Settings saved',
      body: restart
        ? 'The daemon restarted and is running with your new settings.'
        : 'Your changes take effect from your next dictation.',
    }),
    partial: (_, error) => ({
      title: 'Saved, but the daemon didn’t restart',
      body: 'Your changes are saved, but they won’t apply until the daemon restarts.',
      detail: error,
    }),
    error: (_, error) => ({
      title: 'Couldn’t save your settings',
      body: 'Your changes are still here, so you can try again.',
      detail: error,
    }),
  },
  restart: {
    working: () => ({ title: 'Restarting the daemon', body: 'Dictation pauses for a moment.' }),
    done: () => ({ title: 'Daemon restarted', body: 'It’s running again and ready for your next dictation.' }),
    error: (_, error) => ({
      title: 'The daemon didn’t restart',
      body: 'Dictation won’t work until it’s running again.',
      detail: error,
    }),
  },
}

/**
 * Saving and restarting, shown as a dialog in the middle of the window.
 *
 * Each step gets its own line so a two-part save (write the config, restart
 * the daemon) says which part is happening, and which part failed. While
 * anything is running the dialog can't be dismissed: closing it would hide
 * work that is still going on, not stop it.
 */
function OperationDialog({ op, onClose, onRetry, onRetryRestart, returnFocus }) {
  const working = op && !op.outcome
  const phase = op ? op.outcome || 'working' : 'working'
  // Once the settings are written, a save that is still working is only
  // restarting the daemon (including a retry), so say that.
  const restarting =
    op && phase === 'working' && op.kind === 'save' && op.steps[0].status === 'done'
  const copy = !op
    ? { title: '', body: '' }
    : restarting
      ? COPY.restart.working()
      : COPY[op.kind][phase](op.restart, op.error || '')
  const keepOpen = (e) => working && e.preventDefault()

  return (
    <Dialog open={!!op} onOpenChange={(open) => !open && !working && onClose()}>
      <DialogContent
        onEscapeKeyDown={keepOpen}
        onPointerDownOutside={keepOpen}
        onInteractOutside={keepOpen}
        // Radix returns focus to whatever opened the dialog, but the Save
        // button that did is gone once nothing is left to save. Fall back to
        // the page, so keyboard users land where they were rather than at
        // the top of the window.
        onCloseAutoFocus={(e) => {
          e.preventDefault()
          returnFocus?.()
        }}
        aria-busy={working}
      >
        <DialogHeader>
          <DialogTitle>{copy.title}</DialogTitle>
          <DialogDescription>{copy.body}</DialogDescription>
          {copy.detail && (
            <p className="selectable mt-1 rounded-[12px] bg-live/8 px-3.5 py-2.5 text-[13.5px] leading-snug text-live">
              {copy.detail}
            </p>
          )}
        </DialogHeader>

        {op && (
          <ol className="mt-6 flex flex-col gap-3 rounded-[16px] bg-cream px-4 py-3.5" aria-live="polite">
            {op.steps.map((step) => (
              <li key={step.id} className="flex items-center gap-3 text-[14.5px]">
                <span className="grid h-5 w-5 place-items-center">
                  <StepIcon status={step.status} />
                </span>
                <span className={step.status === 'pending' ? 'text-faint' : 'text-ink'}>{step.label}</span>
                <span className="sr-only">
                  {{ pending: 'waiting', working: 'in progress', done: 'done', failed: 'failed' }[step.status]}
                </span>
              </li>
            ))}
          </ol>
        )}

        {!working && (
          <DialogFooter>
            {phase === 'done' && (
              <Button variant="primary" onClick={onClose} autoFocus>
                Done
              </Button>
            )}
            {phase === 'error' && (
              <Button variant="primary" onClick={onRetry} autoFocus>
                Try again
              </Button>
            )}
            {phase === 'partial' && (
              <Button variant="primary" onClick={onRetryRestart} autoFocus>
                Try restart again
              </Button>
            )}
            {phase !== 'done' && (
              <Button variant="ghost" onClick={onClose}>
                Close
              </Button>
            )}
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  )
}

const labelOf = (map, v) => {
  const l = map[v]
  return (typeof l === 'object' ? l.label : l) || String(v)
}
const onOff = (v) => (v ? 'On' : 'Off')

// Every setting the page edits, by the name its row uses, with how to say
// its value. The order is the page's order.
const DESCRIBE = [
  ['display_name', 'Display name', (v) => v || 'Your system name'],
  ['hotkey', 'Hotkey', (v) => labelOf(HOTKEY_LABELS, v)],
  ['activation', 'Activation', (v) => labelOf(ACTIVATION_LABELS, v)],
  ['stt_backend', 'Transcribe with', (v) => labelOf(BACKEND_LABELS, v)],
  ['hinglish', 'Language', (v) => (v ? 'English + Hinglish' : 'English')],
  ['beam_size', 'Accuracy', (v) => labelOf(BEAM_LABELS, v)],
  ['insert_method', 'Insert text by', (v) => labelOf(INSERT_LABELS, v)],
  ['model_override', 'Local model', (v, ctx) => v || `${ctx.recommended} (recommended)`],
  ['compute_type', 'Compute type', String],
  ['cleanup_enabled', 'Clean up transcripts', onOff],
  ['cleanup_scope', 'Apply to', (v) => labelOf(SCOPE_LABELS, v)],
  ['cleanup_strength', 'Correction level', (v) => labelOf(STRENGTH_LABELS, v)],
  ['cleanup_model', 'Cleanup model', (v) => labelOf(CLEANUP_MODEL_LABELS, v)],
  ['cleanup_reasoning', 'Reasoning', (v) => labelOf(REASONING_LABELS, v)],
  ['line_breaks', 'Break long text into paragraphs', onOff],
]

function describeChanges(initial, cfg, keys, ctx) {
  const rows = DESCRIBE.filter(([k]) => initial[k] !== cfg[k]).map(([k, label, say]) => ({
    label,
    from: say(initial[k], ctx),
    to: say(cfg[k], ctx),
  }))
  for (const [value, label] of [
    [keys.openai, 'OpenAI API key'],
    [keys.elevenlabs, 'ElevenLabs API key'],
  ]) {
    if (value === '') rows.push({ label, to: 'Will be removed' })
    else if (value) rows.push({ label, to: 'New key entered' })
  }
  return rows
}

/**
 * Unsaved changes, asked about in the middle of the window.
 *
 * Opens after a change (a pick in a menu or a switch at once; a text field
 * when you leave it, not on every keystroke). It lists exactly what changed
 * and warns when saving restarts the daemon. Keep editing closes it so you
 * can change several things; the next change brings it back with all of them.
 */
function UnsavedDialog({ open, onOpenChange, changes, needsRestart, onSave, onDiscard }) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {needsRestart ? 'Save and restart the daemon?' : 'Save your changes?'}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {needsRestart
              ? 'Some of these only take effect after the daemon restarts. Dictation pauses for a moment while it does.'
              : 'They take effect from your next dictation.'}
          </AlertDialogDescription>
        </AlertDialogHeader>

        <ul className="mt-5 max-h-[220px] divide-y divide-line overflow-y-auto rounded-[16px] bg-cream px-4">
          {changes.map((c) => (
            <li key={c.label} className="flex items-baseline justify-between gap-4 py-2.5 text-[14px]">
              <span className="shrink-0 text-muted">{c.label}</span>
              <span className="min-w-0 text-right">
                {c.from !== undefined && (
                  <>
                    <span className="text-faint line-through decoration-faint/60">{c.from}</span>
                    <span className="mx-1.5 text-faint" aria-label="changed to">→</span>
                  </>
                )}
                <span className="font-medium text-ink">{c.to}</span>
              </span>
            </li>
          ))}
        </ul>

        <AlertDialogFooter className="justify-between">
          <Button variant="ghost" className="-ml-3 text-live hover:bg-live/8 hover:text-live" onClick={onDiscard}>
            Discard
          </Button>
          <div className="flex gap-2">
            <AlertDialogCancel asChild>
              <Button>Keep editing</Button>
            </AlertDialogCancel>
            <AlertDialogAction asChild>
              <Button variant="primary" onClick={onSave}>
                {needsRestart ? 'Save and restart' : 'Save changes'}
              </Button>
            </AlertDialogAction>
          </div>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

const opts = (values, labels) =>
  values.map((v) => {
    const l = labels?.[v]
    return typeof l === 'object' ? { value: v, ...l } : { value: v, label: l || v }
  })

/**
 * A key box with its own Test button.
 *
 * Tests what is currently typed rather than what is saved, so a key can be
 * checked before committing to it — and neither probe costs anything, because
 * both hit a listing endpoint rather than doing real work.
 */
function KeyField({ label, hint, provider, status, value, onChange, onCommit, placeholder }) {
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
          <span className={result.ok ? 'text-good' : 'text-live'}>
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
      {/* Done with the field = focus leaves the whole row, so moving from the
          key box to its own Test button doesn't count. */}
      <div
        className="flex gap-2"
        onBlur={(e) => !e.currentTarget.contains(e.relatedTarget) && onCommit?.(e)}
      >
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
          <Button
            size="md"
            variant="ghost"
            onClick={() => {
              onChange(removing ? null : '')
              onCommit?.()
            }}
          >
            {removing ? 'Keep' : 'Remove'}
          </Button>
        )}
      </div>
    </Field>
  )
}

export default function Settings({ setGuard, updater }) {
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
  const [saving, setSaving] = useState(false)
  // The save/restart dialog: { kind, restart, steps, outcome, error } or null.
  const [op, setOp] = useState(null)
  const pageRef = useRef(null)
  // The unsaved-changes dialog. askTick counts finished edits; each one opens
  // the dialog if anything is actually unsaved (picking the current value, or
  // changing something back, doesn't).
  const [asking, setAsking] = useState(false)
  const [askTick, setAskTick] = useState(0)
  const ask = () => setAskTick((t) => t + 1)
  // Leaving a text field counts as finishing it, with two exceptions: focus
  // going outside the page (the sidebar's leave-without-saving check covers
  // that, and two dialogs at once would be one too many), and the window
  // itself losing focus (alt-tabbing to copy a key isn't "done").
  const askOnLeave = (e) => {
    const to = e?.relatedTarget
    if (to && !pageRef.current?.contains(to)) return
    setTimeout(() => document.hasFocus() && ask(), 0)
  }
  const openerRef = useRef(null)
  const startOp = (next) => {
    if (!op) openerRef.current = document.activeElement
    setOp(next)
  }
  const returnFocus = () => {
    const el = openerRef.current
    ;(el && el.isConnected ? el : pageRef.current)?.focus({ preventScroll: true })
  }

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

  // Tell the app shell whether leaving this page would lose anything.
  const dirtyRef = useRef(false)
  dirtyRef.current = !!dirty
  useEffect(() => {
    setGuard?.(() => dirtyRef.current)
    return () => setGuard?.(null)
  }, [])

  // Open once a menu that made the change has finished closing (its 120ms
  // exit). Otherwise the menu hands focus back to its trigger after the
  // dialog has taken it, and keyboard focus ends up behind the dialog.
  useEffect(() => {
    if (!askTick) return
    const t = setTimeout(() => dirtyRef.current && setAsking(true), 160)
    return () => clearTimeout(t)
  }, [askTick])

  if (!cfg) {
    return (
      <p className="text-[14px] text-faint">
        {loadError ? `Could not load settings: ${loadError}` : 'Loading settings…'}
      </p>
    )
  }

  const set = (key) => (value) => setCfg((prev) => ({ ...prev, [key]: value }))
  // For menus and switches: the change is complete the moment it's made.
  const pick = (key) => (value) => {
    set(key)(value)
    ask()
  }
  const discard = () => {
    setCfg(initial)
    setOpenaiKey(null)
    setSttKey(null)
    setAsking(false)
  }

  const mark = (id, status) =>
    setOp((o) => o && { ...o, steps: o.steps.map((st) => (st.id === id ? { ...st, status } : st)) })
  const finish = (outcome, error = '') => setOp((o) => o && { ...o, outcome, error })

  const restartStep = async () => {
    mark('restart', 'working')
    try {
      await atLeast(window.baatsun.restartDaemon())
      mark('restart', 'done')
      return true
    } catch (err) {
      mark('restart', 'failed')
      return err.message
    }
  }

  const save = async () => {
    const restart = needsRestart
    startOp({
      kind: 'save',
      restart,
      outcome: null,
      error: '',
      steps: [
        { id: 'save', label: 'Save settings', status: 'working' },
        ...(restart ? [{ id: 'restart', label: 'Restart the daemon', status: 'pending' }] : []),
      ],
    })
    setSaving(true)
    try {
      try {
        await atLeast(
          window.baatsun.saveConfig({
            config: cfg,
            openaiKey: openaiKey === null ? undefined : openaiKey,
            sttKey: sttKey === null ? undefined : sttKey,
          }),
        )
      } catch (err) {
        mark('save', 'failed')
        finish('error', err.message)
        return
      }
      // Saved from here on, whatever the restart does.
      mark('save', 'done')
      await load()
      // The sidebar shows the hotkey and activation; tell it they may have changed.
      window.dispatchEvent(new Event('baatsun:config-saved'))
      if (restart) {
        const ok = await restartStep()
        if (ok !== true) return finish('partial', ok)
      }
      finish('done')
    } finally {
      setSaving(false)
    }
  }

  const restartOnly = async () => {
    startOp({
      kind: 'restart',
      restart: true,
      outcome: null,
      error: '',
      steps: [{ id: 'restart', label: 'Restart the daemon', status: 'pending' }],
    })
    const ok = await restartStep()
    finish(ok === true ? 'done' : 'error', ok === true ? '' : ok)
  }

  const cloud = cfg.stt_backend === 'elevenlabs'

  return (
    <div ref={pageRef} tabIndex={-1} className="mx-auto max-w-[760px] pb-6 outline-none">
      <Section title="Profile">
        <Card>
          <Field label="Display name" hint="What the Home page greets you by">
            <TextInput
              value={cfg.display_name}
              onChange={(e) => set('display_name')(e.target.value)}
              onBlur={askOnLeave}
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
              onChange={pick('hotkey')}
              options={opts(choices.hotkey || [], HOTKEY_LABELS)}
            />
          </Field>
          <Field
            label="Activation"
            hint="Hold is the only one where an accidental brush of the chord ends itself"
          >
            <Select
              value={cfg.activation}
              onChange={pick('activation')}
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
              onChange={pick('stt_backend')}
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
              onCommit={askOnLeave}
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
              onChange={(v) => pick('hinglish')(v === 'hinglish')}
              options={[
                { value: 'en', label: 'English', hint: 'English-only model, the fastest' },
                {
                  value: 'hinglish',
                  label: 'English + Hinglish',
                  hint: 'Hears Hindi and Urdu words; the output stays English',
                },
              ]}
            />
          </Field>

          {!cloud && (
            <Field label="Accuracy" hint="More accurate settings take a little longer per dictation">
              <Select
                value={String(cfg.beam_size)}
                onChange={(v) => pick('beam_size')(Number(v))}
                options={(choices.beam_size || [1, 3, 5]).map((v) => ({
                  value: String(v),
                  ...(BEAM_LABELS[v] || { label: String(v) }),
                }))}
              />
            </Field>
          )}

          <Field label="Insert text by" hint="Paste also works for any script and any length">
            <Select
              value={cfg.insert_method}
              onChange={pick('insert_method')}
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
              onChange={pick('model_override')}
              options={[
                {
                  value: '',
                  label: `${cfg.hinglish ? multilingualModel : defaultModel} (recommended)`,
                  hint: 'Picked for the language you set above',
                },
                ...opts(choices.model || []),
              ]}
            />
          </Field>
          <Field label="Compute type">
            <Select
              value={cfg.compute_type}
              onChange={pick('compute_type')}
              options={opts(choices.compute_type || [])}
            />
          </Field>
        </Card>

        {cloud && (
          <p className="mt-2.5 flex items-start gap-2 text-[13.5px] leading-relaxed text-muted">
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
              onChange={pick('cleanup_enabled')}
              label="Clean up transcripts"
            />
          </Field>

          {cfg.cleanup_enabled && (
            <>
              <Field label="Apply to" hint="Prose only: terminals and editors stay verbatim">
                <Select
                  value={cfg.cleanup_scope}
                  onChange={pick('cleanup_scope')}
                  options={opts(choices.cleanup_scope || [], SCOPE_LABELS)}
                />
              </Field>
              <Field
                label="Correction level"
                hint="Neither level rephrases what you said"
              >
                <Select
                  value={cfg.cleanup_strength}
                  onChange={pick('cleanup_strength')}
                  options={opts(choices.cleanup_strength || [], STRENGTH_LABELS)}
                />
              </Field>
              <Field label="Model" hint="Only the transcript text is sent to OpenAI">
                <Select
                  value={cfg.cleanup_model}
                  onChange={pick('cleanup_model')}
                  options={opts(choices.cleanup_model || [], CLEANUP_MODEL_LABELS)}
                />
              </Field>
              {isReasoningModel(cfg.cleanup_model) && (
                <Field
                  label="Reasoning"
                  hint="How much the model thinks before correcting. Higher is slower, and the thinking is billed"
                >
                  <Select
                    value={cfg.cleanup_reasoning || 'low'}
                    onChange={pick('cleanup_reasoning')}
                    options={opts(choices.cleanup_reasoning || ['none', 'low', 'medium', 'high'], REASONING_LABELS)}
                  />
                </Field>
              )}
              <Field
                label="Break long text into paragraphs"
                hint="Never in chat apps, where Enter would send the message"
              >
                <Toggle
                  checked={cfg.line_breaks}
                  onChange={pick('line_breaks')}
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
                onCommit={askOnLeave}
                placeholder="sk-…"
              />
            </>
          )}
        </Card>
      </Section>

      <UpdatesSection updater={updater} />

      <Section title="Daemon">
        <Card>
          <Field label="baatsun.service" hint="Restart if dictation stops responding">
            <Button onClick={restartOnly}>Restart</Button>
          </Field>
        </Card>
      </Section>

      <UnsavedDialog
        open={asking && !!dirty}
        onOpenChange={setAsking}
        changes={
          dirty
            ? describeChanges(initial, cfg, { openai: openaiKey, elevenlabs: sttKey }, {
                recommended: cfg.hinglish ? multilingualModel : defaultModel,
              })
            : []
        }
        needsRestart={needsRestart}
        onSave={save}
        onDiscard={discard}
      />

      <OperationDialog
        op={op}
        returnFocus={returnFocus}
        onClose={() => setOp(null)}
        onRetry={() => (op?.kind === 'restart' ? restartOnly() : save())}
        onRetryRestart={async () => {
          setOp((o) => o && { ...o, outcome: null, error: '' })
          const ok = await restartStep()
          finish(ok === true ? 'done' : 'partial', ok === true ? '' : ok)
        }}
      />
    </div>
  )
}
