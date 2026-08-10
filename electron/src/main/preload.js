/**
 * The only bridge between the renderer and Node.
 *
 * Everything is an explicit named method — no generic "invoke any channel"
 * escape hatch, because that would hand the renderer the whole ipcMain surface
 * and make contextIsolation decorative. If the UI needs something new, it gets
 * a new method here and a matching handler in main.js.
 */
const { contextBridge, ipcRenderer } = require('electron')

/** Unwrap main.js's {ok, value|error} envelope into a value or a throw. */
const call = async (channel, ...args) => {
  const reply = await ipcRenderer.invoke(channel, ...args)
  if (reply && reply.ok === false) throw new Error(reply.error)
  return reply?.value
}

contextBridge.exposeInMainWorld('baatsun', {
  snapshot: () => call('daemon:snapshot'),
  toggle: () => call('daemon:toggle'),
  status: () => call('daemon:status'),
  history: () => call('daemon:history'),
  historyFromDisk: () => call('history:disk'),
  cost: () => call('daemon:cost'),
  clearHistory: () => call('daemon:clear'),
  deleteEntry: (id) => call('daemon:delete', id),
  retypeEntry: (id) => call('daemon:retype', id),

  verifyKey: (provider, key) => call('key:verify', { provider, key }),
  loadConfig: () => call('config:load'),
  saveConfig: (payload) => call('config:save', payload),
  restartDaemon: () => call('daemon:restart'),

  minimize: () => call('window:minimize'),
  maximize: () => call('window:maximize'),
  close: () => call('window:close'),

  /**
   * Subscribe to daemon pushes. Returns an unsubscribe function — React
   * effects must call it, or a remount would stack duplicate listeners and
   * every transcript would appear twice.
   */
  onEvent: (handler) => {
    const listener = (_event, payload) => handler(payload)
    ipcRenderer.on('daemon:event', listener)
    return () => ipcRenderer.removeListener('daemon:event', listener)
  },
  onStatus: (handler) => {
    const listener = (_event, payload) => handler(payload)
    ipcRenderer.on('daemon:status', listener)
    return () => ipcRenderer.removeListener('daemon:status', listener)
  },
})
