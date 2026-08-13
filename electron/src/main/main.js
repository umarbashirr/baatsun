/**
 * Electron main process for the Baatsun application window.
 *
 * This replaces baatsun_gui.py's five-page window and nothing else. The pill
 * stays GTK because it needs wlr-layer-shell (overlay layer, no keyboard focus)
 * to sit above fullscreen windows without stealing focus, and Chromium has no
 * way to ask for that. The tray stays GTK for the same class of reason. So this
 * process owns exactly one window and talks to the same daemon socket the GTK
 * GUI did.
 *
 * Node APIs live here and in preload.js only; the renderer runs with
 * contextIsolation on and nodeIntegration off, and reaches the daemon through
 * the narrow bridge in preload.js.
 */
const { app, BrowserWindow, ipcMain, shell, nativeTheme } = require('electron')
const path = require('node:path')
const { execFile } = require('node:child_process')

const daemon = require('./daemon')
const store = require('./store')
const keycheck = require('./keycheck')

const DEV_SERVER = process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173'
// Opt in, never fall in. This decides whether the window loads the built
// bundle off disk or whatever is answering on a local HTTP port, and that
// window holds both API keys and a bridge that can type into the focused
// window (daemon:retype) and restart a service. app.isPackaged is always false
// here — the app ships as `electron /opt/baatsun/electron`, it is never
// electron-builder-packaged — so it cannot be part of this test, which left a
// single env var standing between the installed app and localhost:5173, in the
// direction where anything unset or unexpected chose the dev server.
// Requiring an explicit "1" means the safe branch is the default for every
// value, including unset, empty and inherited junk.
const isDev = process.env.BAATSUN_DEV === '1'

let mainWindow = null
let unsubscribe = null
// The socket connects in milliseconds, well before the renderer has mounted and
// registered its listeners. The daemon replies to "subscribe" by immediately
// sending the current state and focus — so those, and the connection status
// itself, all land with nobody listening. On a healthy daemon no further event
// ever corrects that, and the window would sit there claiming to be offline and
// idle while dictation worked fine.
//
// So the last of each is kept here and the renderer asks for a snapshot on
// mount. This also survives a reload, which is why it isn't solved by simply
// deferring the subscribe until did-finish-load.
const snapshot = {
  connected: false,
  state: { state: 'idle' },
  focus: { app: '', title: '', surface: '', cleanup: false },
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1080,
    height: 720,
    minWidth: 880,
    minHeight: 560,
    // Dark by default and painted before the renderer has anything to show,
    // so opening the window doesn't flash white on the way in.
    backgroundColor: '#0b0b0e',
    titleBarStyle: 'hidden',
    titleBarOverlay: false,
    // Keeps the window frame out of the way on GNOME while leaving the system
    // controls available — the app draws its own header.
    frame: false,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // On, not off. A sandboxed preload can still require('electron') for
      // contextBridge and ipcRenderer, which is all preload.js uses — the
      // Node work (sockets, files, https) all happens in this process. Leaving
      // it off would buy nothing and cost the renderer's OS-level isolation,
      // which matters because that process holds the API keys.
      sandbox: true,
    },
  })

  mainWindow.once('ready-to-show', () => mainWindow.show())

  if (isDev) {
    mainWindow.loadURL(DEV_SERVER)
  } else {
    mainWindow.loadFile(path.join(__dirname, '../../dist/index.html'))
  }

  // Nothing in this window may open anything, anywhere.
  //
  // This is a key-safety measure, not link hygiene. The renderer holds the API
  // keys so the Settings fields can show them, and the CSP's connect-src and
  // img-src stop it sending them anywhere — openExternal is the one call that
  // sidesteps all of that, because it hands a URL to the user's browser.
  // Filtering by protocol is not enough: https://evil/?k=<key> is a perfectly
  // valid https URL. An allowlist of hosts is the only filter that actually
  // holds, and the UI currently links nowhere, so the allowlist is empty.
  //
  // If a real outbound link is ever added, put its host in ALLOWED_HOSTS rather
  // than loosening this back to "any https".
  const ALLOWED_HOSTS = new Set()
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const { protocol, hostname } = new URL(url)
      if (protocol === 'https:' && ALLOWED_HOSTS.has(hostname)) shell.openExternal(url)
    } catch {
      /* not a URL we can reason about, so not one we will open */
    }
    return { action: 'deny' }
  })

  // Same reasoning for in-place navigation: nothing should ever move this
  // window off the app it was built to show.
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (url !== mainWindow.webContents.getURL()) event.preventDefault()
  })

  // One subscription per window, torn down with it. The daemon pushes state
  // changes, new transcripts and deletions here; the renderer never opens a
  // socket of its own.
  unsubscribe = daemon.subscribe(
    (event) => {
      if (event.type === 'state') snapshot.state = event
      // Kept field by field rather than stored whole, so a stray "type" can't
      // reach the renderer as part of the focus object. surface and cleanup
      // are the daemon's verdict on the next transcript, and the Dictate page
      // shows them — dropping them here would leave it guessing.
      if (event.type === 'focus') {
        snapshot.focus = {
          app: event.app || '',
          title: event.title || '',
          surface: event.surface || '',
          cleanup: !!event.cleanup,
        }
      }
      mainWindow?.webContents.send('daemon:event', event)
    },
    (status) => {
      snapshot.connected = status.connected
      mainWindow?.webContents.send('daemon:status', status)
    },
  )

  mainWindow.on('closed', () => {
    unsubscribe?.()
    unsubscribe = null
    mainWindow = null
  })
}

