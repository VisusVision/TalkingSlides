function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

const WIDTH_STYLES = {
  standard: 'max-w-6xl',
  wide: 'max-w-[1500px]',
  full: 'max-w-none',
};

const GAP_STYLES = {
  sm: 'gap-4',
  md: 'gap-6',
  lg: 'gap-7',
};

const TITLE_STYLES = {
  display: 'display-lg',
  headline: 'headline-md',
};

const MOTION_STYLES = {
  enter: 'motion-page-enter',
  fade: 'motion-fade',
};

function motionClass(motion) {
  if (motion === true) return MOTION_STYLES.enter;
  if (!motion) return '';
  return MOTION_STYLES[motion] || '';
}

export function PageContainer({
  as: Component = 'div',
  width = 'wide',
  gap = 'md',
  motion = false,
  className,
  children,
  ...props
}) {
  return (
    <Component
      className={joinClasses(
        'mx-auto flex w-full min-w-0 flex-col py-6 sm:py-8',
        WIDTH_STYLES[width] || WIDTH_STYLES.wide,
        GAP_STYLES[gap] || GAP_STYLES.md,
        motionClass(motion),
        className,
      )}
      {...props}
    >
      {children}
    </Component>
  );
}

export function PageHeader({
  as: Component = 'header',
  eyebrow,
  title,
  titleAs: TitleComponent = 'h1',
  titleSize = 'display',
  description,
  actions,
  motion = false,
  className,
  children,
  ...props
}) {
  return (
    <Component
      className={joinClasses(
        'flex min-w-0 flex-col gap-4 md:flex-row md:items-end md:justify-between',
        motionClass(motion),
        className,
      )}
      {...props}
    >
      <div className="min-w-0">
        {eyebrow ? <p className="label-sm">{eyebrow}</p> : null}
        {title ? (
          <TitleComponent className={joinClasses(TITLE_STYLES[titleSize] || TITLE_STYLES.display, eyebrow && 'mt-2', 'break-words text-[var(--text-primary)]')}>
            {title}
          </TitleComponent>
        ) : null}
        {description ? (
          <p className="body-md mt-2 max-w-3xl">
            {description}
          </p>
        ) : null}
        {children}
      </div>
      {actions ? (
        <div className="flex w-full min-w-0 flex-wrap items-center gap-2 md:w-auto md:justify-end">
          {actions}
        </div>
      ) : null}
    </Component>
  );
}

export function PageToolbar({
  as: Component = 'section',
  surface = true,
  sticky = false,
  motion = false,
  className,
  children,
  ...props
}) {
  return (
    <Component
      className={joinClasses(
        'min-w-0',
        surface && 'rounded-card border border-[var(--border-subtle)] bg-[var(--surface-container-low)] p-3 sm:p-4',
        sticky && 'sticky top-20 z-30 backdrop-blur-xl',
        motionClass(motion),
        className,
      )}
      {...props}
    >
      <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
        {children}
      </div>
    </Component>
  );
}

PageHeader.Actions = function PageHeaderActions({ className, children, ...props }) {
  return (
    <div className={joinClasses('flex w-full min-w-0 flex-wrap items-center gap-2 md:w-auto md:justify-end', className)} {...props}>
      {children}
    </div>
  );
};

