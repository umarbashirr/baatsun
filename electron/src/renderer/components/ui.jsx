import React from 'react'

/**
 * The small shared vocabulary the pages are built from.
 *
 * Kept in one file because there are only a handful and they are all one
 * screenful — splitting them across a directory would cost more navigation
 * than it saves.
 */

export function Card({ className = '', children, ...rest }) {
  return (
    <div
      className={`rounded-[14px] border border-ink-700/70 bg-ink-850 ${className}`}
      {...rest}
    >
      {children}
    </div>
  )
}

export function Section({ title, description, action, children }) {
  return (
    <section className="mb-9">
      {(title || action) && (
        <header className="mb-3 flex items-end justify-between gap-4">
          <div>
            {title && (
              <h2 className="text-[13px] font-semibold uppercase tracking-[0.09em] text-ink-400">
                {title}
              </h2>
            )}
            {description && <p className="mt-1 text-[13px] text-ink-400">{description}</p>}
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
    default:
      'bg-ink-750 text-ink-100 hover:bg-ink-700 border border-ink-600/80 active:scale-[0.985]',
    primary:
      'bg-saffron-500 text-ink-950 hover:bg-saffron-400 font-semibold active:scale-[0.985]',
    ghost: 'text-ink-300 hover:text-ink-100 hover:bg-ink-800 border border-transparent',
    danger:
      'text-live-500 hover:bg-live-500/10 border border-live-500/30 hover:border-live-500/50',
  }
  const sizes = {
    sm: 'h-8 px-3 text-[13px]',
    md: 'h-9 px-4 text-[13px]',
    lg: 'h-11 px-6 text-[14px]',
  }
  return (
    <button
      type="button"
      className={`no-drag inline-flex items-center justify-center gap-2 rounded-lg transition-all duration-150 disabled:pointer-events-none disabled:opacity-40 ${variants[variant]} ${sizes[size]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  )
}

export function Field({ label, hint, children, htmlFor }) {
  return (
    <div className="flex flex-col gap-1.5 border-b border-ink-700/50 px-4 py-3.5 last:border-0 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <div className="min-w-0 sm:max-w-[58%]">
        <label htmlFor={htmlFor} className="block text-[13.5px] font-medium text-ink-100">
          {label}
        </label>
        {hint && <p className="mt-0.5 text-[12.5px] leading-snug text-ink-400">{hint}</p>}
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
      className={`h-9 min-w-[190px] rounded-lg border border-ink-600/80 bg-ink-750 px-3 text-[13px] text-ink-100 outline-none transition-colors hover:border-ink-500 focus:border-saffron-500 ${className}`}
    >
      {options.map(({ value: v, label }) => (
        <option key={v} value={v} className="bg-ink-800">
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
      className={`relative h-[26px] w-[46px] shrink-0 rounded-full transition-colors duration-200 ${
        checked ? 'bg-saffron-500' : 'bg-ink-700'
      }`}
    >
      <span
        className={`absolute top-[3px] h-5 w-5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
          checked ? 'translate-x-[23px]' : 'translate-x-[3px]'
        }`}
      />
    </button>
  )
}

export function TextInput({ className = '', ...rest }) {
  return (
    <input
      className={`h-9 rounded-lg border border-ink-600/80 bg-ink-750 px-3 text-[13px] text-ink-100 outline-none transition-colors placeholder:text-ink-500 hover:border-ink-500 focus:border-saffron-500 ${className}`}
      style={{ userSelect: 'text' }}
      {...rest}
    />
  )
}

export function Empty({ icon, title, children }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-[14px] border border-dashed border-ink-700 px-6 py-16 text-center">
      {icon && <div className="mb-3 text-ink-600">{icon}</div>}
      <p className="text-[14px] font-medium text-ink-200">{title}</p>
      {children && <p className="mt-1.5 max-w-sm text-[13px] text-ink-400">{children}</p>}
    </div>
  )
}

export function Badge({ tone = 'neutral', children }) {
  const tones = {
    neutral: 'bg-ink-750 text-ink-300 border-ink-600/70',
    accent: 'bg-saffron-500/12 text-saffron-400 border-saffron-500/25',
    live: 'bg-live-500/12 text-live-500 border-live-500/25',
    good: 'bg-good-500/12 text-good-500 border-good-500/25',
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-[3px] text-[11.5px] font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  )
}
