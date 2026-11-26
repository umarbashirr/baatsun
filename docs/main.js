// Landing page behaviour: the hero demo loop, the screenshot tabs, and the
// copy button. No dependencies, nothing loaded from anywhere else.

const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// --- Hero demo -------------------------------------------------------------
const pill = document.getElementById('pill')
const keys = document.getElementById('keys')
const typed = document.getElementById('typed')
const caption = document.getElementById('caption')
const hint = document.getElementById('input-hint')
const input = typed && typed.closest('.demo-input')
const bars = pill ? [...pill.querySelectorAll('.bars i')] : []
const LINE = 'Yes, it’s up. The retry was racing the old socket, so it now waits for the close first.'

async function demo() {
  if (!pill) return
  if (reduced) {
    hint.hidden = true
    typed.textContent = LINE
    input.classList.add('filled')
    caption.textContent = 'Hold Ctrl+Super, speak, let go. The text lands where your cursor is.'
    return
  }
  for (;;) {
    typed.textContent = ''
    input.classList.remove('filled')
    hint.hidden = false
    pill.className = 'pill'
    keys.classList.remove('down')
    caption.textContent = 'Idle: the pill waits at the bottom of the screen'
    await sleep(1600)

    keys.classList.add('down')
    pill.className = 'pill open listening'
    caption.textContent = 'Holding Ctrl+Super. Listening…'
    const t0 = performance.now()
    while (performance.now() - t0 < 2600) {
      bars.forEach((b, i) => {
        const wave = 0.35 + 0.65 * Math.abs(Math.sin((performance.now() / 180) + i * 0.9)) * Math.random()
        b.style.transform = `scaleY(${Math.max(0.2, wave).toFixed(2)})`
      })
      await sleep(90)
    }

    keys.classList.remove('down')
    pill.className = 'pill open busy'
    caption.textContent = 'Released. Transcribing on your CPU…'
    await sleep(1100)

    pill.className = 'pill'
    hint.hidden = true
    input.classList.add('filled')
    caption.textContent = 'Pasted where your cursor was. Your clipboard is restored.'
    // Pasted, not typed: the whole line arrives at once.
    typed.textContent = LINE
    await sleep(3600)
  }
}
demo()

// --- Screenshot tabs -------------------------------------------------------
const shot = document.getElementById('shot')
const tabs = [...document.querySelectorAll('.tabs [role="tab"]')]
// Preload so switching tabs never flashes an empty frame.
tabs.forEach((t) => { const img = new Image(); img.src = t.dataset.src })
function select(tab) {
  tabs.forEach((t) => t.setAttribute('aria-selected', String(t === tab)))
  shot.style.opacity = '0'
  setTimeout(() => {
    shot.src = tab.dataset.src
    shot.alt = tab.dataset.alt
    shot.style.opacity = '1'
  }, reduced ? 0 : 150)
}
tabs.forEach((t, i) => {
  t.addEventListener('click', () => select(t))
  t.addEventListener('keydown', (e) => {
    const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    if (!d) return
    const next = tabs[(i + d + tabs.length) % tabs.length]
    next.focus()
    select(next)
  })
})

// --- Copy buttons ----------------------------------------------------------
document.querySelectorAll('[data-copy]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const text = document.getElementById(btn.dataset.copy).textContent
    try {
      await navigator.clipboard.writeText(text)
      btn.textContent = 'Copied'
    } catch {
      btn.textContent = 'Select & copy'
    }
    setTimeout(() => { btn.textContent = 'Copy' }, 1800)
  })
})
