// Baatsun landing page.
//
// One orchestrated load (the headline), one scroll-driven explanation (a
// dictation on a real desk, pinned while you scroll), and motion that answers
// the visitor: the race, the privacy switches, the page pill. Everything is
// local: Lenis is vendored, fonts and the photo are self-hosted.

const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v))
const hash = (n) => {
  const s = Math.sin(n * 127.1) * 43758.5453
  return s - Math.floor(s)
}

// ─────────────────────────── Smooth scroll ───────────────────────────
let lenis = null
if (!reduced && window.Lenis) {
  lenis = new window.Lenis({ autoRaf: true, anchors: true, lerp: 0.1 })
}

// ─────────────────────────── Hero load ───────────────────────────
const title = document.getElementById('hero-title')
title.innerHTML = title.textContent
  .trim()
  .split(' ')
  .map((w, i) => `<span class="hw" style="--i:${i}">${w}</span>`)
  .join(' ')
Promise.race([document.fonts.ready, new Promise((r) => setTimeout(r, 1200))]).then(() =>
  requestAnimationFrame(() => document.body.classList.add('ready')),
)

// ─────────────────────────── Stage: one dictation ───────────────────────────
const story = document.getElementById('story')
const card = document.getElementById('card')
const beats = [...document.querySelectorAll('.beat')]
const fills = [...document.querySelectorAll('.steps-bar b')]
const RANGES = [
  [0, 0.17],
  [0.17, 0.4],
  [0.4, 0.6],
  [0.6, 1],
]
const RAW = 'hi priya so um the fix is in the reconnect path we were retrying before the the socket closed so every retry raced the old connection'.split(' ')
const FILLERS = [2, 3, 15]
const CLEAN = 'Hi Priya, the fix is in the reconnect path: we were retrying before the socket closed, so every retry raced the old connection.'
const MAIL = 'Hi Priya,\n\nThe fix is in the reconnect path: we were retrying before the socket closed, so every retry raced the old connection.'
const LANDS = [
  'In chat it stays one line, because Enter sends.',
  'In email, the greeting gets a line of its own.',
  'In a terminal, cleanup never runs. You get exactly what you said.',
]

const said = document.getElementById('said')
const words = RAW.map((w) => {
  const s = document.createElement('span')
  s.className = 'w'
  s.textContent = w
  said.append(s)
  return s
})
const cleanSpan = document.createElement('span')
cleanSpan.className = 'said-clean'
cleanSpan.textContent = CLEAN
said.append(cleanSpan)
const lands = document.getElementById('lands')

const keys = [document.getElementById('k-ctrl'), document.getElementById('k-super')]
const spill = document.getElementById('spill')
const apps = [...document.querySelectorAll('.app')]
const winTitle = document.getElementById('win-title')
const TITLES = ['#backend', 'New message', 'Terminal']
const pastes = [
  [document.getElementById('paste-chat'), CLEAN],
  [document.getElementById('paste-mail'), MAIL],
  [document.getElementById('paste-term'), RAW.join(' ')],
]
const pasted = [false, false, false]
let lastBeat = -1
let lastApp = -1

// The ribbon: what you say streams along a curve into the pill, which is
// listening. Nothing is typed yet; whisper transcribes after you let go.
const ribbon = document.getElementById('ribbon')
const ribbonPath = document.getElementById('ribbon-path')
const ribbonText = document.getElementById('ribbon-text')
RAW.forEach((w, i) => {
  const t = document.createElementNS('http://www.w3.org/2000/svg', 'tspan')
  t.textContent = (i ? ' ' : '') + w
  if (FILLERS.includes(i)) t.setAttribute('class', 'r-filler')
  ribbonText.append(t)
})
const rib = { L: 0, len: 0 }
function layoutRibbon() {
  const w = card.clientWidth
  const h = card.clientHeight
  const cx = spill.offsetLeft + spill.offsetWidth / 2
  const cy = spill.offsetTop + spill.offsetHeight / 2
  ribbon.setAttribute('viewBox', `0 0 ${w} ${h}`)
  ribbonPath.setAttribute(
    'd',
    `M ${cx} ${cy} C ${cx + (w - cx) * 0.55} ${cy + 6}, ${w * 0.9} ${cy - h * 0.16}, ${w + 90} ${cy - h * 0.3}`,
  )
  rib.L = ribbonPath.getTotalLength()
  rib.len = ribbonText.parentNode.getComputedTextLength()
}

function flash(el) {
  el.classList.add('flash')
  requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove('flash')))
}

