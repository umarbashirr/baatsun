import React, { useEffect, useMemo, useState } from 'react'
import { Card, Section, Badge } from '../components/ui.jsx'
import {
  computeStats,
  humanCount,
  humanDuration,
  humanSpend,
  TYPING_WPM,
  SPEAKING_WPM,
} from '../lib/stats.js'

const QUOTES = [
  ['The palest ink is better than the best memory.', 'Chinese proverb'],
  ['Speak clearly, if you speak at all.', 'Oliver Wendell Holmes'],
  ['The right word may be effective, but no word was ever as effective as a rightly timed pause.', 'Mark Twain'],
  ['Writing is thinking on paper.', 'William Zinsser'],
  ['What is written without effort is read without pleasure.', 'Samuel Johnson'],
]

function greeting(hour) {
  if (hour < 5) return 'Still up'
  if (hour < 12) return 'Good morning'
  if (hour < 17) return 'Good afternoon'
  if (hour < 21) return 'Good evening'
  return 'Good evening'
}

function Tile({ label, value, hint, tone }) {
  return (
    <Card className="px-4 py-3.5">
      <p className="text-[11.5px] font-medium uppercase tracking-[0.07em] text-ink-400">
        {label}
      </p>
      <p
        className={`mt-1.5 text-[26px] font-semibold leading-none tracking-tight tabular-nums ${
          tone === 'accent' ? 'text-saffron-400' : 'text-ink-50'
        }`}
      >
        {value}
      </p>
      {hint && <p className="mt-1.5 text-[11.5px] leading-snug text-ink-500">{hint}</p>}
    </Card>
  )
}

/**
 * Fourteen days of dictation counts.
 *
 * An SVG rather than a canvas so it scales with the window and stays legible
 * when the user bumps their font size; bars rather than a line because the
 * quantity is a count per day, and a line between two days implies values
 * in between that don't exist.
 */
function Activity({ activity }) {
  const peak = Math.max(1, ...activity.map((d) => d.count))
  return (
    <Card className="px-5 py-4">
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-[13px] font-medium text-ink-200">Last 14 days</h3>
        <span className="text-[11.5px] text-ink-500">peak {peak}</span>
      </div>
      <div className="flex h-[92px] items-end gap-[5px]">
        {activity.map(({ date, count }) => (
          <div
            key={date.toISOString()}
            className="group relative flex flex-1 flex-col justify-end"
            title={`${date.toLocaleDateString(undefined, {
              weekday: 'short',
              day: 'numeric',
              month: 'short',
            })} — ${count} dictation${count === 1 ? '' : 's'}`}
          >
            <div
              className={`w-full rounded-[3px] transition-all duration-200 ${
                count ? 'bg-saffron-500/75 group-hover:bg-saffron-400' : 'bg-ink-750'
              }`}
              // A day with no dictations still gets a sliver, so the row reads
              // as a timeline rather than as gaps in the data.
              style={{ height: count ? `${Math.max(8, (count / peak) * 92)}px` : '3px' }}
            />
          </div>
        ))}
      </div>
      <div className="mt-2 flex justify-between text-[11px] text-ink-500">
        <span>
          {activity[0]?.date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}
        </span>
        <span>Today</span>
      </div>
    </Card>
  )
}

export default function Home({ daemon, onNavigate }) {
  const [name, setName] = useState('')
  const stats = useMemo(() => computeStats(daemon.entries), [daemon.entries])

  useEffect(() => {
    window.baatsun
      .loadConfig()
      .then(({ config }) => setName((config.display_name || '').trim()))
      .catch(() => setName(''))
  }, [])

  // Stable for the life of the window: a quote that changed while you were
  // reading it would be a distraction on a page you open constantly.
  const [quote, author] = useMemo(
    () => QUOTES[Math.floor(Math.random() * QUOTES.length)],
    [],
  )

  const hello = greeting(new Date().getHours())

  return (
    <div className="mx-auto max-w-[880px]">
      <div className="mb-7">
        <h2 className="text-[27px] font-semibold tracking-tight text-ink-50">
          {hello}
          {name ? `, ${name}` : ''}
        </h2>
        <p className="mt-1.5 text-[13.5px] text-ink-400">
          {stats.today > 0 ? (
            <>
              {stats.today} dictation{stats.today === 1 ? '' : 's'} today
              {stats.streak > 1 && ` · ${stats.streak} day streak`}
            </>
          ) : (
            'Nothing dictated yet today.'
          )}
        </p>
      </div>

      <figure className="mb-8 border-l-2 border-saffron-500/50 pl-4">
        <blockquote className="selectable text-[14px] italic leading-relaxed text-ink-300">
          “{quote}”
        </blockquote>
        <figcaption className="mt-1 text-[12px] text-ink-500">— {author}</figcaption>
      </figure>

      <Section title="At a glance">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          <Tile label="Dictations" value={humanCount(stats.total)} />
          <Tile label="Words" value={humanCount(stats.words)} />
          <Tile
            label="Time saved"
            value={humanDuration(stats.savedSecs)}
            tone="accent"
            hint={`vs typing at ${TYPING_WPM} wpm`}
          />
          <Tile label="Day streak" value={String(stats.streak)} />
          <Tile
            label="Time spoken"
            value={humanDuration(stats.spokenSecs)}
            hint={`older entries estimated at ${SPEAKING_WPM} wpm`}
          />
          <Tile
            label="Cleanup spend"
            value={humanSpend(stats.spend)}
            hint={stats.spendEstimated ? 'partly estimated' : 'from reported usage'}
          />
        </div>
      </Section>

      <Section title="Activity">
        <Activity activity={stats.activity} />
      </Section>

      {stats.topApps.length > 0 && (
        <Section title="Where it lands">
          <Card className="divide-y divide-ink-700/50">
            {stats.topApps.slice(0, 5).map(([app, count]) => (
              <div key={app} className="flex items-center justify-between px-4 py-2.5">
                <span className="text-[13.5px] text-ink-200">{app}</span>
                <div className="flex items-center gap-3">
                  <div className="h-1 w-24 overflow-hidden rounded-full bg-ink-750">
                    <div
                      className="h-full rounded-full bg-saffron-500/70"
                      style={{ width: `${(count / stats.topApps[0][1]) * 100}%` }}
                    />
                  </div>
                  <span className="w-8 text-right text-[12.5px] tabular-nums text-ink-400">
                    {count}
                  </span>
                </div>
              </div>
            ))}
          </Card>
        </Section>
      )}

      {stats.total === 0 && daemon.loaded && (
        <Card className="px-5 py-6 text-center">
          <p className="text-[14px] font-medium text-ink-200">Nothing here yet</p>
          <p className="mx-auto mt-1.5 max-w-md text-[13px] text-ink-400">
            Hold your hotkey anywhere, say something, and let go. The transcript is typed
            into whatever window has focus.
          </p>
          <button
            type="button"
            onClick={() => onNavigate('dictate')}
            className="mt-4 text-[13px] font-medium text-saffron-400 hover:text-saffron-500"
          >
            Open Dictate →
          </button>
        </Card>
      )}
    </div>
  )
}
