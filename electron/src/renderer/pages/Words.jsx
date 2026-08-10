import React, { useEffect, useState } from 'react'
import { Card, Button, Empty, TextInput } from '../components/ui.jsx'

/**
 * The vocabulary list, stored as the one comma-separated string the daemon
 * already reads.
 *
 * It is edited here as chips because that is what it is — a list of names —
 * but written back as the same string baatsun_config.py expects, so the local
 * decoder's initial_prompt, the ElevenLabs keyterms and the cleanup prompt all
 * keep reading the field they already read.
 */
const toList = (value) =>
  (value || '')
    .split(',')
    .map((term) => term.trim())
    .filter(Boolean)

const toField = (list) => list.join(', ')

export default function Words() {
  const [terms, setTerms] = useState([])
  const [draft, setDraft] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    window.baatsun
      .loadConfig()
      .then(({ config }) => setTerms(toList(config.vocabulary)))
      .catch(() => setTerms([]))
      .finally(() => setLoaded(true))
  }, [])

  const persist = async (next) => {
    setTerms(next)
    setSaving(true)
    try {
      await window.baatsun.saveConfig({ config: { vocabulary: toField(next) } })
    } finally {
      // Brief, but it is the only confirmation that a chip edit reached disk.
      setTimeout(() => setSaving(false), 500)
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
        <h2 className="text-[19px] font-semibold tracking-tight text-ink-50">Words</h2>
        <p className="mt-1.5 max-w-lg text-[13.5px] leading-relaxed text-ink-400">
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
        <p className="mt-2 text-[11.5px] text-ink-500">
          {terms.length} term{terms.length === 1 ? '' : 's'}
          {saving && ' · saved'}
        </p>
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
              className="group inline-flex items-center gap-1.5 rounded-lg border border-ink-600/80 bg-ink-800 py-1.5 pl-3 pr-1.5 text-[13px] text-ink-200"
            >
              <span className="selectable">{term}</span>
              <button
                type="button"
                aria-label={`Remove ${term}`}
                onClick={() => persist(terms.filter((t) => t !== term))}
                className="grid h-5 w-5 place-items-center rounded text-ink-500 transition-colors hover:bg-ink-700 hover:text-live-500"
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
    </div>
  )
}
