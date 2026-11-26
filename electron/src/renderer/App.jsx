import React, { useEffect, useRef, useState } from 'react'
import { useDaemon } from './lib/useDaemon.js'
import logo from './assets/logo.svg'
import Home from './pages/Home.jsx'
import Dictate from './pages/Dictate.jsx'
import History from './pages/History.jsx'
import Costs from './pages/Costs.jsx'
import Words from './pages/Words.jsx'
import Settings from './pages/Settings.jsx'
import UpdateDialog from './components/UpdateDialog.jsx'
import { Badge, Button } from './components/ui.jsx'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogAction,
  AlertDialogCancel,
} from './components/alert-dialog.jsx'

const PAGES = [
  { id: 'home', label: 'Home', Component: Home },
  { id: 'dictate', label: 'Dictate', Component: Dictate },
  { id: 'history', label: 'History', Component: History },
  { id: 'words', label: 'Words', Component: Words },
  { id: 'costs', label: 'Costs', Component: Costs },
  { id: 'settings', label: 'Settings', Component: Settings },
]
// The everyday pages sit at the top; Settings sits at the foot of the
// sidebar, where people look for it and where it can't crowd the rest.
const MAIN = PAGES.filter((p) => p.id !== 'settings')

const HOTKEY_KEYS = {
  'ctrl+super': ['Ctrl', 'Super'],
  'ctrl+alt': ['Ctrl', 'Alt'],
  'alt+super': ['Alt', 'Super'],
  'ctrl+shift': ['Ctrl', 'Shift'],
}

function Keycaps({ keys }) {
  return (
    <span className="inline-flex items-center gap-1 align-middle">
      {keys.map((k, i) => (
        <React.Fragment key={k}>
          {i > 0 && <span className="text-faint">+</span>}
          <kbd className="rounded-[6px] border border-line bg-cream px-1.5 py-px font-sans text-[12px] font-medium text-ink shadow-[0_1px_0_var(--color-sand-2)]">
            {k}
          </kbd>
        </React.Fragment>
      ))}
    </span>
  )
}

/**
 * What the daemon is doing right now, at the foot of the sidebar.
 *
 * Replaces a bare "Connected": the useful things to know at a glance are
 * whether you can dictate, how (your actual hotkey), and whether it's
 * listening. Clicking it opens Dictate.
 */
function StatusCard({ daemon, config, onOpen }) {
  const state = daemon.state.state
  const keys = HOTKEY_KEYS[config?.hotkey] || HOTKEY_KEYS['ctrl+super']
  const toggle = config?.activation === 'toggle'

  let dot = 'bg-good'
  let title = 'Ready'
  let body = (
    <>
      <Keycaps keys={keys} />
      <span className="mt-1.5 block">{toggle ? 'Press' : 'Hold'} anywhere to dictate</span>
    </>
  )
  if (!daemon.connected) {
    dot = 'bg-faint'
    title = 'Daemon offline'
    body = 'Reconnecting…'
  } else if (state === 'listening') {
    dot = 'bg-live animate-live'
    title = 'Listening'
    body = toggle ? 'Press the hotkey again to stop' : 'Let go to transcribe'
  } else if (state === 'transcribing') {
    dot = null
    title = 'Transcribing'
    body = config?.stt_backend === 'elevenlabs' ? 'With ElevenLabs' : 'On your CPU'
  }

  return (
    <button
      type="button"
      onClick={onOpen}
      // One height for every state, so Settings above it doesn't jump each
      // time a dictation starts and ends.
      className="min-h-[100px] w-full rounded-[18px] border border-line bg-card px-4 py-3 text-left shadow-[0_1px_3px_rgba(27,26,23,0.06)] transition-colors hover:border-sand-2"
    >
      <span className="flex items-center gap-2 text-[14px] font-medium text-ink">
        {dot ? (
          <span className={`h-2 w-2 rounded-full ${dot}`} />
        ) : (
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-green/25 border-t-green" />
        )}
        {title}
      </span>
      <span className="mt-1.5 block text-[13px] leading-snug text-muted" aria-live="polite">
        {body}
      </span>
    </button>
  )
}

