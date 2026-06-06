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
        'rounded-3xl p-5 sm:p-6',
        elevated ? 'token-surface-elevated' : 'token-surface',
        className,
      )}
      {...props}
    >
      {children}
    </Component>
  );
}
