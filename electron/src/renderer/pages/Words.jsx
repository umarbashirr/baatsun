import React, { useEffect, useState } from 'react'
import { Card, Button, Empty, TextInput, Section } from '../components/ui.jsx'
import { useTimer } from '../lib/useTimer.js'

/**
 * The vocabulary list, stored as the one comma-separated string the daemon
 * already reads.
 *
 * It is edited here as chips because that is what it is — a list of names —
 * but written back as the same string baatsun_config.py expects, so the local
 * decoder's initial_prompt, the ElevenLabs keyterms and the cleanup prompt all
 * keep reading the field they already read.
 */
// De-duplicated (case-insensitively), because each chip is keyed by its term:
// a hand-edited "Foo, Foo" would collide, and removing one removed both.
const toList = (value) => {
  const seen = new Set()
  return (value || '')
    .split(',')
    .map((term) => term.trim())
    .filter((term) => {
      const key = term.toLowerCase()
      if (!term || seen.has(key)) return false
      seen.add(key)
      return true
    })
}

const toField = (list) => list.join(', ')

export default function Words() {
  const [terms, setTerms] = useState([])
  const [draft, setDraft] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const clearSavingLater = useTimer()

  useEffect(() => {
    window.baatsun
      .loadConfig()
      .then(({ config }) => setTerms(toList(config.vocabulary)))
      .catch(() => setTerms([]))
      .finally(() => setLoaded(true))
  }, [])

  const persist = async (next) => {
    const previous = terms
    setTerms(next)
    setSaving(true)
    setError('')
    try {
      await window.baatsun.saveConfig({ config: { vocabulary: toField(next) } })
    } catch (err) {
      // Put the chips back to what is actually on disk.
      setTerms(previous)
      setError(`Could not save: ${err.message}`)
    } finally {
      // Brief, but it is the only confirmation that a chip edit reached disk.
      clearSavingLater(() => setSaving(false), 500)
    }
  }

  const add = async () => {
    const value = draft.trim().replace(/,+$/, '')
    if (!value) return
    // A comma in the box means the person pasted a list; take all of it.
    const incoming = toList(value)
    const merged = [...terms]
    for (const term of incoming) {
      if (!merged.some((t) => t.toLowerCase() === term.toLowerCase())) merged.push(term)
    }
    setDraft('')
    await persist(merged)
  }

  return (
    <div className="mx-auto max-w-[720px]">
      <div className="mb-6">
        <h2 className="font-serif text-[42px] leading-[1.05] text-ink">Words</h2>
        <h3 className="mt-6 font-serif text-[22px] leading-tight text-ink">Vocabulary</h3>
        <p className="mt-1.5 max-w-lg text-[14.5px] leading-relaxed text-muted">
          Names the transcriber reliably mishears — your own name, the products you talk
          about, the tools you use. These are fed to the decoder as it listens, which fixes
          them properly. A proofreader asked to correct “Cloud Code” afterwards has to guess
          it was wrong, and mostly doesn’t.
        </p>
      </div>

      <Card className="mb-5 p-4">
        <div className="flex gap-2">
          <TextInput
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') add()
            }}
            placeholder="Add a name, or paste a comma-separated list…"
            className="flex-1"
            aria-label="Add a word"
          />
          <Button variant="primary" onClick={add} disabled={!draft.trim()}>
            Add
          </Button>
        </div>
        <p className="mt-2 text-[12.5px] text-faint">
          {terms.length} term{terms.length === 1 ? '' : 's'}
          {saving && !error && ' · saved'}
        </p>
        {error && <p className="mt-1 text-[13px] text-live">{error}</p>}
      </Card>

      {loaded && terms.length === 0 ? (
        <Empty title="No words yet">
          Add the names you say often that come out wrong. They take effect on your next
          dictation — no restart needed.
        </Empty>
      ) : (
        <div className="flex flex-wrap gap-2">
          {terms.map((term) => (
            <span
              key={term}
              className="group inline-flex items-center gap-1.5 rounded-full bg-mint py-1.5 pl-3.5 pr-1.5 text-[14px] font-medium text-green"
            >
              <span className="selectable">{term}</span>
              <button
                type="button"
                aria-label={`Remove ${term}`}
                onClick={() => persist(terms.filter((t) => t !== term))}
                className="grid h-6 w-6 place-items-center rounded-full text-green/60 transition-colors hover:bg-card hover:text-live"
              >
                <svg
                  viewBox="0 0 24 24"
                  className="h-3 w-3"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                >
                  <path d="M6 6l12 12M18 6 6 18" />
                </svg>
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="mt-10">
        <Corrections />
        <Snippets />
      </div>
    </div>
  )
}

/** A list stored in config.json, saved as soon as it changes. */
function useConfigList(key) {
  const [items, setItems] = useState([])
  const [error, setError] = useState('')
  useEffect(() => {
    window.baatsun
      .loadConfig()
      .then(({ config }) => setItems(Array.isArray(config[key]) ? config[key] : []))
      .catch(() => setItems([]))
  }, [key])
  const save = async (next) => {
    const previous = items
    setItems(next)
    setError('')
    try {
      await window.baatsun.saveConfig({ config: { [key]: next } })
    } catch (err) {
      setItems(previous)
      setError(`Could not save: ${err.message}`)
    }
  }
  return [items, save, error]
}

function RemoveButton({ label, onClick }) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-faint transition-colors hover:bg-sand hover:text-live"
    >
      <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
        <path d="M6 6l12 12M18 6 6 18" />
      </svg>
    </button>
  )
}

/**
 * "Heard X, meant Y" pairs. Unlike vocabulary (a hint), these always apply,
 * to every transcript, before cleanup — and they work with cleanup off.
 * History's "Fix" adds to the same list.
 */
function Corrections() {
  const [items, save, error] = useConfigList('corrections')
  const [heard, setHeard] = useState('')
  const [correct, setCorrect] = useState('')
  const add = () => {
    const h = heard.trim()
    const c = correct.trim()
    if (!h || !c) return
    save([...items.filter((i) => i.heard.toLowerCase() !== h.toLowerCase()), { heard: h, correct: c }])
    setHeard('')
    setCorrect('')
  }
  return (
    <Section
      title="Corrections"
      description="When the transcriber keeps hearing one thing as another. Applied to every dictation, even with cleanup off. Select words in History and press Fix to add one from there."
    >
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <TextInput value={heard} onChange={(e) => setHeard(e.target.value)} placeholder="Heard as… (ground mainly)" className="min-w-0 flex-1" aria-label="Heard as" />
          <span className="text-faint" aria-hidden="true">→</span>
          <TextInput
            value={correct}
            onChange={(e) => setCorrect(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && add()}
            placeholder="Should be… (grant me leave)"
            className="min-w-0 flex-1"
            aria-label="Should be"
          />
          <Button variant="primary" onClick={add} disabled={!heard.trim() || !correct.trim()}>
            Add
          </Button>
        </div>
        {error && <p className="mt-2 text-[13px] text-live">{error}</p>}
        {items.length > 0 && (
          <ul className="mt-3 divide-y divide-line">
            {items.map((i) => (
              <li key={i.heard} className="flex items-center gap-3 py-2 text-[14.5px]">
                <span className="selectable min-w-0 flex-1 truncate text-muted line-through decoration-faint">{i.heard}</span>
                <span className="text-faint" aria-hidden="true">→</span>
                <span className="selectable min-w-0 flex-1 truncate font-medium text-ink">{i.correct}</span>
                <RemoveButton label={`Remove correction for ${i.heard}`} onClick={() => save(items.filter((x) => x !== i))} />
              </li>
            ))}
          </ul>
        )}
      </Card>
    </Section>
  )
}

/** Spoken shortcuts: say the trigger on its own and the saved text is typed. */
function Snippets() {
  const [items, save, error] = useConfigList('snippets')
  const [trigger, setTrigger] = useState('')
  const [text, setText] = useState('')
  const add = () => {
    const t = trigger.trim()
    if (!t || !text.trim()) return
    save([...items.filter((i) => i.trigger.toLowerCase() !== t.toLowerCase()), { trigger: t, text }])
    setTrigger('')
    setText('')
  }
  return (
    <Section
      title="Snippets"
      description="Say the trigger on its own, or “insert” and the trigger, and the saved text is typed exactly as written."
    >
      <Card className="p-4">
        <div className="flex flex-col gap-2">
          <TextInput value={trigger} onChange={(e) => setTrigger(e.target.value)} placeholder="Trigger phrase, like “my signature”" aria-label="Trigger phrase" />
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="The text to type"
            rows={3}
            aria-label="Snippet text"
            style={{ userSelect: 'text' }}
            className="rounded-[16px] border border-line bg-cream px-4 py-2.5 text-[14px] leading-relaxed text-ink outline-none transition-colors placeholder:text-faint hover:border-sand-2 focus:border-green"
          />
          <div className="flex justify-end">
            <Button variant="primary" onClick={add} disabled={!trigger.trim() || !text.trim()}>
              Add snippet
            </Button>
          </div>
        </div>
        {error && <p className="mt-2 text-[13px] text-live">{error}</p>}
        {items.length > 0 && (
          <ul className="mt-3 divide-y divide-line">
            {items.map((i) => (
              <li key={i.trigger} className="flex items-start gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="text-[14.5px] font-medium text-ink">“{i.trigger}”</p>
                  <p className="selectable mt-0.5 line-clamp-3 whitespace-pre-line text-[13.5px] text-muted">{i.text}</p>
                </div>
                <RemoveButton label={`Remove snippet ${i.trigger}`} onClick={() => save(items.filter((x) => x !== i))} />
              </li>
            ))}
          </ul>
        )}
      </Card>
    </Section>
  )
}
