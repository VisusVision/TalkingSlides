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
        'focus-ring inline-flex items-center justify-center gap-2 rounded-pill font-medium transition duration-normal ease-standard disabled:cursor-not-allowed disabled:opacity-60',
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
