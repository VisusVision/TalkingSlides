function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

const VARIANT_STYLES = {
  primary:
    'bg-[image:var(--accent-gradient)] text-white font-bold shadow-token-xs enabled:hover:brightness-[1.02] enabled:hover:shadow-token-sm',
  secondary:
    'bg-[var(--surface-container-highest)] text-[var(--text-primary)] enabled:hover:bg-[color:var(--hover-surface-strong)]',
  ghost:
    'bg-transparent text-[var(--text-secondary)] enabled:hover:bg-[color:var(--hover-surface)] enabled:hover:text-[var(--text-primary)]',
};

const SIZE_STYLES = {
  sm: 'h-control-sm px-3 text-sm',
  md: 'h-control-md px-5 text-sm',
  lg: 'h-control-lg px-6 text-base',
};

export default function Button({
  type = 'button',
  variant = 'primary',
  size = 'md',
  fullWidth = false,
  className,
  children,
  ...props
}) {
  return (
    <button
      type={type}
      className={joinClasses(
        'focus-ring motion-interactive inline-flex items-center justify-center gap-2 rounded-pill font-medium enabled:active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-70 disabled:saturate-75 disabled:shadow-none',
        VARIANT_STYLES[variant] || VARIANT_STYLES.primary,
        SIZE_STYLES[size] || SIZE_STYLES.md,
        fullWidth && 'w-full',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