/** Wrap a handler so a thrown error reaches the renderer as data, not a crash. */
const handle = (channel, fn) =>
  ipcMain.handle(channel, async (_event, ...args) => {
    try {
      return { ok: true, value: await fn(...args) }
    } catch (err) {
      return { ok: false, error: err?.message || String(err) }
    }
  })

handle('daemon:snapshot', () => snapshot)
handle('daemon:toggle', () => daemon.toggle())
handle('daemon:status', () => daemon.status())
handle('daemon:history', () => daemon.history())
handle('daemon:cost', () => daemon.cost())
handle('daemon:clear', () => daemon.clear())
handle('daemon:delete', (id) => daemon.remove(id))
handle('daemon:retype', (id) => daemon.retype(id))

handle('config:load', () => ({
  config: store.loadConfig(),
  choices: store.CHOICES,
  defaultModel: store.DEFAULT_MODEL,
  openaiKey: store.loadOpenaiKey(),
  sttKey: store.loadSttKey(),
  configPath: store.CONFIG_PATH,
}))

handle('config:save', ({ config, openaiKey, sttKey }) => {
  const saved = store.saveConfig(config)
  if (openaiKey !== undefined) store.saveOpenaiKey(openaiKey)
  if (sttKey !== undefined) store.saveSttKey(sttKey)
  return saved
})

// Read off disk rather than the socket, so History still renders when the
// daemon is down — the GTK window did the same.
handle('history:disk', () => store.loadHistoryFromDisk())

// Tests the key in the box, not the one on disk, so it can be checked before
// saving — pressing Test on a key you haven't committed to is the whole point.
handle('key:verify', ({ provider, key }) =>
  provider === 'elevenlabs' ? keycheck.verifyElevenlabs(key) : keycheck.verifyOpenai(key),
)

handle('daemon:restart', () =>
  new Promise((resolve, reject) => {
    execFile('systemctl', ['--user', 'restart', 'baatsun.service'], (err, _out, stderr) =>
      err ? reject(new Error(stderr?.trim() || err.message)) : resolve('restarted'),
    )
  }),
)

handle('window:minimize', () => mainWindow?.minimize())
handle('window:maximize', () =>
  mainWindow?.isMaximized() ? mainWindow.unmaximize() : mainWindow?.maximize(),
)
handle('window:close', () => mainWindow?.close())

// Both are needed and they cover different display servers: setName is what
// XWayland reports as WM_CLASS, setDesktopName is the app_id a Wayland
// compositor matches against the .desktop file. Get either wrong and the
// window shows up in the dock as a generic "Electron" with the wrong icon.
app.setName('Baatsun')
app.setDesktopName('baatsun-gui.desktop')

// The tray's "Show History" launches this same command every time it is
// clicked, and so does the desktop entry. The GTK window it replaces was
// single-instance through GApplication's D-Bus activation, so clicking twice
// re-presented the existing window; without this lock it would stack a new one
// every click, each with its own daemon subscription.
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (!mainWindow) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.show()
    mainWindow.focus()
  })

  app.whenReady().then(() => {
    nativeTheme.themeSource = 'dark'
    createWindow()
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
  })
}

app.on('window-all-closed', () => {
  // Closing the window closes the app. The daemon is a separate systemd
  // service and keeps running — dictation must keep working with no window
  // open, which is the whole point of the hotkey.
  if (process.platform !== 'darwin') app.quit()
})