function NavItem({ id, label, current, onSelect, marker }) {
  const on = id === current
  return (
    <button
      type="button"
      onClick={() => onSelect(id)}
      aria-current={on ? 'page' : undefined}
      className={`flex items-center gap-3 rounded-full px-4 py-2.5 text-[15px] transition-colors ${
        on
          ? 'bg-card font-medium text-ink shadow-[0_1px_3px_rgba(27,26,23,0.08)]'
          : 'text-muted hover:bg-sand-2/60 hover:text-ink'
      }`}
    >
      <Icon name={id} className={`h-[18px] w-[18px] ${on ? 'text-green' : ''}`} />
      <span className="flex-1 text-left">{label}</span>
      {marker}
    </button>
  )
}

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
    <header className="drag flex h-14 shrink-0 items-center justify-between px-8">
      <div className="flex items-center gap-3">
        <h1 className="text-[15px] font-medium text-muted">{title}</h1>
        {recording && (
          <Badge tone="live">
            <span className="animate-live h-1.5 w-1.5 rounded-full bg-live" />
            Recording
          </Badge>
        )}
        {transcribing && (
          <Badge tone="accent">
            <span className="animate-live h-1.5 w-1.5 rounded-full bg-green" />
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
            className={`grid h-8 w-8 place-items-center rounded-full text-muted transition-colors hover:text-ink ${
              label === 'Close' ? 'hover:bg-live hover:text-white' : 'hover:bg-sand'
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
  // A page with unsaved work (Settings) registers a check here; switching
  // pages asks first rather than silently throwing the work away.
  const guard = useRef(null)
  const [leaving, setLeaving] = useState(null)
  const go = (id) => {
    if (id === page) return
    if (guard.current?.()) setLeaving(id)
    else setPage(id)
  }
  const daemon = useDaemon()
  const active = PAGES.find((p) => p.id === page) || PAGES[0]

  // The sidebar's status card shows the hotkey and activation the daemon is
  // actually using: re-read when it reconnects (it restarts after those
  // change) and when Settings says it saved.
  const [config, setConfig] = useState(null)
  useEffect(() => {
    const read = () =>
      window.baatsun
        .loadConfig()
        .then(({ config: c }) => setConfig(c))
        .catch(() => {})
    read()
    window.addEventListener('baatsun:config-saved', read)
    return () => window.removeEventListener('baatsun:config-saved', read)
  }, [daemon.connected])

  // Updates: checked once when the window opens, and again on demand from
  // Settings. A newer release opens the update dialog by itself; "Not now"
  // leaves a reminder in the sidebar instead of asking again this session.
  const [update, setUpdate] = useState(null)
  const [updateOpen, setUpdateOpen] = useState(false)
  const checkForUpdate = async ({ announce = false } = {}) => {
    const info = await window.baatsun.checkUpdate()
    setUpdate(info)
    if (info?.available && announce) setUpdateOpen(true)
    return info
  }
  useEffect(() => {
    checkForUpdate({ announce: true }).catch(() => {})
  }, [])
  const updater = { info: update, check: checkForUpdate, open: () => setUpdateOpen(true) }

  // Dictate carries the live state in the nav, so it shows from any page.
  const recState = daemon.connected ? daemon.state.state : 'idle'
  const dictateMarker =
    recState === 'listening' ? (
      <span className="animate-live h-2 w-2 rounded-full bg-live" aria-label="Listening" />
    ) : recState === 'transcribing' ? (
      <span
        className="h-3 w-3 animate-spin rounded-full border-2 border-green/25 border-t-green"
        aria-label="Transcribing"
      />
    ) : null
  const { Component } = active

  return (
    <div className="flex h-full">
      <nav className="drag flex w-[220px] shrink-0 flex-col bg-sand" aria-label="Pages">
        {/* The logo: ब hanging from the Baatsun pill (brand/README.md). */}
        <div className="flex h-16 items-center gap-2.5 px-5 pt-1">
          <img src={logo} alt="" width="30" height="30" className="rounded-[8px]" draggable="false" />
          <span className="font-serif text-[25px] leading-none text-ink">baatsun</span>
        </div>

        <div className="no-drag flex flex-1 flex-col gap-1 px-3 pt-3">
          {MAIN.map(({ id, label }) => (
            <NavItem
              key={id}
              id={id}
              label={label}
              current={page}
              onSelect={go}
              marker={id === 'dictate' ? dictateMarker : null}
            />
          ))}
        </div>

        <div className="no-drag flex flex-col gap-3 px-3 pb-4">
          {update?.available && !updateOpen && (
            <button
              type="button"
              onClick={() => setUpdateOpen(true)}
              className="flex items-center gap-2.5 rounded-[12px] bg-mint px-3 py-2.5 text-left text-[14px] font-medium text-green transition-colors hover:bg-mint-2"
            >
              <span className="h-2 w-2 rounded-full bg-green" aria-hidden="true" />
              Update to {update.latest}
            </button>
          )}
          <NavItem id="settings" label="Settings" current={page} onSelect={go} />
          <StatusCard daemon={daemon} config={config} onOpen={() => go('dictate')} />
        </div>
      </nav>

      <main className="flex min-w-0 flex-1 flex-col">
        <TitleBar title={active.label} state={daemon.state} connected={daemon.connected} />
        <div key={page} className="animate-rise flex-1 overflow-y-auto px-8 pb-10 pt-4">
          <Component
            daemon={daemon}
            onNavigate={go}
            setGuard={(fn) => (guard.current = fn)}
            updater={updater}
          />
        </div>
      </main>

      <UpdateDialog info={update} open={updateOpen && !!update?.available} onOpenChange={setUpdateOpen} />

      <AlertDialog open={!!leaving} onOpenChange={(open) => !open && setLeaving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Leave without saving?</AlertDialogTitle>
            <AlertDialogDescription>
              Your changes in {active.label} haven’t been saved. If you leave now, they’re lost.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="justify-end">
            <AlertDialogCancel asChild>
              <Button>Keep editing</Button>
            </AlertDialogCancel>
            <AlertDialogAction asChild>
              <Button
                variant="danger"
                onClick={() => {
                  guard.current = null
                  setPage(leaving)
                }}
              >
                Discard and leave
              </Button>
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
