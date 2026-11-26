import React, { useEffect, useMemo, useState } from 'react'
import ActivityChart from '../components/ActivityChart.jsx'
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
    <Card className={`px-5 py-[18px] ${tone === 'accent' ? 'border-transparent bg-green' : ''}`}>
      <p className={`text-[14px] font-medium ${tone === 'accent' ? 'text-mint' : 'text-muted'}`}>
        {label}
      </p>
      <p
        className={`mt-2 font-serif text-[36px] leading-none tabular-nums ${
          tone === 'accent' ? 'text-cream' : 'text-ink'
        }`}
      >
        {value}
      </p>
      {hint && (
        <p className={`mt-2 text-[13.5px] leading-snug ${tone === 'accent' ? 'text-mint-2' : 'text-faint'}`}>
          {hint}
        </p>
      )}
    </Card>
  )
}

export default function Home({ daemon, onNavigate }) {
  const [name, setName] = useState('')
  const [cleanupModel, setCleanupModel] = useState('gpt-4o-mini')
  const stats = useMemo(
    () => computeStats(daemon.entries, cleanupModel),
    [daemon.entries, cleanupModel],
  )

  useEffect(() => {
    window.baatsun
      .loadConfig()
      .then(({ config }) => {
        setName((config.display_name || '').trim())
        setCleanupModel(config.cleanup_model || 'gpt-4o-mini')
      })
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
      <div className="mb-8">
        <h2 className="font-serif text-[42px] leading-[1.05] text-ink">
          {hello}
          {name ? `, ${name}` : ''}
        </h2>
        <p className="mt-2 text-[15.5px] text-muted">
          {stats.today > 0 ? (
            <>
              {stats.today} dictation{stats.today === 1 ? '' : 's'} today
              {stats.streak > 1 && `, and a ${stats.streak}-day streak`}
            </>
          ) : (
            'Nothing dictated yet today.'
          )}
        </p>
      </div>

      <figure className="mb-10 rounded-[20px] bg-sand px-6 py-5">
        <blockquote className="selectable font-serif text-[19px] italic leading-snug text-ink">
          “{quote}”
        </blockquote>
        <figcaption className="mt-2 text-[14px] text-muted">{author}</figcaption>
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
        <ActivityChart entries={daemon.entries} />
      </Section>

      {stats.topApps.length > 0 && (
        <Section title="Where it lands">
          <Card className="divide-y divide-line">
            {stats.topApps.slice(0, 5).map(([app, count]) => (
              <div key={app} className="flex items-center justify-between px-5 py-3">
                <span className="text-[15px] text-ink">{app}</span>
                <div className="flex items-center gap-3">
                  <div className="h-1.5 w-28 overflow-hidden rounded-full bg-sand">
                    <div
                      className="h-full rounded-full bg-green"
                      style={{ width: `${(count / stats.topApps[0][1]) * 100}%` }}
                    />
                  </div>
                  <span className="w-8 text-right text-[14.5px] tabular-nums text-muted">
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
          <p className="font-serif text-[22px] text-ink">Nothing here yet</p>
          <p className="mx-auto mt-1.5 max-w-md text-[14px] text-muted">
            Hold your hotkey anywhere, say something, and let go. The transcript is typed
            into whatever window has focus.
          </p>
          <button
            type="button"
            onClick={() => onNavigate('dictate')}
            className="mt-4 rounded-full bg-green px-5 py-2 text-[14px] font-medium text-cream hover:bg-green-2"
          >
            Open Dictate
          </button>
        </Card>
      )}
    </div>
  )
}
