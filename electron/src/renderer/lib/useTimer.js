import { useCallback, useEffect, useRef } from 'react'

/**
 * setTimeout that cancels itself when the component goes away.
 *
 * Every timer in this app is the same shape: show something — "Copied", a
 * toast, a confirm prompt — and clear it a moment later. A bare setTimeout
 * there has two problems, and both are quiet. It fires into a component that
 * may already be gone, which History does constantly: its rows unmount on
 * every keystroke in the search box, so a row copied and then filtered away
 * still has a timer holding it. And calling it twice before the first fires
 * strands the earlier timer, so a double click leaves two racing to clear the
 * same flag.
 *
 * Returns a start function; calling it again replaces the pending timer rather
 * than adding to it.
 */
export function useTimer() {
  const id = useRef(null)

  useEffect(() => () => clearTimeout(id.current), [])

  return useCallback((fn, delay) => {
    clearTimeout(id.current)
    id.current = setTimeout(fn, delay)
  }, [])
}
