/**
 * In-app updates: find a newer release on GitHub, download its .deb, check it,
 * and install it with the desktop's own password prompt.
 *
 * Everything that touches the network or root lives here in the main process.
 * The renderer only ever sees the result of a check and progress events, and
 * it cannot choose a URL or a file path: download() and install() work only on
 * what the last check() found and the last download() verified.
 *
 * Installing a .deb needs root, which an app window must not hold. pkexec asks
 * polkit, and GNOME shows its usual centred password dialog, so updating needs
 * no terminal and no stored credentials.
 */
const { app, net } = require('electron')
const crypto = require('node:crypto')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { execFile, spawn } = require('node:child_process')

const REPO = 'umarbashirr/baatsun'
const LATEST_URL = `https://api.github.com/repos/${REPO}/releases/latest`
// Assets are only ever fetched from this repo's own release downloads, whatever
// the API response says.
const DOWNLOAD_PREFIX = `https://github.com/${REPO}/releases/download/`
const DEB_NAME = /^baatsun_(\d+\.\d+\.\d+)_amd64\.deb$/

const CACHE_DIR = path.join(
  process.env.XDG_CACHE_HOME || path.join(os.homedir(), '.cache'),
  'baatsun',
  'updates',
)

let lastCheck = null
let verifiedFile = null

const run = (cmd, args, opts = {}) =>
  new Promise((resolve, reject) => {
    execFile(cmd, args, { maxBuffer: 16 * 1024 * 1024, ...opts }, (err, stdout, stderr) => {
      if (err) {
        err.stderr = String(stderr || '').trim()
        reject(err)
      } else resolve(String(stdout).trim())
    })
  })

/** The installed .deb's version, or null when this isn't a packaged install. */
async function installedVersion() {
  try {
    const v = await run('dpkg-query', ['-W', '-f=${Version}', 'baatsun'])
    return v || null
  } catch {
    return null
  }
}

/** Numeric x.y.z comparison; anything after the third number is ignored. */
function newer(a, b) {
  const parts = (v) => (String(v).match(/\d+/g) || []).slice(0, 3).map(Number)
  const [pa, pb] = [parts(a), parts(b)]
  for (let i = 0; i < 3; i++) {
    if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0)
  }
  return false
}