function updateStory() {
  const r = story.getBoundingClientRect()
  const total = r.height - innerHeight
  const p = reduced ? 1 : clamp(-r.top / total)

  // Before it pins, the card grows to full size as it rises into place.
  if (!reduced) card.style.setProperty('--cs', (1 - 0.05 * clamp(r.top / innerHeight)).toFixed(4))

  const beat = RANGES.findIndex(([, b]) => p < b || b === 1)
  RANGES.forEach(([a, b], i) => {
    fills[i].style.transform = `scaleX(${clamp((p - a) / (b - a)).toFixed(4)})`
  })
  if (beat !== lastBeat && !reduced) {
    beats.forEach((b, i) => b.classList.toggle('on', i === beat))
    lastBeat = beat
  }

  keys.forEach((k) => k.classList.toggle('down', p >= 0.05 && p < 0.41))
  spill.classList.toggle('open', p >= 0.07 && p < 0.47)
  spill.classList.toggle('listening', p >= 0.1 && p < 0.41)
  spill.classList.toggle('busy', p >= 0.41 && p < 0.47)

  // While you talk, the words stream into the pill.
  const rp = clamp((p - 0.1) / 0.31)
  ribbon.classList.toggle('on', p >= 0.09 && p < 0.42)
  if (rib.L) ribbonText.setAttribute('startOffset', (rib.L - rp * (rib.L + rib.len)).toFixed(1))

  // After you let go, the transcript comes out, gets cleaned, and is pasted.
  said.classList.toggle('show', p >= 0.46 && p < 0.62)
  FILLERS.forEach((i, j) => words[i].classList.toggle('struck', p >= 0.5 + j * 0.014))
  said.classList.toggle('clean', p >= 0.55)
  said.classList.toggle('gone', p >= 0.62)

  const app = p < 0.76 ? 0 : p < 0.88 ? 1 : 2
  if (app !== lastApp) {
    apps.forEach((a, i) => a.classList.toggle('on', i === app))
    winTitle.textContent = TITLES[app]
    lands.textContent = LANDS[app]
    lastApp = app
  }
  lands.classList.toggle('in', p >= 0.64)
  pastes.forEach(([el, text], i) => {
    const show = p >= 0.62 && (i === 0 || app >= i)
    if (show && !pasted[i]) {
      el.textContent = text
      flash(el)
    } else if (!show && pasted[i]) {
      el.textContent = ''
    }
    pasted[i] = show
  })
}

// ─────────────────────────── Speaking vs typing ───────────────────────────
// The rates are the ones Baatsun's own Home page uses for "time saved":
// 40 wpm typed, 150 wpm spoken. Plus the ~1.6 s small.en takes to transcribe.
const RACE_TEXT =
  'Quick update on the release. The reconnect fix is merged, the flaky tests are gone, and the build is green again. I still need a review on the migration before Friday, so if you have twenty minutes tomorrow morning, please take a look and leave comments on the pull request.'
const RACE_WORDS = RACE_TEXT.split(' ').length
const TYPE_S = (RACE_WORDS / 40) * 60
const SPEAK_S = (RACE_WORDS / 150) * 60
const TRANSCRIBE_S = 1.6
const SPEED = 6

const laneSpeak = document.getElementById('lane-speak')
const laneType = document.getElementById('lane-type')
const lanes = [laneSpeak, laneType].map((el) => ({
  el,
  fill: el.querySelector('.lane-fill'),
  time: el.querySelector('.lane-time'),
  state: el.querySelector('.lane-state'),
  text: el.querySelector('.lane-text'),
  done: el.querySelector('.done'),
  todo: el.querySelector('.todo'),
}))
const barsEl = laneSpeak.querySelector('.lane-bars')
const BAR_N = 96
const bars = Array.from({ length: BAR_N }, (_, i) => {
  const b = document.createElement('i')
  const gate = Math.sin(i * 0.35) * Math.sin(i * 0.11 + 1) > -0.2 ? 1 : 0.25
  b.style.setProperty('--h', `${Math.round(10 + 70 * gate * (0.3 + 0.7 * hash(i + 7)))}px`)
  barsEl.append(b)
  return b
})
const fmt = (sec) => {
  const s = Math.floor(sec)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
let raceRaf = 0
let raceT0 = 0

function raceFrame(now) {
  const sim = reduced ? TYPE_S : ((now - raceT0) / 1000) * SPEED
  const [sp, ty] = lanes
  const speakTotal = SPEAK_S + TRANSCRIBE_S

  const st = Math.min(sim, speakTotal)
  sp.time.textContent = fmt(sim >= speakTotal ? Math.round(speakTotal) : st)
  sp.fill.style.transform = `scaleX(${(st / TYPE_S).toFixed(4)})`
  const lit = Math.floor(clamp(sim / SPEAK_S) * BAR_N)
  bars.forEach((b, i) => b.classList.toggle('in', i < lit))
  if (sim < SPEAK_S) {
    sp.state.textContent = 'Listening…'
  } else if (sim < speakTotal) {
    sp.state.textContent = 'Transcribing on your CPU…'
  } else if (!sp.el.classList.contains('finished')) {
    sp.el.classList.add('finished')
    barsEl.classList.add('gone')
    sp.done.textContent = RACE_TEXT
    sp.todo.textContent = ''
    sp.state.textContent = `Pasted. Done in ${Math.round(speakTotal)} seconds.`
    sp.state.classList.add('done')
    if (!reduced) flash(sp.text)
  }

  const tt = Math.min(sim, TYPE_S)
  const chars = Math.floor((tt / TYPE_S) * RACE_TEXT.length)
  ty.done.textContent = RACE_TEXT.slice(0, chars)
  ty.todo.textContent = RACE_TEXT.slice(chars)
  ty.time.textContent = fmt(tt)
  ty.fill.style.transform = `scaleX(${(tt / TYPE_S).toFixed(4)})`
  if (sim >= TYPE_S) {
    ty.state.textContent = `Done in ${fmt(TYPE_S)}.`
    ty.state.classList.add('done')
    raceRaf = 0
    return
  }
  ty.state.textContent = 'Typing…'
  raceRaf = requestAnimationFrame(raceFrame)
}

function startRace() {
  cancelAnimationFrame(raceRaf)
  lanes.forEach((l) => {
    l.el.classList.remove('finished')
    l.state.classList.remove('done')
    l.done.textContent = ''
    l.todo.textContent = RACE_TEXT
  })
  barsEl.classList.remove('gone')
  raceT0 = performance.now()
  raceRaf = requestAnimationFrame(raceFrame)
}
// Reserve the paragraph's height before anything runs, so nothing jumps.
lanes.forEach((l) => (l.todo.textContent = RACE_TEXT))

if (reduced) {
  raceFrame(performance.now())
} else {
  const io = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        io.disconnect()
        startRace()
      }
    },
    { threshold: 0.35 },
  )
  io.observe(document.getElementById('race'))
}
document.getElementById('race-again').addEventListener('click', startRace)

