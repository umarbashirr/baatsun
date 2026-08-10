/**
 * "Is this key any good?" for the two API keys Settings can hold.
 *
 * Both are a plain authenticated GET against an endpoint that costs nothing —
 * deliberately not a real transcription or a real cleanup call, which would
 * bill the user for pressing a Test button.
 *
 * This duplicates baatsun_stt.verify_key rather than shelling out to it. The
 * check is one request with no shared logic worth reusing, and going through
 * Python would mean resolving an interpreter and a module path that differ
 * between an installed tree and a checkout — more to go wrong than the request
 * it would be wrapping.
 */
const https = require('node:https')

const TIMEOUT = 10_000

function probe({ host, path, headers }) {
  return new Promise((resolve) => {
    const req = https.request(
      { host, path, method: 'GET', headers, timeout: TIMEOUT },
      (res) => {
        // The body is only needed when something went wrong, and even then only
        // for its message — so drain it either way rather than leaking the socket.
        let body = ''
        res.on('data', (chunk) => {
          body += chunk
        })
        res.on('end', () => resolve({ status: res.statusCode, body }))
      },
    )
    req.on('timeout', () => {
      req.destroy()
      resolve({ status: 0, body: 'timed out' })
    })
    req.on('error', (err) => resolve({ status: 0, body: err.message }))
    req.end()
  })
}

function messageFrom(body) {
  try {
    const parsed = JSON.parse(body)
    const detail = parsed.detail ?? parsed.error
    if (typeof detail === 'string') return detail
    return detail?.message || parsed.message || ''
  } catch {
    return ''
  }
}

/** ElevenLabs puts a machine-readable reason in detail.status. */
function statusFrom(body) {
  try {
    return JSON.parse(body).detail?.status || ''
  } catch {
    return ''
  }
}

async function verifyElevenlabs(key) {
  if (!key) return { ok: false, message: 'No API key set.' }
  const { status, body } = await probe({
    host: 'api.elevenlabs.io',
    path: '/v1/user',
    headers: { 'xi-api-key': key },
  })
  if (status === 200) return { ok: true, message: 'Working — scribe_v2 ready.' }
  // A scoped key is a valid key.
  //
  // This probe reads the account profile, which needs the user_read
  // permission — and dictation never does. ElevenLabs only reports a missing
  // permission *after* it has authenticated the key, so this 401 is proof the
  // key is good rather than evidence it is bad. Restricting a key to just
  // Speech to Text is the careful way to configure one, and reporting that as
  // "rejected" told exactly the users who did the right thing that their
  // working key was broken.
  if (status === 401 && statusFrom(body) === 'missing_permissions') {
    return { ok: true, message: 'Working — valid key, scoped to its own permissions.' }
  }
  // Surface what the API said rather than collapsing every 401 to one string:
  // the reason is the whole value of pressing Test.
  if (status === 401 || status === 403) {
    return { ok: false, message: `Key rejected by ElevenLabs. ${messageFrom(body)}`.trim() }
  }
  if (status === 0) return { ok: false, message: `ElevenLabs unreachable: ${body}` }
  return { ok: false, message: `HTTP ${status} ${messageFrom(body)}`.trim() }
}

async function verifyOpenai(key) {
  if (!key) return { ok: false, message: 'No API key set.' }
  const { status, body } = await probe({
    host: 'api.openai.com',
    path: '/v1/models',
    headers: { Authorization: `Bearer ${key}` },
  })
  if (status === 200) return { ok: true, message: 'Working — key accepted.' }
  if (status === 401 || status === 403) return { ok: false, message: 'Key rejected by OpenAI.' }
  if (status === 0) return { ok: false, message: `OpenAI unreachable: ${body}` }
  return { ok: false, message: `HTTP ${status} ${messageFrom(body)}`.trim() }
}

module.exports = { verifyElevenlabs, verifyOpenai }
