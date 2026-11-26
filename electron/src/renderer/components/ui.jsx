import React from 'react'

/**
 * The small shared vocabulary the pages are built from.
 *
 * Kept in one file because there are only a handful and they are all one
 * screenful — splitting them across a directory would cost more navigation
 * than it saves.
 *
 * The look follows the website: white cards on warm paper, pill-shaped
 * controls, deep green for the one thing that matters on a screen.
 */

export function Card({ className = '', children, ...rest }) {
  return (
    <div
      className={`rounded-[20px] border border-line bg-card shadow-[0_1px_2px_rgba(27,26,23,0.04)] ${className}`}
      {...rest}
    >
      {children}
    </div>
  )
}

export function Section({ title, description, action, children }) {
  return (
    <section className="mb-10">
      {(title || action) && (
        <header className="mb-3.5 flex items-end justify-between gap-4">
          <div>
            {title && <h2 className="font-serif text-[22px] leading-tight text-ink">{title}</h2>}
            {description && <p className="mt-1 text-[14px] text-muted">{description}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  )
}

export function Button({
  variant = 'default',
  size = 'md',
  className = '',
  children,
  ...rest
}) {
  const variants = {
    default: 'bg-card text-ink border border-line hover:bg-sand hover:border-sand-2',
    primary: 'bg-green text-cream font-semibold hover:bg-green-2 border border-transparent',
    ghost: 'text-muted hover:text-ink hover:bg-sand border border-transparent',
    danger: 'text-live border border-live/30 hover:bg-live/8 hover:border-live/50',
  }
  const sizes = {
    sm: 'h-8 px-3.5 text-[13.5px]',
    md: 'h-10 px-5 text-[14px]',
    lg: 'h-12 px-7 text-[15px]',
  }
  return (
    <button
      type="button"
      className={`no-drag inline-flex items-center justify-center gap-2 rounded-full font-medium transition-[background-color,border-color,color,transform] duration-150 active:scale-[0.97] disabled:pointer-events-none disabled:opacity-40 ${variants[variant]} ${sizes[size]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  )
}

export function Field({ label, hint, children, htmlFor }) {
  return (
    <div className="flex flex-col gap-2 border-b border-line px-5 py-4 last:border-0 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <div className="min-w-0 sm:max-w-[58%]">
        <label htmlFor={htmlFor} className="block text-[15px] font-medium text-ink">
          {label}
        </label>
        {hint && <p className="mt-0.5 text-[13.5px] leading-snug text-muted">{hint}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}

export function Select({ value, onChange, options, className = '' }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`h-10 min-w-[200px] rounded-full border border-line bg-cream px-4 text-[14px] text-ink outline-none transition-colors hover:border-sand-2 focus:border-green ${className}`}
    >
      {options.map(({ value: v, label }) => (
        <option key={v} value={v} className="bg-card">
          {label}
        </option>
      ))}
    </select>
  )
}

export function Toggle({ checked, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative h-[28px] w-[48px] shrink-0 rounded-full transition-colors duration-200 ${
        checked ? 'bg-green' : 'bg-sand-2'
      }`}
    >
      {/*
        left-0 is load-bearing. Without it the knob has no inset, so it falls
        back to its static position — and a button carries a UA text-align of
        center, which centres that origin in the track. The translate below
        then measured from 13px instead of 0, putting the "on" knob at 36-56px
        inside a 46px track: hanging off the right edge, with "off" already
        sitting half-way across. Preflight resets a button's padding, margin
        and border, but not its text-align.
      */}
      <span
        className={`absolute left-0 top-[3px] h-[22px] w-[22px] rounded-full bg-white shadow-[0_1px_3px_rgba(27,26,23,0.25)] transition-transform duration-200 ${
          checked ? 'translate-x-[23px]' : 'translate-x-[3px]'
        }`}
      />
    </button>
  )
}

export function TextInput({ className = '', ...rest }) {
  return (
    <input
      className={`h-10 rounded-full border border-line bg-cream px-4 text-[14px] text-ink outline-none transition-colors placeholder:text-faint hover:border-sand-2 focus:border-green ${className}`}
      style={{ userSelect: 'text' }}
      {...rest}
    />
  )
}

export function Empty({ icon, title, children }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-[20px] border border-dashed border-sand-2 bg-card/50 px-6 py-16 text-center">
      {icon && <div className="mb-3 text-faint">{icon}</div>}
      <p className="font-serif text-[20px] text-ink">{title}</p>
      {children && <p className="mt-1.5 max-w-sm text-[14px] text-muted">{children}</p>}
    </div>
  )
}

export function Badge({ tone = 'neutral', children }) {
  const tones = {
    neutral: 'bg-sand text-muted border-transparent',
    accent: 'bg-mint text-green border-transparent',
    live: 'bg-live/10 text-live border-live/20',
    good: 'bg-mint text-good border-transparent',
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[12.5px] font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  )
}