/** Release notes without the maintainer's build footnote. */
function cleanNotes(body) {
  return String(body || '')
    .split('\n')
    // The footnote is for the maintainer, and the install command is what
    // the Update button already does.
    .filter((line) => !/built and attached by hand|^\s*install:/i.test(line))
    // Shown as plain text, so drop the markdown that would print literally.
    .map((line) =>
      line
        .replace(/^#{1,6}\s*/, '')
        .replace(/^\s*[-*]\s+/, '• ')
        .replace(/\*\*|__|`/g, ''),
    )
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

async function check() {
  const current = await installedVersion()
  if (!current) {
    lastCheck = null
    return { supported: false }
  }
  const res = await net.fetch(LATEST_URL, {
    headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'baatsun-updater' },
  })
  if (!res.ok) throw new Error(`GitHub answered ${res.status} when checking for updates`)
  const release = await res.json()
  const latest = String(release.tag_name || '').replace(/^v/, '')
  const deb = (release.assets || []).find((a) => DEB_NAME.test(a.name || ''))
  const sha = deb && (release.assets || []).find((a) => a.name === `${deb.name}.sha256`)
  const usable =
    deb &&
    sha &&
    deb.browser_download_url?.startsWith(DOWNLOAD_PREFIX) &&
    sha.browser_download_url?.startsWith(DOWNLOAD_PREFIX) &&
    deb.name.match(DEB_NAME)[1] === latest
  const available = Boolean(usable && newer(latest, current))

  lastCheck = available
    ? { latest, debUrl: deb.browser_download_url, shaUrl: sha.browser_download_url, name: deb.name }
    : null
  return {
    supported: true,
    current,
    latest,
    available,
    title: String(release.name || `v${latest}`),
    notes: cleanNotes(release.body),
    size: deb?.size || 0,
    url: release.html_url || `https://github.com/${REPO}/releases`,
  }
}

/** Download and verify the release the last check found. */
async function download(onProgress) {
  if (!lastCheck) throw new Error('Check for updates first.')
  const { latest, debUrl, shaUrl, name } = lastCheck
  verifiedFile = null

  const shaRes = await net.fetch(shaUrl)
  if (!shaRes.ok) throw new Error(`Couldn’t fetch the checksum (HTTP ${shaRes.status})`)
  const expected = (await shaRes.text()).trim().split(/\s+/)[0].toLowerCase()
  if (!/^[0-9a-f]{64}$/.test(expected)) throw new Error('The release’s checksum file is malformed.')

  fs.mkdirSync(CACHE_DIR, { recursive: true })
  const target = path.join(CACHE_DIR, name)
  const partial = `${target}.part`

  const res = await net.fetch(debUrl)
  if (!res.ok || !res.body) throw new Error(`The download failed (HTTP ${res.status})`)
  const total = Number(res.headers.get('content-length')) || 0
  const hash = crypto.createHash('sha256')
  const out = fs.createWriteStream(partial)
  let received = 0
  try {
    for await (const chunk of res.body) {
      hash.update(chunk)
      received += chunk.length
      if (!out.write(chunk)) await new Promise((r) => out.once('drain', r))
      onProgress?.({ step: 'download', received, total })
    }
    await new Promise((resolve, reject) => out.end((err) => (err ? reject(err) : resolve())))
  } catch (err) {
    out.destroy()
    fs.rmSync(partial, { force: true })
    throw err
  }

  onProgress?.({ step: 'verify' })
  if (hash.digest('hex') !== expected) {
    fs.rmSync(partial, { force: true })
    throw new Error('The download didn’t match its checksum, so it was thrown away. Try again.')
  }
  // The package itself has to say it is Baatsun at the version we expect.
  const fields = await run('dpkg-deb', ['-f', partial, 'Package', 'Version'])
  if (!/^Package: baatsun$/m.test(fields) || !new RegExp(`^Version: ${latest.replace(/\./g, '\\.')}$`, 'm').test(fields)) {
    fs.rmSync(partial, { force: true })
    throw new Error('The downloaded file isn’t the expected Baatsun package.')
  }
  fs.renameSync(partial, target)
  verifiedFile = target
  return { file: target }
}

/** Install the verified download as root, through the desktop's password prompt. */
async function install() {
  if (!verifiedFile || !fs.existsSync(verifiedFile)) throw new Error('Download the update first.')
  try {
    await run('pkexec', ['apt-get', 'install', '-y', verifiedFile])
  } catch (err) {
    // pkexec: 126 is a dismissed password dialog, 127 a refused one.
    if (err.code === 126) throw new Error('The password prompt was cancelled, so nothing was installed.')
    if (err.code === 127) throw new Error('You weren’t authorised to install software.')
    if (err.code === 'ENOENT') throw new Error('pkexec isn’t available. Install the update from a terminal instead.')
    const detail = (err.stderr || '').split('\n').filter(Boolean).slice(-3).join(' ')
    if (/lock/i.test(detail)) {
      throw new Error('Another installer is running (often automatic updates). Wait a minute and try again.')
    }
    throw new Error(detail || err.message)
  }
  // The daemon runs the new code from its next start.
  await run('systemctl', ['--user', 'restart', 'baatsun.service']).catch(() => {})
  for (const f of fs.readdirSync(CACHE_DIR)) fs.rmSync(path.join(CACHE_DIR, f), { force: true })
  verifiedFile = null
  return 'installed'
}

/**
 * Reopen the window on the new build. A fresh baatsun-gui would find this
 * process still holding the single-instance lock and exit, so it starts a beat
 * after this one quits.
 */
function relaunch() {
  spawn('sh', ['-c', 'sleep 1; exec baatsun-gui'], { detached: true, stdio: 'ignore' }).unref()
  app.quit()
}

module.exports = { check, download, install, relaunch }
