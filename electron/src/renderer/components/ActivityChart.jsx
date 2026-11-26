import React, { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Card, Segmented } from './ui.jsx'
import { dailyCounts } from '../lib/stats.js'

/**
 * Dictations per day, with a range picker.
 *
 * One series of counts over time, so: columns from a single baseline, one
 * colour, a y-axis of whole numbers and an x-axis of dates thinned to what
 * fits. Bars rather than a line because a count per day has no value between
 * two days. Hover or arrow keys read out a day; a visually hidden table holds
 * every value, so nothing is only in the picture.
 */

const RANGES = [
  { value: 7, label: '7 days' },
  { value: 14, label: '14 days' },
  { value: 30, label: '30 days' },
  { value: 90, label: '90 days' },
]
const STORE_KEY = 'baatsun.activityRange'

const HEIGHT = 196
const PAD = { top: 12, right: 6, bottom: 28, left: 34 }
const GAP = 2 // surface gap between adjacent bars
const MAX_BAR = 24
const RADIUS = 4

// Whole-number ticks (these are counts), at most five including zero.
const STEPS = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000]
function niceScale(max) {
  if (max <= 0) return { top: 4, ticks: [0, 1, 2, 3, 4] }
  const step = STEPS.find((s) => max / s <= 4) || Math.ceil(max / 4)
  const top = Math.max(step, Math.ceil(max / step) * step)
  const ticks = []
  for (let t = 0; t <= top; t += step) ticks.push(t)
  return { top, ticks }
}

const fmtDay = (d) => d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
const fmtLong = (d) =>
  d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
const plural = (n) => `${n} dictation${n === 1 ? '' : 's'}`

// A column with a 4px rounded data end and a square foot on the baseline.
function barPath(x, y, w, h) {
  const r = Math.min(RADIUS, w / 2, h)
  return `M${x},${y + h}V${y + r}A${r},${r} 0 0 1 ${x + r},${y}H${x + w - r}A${r},${r} 0 0 1 ${x + w},${y + r}V${y + h}Z`
}

