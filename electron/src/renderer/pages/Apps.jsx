import React, { useCallback, useEffect, useState } from 'react'
import { Card, Button, Badge, Empty, Select, TextInput, Section } from '../components/ui.jsx'
import { prettyApp } from '../lib/stats.js'

const SURFACE_OPTIONS = [
  { value: 'email', label: 'Email', hint: 'Greeting and sign-off on their own lines, paragraphs' },
  { value: 'chat', label: 'Chat', hint: 'One line, Enter sends' },
  { value: 'post', label: 'Post', hint: 'X, Threads: short, scannable' },
  { value: 'social', label: 'Feed post', hint: 'LinkedIn, Facebook: longer post' },
  { value: 'docs', label: 'Document', hint: 'Written prose in paragraphs' },
  { value: 'code', label: 'Code', hint: 'Terminals and editors: kept verbatim' },
]
const surfaceLabel = (v) => SURFACE_OPTIONS.find((o) => o.value === v)?.label || v

/**
 * How each window is treated, and your own rules for the ones Baatsun gets
 * wrong. Rules are saved straight to config.json; the daemon reads them on
 * every dictation, so nothing restarts.
 */
export default function Apps({ daemon }) {
  const [rules, setRules] = useState([])
  const [windows, setWindows] = useState([])
  const [error, setError] = useState('')
  const [match, setMatch] = useState('')
  const [surface, setSurface] = useState('email')

  const loadWindows = useCallback(
    () =>
      window.baatsun
        .windows()
        .then((w) => setWindows(Array.isArray(w) ? w : []))
        .catch(() => setWindows([])),
    [],
  )

  useEffect(() => {
    window.baatsun
      .loadConfig()
      .then(({ config }) => setRules(Array.isArray(config.app_rules) ? config.app_rules : []))
      .catch(() => setRules([]))
  }, [])
  // Refresh as you switch windows: the list is "what you've just used".
  useEffect(() => {
    loadWindows()
  }, [loadWindows, daemon.focus.app, daemon.focus.title, daemon.connected])

  const save = async (next) => {
    const previous = rules
    setRules(next)
    setError('')
    try {
      await window.baatsun.saveConfig({ config: { app_rules: next } })
      await window.baatsun.refocus().catch(() => {})
      loadWindows()
    } catch (err) {
      setRules(previous)
      setError(`Could not save: ${err.message}`)
    }
  }

  const setRule = (m, s) => {
    const key = m.trim().toLowerCase()
    if (!key) return
    save([...rules.filter((r) => r.match.trim().toLowerCase() !== key), { match: m.trim(), surface: s }])
  }

  return (
    <div className="mx-auto max-w-[760px]">
      <div className="mb-6">
        <h2 className="font-serif text-[42px] leading-[1.05] text-ink">Apps</h2>
        <p className="mt-1.5 max-w-lg text-[14.5px] leading-relaxed text-muted">
          Baatsun lays text out for the kind of place it lands: an email, a chat, a post. If it
          gets a window wrong, change it here and it sticks.
        </p>
      </div>

      {error && <p className="mb-4 text-[13px] text-live">{error}</p>}

      <Section title="Recent windows" description="The windows you’ve switched to since the daemon started.">
        {windows.length === 0 ? (
          <Empty title="No windows yet">Switch to the apps you dictate into and they’ll show up here.</Empty>
        ) : (
          <Card className="divide-y divide-line">
            {windows.map((w) => (
              <div key={w.match} className="flex items-center gap-4 px-5 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[15px] font-medium text-ink">
                    {w.match === w.app ? prettyApp(w.app) : w.match}
                  </p>
                  <p className="text-[13px] text-muted">
                    {prettyApp(w.app)}
                    {w.ruled && ' · your rule'}
                  </p>
                </div>
                <Select
                  value={w.surface}
                  onChange={(v) => setRule(w.match, v)}
                  options={SURFACE_OPTIONS}
                  aria-label={`How ${w.match} is treated`}
                />
              </div>
            ))}
          </Card>
        )}
      </Section>

      <Section
        title="Your rules"
        description="Matched against the window title (whole words) or the app’s name, before anything built in."
      >
        <Card className="p-4">
          <div className="flex flex-wrap items-center gap-2">
            <TextInput
              value={match}
              onChange={(e) => setMatch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && match.trim()) {
                  setRule(match, surface)
                  setMatch('')
                }
              }}
              placeholder="Part of the window title, like “Company Mail”"
              className="min-w-0 flex-1"
              aria-label="Window title to match"
            />
            <Select value={surface} onChange={setSurface} options={SURFACE_OPTIONS} aria-label="Treat it as" />
            <Button
              variant="primary"
              disabled={!match.trim()}
              onClick={() => {
                setRule(match, surface)
                setMatch('')
              }}
            >
              Add rule
            </Button>
          </div>
          {rules.length > 0 && (
            <ul className="mt-3 divide-y divide-line">
              {rules.map((r) => (
                <li key={r.match} className="flex items-center gap-3 py-2">
                  <span className="selectable min-w-0 flex-1 truncate text-[14.5px] text-ink">“{r.match}”</span>
                  <Badge tone="accent">{surfaceLabel(r.surface)}</Badge>
                  <Button size="sm" variant="ghost" onClick={() => save(rules.filter((x) => x !== r))}>
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </Section>
    </div>
  )
}
