import React, { useCallback, useEffect, useState } from 'react'
import { Card, Section, Badge, Button, Empty } from '../components/ui.jsx'
import { exactSpend, humanCount, humanDuration } from '../lib/stats.js'

/**
 * What the two APIs have actually cost.
 *
 * Every number on this page comes from the daemon's "cost" command rather than
 * being totalled here: the daemon is the only place that knows which backend
 * transcribed a given entry and what the rates are, and a second implementation
 * of the arithmetic in the renderer would eventually disagree with it. That is
 * also why there is no read-from-disk fallback the way History has one — the
 * report is computed, not stored, so with the daemon down there is nothing
 * honest to show.
 */

const PERIODS = [
  { id: 'today', label: 'Today' },
  { id: 'week', label: '7 days' },
  { id: 'month', label: '30 days' },
  { id: 'all', label: 'All' },
]

function Tabs({ value, onChange }) {
  return (
    <div className="inline-flex rounded-lg border border-ink-700/70 bg-ink-850 p-0.5">
      {PERIODS.map(({ id, label }) => (
        <button
          key={id}
          type="button"
          onClick={() => onChange(id)}
          aria-pressed={id === value}
          className={`rounded-[7px] px-3 py-1.5 text-[12.5px] transition-colors ${
            id === value
              ? 'bg-ink-750 font-medium text-ink-50'
              : 'text-ink-400 hover:text-ink-200'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

function Row({ label, value }) {
  return (
    <div className="flex items-baseline justify-between px-4 py-2.5">
      <span className="text-[13px] text-ink-400">{label}</span>
      <span className="text-[13px] tabular-nums text-ink-200">{value}</span>
    </div>
  )
}

/**
 * One vendor's share of the period.
 *
 * The estimated count is on the card rather than in a footnote because it
 * qualifies this figure specifically: a total that is half estimate deserves to
 * say so where the total is read.
 */
function Vendor({ name, what, cost, estimated, children }) {
  return (
    <Card>
      <div className="flex items-start justify-between gap-4 border-b border-ink-700/50 px-4 py-3.5">
        <div>
          <p className="text-[13.5px] font-medium text-ink-100">{name}</p>
          <p className="mt-0.5 text-[12px] text-ink-500">{what}</p>
        </div>
        <p className="text-[20px] font-semibold leading-none tabular-nums text-ink-50">
          {exactSpend(cost)}
        </p>
      </div>
      <div className="divide-y divide-ink-700/40">{children}</div>
      {estimated > 0 && (
        <div className="border-t border-ink-700/50 px-4 py-2.5 text-[12px] text-ink-500">
          {estimated} of these {estimated === 1 ? 'was' : 'were'} estimated — the call
          happened before the daemon recorded what it used.
        </div>
      )}
    </Card>
  )
}

export default function Costs({ daemon }) {
  const [report, setReport] = useState(null)
  const [error, setError] = useState('')
  const [period, setPeriod] = useState('today')
  // Only true for the first fetch: a refresh triggered by a new dictation
  // shouldn't blank a page you are reading.
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      setReport(await window.baatsun.cost())
      setError('')
    } catch (err) {
      setError(err?.message || 'The daemon did not answer.')
    } finally {
      setLoading(false)
    }
  }, [])

  // Refetch when a dictation lands, so the figure doesn't sit stale while the
  // page is open, and when the daemon comes back after a restart.
  const count = daemon.entries.length
  useEffect(() => {
    load()
  }, [load, count, daemon.connected])

  if (loading) {
    return <p className="text-[13px] text-ink-500">Totalling…</p>
  }

  if (!report) {
    return (
      <Empty title="No cost report">
        {error || 'The daemon is not answering.'} The total is worked out from history by
        the daemon itself, so there is nothing to show while it is down.
      </Empty>
    )
  }

  const bucket = report.periods[period]
  const { elevenlabs: eleven, openai } = bucket
  const rates = report.rates || {}
  const openaiRate = (rates.openai?.per_million || {})[rates.openai?.model]
  const since = report.window?.since
    ? new Date(report.window.since * 1000).toLocaleDateString(undefined, {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
      })
    : null

  return (
    <div className="mx-auto max-w-[880px]">
      <div className="mb-7 flex items-end justify-between gap-4">
        <div>
          <h2 className="text-[27px] font-semibold tracking-tight text-ink-50">
            {exactSpend(bucket.total)}
          </h2>
          <p className="mt-1.5 text-[13.5px] text-ink-400">
            across {bucket.dictations} dictation{bucket.dictations === 1 ? '' : 's'}
            {period === 'today'
              ? ' today'
              : period === 'all'
                ? ' in history'
                : ` in the last ${period === 'week' ? '7' : '30'} days`}
          </p>
        </div>
        <Tabs value={period} onChange={setPeriod} />
      </div>

      <Section title="By vendor">
        <div className="grid gap-3 md:grid-cols-2">
          <Vendor
            name="ElevenLabs"
            what={`Transcription · ${rates.elevenlabs?.model || 'scribe'}`}
            cost={eleven.cost}
            estimated={eleven.estimated}
          >
            <Row label="Dictations" value={humanCount(eleven.dictations)} />
            <Row label="Audio billed" value={humanDuration(eleven.secs)} />
            <Row
              label="Rate"
              // Keyterms are billed on top, and only on dictations sent with a
              // vocabulary, so the two halves are shown rather than a single
              // blended figure that would be wrong for anyone with no words set.
              value={
                rates.elevenlabs
                  ? `$${rates.elevenlabs.per_hour.toFixed(2)} + $${rates.elevenlabs.keyterms_per_hour.toFixed(2)} keyterms, per hour`
                  : '—'
              }
            />
          </Vendor>

          <Vendor
            name="OpenAI"
            what={`Cleanup · ${rates.openai?.model || 'not set'}`}
            cost={openai.cost}
            estimated={openai.estimated}
          >
            <Row label="Calls" value={humanCount(openai.calls)} />
            <Row
              label="Tokens"
              value={`${humanCount(openai.in)} in · ${humanCount(openai.out)} out`}
            />
            <Row
              label="Rate"
              value={
                openaiRate
                  ? `$${openaiRate[0].toFixed(2)} in · $${openaiRate[1].toFixed(2)} out, per million`
                  : 'unpriced model'
              }
            />
          </Vendor>
        </div>
      </Section>

      <Section title="What this counts">
        <Card className="divide-y divide-ink-700/50">
          <div className="px-4 py-3">
            <p className="text-[13px] text-ink-200">
              Totalled from the {report.window.entries} transcript
              {report.window.entries === 1 ? '' : 's'} still in history
              {since && `, back to ${since}`}.
            </p>
            <p className="mt-1 text-[12.5px] leading-snug text-ink-500">
              {report.window.at_limit
                ? `History is at its ${report.window.limit}-entry cap, so older dictations have already been dropped — even "All" is a floor on what you have spent, not the whole of it.`
                : 'This is a window on history, not a lifetime ledger: clearing History resets it.'}
            </p>
          </div>

          {bucket.unattributed > 0 && (
            <div className="px-4 py-3">
              <p className="flex items-center gap-2 text-[13px] text-ink-200">
                {bucket.unattributed} unattributed
                <Badge tone="neutral">never billed</Badge>
              </p>
              <p className="mt-1 text-[12.5px] leading-snug text-ink-500">
                Written before the daemon recorded which backend transcribed them. There
                is no way to tell now whether they ran locally or in the cloud, and a
                guess is worst precisely on a spend total — so they are counted here and
                charged nowhere.
              </p>
            </div>
          )}

          <div className="px-4 py-3">
            <p className="flex items-center gap-2 text-[13px] text-ink-200">
              Transcribing {report.backend === 'elevenlabs' ? 'in the cloud' : 'locally'}
              <Badge tone={report.backend === 'elevenlabs' ? 'accent' : 'good'}>
                {report.backend}
              </Badge>
            </p>
            <p className="mt-1 text-[12.5px] leading-snug text-ink-500">
              {report.backend === 'elevenlabs'
                ? 'Every dictation from here bills ElevenLabs for its audio. Local transcriptions already in history stay at zero.'
                : 'Local dictations cost nothing, and are recorded as zero rather than left unknown. Only the cleanup pass, if you have it on, reaches a paid API.'}
            </p>
          </div>
        </Card>
      </Section>

      <div className="flex items-center gap-3 text-[12px] text-ink-500">
        <Button variant="ghost" size="sm" onClick={load}>
          Refresh
        </Button>
        <span>
          As of{' '}
          {new Date(report.generated * 1000).toLocaleTimeString(undefined, {
            hour: 'numeric',
            minute: '2-digit',
          })}
        </span>
      </div>
    </div>
  )
}
