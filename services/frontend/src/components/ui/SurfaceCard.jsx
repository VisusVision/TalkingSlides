function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

const VARIANT_STYLES = {
  default: 'token-surface',
  elevated: 'token-surface-elevated shadow-token-sm',
  muted: 'token-surface bg-[color:var(--surface-muted)]/30',
  accent: 'border border-[color:color-mix(in_srgb,var(--accent-primary),transparent_72%)] bg-[color:var(--hover-accent-soft)]',
  danger: 'border border-[color:var(--feedback-danger-fg)] bg-[color:var(--feedback-danger-bg)]',
};

const PADDING_STYLES = {
  none: '',
  sm: 'p-3 sm:p-4',
  md: 'p-5 sm:p-6',
  lg: 'p-6 sm:p-8',
};

const TITLE_STYLES = {
  sm: 'text-sm font-semibold',
  md: 'text-base font-semibold',
  lg: 'text-xl font-bold',
};

function SurfaceCard({
  as: Component = 'section',
  elevated = false,
  variant,
  padding = 'md',
  interactive = false,
  disabled = false,
  className,
  children,
  ...props
}) {
  const resolvedVariant = variant || (elevated ? 'elevated' : 'default');

  return (
    <Component
      className={joinClasses(
        'rounded-card transition duration-normal ease-standard',
        VARIANT_STYLES[resolvedVariant] || VARIANT_STYLES.default,
        PADDING_STYLES[padding] ?? PADDING_STYLES.md,
        interactive && !disabled && 'focus-ring hover:-translate-y-0.5 hover:shadow-token-sm',
        disabled && 'pointer-events-none opacity-60',
        className,
      )}
      aria-disabled={disabled || props['aria-disabled'] || undefined}
      {...props}
    >
      {children}
    </Component>
  );
}

function SurfaceCardHeader({ as: Component = 'div', layout = 'split', className, children, ...props }) {
  return (
    <Component
      className={joinClasses(
        layout === 'stack' ? 'space-y-1' : 'flex items-start justify-between gap-4',
        className,
      )}
      {...props}
    >
      {children}
    </Component>
  );
}

function SurfaceCardBody({ as: Component = 'div', className, children, ...props }) {
  return (
    <Component className={joinClasses('space-y-4', className)} {...props}>
      {children}
    </Component>
  );
}

function SurfaceCardFooter({ as: Component = 'div', className, children, ...props }) {
  return (
    <Component
      className={joinClasses(
        'flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border-subtle)] pt-4',
        className,
      )}
      {...props}
    >
      {children}
    </Component>
  );
}

function SurfaceCardTitle({ as: Component = 'h2', size = 'lg', className, children, ...props }) {
  return (
    <Component
      className={joinClasses(
        'font-[var(--font-display)] leading-snug text-[var(--text-primary)]',
        TITLE_STYLES[size] || TITLE_STYLES.lg,
        className,
      )}
      {...props}
    >
      {children}
    </Component>
  );
}

function SurfaceCardDescription({ as: Component = 'p', className, children, ...props }) {
  return (
    <Component
      className={joinClasses('text-sm leading-relaxed text-[var(--text-secondary)]', className)}
      {...props}
    >
      {children}
    </Component>
  );
}

SurfaceCard.Header = SurfaceCardHeader;
SurfaceCard.Body = SurfaceCardBody;
SurfaceCard.Footer = SurfaceCardFooter;
SurfaceCard.Title = SurfaceCardTitle;
SurfaceCard.Description = SurfaceCardDescription;

export default SurfaceCard;
