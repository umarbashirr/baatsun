/**
 * A port of compute_stats/compute_spend from baatsun_gui.py.
 *
 * Deliberately the same numbers rather than "close enough": the Home page is
 * the only place a person sees what the tool has done for them, and a streak or
 * a spend figure that disagrees with what the old window said reads as a bug
 * even when both are defensible. The constants below are the Python ones.
 */

// A composition speed, not a copy-typing speed: nobody drafts prose at their
// typing-test number.
export const TYPING_WPM = 40
// Fallback speaking rate, used only for entries recorded before the daemon
// started storing the real duration.
export const SPEAKING_WPM = 150
export const ACTIVITY_DAYS = 14

// USD per million tokens, (input, output). A snapshot, like the Python one —
// a model that isn't listed reports no cost rather than a guessed one.
const PRICING = {
  'gpt-4o-mini': [0.15, 0.6],
  'gpt-4o': [2.5, 10.0],
  'gpt-4.1': [2.0, 8.0],
  'gpt-4.1-mini': [0.4, 1.6],
  'gpt-4.1-nano': [0.1, 0.4],
}

const wordCount = (text) => (text || '').split(/\s+/).filter(Boolean).length
// Four characters per token is the usual English approximation, and it is only
// ever used for entries written before usage was recorded.
const estimateTokens = (text) => Math.max(1, Math.round((text || '').length / 4))
const dayKey = (date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(
    date.getDate(),
  ).padStart(2, '0')}`

export function prettyApp(app) {
  if (!app) return 'Unknown'
  const base = app.split('.').pop().replace(/-/g, ' ')
  return base.charAt(0).toUpperCase() + base.slice(1)
}

export function computeStats(entries) {
  const today = new Date()
  today.setHours(0, 0, 0, 0)

  const counts = new Map()
  for (let n = ACTIVITY_DAYS - 1; n >= 0; n -= 1) {
    const day = new Date(today)
    day.setDate(day.getDate() - n)
    counts.set(dayKey(day), { date: day, count: 0 })
  }

  let words = 0
  let cleaned = 0
  let spokenSecs = 0
  let spend = 0
  let spendEstimated = false
  const perApp = new Map()
  const daysSeen = new Set()

  for (const entry of entries) {
    const count = wordCount(entry.text)
    words += count
    if (entry.raw) cleaned += 1
    spokenSecs += entry.secs || (count / SPEAKING_WPM) * 60

    if (entry.app) {
      const name = prettyApp(entry.app)
      perApp.set(name, (perApp.get(name) || 0) + 1)
    }

    // Real reported usage where we have it; an estimate only for the older
    // entries that predate recording it, and the UI says which it is.
    if (entry.usage && typeof entry.usage.cost === 'number') {
      spend += entry.usage.cost
    } else if (entry.raw) {
      const price = PRICING['gpt-4o-mini']
      spend +=
        (estimateTokens(entry.raw) * price[0] + estimateTokens(entry.text) * price[1]) /
        1_000_000
      spendEstimated = true
    }

    if (typeof entry.ts !== 'number') continue
    const day = new Date(entry.ts * 1000)
    day.setHours(0, 0, 0, 0)
    const key = dayKey(day)
    daysSeen.add(key)
    if (counts.has(key)) counts.get(key).count += 1
  }

  // Consecutive days ending today, or ending yesterday if today is still empty
  // — a streak shouldn't look broken at breakfast.
  let streak = 0
  const cursor = new Date(today)
  if (!daysSeen.has(dayKey(cursor))) cursor.setDate(cursor.getDate() - 1)
  while (daysSeen.has(dayKey(cursor))) {
    streak += 1
    cursor.setDate(cursor.getDate() - 1)
  }

  const typingSecs = (words / TYPING_WPM) * 60
  return {
    total: entries.length,
    words,
    today: counts.get(dayKey(today))?.count || 0,
    streak,
    cleaned,
    spokenSecs,
    savedSecs: Math.max(0, typingSecs - spokenSecs),
    spend,
    spendEstimated,
    topApps: [...perApp.entries()].sort((a, b) => b[1] - a[1]),
    activity: [...counts.values()],
  }
}

/** "2h 5m", "45s" — the same shape human_duration produces in Python. */
export function humanDuration(seconds) {
  const total = Math.round(seconds || 0)
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours}h ${rest}m` : `${hours}h`
}

export function humanCount(n) {
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`
  return `${(n / 1_000_000).toFixed(1)}M`
}

/** Cleanup costs a fraction of a cent, so two decimals would read as "$0.00". */
export function humanSpend(usd) {
  if (!usd) return '$0'
  if (usd < 0.01) return '<$0.01'
  return `$${usd.toFixed(2)}`
}

/**
 * Like humanSpend, but never rounds a real charge away to nothing.
 *
 * The Home tile has one line to give a figure in, so "<$0.01" is the honest
 * summary there. The Costs page is answering exactly what a vendor billed, has
 * the room for the digits, and a column of "<$0.01" would hide which of the two
 * vendors the money actually went to.
 */
export function exactSpend(usd) {
  if (!usd) return '$0'
  if (usd < 0.01) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

export function groupByDay(entries) {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const yesterday = new Date(today)
  yesterday.setDate(yesterday.getDate() - 1)

  const groups = new Map()
  for (const entry of entries) {
    const when = new Date((entry.ts || 0) * 1000)
    const day = new Date(when)
    day.setHours(0, 0, 0, 0)
    let label
    if (day.getTime() === today.getTime()) label = 'Today'
    else if (day.getTime() === yesterday.getTime()) label = 'Yesterday'
    else
      label = when.toLocaleDateString(undefined, {
        weekday: 'long',
        day: 'numeric',
        month: 'long',
      })
    if (!groups.has(label)) groups.set(label, [])
    groups.get(label).push(entry)
  }
  return [...groups.entries()]
}
