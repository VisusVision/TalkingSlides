function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

export default function SurfaceCard({
  as: Component = 'section',
  elevated = false,
  className,
  children,
  ...props
}) {
  return (
    <Component
      className={joinClasses(
        'rounded-card p-5 sm:p-6',
        elevated ? 'token-surface-elevated' : 'token-surface',
        elevated && 'shadow-token-sm',
        className,
      )}
      {...props}
    >
      {children}
    </Component>
  );
}
