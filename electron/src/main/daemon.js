/**
 * Client for the baatsun daemon's unix socket.
 *
 * The protocol is line-oriented and tiny: you write a command string, and for
 * most commands you read one reply back. "subscribe" is the exception — it
 * never replies, it just leaves the socket open and pushes newline-delimited
 * JSON events until one side hangs up.
 *
 * Two connection styles, because those are two different things:
 *   - request()   opens a socket, sends one command, reads the reply, closes.
 *   - subscribe() holds one socket open for the life of the window.
 *
 * The daemon is a systemd user service that can restart under us (the Settings
 * page restarts it whenever the hotkey or the transcription backend changes),
 * so the subscription reconnects on its own rather than treating a dropped
 * socket as fatal.
 */
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')

const SOCKET_PATH = path.join(
  process.env.XDG_RUNTIME_DIR || `/run/user/${os.userInfo().uid}`,
  'baatsun.sock',
)

// Long enough to cover a daemon busy transcribing (it answers on a worker, but
// a cloud round trip can hold the state lock for up to 30s), short enough that
// a genuinely dead socket doesn't hang the UI forever.
const REQUEST_TIMEOUT = 35_000
const RECONNECT_DELAY = 1_500

function request(command, { expectJson = false } = {}) {
  return new Promise((resolve, reject) => {
    const socket = net.connect({ path: SOCKET_PATH })
    let buffer = ''
    let settled = false

    const finish = (fn, value) => {
      if (settled) return
      settled = true
      socket.destroy()
      fn(value)
    }

    socket.setTimeout(REQUEST_TIMEOUT)
    socket.on('connect', () => socket.write(command))
    socket.on('data', (chunk) => {
      buffer += chunk.toString('utf8')
    })
    // The daemon closes the socket when it has finished replying, so "end" is
    // the only reliable signal that the whole reply has arrived — history is
    // far larger than one TCP segment and would otherwise be truncated.
    socket.on('end', () => {
      const text = buffer.trim()
      if (!expectJson) return finish(resolve, text)
      try {
        finish(resolve, JSON.parse(text))
      } catch (err) {
        finish(reject, new Error(`unparseable reply to "${command}": ${err.message}`))
      }
    })
    socket.on('timeout', () => finish(reject, new Error(`"${command}" timed out`)))
    socket.on('error', (err) => finish(reject, err))
  })
}

/**
 * Hold a subscription open, calling onEvent for every event the daemon pushes
 * and onStatus whenever the connection itself comes or goes.
 *
 * Returns a stop function. Callers must use it — without it the reconnect
 * timer outlives the window and keeps a dead renderer's socket cycling.
 */
function subscribe(onEvent, onStatus) {
  let socket = null
  let timer = null
  let stopped = false

  const connect = () => {
    if (stopped) return
    socket = net.connect({ path: SOCKET_PATH })
    let buffer = ''

    socket.on('connect', () => onStatus({ connected: true }))
    socket.on('data', (chunk) => {
      buffer += chunk.toString('utf8')
      // Events are newline-delimited and can arrive several to a chunk, or
      // split across two. Keep the trailing fragment for the next chunk.
      const lines = buffer.split('\n')
      buffer = lines.pop()
      for (const line of lines) {
        if (!line.trim()) continue
        try {
          onEvent(JSON.parse(line))
        } catch {
          // A malformed line is not worth tearing the connection down for.
        }
      }
    })

    const retry = () => {
      if (stopped) return
      onStatus({ connected: false })
      socket = null
      timer = setTimeout(connect, RECONNECT_DELAY)
    }
    socket.on('error', retry)
    socket.on('close', retry)
  }

  connect()
  return () => {
    stopped = true
    if (timer) clearTimeout(timer)
    if (socket) socket.destroy()
  }
}

module.exports = {
  SOCKET_PATH,
  request,
  subscribe,
  toggle: () => request('toggle'),
  status: () => request('status'),
  history: () => request('history', { expectJson: true }),
  clear: () => request('clear'),
  remove: (id) => request(`delete ${id}`),
  retype: (id) => request(`retype ${id}`),
}
