import React, { useState } from 'react'
import { useDaemon } from './lib/useDaemon.js'
import Home from './pages/Home.jsx'
import Dictate from './pages/Dictate.jsx'
import History from './pages/History.jsx'
import Costs from './pages/Costs.jsx'
import Words from './pages/Words.jsx'
import Settings from './pages/Settings.jsx'
import { Badge } from './components/ui.jsx'

const PAGES = [
  { id: 'home', label: 'Home', Component: Home },
  { id: 'dictate', label: 'Dictate', Component: Dictate },
  { id: 'history', label: 'History', Component: History },
  { id: 'costs', label: 'Costs', Component: Costs },
  { id: 'words', label: 'Words', Component: Words },
  { id: 'settings', label: 'Settings', Component: Settings },
]

const ICONS = {
  home: 'M3 10.5 12 3l9 7.5M5.5 9v11h13V9',
  dictate: 'M12 15a3.5 3.5 0 0 0 3.5-3.5v-5a3.5 3.5 0 1 0-7 0v5A3.5 3.5 0 0 0 12 15Zm7-3.5a7 7 0 0 1-14 0M12 18.5V22',
  history: 'M3.5 12a8.5 8.5 0 1 0 2.6-6.1M3.5 4.5V10H9M12 7.5V12l3.5 2',
  costs: 'M14.5 8.5h-4a1.75 1.75 0 0 0 0 3.5h3a1.75 1.75 0 0 1 0 3.5h-4M12 7v10M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z',
  words: 'M4 6h16M4 12h10M4 18h13',
  settings:
    'M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Zm8-3.5a8 8 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a8 8 0 0 0-2-1.2L15 3H9l-.4 2.6a8 8 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a8.1 8.1 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a8 8 0 0 0 2 1.2L9 21h6l.4-2.6a8 8 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.07-.4.1-.8.1-1.2Z',
}

function Icon({ name, className = 'h-[18px] w-[18px]' }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={ICONS[name]} />
    </svg>
  )
}

/**
 * The window's own title bar.
 *
 * The frame is off (main.js) so the header can hold the page title and the
 * live state together — with a system frame those would sit in two separate
 * bars saying different things.
 */
function TitleBar({ title, state, connected }) {
  // "listening" is the name the daemon broadcasts; see the note on COPY in
  // pages/Dictate.jsx.
  const recording = state.state === 'listening'
  const transcribing = state.state === 'transcribing'

  return (
    <header className="drag flex h-12 shrink-0 items-center justify-between border-b border-ink-700/60 bg-ink-900/80 px-5 backdrop-blur">
      <div className="flex items-center gap-3">
        <h1 className="text-[13.5px] font-semibold text-ink-100">{title}</h1>
        {recording && (
          <Badge tone="live">
            <span className="animate-live h-1.5 w-1.5 rounded-full bg-live-500" />
            Recording
          </Badge>
        )}
        {transcribing && (
          <Badge tone="accent">
            <span className="animate-live h-1.5 w-1.5 rounded-full bg-saffron-500" />
            Transcribing
          </Badge>
        )}
        {!connected && <Badge tone="neutral">Daemon offline</Badge>}
      </div>

      <div className="no-drag flex items-center gap-1">
        {[
          { label: 'Minimise', action: () => window.baatsun.minimize(), d: 'M5 12h14' },
          {
            label: 'Maximise',
            action: () => window.baatsun.maximize(),
            d: 'M6 6h12v12H6z',
          },
          { label: 'Close', action: () => window.baatsun.close(), d: 'M6 6l12 12M18 6 6 18' },
        ].map(({ label, action, d }) => (
          <button
            key={label}
            type="button"
            aria-label={label}
            onClick={action}
            className={`grid h-7 w-7 place-items-center rounded-md text-ink-400 transition-colors hover:text-ink-100 ${
              label === 'Close' ? 'hover:bg-live-500 hover:text-white' : 'hover:bg-ink-750'
            }`}
          >
            <svg
              viewBox="0 0 24 24"
              className="h-3.5 w-3.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            >
              <path d={d} />
            </svg>
          </button>
        ))}
      </div>
    </header>
  )
}

export default function App() {
  const [page, setPage] = useState('home')
  const daemon = useDaemon()
  const active = PAGES.find((p) => p.id === page) || PAGES[0]
  const { Component } = active

  return (
    <div className="flex h-full">
      <nav className="drag flex w-[196px] shrink-0 flex-col border-r border-ink-700/60 bg-ink-950">
        <div className="flex h-12 items-center gap-2.5 px-5">
          <div className="grid h-6 w-6 place-items-center rounded-md bg-saffron-500 text-[13px] font-bold text-ink-950">
            ब
          </div>
          <span className="text-[14px] font-semibold tracking-tight">Baatsun</span>
        </div>

        <div className="no-drag flex flex-1 flex-col gap-0.5 px-3 py-3">
          {PAGES.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              onClick={() => setPage(id)}
              aria-current={id === page ? 'page' : undefined}
              className={`flex items-center gap-3 rounded-lg px-3 py-2 text-[13.5px] transition-colors ${
                id === page
                  ? 'bg-ink-800 font-medium text-ink-50'
                  : 'text-ink-400 hover:bg-ink-850 hover:text-ink-200'
              }`}
            >
              <Icon name={id} />
              {label}
            </button>
          ))}
        </div>

        <div className="no-drag px-5 pb-4 text-[11.5px] text-ink-500">
          <span
            className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle ${
              daemon.connected ? 'bg-good-500' : 'bg-ink-600'
            }`}
          />
          {daemon.connected ? 'Connected' : 'Reconnecting…'}
        </div>
      </nav>

      <main className="flex min-w-0 flex-1 flex-col">
        <TitleBar title={active.label} state={daemon.state} connected={daemon.connected} />
        <div key={page} className="animate-rise flex-1 overflow-y-auto px-8 py-7">
          <Component daemon={daemon} onNavigate={setPage} />
        </div>
      </main>
    </div>
  )
}
