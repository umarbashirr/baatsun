import React from 'react'
import * as SelectPrimitive from '@radix-ui/react-select'
import { cn } from '../lib/utils.js'

/**
 * shadcn/ui's Select, ported to plain JSX and restyled with the app's tokens.
 *
 * Same primitives and structure as the upstream component (Radix Select
 * underneath, so keyboard navigation, typeahead, focus return and screen
 * reader semantics come from there), with two departures:
 *  - icons are inlined rather than pulled from lucide-react, to keep the
 *    dependency count down for three glyphs;
 *  - the open/close motion is our own CSS (index.css, .select-content), since
 *    the project does not use tailwindcss-animate.
 */

const Chevron = ({ className, up = false }) => (
  <svg
    viewBox="0 0 24 24"
    className={className}
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d={up ? 'm18 15-6-6-6 6' : 'm6 9 6 6 6-6'} />
  </svg>
)

const Check = ({ className }) => (
  <svg
    viewBox="0 0 24 24"
    className={className}
    fill="none"
    stroke="currentColor"
    strokeWidth="2.4"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M20 6 9 17l-5-5" />
  </svg>
)

const Select = SelectPrimitive.Root
const SelectGroup = SelectPrimitive.Group
const SelectValue = SelectPrimitive.Value

const SelectTrigger = React.forwardRef(function SelectTrigger({ className, children, ...props }, ref) {
  return (
    <SelectPrimitive.Trigger
      ref={ref}
      className={cn(
        'no-drag group inline-flex h-10 min-w-[200px] items-center justify-between gap-3 rounded-full border border-line bg-cream py-0 pl-4 pr-3 text-left text-[14px] text-ink',
        'transition-[background-color,border-color,box-shadow] duration-150',
        'hover:border-sand-2 hover:bg-card',
        'focus-visible:outline-none focus-visible:border-green focus-visible:shadow-[0_0_0_3px_rgba(31,61,46,0.14)]',
        'data-[state=open]:border-green data-[state=open]:bg-card data-[state=open]:shadow-[0_0_0_3px_rgba(31,61,46,0.14)]',
        'disabled:cursor-not-allowed disabled:opacity-50 data-[placeholder]:text-faint',
        '[&>span]:min-w-0 [&>span]:truncate',
        className,
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <Chevron className="h-4 w-4 shrink-0 text-muted transition-transform duration-200 group-data-[state=open]:rotate-180" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  )
})

const SelectScrollUpButton = React.forwardRef(function SelectScrollUpButton({ className, ...props }, ref) {
  return (
    <SelectPrimitive.ScrollUpButton
      ref={ref}
      className={cn('flex cursor-default items-center justify-center py-1 text-muted', className)}
      {...props}
    >
      <Chevron up className="h-4 w-4" />
    </SelectPrimitive.ScrollUpButton>
  )
})

const SelectScrollDownButton = React.forwardRef(function SelectScrollDownButton({ className, ...props }, ref) {
  return (
    <SelectPrimitive.ScrollDownButton
      ref={ref}
      className={cn('flex cursor-default items-center justify-center py-1 text-muted', className)}
      {...props}
    >
      <Chevron className="h-4 w-4" />
    </SelectPrimitive.ScrollDownButton>
  )
})

const SelectContent = React.forwardRef(function SelectContent(
  { className, children, position = 'popper', align = 'end', ...props },
  ref,
) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        ref={ref}
        position={position}
        className={cn(
          'select-content relative z-50 max-h-[min(var(--radix-select-content-available-height),360px)] min-w-[12rem] overflow-hidden rounded-[18px] border border-line bg-card text-ink',
          'shadow-[0_18px_40px_-12px_rgba(27,26,23,0.25),0_2px_6px_rgba(27,26,23,0.06)]',
          // Wide enough for an option and its hint on one or two lines, never
          // narrower than the trigger it opens from.
          position === 'popper' && 'w-[max(var(--radix-select-trigger-width),18rem)]',
          className,
        )}
        sideOffset={6}
        align={align}
        collisionPadding={12}
        {...props}
      >
        <SelectScrollUpButton />
        <SelectPrimitive.Viewport className="p-1.5">{children}</SelectPrimitive.Viewport>
        <SelectScrollDownButton />
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  )
})

const SelectLabel = React.forwardRef(function SelectLabel({ className, ...props }, ref) {
  return (
    <SelectPrimitive.Label
      ref={ref}
      className={cn('px-3 pb-1 pt-2 text-[12.5px] font-medium text-faint', className)}
      {...props}
    />
  )
})

const SelectItem = React.forwardRef(function SelectItem({ className, children, hint, ...props }, ref) {
  return (
    <SelectPrimitive.Item
      ref={ref}
      className={cn(
        'relative flex w-full cursor-default select-none items-start gap-3 rounded-[12px] py-2 pl-3 pr-9 text-[14px] outline-none',
        'transition-colors duration-100',
        'data-[highlighted]:bg-sand data-[state=checked]:font-medium',
        'data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        className,
      )}
      {...props}
    >
      <span className="min-w-0">
        <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
        {hint && <span className="mt-0.5 block text-[12.5px] font-normal leading-snug text-muted">{hint}</span>}
      </span>
      <span className="absolute right-3 top-2.5 flex h-4 w-4 items-center justify-center text-green">
        <SelectPrimitive.ItemIndicator>
          <Check className="h-4 w-4" />
        </SelectPrimitive.ItemIndicator>
      </span>
    </SelectPrimitive.Item>
  )
})

const SelectSeparator = React.forwardRef(function SelectSeparator({ className, ...props }, ref) {
  return <SelectPrimitive.Separator ref={ref} className={cn('mx-2 my-1 h-px bg-line', className)} {...props} />
})

export {
  Select,
  SelectGroup,
  SelectValue,
  SelectTrigger,
  SelectContent,
  SelectLabel,
  SelectItem,
  SelectSeparator,
  SelectScrollUpButton,
  SelectScrollDownButton,
}
