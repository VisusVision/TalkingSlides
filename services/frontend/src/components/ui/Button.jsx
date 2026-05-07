function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

const VARIANT_STYLES = {
  primary:
    'bg-[image:var(--accent-gradient)] text-white font-bold hover:scale-105 active:scale-95',
  secondary:
    'bg-[var(--surface-container-highest)] text-[var(--text-primary)] hover:bg-[color:var(--hover-surface-strong)]',
  ghost:
    'bg-transparent text-[var(--text-secondary)] hover:bg-[color:var(--hover-surface)] hover:text-[var(--text-primary)]',
};

const SIZE_STYLES = {
  sm: 'h-9 px-3 text-sm',
  md: 'h-11 px-5 text-sm',
  lg: 'h-12 px-6 text-base',
};

export default function Button({
  type = 'button',
  variant = 'primary',
  size = 'md',
  className,
  children,
  ...props
}) {
  return (
    <button
      type={type}
      className={joinClasses(
        'focus-ring inline-flex items-center justify-center gap-2 rounded-full font-medium transition duration-200 disabled:cursor-not-allowed disabled:opacity-60',
        VARIANT_STYLES[variant] || VARIANT_STYLES.primary,
        SIZE_STYLES[size] || SIZE_STYLES.md,
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
