import React, { useEffect, useState } from 'react'
import { Button } from './ui.jsx'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from './dialog.jsx'

const STEPS = [
  { id: 'download', label: 'Download' },
  { id: 'verify', label: 'Check the download' },
  { id: 'install', label: 'Install (asks for your password)' },
  { id: 'restart', label: 'Reopen Baatsun' },
]

const mb = (bytes) => `${(bytes / 1024 / 1024).toFixed(1)} MB`

function StepIcon({ status }) {
  if (status === 'working') {
    return <span className="h-[18px] w-[18px] animate-spin rounded-full border-2 border-green/20 border-t-green" />
  }
  if (status === 'done' || status === 'failed') {
    return (
      <span
        className={`grid h-[20px] w-[20px] place-items-center rounded-full ${
          status === 'done' ? 'bg-green text-cream' : 'bg-live text-white'
        }`}
      >
        <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d={status === 'done' ? 'M20 6 9 17l-5-5' : 'M18 6 6 18M6 6l12 12'} />
        </svg>
      </span>
    )
  }
  return <span className="mx-[5px] h-2.5 w-2.5 rounded-full border-2 border-sand-2" />
}

/**
 * "A new version is available", then the update itself, in one centred dialog.
 *
 * While the update runs the dialog can't be dismissed: closing it would hide
 * an install that is still going, not stop it. On success the app reopens on
 * the new build by itself.
 */
export default function UpdateDialog({ info, open, onOpenChange }) {
  const [step, setStep] = useState(null) // null = not started
  const [failed, setFailed] = useState(null)
  const [progress, setProgress] = useState({ received: 0, total: 0 })

  useEffect(
    () =>
      window.baatsun.onUpdateProgress((p) => {
        if (p.step === 'download') setProgress({ received: p.received, total: p.total })
        if (p.step === 'verify') setStep('verify')
      }),
    [],
  )

  const working = step !== null && !failed
  // Reopening after a failed attempt starts from the offer again.
  const close = () => {
    setStep(null)
    setFailed(null)
    onOpenChange(false)
  }
  const keepOpen = (e) => working && e.preventDefault()

  const start = async () => {
    setFailed(null)
    setProgress({ received: 0, total: info?.size || 0 })
    try {
      setStep('download')
      await window.baatsun.downloadUpdate()
      setStep('install')
      await window.baatsun.installUpdate()
      setStep('restart')
      await window.baatsun.relaunch()
    } catch (err) {
      setFailed(err.message || String(err))
    }
  }

  const statusOf = (id) => {
    const at = STEPS.findIndex((s) => s.id === step)
    const i = STEPS.findIndex((s) => s.id === id)
    if (step === null || i > at) return 'pending'
    if (i < at) return 'done'
    return failed ? 'failed' : 'working'
  }

  const pct = progress.total ? Math.min(100, Math.round((progress.received / progress.total) * 100)) : null

  let title = `Baatsun ${info?.latest} is available`
  let body = `You have ${info?.current}. Updating takes about a minute, and dictation pauses while it installs.`
  if (working) {
    title = `Updating to ${info?.latest}`
    body = step === 'install'
      ? 'Enter your password in the system prompt. Installing can take a minute.'
      : 'Keep this window open.'
  } else if (failed) {
    title = 'The update didn’t finish'
    body = 'Nothing has changed if it stopped before installing. You can try again.'
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && !working && close()}>
      <DialogContent onEscapeKeyDown={keepOpen} onPointerDownOutside={keepOpen} onInteractOutside={keepOpen} aria-busy={working}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{body}</DialogDescription>
          {failed && (
            <p className="selectable mt-1 rounded-[12px] bg-live/8 px-3.5 py-2.5 text-[13.5px] leading-snug text-live">
              {failed}
            </p>
          )}
        </DialogHeader>

        {step === null && info?.notes && (
          <div className="selectable mt-5 max-h-[220px] overflow-y-auto whitespace-pre-line rounded-[16px] bg-cream px-4 py-3.5 text-[13.5px] leading-relaxed text-muted">
            {info.notes}
          </div>
        )}

        {step !== null && (
          <ol className="mt-6 flex flex-col gap-3 rounded-[16px] bg-cream px-4 py-3.5" aria-live="polite">
            {STEPS.map((s) => (
              <li key={s.id} className="flex items-center gap-3 text-[14.5px]">
                <span className="grid h-5 w-5 place-items-center">
                  <StepIcon status={statusOf(s.id)} />
                </span>
                <span className={statusOf(s.id) === 'pending' ? 'text-faint' : 'text-ink'}>{s.label}</span>
                {s.id === 'download' && statusOf('download') === 'working' && (
                  <span className="ml-auto tabular-nums text-[13px] text-muted">
                    {pct !== null ? `${pct}% of ${mb(progress.total)}` : mb(progress.received)}
                  </span>
                )}
              </li>
            ))}
          </ol>
        )}

        {!working && (
          <DialogFooter>
            <Button variant="primary" onClick={start} autoFocus>
              {failed ? 'Try again' : 'Update now'}
            </Button>
            <Button variant="ghost" onClick={close}>
              {failed ? 'Close' : 'Not now'}
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  )
}
