import React, { useMemo, useState } from 'react'
import { Card, Button, Badge, Empty, TextInput } from '../components/ui.jsx'
import { groupByDay, prettyApp } from '../lib/stats.js'
import { useTimer } from '../lib/useTimer.js'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from '../components/dialog.jsx'

// Entries that aren't plain dictation, and what their "raw" text was.
const MODES = {
  snippet: { badge: 'snippet', raw: 'What you said' },
  edit: { badge: 'voice edit', raw: 'Instruction' },
  compose: { badge: 'written by voice', raw: 'Instruction' },
  notes: { badge: 'notes', raw: 'Transcript' },
}

/**
 * Teach a correction: "heard X, meant Y". Saved to config's corrections list,
 * which the daemon applies to every transcript from the next one on.
 */
function FixDialog({ open, heard: initialHeard, onClose }) {
  const [heard, setHeard] = useState('')
  const [correct, setCorrect] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  React.useEffect(() => {
    if (open) {
      setHeard(initialHeard || '')
      setCorrect('')
      setError('')
    }
  }, [open, initialHeard])

  const save = async () => {
    const h = heard.trim()
    const c = correct.trim()
    if (!h || !c) return
    setSaving(true)
    setError('')
    try {
      const { config } = await window.baatsun.loadConfig()
      const list = Array.isArray(config.corrections) ? config.corrections : []
      const next = [...list.filter((i) => i.heard.toLowerCase() !== h.toLowerCase()), { heard: h, correct: c }]
      await window.baatsun.saveConfig({ config: { corrections: next } })
      onClose()
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Fix a misheard word</DialogTitle>
          <DialogDescription>
            From your next dictation on, Baatsun replaces this wherever it’s heard. Manage these on the Words page.
          </DialogDescription>
        </DialogHeader>
        <div className="mt-5 flex flex-col gap-3">
          <label className="flex flex-col gap-1.5 text-[13.5px] font-medium text-muted">
            It was heard as
            <TextInput value={heard} onChange={(e) => setHeard(e.target.value)} placeholder="ground mainly" />
          </label>
          <label className="flex flex-col gap-1.5 text-[13.5px] font-medium text-muted">
            It should be
            <TextInput
              autoFocus
              value={correct}
              onChange={(e) => setCorrect(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && save()}
              placeholder="grant me leave"
            />
          </label>
          {error && <p className="text-[13px] text-live">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="primary" onClick={save} disabled={saving || !heard.trim() || !correct.trim()}>
            Save correction
          </Button>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'cleaned', label: 'Cleaned' },
  { id: 'verbatim', label: 'Verbatim' },
]

function timeOf(ts) {
  return new Date((ts || 0) * 1000).toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  })
}

function Entry({ entry, onRetype, onDelete, onFix }) {
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const textRef = React.useRef(null)
  const fixRef = React.useRef('')
  // Rows unmount on every keystroke in the search box, so this timer outlives
  // its component more often than not.
  const clearLater = useTimer()

  // Every action reports its own failure on the row; an unhandled rejection
  // here used to fail silently.
  const run = (fn) => async () => {
    setError('')
    try {
      await fn()
    } catch (err) {
      setError(err.message || String(err))
      clearLater(() => setError(''), 4000)
    }
  }

  const copy = run(async () => {
    await navigator.clipboard.writeText(entry.text || '')
    setCopied(true)
    clearLater(() => setCopied(false), 1400)
  })

  return (
    <div className="group px-4 py-3 transition-colors hover:bg-sand/60">
      <div className="mb-1.5 flex items-center gap-2 text-[12.5px] text-faint">
        <span className="tabular-nums">{timeOf(entry.ts)}</span>
        {entry.app && <span className="text-faint">·</span>}
        {entry.app && <span>{prettyApp(entry.app)}</span>}
        {MODES[entry.mode] ? (
          <Badge tone="accent">{MODES[entry.mode].badge}</Badge>
        ) : (
          entry.raw && <Badge tone="accent">cleaned</Badge>
        )}
        {entry.secs != null && (
          <>
            <span className="text-faint">·</span>
            <span className="tabular-nums">{entry.secs}s</span>
          </>
        )}

        <div className="ml-auto flex items-center gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
          <Button size="sm" variant="ghost" onClick={copy}>
            {copied ? 'Copied' : 'Copy'}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            title="Select the misheard words first"
            // On mousedown, before the click can collapse the selection.
            onMouseDown={() => {
              const sel = window.getSelection()
              const text = sel ? sel.toString().trim() : ''
              const inside = sel?.anchorNode && textRef.current?.contains(sel.anchorNode)
              fixRef.current = inside ? text : ''
            }}
            onClick={() => onFix(fixRef.current)}
          >
            Fix
          </Button>
          <Button size="sm" variant="ghost" onClick={run(() => onRetype(entry.id))}>
            Insert
          </Button>
          <Button size="sm" variant="ghost" onClick={run(() => onDelete(entry.id))}>
            Delete
          </Button>
        </div>
      </div>

      <p ref={textRef} className="selectable whitespace-pre-line text-[14.5px] leading-relaxed text-ink">
        {entry.text}
      </p>
      {error && <p className="mt-1 text-[13px] text-live">{error}</p>}

      {entry.raw && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[12.5px] text-faint hover:text-muted">
            {MODES[entry.mode]?.raw || 'Before cleanup'}
          </summary>
          <p className="selectable mt-1.5 whitespace-pre-line border-l-2 border-line pl-3 text-[13.5px] leading-relaxed text-muted">
            {entry.raw}
          </p>
        </details>
      )}
    </div>
  )
}

