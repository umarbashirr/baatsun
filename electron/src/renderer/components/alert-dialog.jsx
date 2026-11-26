import React from 'react'
import * as AlertDialogPrimitive from '@radix-ui/react-alert-dialog'
import { cn } from '../lib/utils.js'

/**
 * shadcn/ui's AlertDialog, ported to plain JSX and styled like our Dialog.
 *
 * An alert dialog is for a decision the user has to make before going on:
 * it can't be dismissed by clicking outside, Escape means Cancel, and focus
 * starts on the Cancel button so a stray Enter never confirms anything.
 * Pass our <Button> through AlertDialogAction / AlertDialogCancel with
 * asChild to get the app's button styles.
 */

const AlertDialog = AlertDialogPrimitive.Root
const AlertDialogTrigger = AlertDialogPrimitive.Trigger
const AlertDialogPortal = AlertDialogPrimitive.Portal
const AlertDialogAction = AlertDialogPrimitive.Action
const AlertDialogCancel = AlertDialogPrimitive.Cancel

const AlertDialogOverlay = React.forwardRef(function AlertDialogOverlay({ className, ...props }, ref) {
  return (
    <AlertDialogPrimitive.Overlay
      ref={ref}
      className={cn('dialog-overlay fixed inset-0 z-50 bg-ink/25 backdrop-blur-[2px]', className)}
      {...props}
    />
  )
})

const AlertDialogContent = React.forwardRef(function AlertDialogContent({ className, ...props }, ref) {
  return (
    <AlertDialogPortal>
      <AlertDialogOverlay />
      <AlertDialogPrimitive.Content
        ref={ref}
        className={cn(
          'dialog-content no-drag fixed left-1/2 top-1/2 z-50 w-[min(480px,calc(100vw-48px))] -translate-x-1/2 -translate-y-1/2',
          'rounded-[24px] border border-line bg-card p-7 text-ink',
          'shadow-[0_30px_70px_-20px_rgba(27,26,23,0.35),0_2px_8px_rgba(27,26,23,0.08)]',
          'focus:outline-none',
          className,
        )}
        {...props}
      />
    </AlertDialogPortal>
  )
})

const AlertDialogHeader = ({ className, ...props }) => (
  <div className={cn('flex flex-col gap-1.5', className)} {...props} />
)

const AlertDialogFooter = ({ className, ...props }) => (
  <div className={cn('mt-7 flex items-center gap-2', className)} {...props} />
)

const AlertDialogTitle = React.forwardRef(function AlertDialogTitle({ className, ...props }, ref) {
  return (
    <AlertDialogPrimitive.Title
      ref={ref}
      className={cn('font-serif text-[26px] leading-tight text-ink', className)}
      {...props}
    />
  )
})

const AlertDialogDescription = React.forwardRef(function AlertDialogDescription(
  { className, ...props },
  ref,
) {
  return (
    <AlertDialogPrimitive.Description
      ref={ref}
      className={cn('text-[14.5px] leading-relaxed text-muted', className)}
      {...props}
    />
  )
})

export {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogPortal,
  AlertDialogOverlay,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogAction,
  AlertDialogCancel,
}
