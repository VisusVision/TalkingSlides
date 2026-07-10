function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

const RADIUS_STYLES = {
  sm: 'rounded-token-sm',
  md: 'rounded-token-md',
  lg: 'rounded-token-lg',
  card: 'rounded-card',
  full: 'rounded-full',
};

const TEXT_WIDTHS = ['w-full', 'w-11/12', 'w-4/5', 'w-2/3'];

function Skeleton({
  as: Component = 'div',
  rounded = 'md',
  className,
  children,
  ...props
}) {
  return (
    <Component
      aria-hidden="true"
      className={joinClasses(
        'visus-loading-sheen pointer-events-none select-none bg-[color:var(--surface-container-high)]',
        RADIUS_STYLES[rounded] || RADIUS_STYLES.md,
        className,
      )}
      {...props}
    >
      {children}
    </Component>
  );
}

function SkeletonText({
  lines = 1,
  className,
  lineClassName,
  widths = TEXT_WIDTHS,
}) {
  return (
    <div aria-hidden="true" className={joinClasses('space-y-2', className)}>
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton
          key={`skeleton-text-${index}`}
          className={joinClasses('h-3', widths[index % widths.length], lineClassName)}
          rounded="full"
        />
      ))}
    </div>
  );
}

function SkeletonAvatar({ size = 'md', className }) {
  const sizeClass = size === 'sm' ? 'h-8 w-8' : size === 'lg' ? 'h-14 w-14' : 'h-11 w-11';
  return <Skeleton className={joinClasses(sizeClass, 'shrink-0', className)} rounded="full" />;
}

function SkeletonCard({ className, children }) {
  return (
    <div
      aria-hidden="true"
      className={joinClasses('pointer-events-none rounded-card token-surface p-4', className)}
    >
      {children || (
        <div className="space-y-4">
          <Skeleton className="h-36 w-full" rounded="lg" />
          <SkeletonText lines={3} />
        </div>
      )}
    </div>
  );
}

function SkeletonList({ count = 3, className, itemClassName, children }) {
  return (
    <div aria-hidden="true" className={joinClasses('grid gap-3', className)}>
      {Array.from({ length: count }, (_, index) => (
        <SkeletonCard key={`skeleton-list-${index}`} className={itemClassName}>
          {typeof children === 'function' ? children(index) : children}
        </SkeletonCard>
      ))}
    </div>
  );
}

function SkeletonTableRow({ columns = 4, className }) {
  return (
    <tr aria-hidden="true" className={className}>
      {Array.from({ length: columns }, (_, index) => (
        <td key={`skeleton-table-cell-${index}`} className="px-5 py-4 sm:px-8">
          <Skeleton className={index === 0 ? 'h-4 w-40' : 'h-4 w-20'} rounded="full" />
        </td>
      ))}
    </tr>
  );
}

Skeleton.Text = SkeletonText;
Skeleton.Avatar = SkeletonAvatar;
Skeleton.Card = SkeletonCard;
Skeleton.List = SkeletonList;
Skeleton.TableRow = SkeletonTableRow;

export default Skeleton;