export default function History({ daemon }) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [confirmClear, setConfirmClear] = useState(false)
  const [clearError, setClearError] = useState('')
  const [fixing, setFixing] = useState(null)
  const resetConfirmLater = useTimer()

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const matched = daemon.entries.filter((entry) => {
      if (filter === 'cleaned' && !entry.raw) return false
      if (filter === 'verbatim' && entry.raw) return false
      if (!needle) return true
      return (entry.text || '').toLowerCase().includes(needle)
    })
    // Newest first: the thing you just said is the thing you want.
    return groupByDay([...matched].reverse())
  }, [daemon.entries, query, filter])

  const total = groups.reduce((sum, [, items]) => sum + items.length, 0)

  return (
    <div className="mx-auto max-w-[820px]">
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <TextInput
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search transcripts…"
          className="min-w-0 flex-1"
          aria-label="Search transcripts"
        />
        <div className="flex rounded-full border border-line bg-card p-1">
          {FILTERS.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              onClick={() => setFilter(id)}
              className={`rounded-full px-4 py-1.5 text-[14px] transition-colors ${
                filter === id ? 'bg-green font-medium text-cream' : 'text-muted hover:text-ink'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {daemon.entries.length > 0 && (
          <Button
            variant={confirmClear ? 'danger' : 'ghost'}
            size="md"
            onClick={async () => {
              if (!confirmClear) {
                setConfirmClear(true)
                resetConfirmLater(() => setConfirmClear(false), 4000)
                return
              }
              try {
                await daemon.clear()
                setClearError('')
              } catch (err) {
                setClearError(`Could not clear history: ${err.message}`)
              } finally {
                setConfirmClear(false)
              }
            }}
          >
            {confirmClear ? 'Really clear all?' : 'Clear'}
          </Button>
        )}
      </div>
      {clearError && <p className="mb-3 text-[13px] text-live">{clearError}</p>}

      {total > 0 && (
        <p className="mb-3 text-[13px] text-faint">
          {total} of {daemon.entries.length} shown
        </p>
      )}

      {groups.length === 0 ? (
        <Empty title={query || filter !== 'all' ? 'Nothing matches' : 'No dictations yet'}>
          {query || filter !== 'all'
            ? 'Try a different search, or clear the filter.'
            : 'Everything you dictate shows up here, grouped by day.'}
        </Empty>
      ) : (
        groups.map(([label, items]) => (
          <section key={label} className="mb-6">
            <h3 className="mb-2.5 font-serif text-[20px] text-ink">
              {label}
            </h3>
            <Card className="divide-y divide-line overflow-hidden">
              {items.map((entry) => (
                <Entry
                  key={entry.id}
                  entry={entry}
                  onRetype={daemon.retype}
                  onDelete={daemon.remove}
                  onFix={(heard) => setFixing({ heard })}
                />
              ))}
            </Card>
          </section>
        ))
      )}

      <FixDialog open={!!fixing} heard={fixing?.heard} onClose={() => setFixing(null)} />
    </div>
  )
}
