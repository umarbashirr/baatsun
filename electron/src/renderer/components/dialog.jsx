import React from 'react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { cn } from '../lib/utils.js'

/**
 * shadcn/ui's Dialog, ported to plain JSX and styled with the app's tokens.
 *
 * Radix provides the modal behaviour: focus is trapped inside and returned to
 * whatever opened it, the rest of the window is inert, and Escape closes it
 * unless the caller prevents that (Settings does while a save is running).
 * Motion is our own CSS (index.css, .dialog-overlay / .dialog-content).
 */

const Dialog = DialogPrimitive.Root
const DialogTrigger = DialogPrimitive.Trigger
const DialogPortal = DialogPrimitive.Portal
const DialogClose = DialogPrimitive.Close

const DialogOverlay = React.forwardRef(function DialogOverlay({ className, ...props }, ref) {
  return (
    <DialogPrimitive.Overlay
      ref={ref}
      className={cn('dialog-overlay fixed inset-0 z-50 bg-ink/25 backdrop-blur-[2px]', className)}
      {...props}
    />
  )
})

const DialogContent = React.forwardRef(function DialogContent(
  { className, children, ...props },
  ref,
) {
  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Content
        ref={ref}
        className={cn(
          'dialog-content no-drag fixed left-1/2 top-1/2 z-50 w-[min(440px,calc(100vw-48px))] -translate-x-1/2 -translate-y-1/2',
          'rounded-[24px] border border-line bg-card p-7 text-ink',
          'shadow-[0_30px_70px_-20px_rgba(27,26,23,0.35),0_2px_8px_rgba(27,26,23,0.08)]',
          'focus:outline-none',
          className,
        )}
        {...props}
      >
        {children}
      </DialogPrimitive.Content>
    </DialogPortal>
  )
})

const DialogHeader = ({ className, ...props }) => (
  <div className={cn('flex flex-col gap-1.5', className)} {...props} />
)

const DialogFooter = ({ className, ...props }) => (
  <div className={cn('mt-7 flex flex-row-reverse items-center gap-2', className)} {...props} />
)

const DialogTitle = React.forwardRef(function DialogTitle({ className, ...props }, ref) {
  return (
    <DialogPrimitive.Title
      ref={ref}
      className={cn('font-serif text-[26px] leading-tight text-ink', className)}
      {...props}
    />
  )
})

const DialogDescription = React.forwardRef(function DialogDescription({ className, ...props }, ref) {
  return (
    <DialogPrimitive.Description
      ref={ref}
      className={cn('text-[14.5px] leading-relaxed text-muted', className)}
      {...props}
    />
  )
})

export {
  Dialog,
  DialogTrigger,
  DialogPortal,
  DialogOverlay,
  DialogClose,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
}