function useWidth() {
  const ref = useRef(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return [ref, width]
}

export default function ActivityChart({ entries }) {
  const [range, setRange] = useState(() => Number(localStorage.getItem(STORE_KEY)) || 14)
  const choose = (v) => {
    setRange(v)
    localStorage.setItem(STORE_KEY, String(v))
  }
  const { days, coveredFrom } = useMemo(() => dailyCounts(entries, range), [entries, range])
  const [plotRef, width] = useWidth()
  const [active, setActive] = useState(null)

  const total = days.reduce((n, d) => n + d.count, 0)
  const busiest = days.reduce((best, d) => (d.count > (best?.count || 0) ? d : best), null)
  const { top, ticks } = niceScale(busiest?.count || 0)

  const plotW = Math.max(0, width - PAD.left - PAD.right)
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const band = days.length ? plotW / days.length : 0
  const barW = Math.max(2, Math.min(MAX_BAR, band - GAP))
  const yOf = (v) => PAD.top + plotH - (v / top) * plotH
  const xOf = (i) => PAD.left + band * i

  // Label as many days as fit, counting back from today so today always
  // has one. The week view names weekdays; longer ranges give dates.
  const minLabel = range === 7 ? 40 : 54
  const every = Math.max(1, Math.ceil(minLabel / Math.max(band, 1)))
  const labelled = new Set()
  for (let i = days.length - 1; i >= 0; i -= every) labelled.add(i)
  const xLabel = (d, i) =>
    i === days.length - 1
      ? 'Today'
      : range === 7
        ? d.toLocaleDateString(undefined, { weekday: 'short' })
        : fmtDay(d)

  // Days the history can't speak for (only once it's at its 500 cap).
  const uncovered = coveredFrom ? days.findIndex((d) => d.date >= coveredFrom) : 0

  const onKey = (e) => {
    const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    if (e.key === 'Home') setActive(0)
    else if (e.key === 'End') setActive(days.length - 1)
    else if (d) setActive((a) => Math.min(days.length - 1, Math.max(0, (a ?? days.length - 1) + d)))
    else return
    e.preventDefault()
  }

  const shown = active != null ? days[active] : null
  const tipLeft = shown ? Math.min(Math.max(xOf(active) + band / 2, 70), width - 70) : 0

  return (
    <Card className="px-5 pb-4 pt-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-[15px] font-medium text-ink">Dictations per day</h3>
          <p className="mt-0.5 text-[13.5px] text-muted">
            {total > 0
              ? `${uncovered > 0 ? 'At least ' : ''}${plural(total)} in the last ${range} days. Busiest: ${fmtLong(busiest.date)}, with ${busiest.count}.`
              : `Nothing dictated in the last ${range} days.`}
          </p>
        </div>
        <Segmented value={range} onChange={choose} options={RANGES} label="Range" />
      </div>

      <div ref={plotRef} className="relative" onPointerLeave={() => setActive(null)}>
        {width > 0 && (
          <svg
            width={width}
            height={HEIGHT}
            className="block overflow-visible outline-none focus-visible:rounded-[10px] focus-visible:ring-2 focus-visible:ring-green/40"
            tabIndex={0}
            role="group"
            aria-label={`Dictations per day for the last ${range} days. Use the left and right arrow keys to read each day.`}
            onKeyDown={onKey}
            onFocus={() => setActive((a) => a ?? days.length - 1)}
            onBlur={() => setActive(null)}
          >
            {/* Not in history: shaded, not drawn as a quiet stretch of zeros. */}
            {uncovered > 0 && (
              <g aria-hidden="true">
                <rect x={PAD.left} y={PAD.top} width={band * uncovered} height={plotH} fill="var(--color-sand)" opacity="0.7" rx="6" />
                {band * uncovered > 90 && (
                  <text x={PAD.left + (band * uncovered) / 2} y={PAD.top + 16} textAnchor="middle" className="fill-faint text-[11.5px]">
                    not fully in history
                  </text>
                )}
              </g>
            )}

            {/* Gridlines and y-axis: recessive, whole numbers. */}
            <g aria-hidden="true">
              {ticks.map((t) => (
                <g key={t}>
                  <line
                    x1={PAD.left}
                    x2={width - PAD.right}
                    y1={yOf(t)}
                    y2={yOf(t)}
                    stroke={t === 0 ? 'var(--color-sand-2)' : 'var(--color-line)'}
                    strokeWidth="1"
                    strokeDasharray={t === 0 ? undefined : '2 4'}
                  />
                  <text x={PAD.left - 10} y={yOf(t)} dy="0.32em" textAnchor="end" className="fill-faint text-[12px] tabular-nums">
                    {t.toLocaleString()}
                  </text>
                </g>
              ))}
            </g>

            {/* Columns. */}
            <g key={range} className="chart-bars" aria-hidden="true">
              {days.map((d, i) => {
                if (!d.count) return null
                const x = xOf(i) + (band - barW) / 2
                const y = yOf(d.count)
                return (
                  <path
                    key={d.date.toISOString()}
                    d={barPath(x, y, barW, PAD.top + plotH - y)}
                    fill={active === i ? 'var(--color-green-2)' : 'var(--color-green)'}
                    opacity={active == null || active === i ? 1 : 0.55}
                    className="transition-[opacity,fill] duration-150"
                  />
                )
              })}
            </g>

            {/* X-axis labels. */}
            <g aria-hidden="true">
              {days.map((d, i) =>
                labelled.has(i) ? (
                  <text
                    key={i}
                    x={xOf(i) + band / 2}
                    y={HEIGHT - 8}
                    textAnchor={i === days.length - 1 && every > 1 ? 'end' : 'middle'}
                    dx={i === days.length - 1 && every > 1 ? band / 2 : 0}
                    className={`text-[12px] ${active === i ? 'fill-ink' : 'fill-faint'}`}
                  >
                    {xLabel(d.date, i)}
                  </text>
                ) : null,
              )}
            </g>

            {/* Hit targets: the whole column for each day, wider than any bar. */}
            <g>
              {days.map((d, i) => (
                <rect
                  key={i}
                  x={xOf(i)}
                  y={PAD.top}
                  width={band}
                  height={plotH}
                  fill="transparent"
                  onPointerEnter={() => setActive(i)}
                />
              ))}
            </g>
          </svg>
        )}

        {shown && (
          <div
            className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-[12px] border border-line bg-card px-3 py-2 shadow-[0_10px_24px_-10px_rgba(27,26,23,0.3)]"
            style={{ left: tipLeft, top: Math.max(4, yOf(shown.count) - 10) }}
          >
            <p className="whitespace-nowrap text-[14px] font-semibold text-ink tabular-nums">{plural(shown.count)}</p>
            <p className="whitespace-nowrap text-[12.5px] text-muted">
              {fmtLong(shown.date)}
              {coveredFrom && shown.date < coveredFrom ? ', not fully counted' : ''}
            </p>
          </div>
        )}
        <p className="sr-only" aria-live="polite">
          {shown ? `${fmtLong(shown.date)}: ${plural(shown.count)}` : ''}
        </p>
      </div>

      {coveredFrom && uncovered > 0 && (
        <p className="mt-2 text-[13px] text-faint">
          History keeps your last 500 dictations, so days before {fmtDay(coveredFrom)} aren’t fully counted.
        </p>
      )}

      <table className="sr-only">
        <caption>Dictations per day, last {range} days</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">Dictations</th>
          </tr>
        </thead>
        <tbody>
          {days.map((d) => (
            <tr key={d.date.toISOString()}>
              <th scope="row">{fmtLong(d.date)}</th>
              <td>{d.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}
