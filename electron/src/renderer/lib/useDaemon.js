import { useCallback, useEffect, useMemo, useState } from 'react'

const api = window.baatsun

// HISTORY_LIMIT in baatsun.py. The daemon trims to this on every write, so a
// window that appended transcripts forever would drift past what the daemon
// actually holds and keep entries alive that no longer exist anywhere else.
const HISTORY_LIMIT = 500

/**
 * The single source of live truth for the window.
 *
 * One subscription, held here and shared by every page, because the daemon
 * pushes to one socket and the pages all want the same three things: what state
 * dictation is in, where the next transcript will land, and the history list.
 *
 * History is kept here rather than refetched per page so a new transcript can
 * be prepended the moment it arrives, instead of after a round trip.
 */
export function useDaemon() {
  const [state, setState] = useState({ state: 'idle' })
  const [focus, setFocus] = useState({ app: '', title: '', surface: '', cleanup: false })
  const [entries, setEntries] = useState([])
  const [connected, setConnected] = useState(false)
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setEntries(await api.history())
    } catch {
      // The daemon is the better source (it holds unsaved entries), but the
      // window must still show history when it's down — a restart shouldn't
      // look like data loss.
      try {
        setEntries(await api.historyFromDisk())
      } catch {
        setEntries([])
      }
    } finally {
      setLoaded(true)
    }
  }, [])

  useEffect(() => {
    refresh()
    // Ask for what already happened rather than waiting to be told: the socket
    // is up and the daemon has already sent its opening state and focus by the
    // time this mounts, and a healthy daemon never repeats them.
    api
      .snapshot()
      .then((snap) => {
        setConnected(snap.connected)
        setState(snap.state)
        setFocus(snap.focus)
      })
      .catch(() => {})

    const offEvent = api.onEvent((event) => {
      switch (event.type) {
        case 'state':
          setState(event)
          break
        case 'focus':
          setFocus({
            app: event.app || '',
            title: event.title || '',
            surface: event.surface || '',
            cleanup: !!event.cleanup,
          })
          break
        case 'transcript':
          // Newest last, matching the daemon's own ordering; the pages that
          // want newest-first reverse at render time. Trimmed to the same
          // limit the daemon keeps, so a long-lived window stays in step.
          setEntries((prev) => [...prev, event.entry].slice(-HISTORY_LIMIT))
          break
        case 'deleted':
          setEntries((prev) => prev.filter((e) => e.id !== event.id))
          break
        case 'history_cleared':
          setEntries([])
          break
        default:
          break
      }
    })

    const offStatus = api.onStatus(({ connected: isUp }) => {
      setConnected(isUp)
      // A reconnect means the daemon restarted, and anything dictated while we
      // were away is missing from our copy.
      if (isUp) refresh()
    })

    return () => {
      offEvent()
      offStatus()
    }
  }, [refresh])

  const actions = useMemo(
    () => ({
      toggle: () => api.toggle(),
      retype: (id) => api.retypeEntry(id),
      remove: (id) => api.deleteEntry(id),
      clear: () => api.clearHistory(),
      refresh,
    }),
    [refresh],
  )

  return { state, focus, entries, connected, loaded, ...actions }
}