// ─────────────────────────── Privacy switches ───────────────────────────
const flows = { text: false, audio: false }
const status = document.getElementById('flow-status')
const STATUS = {
  none: 'Both are off, so nothing is sent anywhere.',
  text: 'Your transcript text goes to OpenAI. Your audio stays here.',
  audio: 'Your recorded audio goes to ElevenLabs. Nothing else is sent.',
  both: 'Your recorded audio goes to ElevenLabs, and the transcript text goes to OpenAI.',
}
const OUT_LABEL = { audio: 'Gets your audio', text: 'Gets transcript text' }
document.querySelectorAll('.switch').forEach((sw) => {
  sw.addEventListener('click', () => {
    const key = sw.dataset.flow
    flows[key] = !flows[key]
    sw.setAttribute('aria-checked', String(flows[key]))
    const out = document.querySelector(`.out[data-out="${key}"]`)
    out.classList.toggle('on', flows[key])
    out.querySelector('small').textContent = flows[key] ? OUT_LABEL[key] : 'Not sent'
    document.querySelector(`.node[data-node="${key}"]`).classList.toggle('leak', flows[key])
    status.textContent = STATUS[flows.text && flows.audio ? 'both' : flows.text ? 'text' : flows.audio ? 'audio' : 'none']
    status.classList.toggle('leaving', flows.text || flows.audio)
  })
})

// ─────────────────────────── Reel and page pill ───────────────────────────
const reelTrack = document.getElementById('reel-track')
const pill = document.getElementById('pill')
const pillZones = ['race', 'privacy', 'screens'].map((id) => document.getElementById(id)).concat(document.querySelector('.details'))

function updateReel() {
  if (reduced || innerWidth < 900) {
    reelTrack.style.transform = ''
    return
  }
  const r = reelTrack.parentElement.getBoundingClientRect()
  const prog = clamp((innerHeight * 0.4 - r.top) / (innerHeight * 0.9))
  const travel = Math.max(0, reelTrack.scrollWidth - innerWidth + 80)
  reelTrack.style.transform = `translate3d(${(-prog * travel).toFixed(1)}px,0,0)`
}

function updatePill() {
  const mid = innerHeight * 0.5
  pill.classList.toggle(
    'show',
    pillZones.some((el) => {
      const r = el.getBoundingClientRect()
      return r.top < mid && r.bottom > mid
    }),
  )
}

let ticking = false
function onScroll() {
  if (ticking) return
  ticking = true
  requestAnimationFrame(() => {
    ticking = false
    updateStory()
    updateReel()
    updatePill()
  })
}
addEventListener('scroll', onScroll, { passive: true })
let resizeT = 0
addEventListener('resize', () => {
  clearTimeout(resizeT)
  resizeT = setTimeout(() => {
    layoutRibbon()
    onScroll()
  }, 120)
})
document.fonts.ready.then(() => {
  layoutRibbon()
  onScroll()
})
layoutRibbon()
onScroll()

// ─────────────────────────── Copy buttons ───────────────────────────
document.querySelectorAll('[data-copy]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const el = document.getElementById(btn.dataset.copy)
    try {
      await navigator.clipboard.writeText(el.textContent)
      btn.textContent = 'Copied'
    } catch {
      btn.textContent = 'Press Ctrl+C'
      const range = document.createRange()
      range.selectNodeContents(el)
      getSelection().removeAllRanges()
      getSelection().addRange(range)
    }
    setTimeout(() => (btn.textContent = 'Copy'), 1800)
  })
})
