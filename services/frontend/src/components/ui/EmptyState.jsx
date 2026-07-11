import SurfaceCard from './SurfaceCard';

function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

const LAYOUT_STYLES = {
  default: {
    root: 'min-h-44 px-5 py-8 sm:px-8 sm:py-10',
    icon: 'h-12 w-12',
    iconSize: 22,
    title: 'text-lg sm:text-xl',
    description: 'mt-2 max-w-xl text-sm sm:text-base',
    content: 'mt-4',
    actions: 'mt-5',
  },
  compact: {
    root: 'min-h-32 px-4 py-5 sm:px-5 sm:py-6',
    icon: 'h-10 w-10',
    iconSize: 18,
    title: 'text-base',
    description: 'mt-1.5 max-w-lg text-sm',
    content: 'mt-3',
    actions: 'mt-4',
  },
};

export default function EmptyState({
  as: Component = 'section',
  icon: Icon,
  title,
  description,
  action,
  secondaryAction,
  compact = false,
  contained = false,
  className,
  titleAs: Title = 'h2',
  children,
  ...props
}) {
  const layout = compact ? LAYOUT_STYLES.compact : LAYOUT_STYLES.default;
  const body = (
    <>
      {Icon ? (
        <span
          className={joinClasses(
            'mx-auto inline-flex shrink-0 items-center justify-center rounded-2xl bg-[color:var(--surface-muted)]/45 text-[var(--accent-primary)]',
            layout.icon,
          )}
          aria-hidden="true"
        >
          <Icon size={layout.iconSize} aria-hidden="true" focusable="false" />
        </span>
      ) : null}

      {title ? (
        <Title
          className={joinClasses(
            Icon ? 'mt-4' : '',
            'font-[var(--font-display)] font-bold leading-snug text-[var(--text-primary)]',
            layout.title,
          )}
        >
          {title}
        </Title>
      ) : null}

      {description ? (
        <p className={joinClasses('mx-auto leading-relaxed text-[var(--text-secondary)]', layout.description)}>
          {description}
        </p>
      ) : null}

      {children ? (
        <div className={joinClasses('mx-auto max-w-xl', layout.content)}>
          {children}
        </div>
      ) : null}

      {(action || secondaryAction) ? (
        <div className={joinClasses('flex flex-col items-center justify-center gap-2 sm:flex-row sm:gap-3', layout.actions)}>
          {action}
          {secondaryAction}
        </div>
      ) : null}
    </>
  );

  const rootClassName = joinClasses(
    'flex min-w-0 flex-col items-center justify-center text-center',
    layout.root,
    className,
  );

  if (contained) {
    return (
      <SurfaceCard
        as={Component}
        variant="muted"
        padding="none"
        className={rootClassName}
        data-empty-state=""
        {...props}
      >
        {body}
      </SurfaceCard>
    );
  }

  return (
    <Component className={rootClassName} data-empty-state="" {...props}>
      {body}
    </Component>
  );
}
