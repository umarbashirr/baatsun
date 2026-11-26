/**
 * Reads and writes the same files baatsun_config.py owns.
 *
 * Deliberately a mirror rather than a second source of truth: the daemon and
 * the GTK pill still read these files with the Python module, so the shapes,
 * the paths and the permissions all have to match exactly. DEFAULTS below is
 * DEFAULT_CONFIG from baatsun_config.py — when one changes, so must the other.
 *
 * The unknown-key drop matters on upgrade for the same reason it does in
 * Python: older configs carry "language", "hinglish_model" and "model" from the
 * versions that had an English/Hinglish switch, and honouring any of them would
 * pin an upgraded install to a model it no longer wants.
 */
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const CONFIG_DIR = path.join(os.homedir(), '.config', 'baatsun')
const CONFIG_PATH = path.join(CONFIG_DIR, 'config.json')
const OPENAI_KEY_PATH = path.join(CONFIG_DIR, 'openai.key')
const STT_KEY_PATH = path.join(CONFIG_DIR, 'elevenlabs.key')
const HISTORY_PATH = path.join(os.homedir(), '.local', 'share', 'baatsun', 'history.json')

const DEFAULTS = {
  display_name: '',
  model_override: '',
  compute_type: 'int8',
  stt_backend: 'local',
  hotkey: 'ctrl+super',
  activation: 'hold',
  cleanup_enabled: false,
  cleanup_model: 'gpt-4o-mini',
  cleanup_scope: 'prose',
  vocabulary: '',
  cleanup_strength: 'grammar',
  hinglish: false,
  beam_size: 5,
  insert_method: 'paste',
  line_breaks: true,
}

const CHOICES = {
  stt_backend: ['local', 'elevenlabs'],
  compute_type: ['int8', 'int8_float16', 'float16', 'float32'],
  model: ['base.en', 'distil-small.en', 'medium.en', 'distil-large-v3.5'],
  activation: ['hold', 'toggle', 'hybrid'],
  hotkey: ['ctrl+super', 'ctrl+alt', 'alt+super', 'ctrl+shift'],
  cleanup_scope: ['prose', 'all'],
  cleanup_strength: ['grammar', 'natural'],
  cleanup_model: ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1', 'gpt-4.1-mini', 'gpt-4.1-nano'],
  beam_size: [1, 3, 5],
  insert_method: ['paste', 'type'],
}

const DEFAULT_MODEL = 'small.en'
// DEFAULT_MULTILINGUAL_MODEL in baatsun_config.py: used when hinglish is on.
const DEFAULT_MULTILINGUAL_MODEL = 'small'

function loadConfig() {
  const cfg = { ...DEFAULTS }
  let stored
  try {
    stored = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'))
  } catch {
    return cfg
  }
  if (stored && typeof stored === 'object' && !Array.isArray(stored)) {
    for (const [key, value] of Object.entries(stored)) {
      if (key in DEFAULTS) cfg[key] = value
    }
  }
  return cfg
}

function saveConfig(patch) {
  // Merge into what is on disk rather than over the defaults: keys the UI
  // doesn't show must survive a save, exactly as in the GTK Settings page.
  const cfg = { ...loadConfig() }
  for (const [key, value] of Object.entries(patch || {})) {
    if (key in DEFAULTS) cfg[key] = value
  }
  fs.mkdirSync(CONFIG_DIR, { recursive: true })
  const tmp = `${CONFIG_PATH}.tmp`
  fs.writeFileSync(tmp, `${JSON.stringify(cfg, null, 2)}\n`)
  fs.renameSync(tmp, CONFIG_PATH)
  return cfg
}

function loadKey(keyPath, envVar) {
  const fromEnv = (process.env[envVar] || '').trim()
  if (fromEnv) return fromEnv
  try {
    return fs.readFileSync(keyPath, 'utf8').trim()
  } catch {
    return ''
  }
}

/**
 * What the renderer is allowed to know about a key: whether one is set, where
 * it comes from, and its last four characters. Never the key itself — the
 * renderer only ever writes keys, it has no reason to read them back.
 */
function keyStatus(keyPath, envVar) {
  const fromEnv = !!(process.env[envVar] || '').trim()
  const key = loadKey(keyPath, envVar)
  return { set: !!key, fromEnv, hint: key ? key.slice(-4) : '' }
}

function saveKey(keyPath, value) {
  fs.mkdirSync(CONFIG_DIR, { recursive: true })
  const key = (value || '').trim()
  if (!key) {
    try {
      fs.unlinkSync(keyPath)
    } catch {
      /* already gone */
    }
    return
  }
  const tmp = `${keyPath}.tmp`
  // A stale tmp file keeps its old mode (the mode option only applies on
  // create), so remove it first; then 0600 from the outset rather than chmod
  // afterwards, which would leave the key briefly world-readable.
  try {
    fs.unlinkSync(tmp)
  } catch {
    /* none left over */
  }
  fs.writeFileSync(tmp, `${key}\n`, { mode: 0o600, flag: 'wx' })
  fs.renameSync(tmp, keyPath)
}

/** History straight off disk, for the moments the daemon isn't reachable. */
function loadHistoryFromDisk() {
  try {
    const parsed = JSON.parse(fs.readFileSync(HISTORY_PATH, 'utf8'))
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

module.exports = {
  CONFIG_PATH,
  HISTORY_PATH,
  DEFAULTS,
  CHOICES,
  DEFAULT_MODEL,
  DEFAULT_MULTILINGUAL_MODEL,
  loadConfig,
  saveConfig,
  loadHistoryFromDisk,
  openaiKeyStatus: () => keyStatus(OPENAI_KEY_PATH, 'OPENAI_API_KEY'),
  sttKeyStatus: () => keyStatus(STT_KEY_PATH, 'ELEVENLABS_API_KEY'),
  loadOpenaiKey: () => loadKey(OPENAI_KEY_PATH, 'OPENAI_API_KEY'),
  saveOpenaiKey: (v) => saveKey(OPENAI_KEY_PATH, v),
  loadSttKey: () => loadKey(STT_KEY_PATH, 'ELEVENLABS_API_KEY'),
  saveSttKey: (v) => saveKey(STT_KEY_PATH, v),
}
