function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

const VARIANT_STYLES = {
  neutral: 'bg-[var(--surface-container-high)] text-[var(--text-secondary)]',
  success: 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]',
  danger: 'bg-[color:var(--status-danger-bg)] text-[color:var(--status-danger-fg)]',
  warning: 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]',
  info: 'bg-[color:var(--status-info-bg)] text-[color:var(--status-info-fg)]',
  accent: 'bg-[color:var(--hover-accent-soft)] text-[var(--accent-primary)]',
};

const SIZE_STYLES = {
  sm: 'px-2 py-1 text-xs',
  md: 'px-3 py-1.5 text-xs',
};

export default function Badge({
  as: Component = 'span',
  variant = 'neutral',
  size = 'sm',
  className,
  children,
  ...props
}) {
  return (
    <Component
      className={joinClasses(
        'inline-flex w-fit max-w-full items-center gap-1.5 rounded-pill font-semibold leading-tight',
        VARIANT_STYLES[variant] || VARIANT_STYLES.neutral,
        SIZE_STYLES[size] || SIZE_STYLES.sm,
        className,
      )}
      {...props}
    >
      {children}
    </Component>
  );
}
