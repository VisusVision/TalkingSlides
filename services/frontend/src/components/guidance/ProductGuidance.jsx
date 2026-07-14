import { AlertTriangle, CheckCircle2, Circle, CircleCheck, LoaderCircle, XCircle } from 'lucide-react';
import { useId } from 'react';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import SurfaceCard from '../ui/SurfaceCard';
import { useProductGuidanceCopy } from './productGuidanceCopy';

function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

const STATUS_CONFIG = {
  ready: { variant: 'success', icon: CheckCircle2, copyKey: 'statusReady' },
  'needs-attention': { variant: 'warning', icon: AlertTriangle, copyKey: 'statusNeedsAttention' },
  processing: { variant: 'info', icon: LoaderCircle, copyKey: 'statusProcessing', spinning: true },
  failed: { variant: 'danger', icon: XCircle, copyKey: 'statusFailed' },
  completed: { variant: 'success', icon: CircleCheck, copyKey: 'statusCompleted' },
  neutral: { variant: 'neutral', icon: Circle, copyKey: 'statusNeutral' },
};

function visibleAction(action) {
  return Boolean(action?.label && (typeof action.onClick === 'function' || action.href));
}

function actionKey(action) {
  return action.key || action.label || action.href;
}

function GuidanceAction({ action, fallbackVariant = 'secondary' }) {
  if (!visibleAction(action)) return null;

  const content = (
    <>
      {action.icon}
      <span>{action.label}</span>
    </>
  );

  if (action.href) {
    return (
      <a
        href={action.href}
        className={joinClasses(
          'focus-ring motion-interactive inline-flex h-control-sm items-center justify-center gap-2 rounded-pill bg-[var(--surface-container-highest)] px-3 text-sm font-semibold text-[var(--text-primary)] hover:bg-[color:var(--hover-surface-strong)]',
          action.className,
        )}
        title={action.title || action.label}
      >
        {content}
      </a>
    );
  }

  return (
    <Button
      variant={action.variant || fallbackVariant}
      size="sm"
      onClick={action.onClick}
      disabled={action.disabled}
      title={action.title || action.label}
      className={action.className}
    >
      {content}
    </Button>
  );
}

function normalizedStatus(status) {
  return Object.prototype.hasOwnProperty.call(STATUS_CONFIG, status) ? status : 'neutral';
}

export default function ProductGuidance({
  status = 'neutral',
  eyebrow = '',
  title = '',
  description = '',
  primaryAction = null,
  secondaryActions = [],
  items = [],
  className = '',
  headingId = '',
  itemsLabel = '',
  actionTitle = '',
  copy: providedCopy = null,
  ...props
}) {
  const documentCopy = useProductGuidanceCopy();
  const copy = providedCopy || documentCopy;
  const generatedHeadingId = useId();
  const resolvedHeadingId = headingId || `product-guidance-${generatedHeadingId}`;
  const resolvedStatus = normalizedStatus(status);
  const statusConfig = STATUS_CONFIG[resolvedStatus];
  const StatusIcon = statusConfig.icon;
  const visibleItems = items.filter((item) => item?.title || item?.description || item?.metadata);
  const visibleSecondaryActions = secondaryActions.filter(visibleAction);
  const hasPrimaryAction = visibleAction(primaryAction);
  const hasActions = hasPrimaryAction || visibleSecondaryActions.length > 0;

  return (
    <SurfaceCard
      as="section"
      variant="elevated"
      padding="lg"
      data-testid="product-guidance"
      data-status={resolvedStatus}
      aria-labelledby={resolvedHeadingId}
      className={joinClasses('motion-fade min-w-0 overflow-hidden', className)}
      {...props}
    >
      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Badge variant={statusConfig.variant} size="md">
              <StatusIcon
                size={14}
                className={statusConfig.spinning ? 'motion-safe:animate-spin' : ''}
                aria-hidden="true"
              />
              <span>{copy[statusConfig.copyKey]}</span>
            </Badge>
            {(eyebrow || copy.eyebrow) && (
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--accent-primary)]">
                {eyebrow || copy.eyebrow}
              </p>
            )}
          </div>
          <h2 id={resolvedHeadingId} className="mt-2 text-lg font-bold leading-snug text-[var(--text-primary)]">
            {title}
          </h2>
          {description && (
            <p className="mt-1 max-w-3xl text-sm leading-6 text-[var(--text-secondary)]">
              {description}
            </p>
          )}
        </div>

        {hasActions && (
          <div className="min-w-0 space-y-2 lg:min-w-[13rem] lg:max-w-[20rem] lg:text-end">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--accent-primary)]">
              {actionTitle || copy.actionTitle}
            </p>
            <div className="flex min-w-0 flex-wrap gap-2 lg:justify-end">
              {hasPrimaryAction && <GuidanceAction action={primaryAction} fallbackVariant="primary" />}
              {visibleSecondaryActions.map((action) => (
                <GuidanceAction key={actionKey(action)} action={action} />
              ))}
            </div>
          </div>
        )}
      </div>

      {visibleItems.length > 0 && (
        <ul
          className="mt-4 grid min-w-0 gap-2 border-t border-[var(--border-subtle)] pt-4 md:grid-cols-3"
          aria-label={itemsLabel || copy.itemsLabel}
        >
          {visibleItems.slice(0, 3).map((item) => {
            const itemStatus = normalizedStatus(item.status);
            const itemConfig = STATUS_CONFIG[itemStatus];
            const ItemIcon = itemConfig.icon;
            return (
              <li
                key={item.key || item.title || item.description}
                className="min-w-0 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-container-low)] p-3"
              >
                <div className="flex min-w-0 items-start gap-2">
                  <span
                    className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-[var(--surface-container-high)] text-[var(--text-secondary)]"
                    aria-hidden="true"
                  >
                    <ItemIcon size={13} className={itemConfig.spinning ? 'motion-safe:animate-spin' : ''} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      {item.title && (
                        <p className="min-w-0 text-sm font-semibold text-[var(--text-primary)]">
                          {item.title}
                        </p>
                      )}
                      <Badge variant={itemConfig.variant}>{item.statusLabel || copy[itemConfig.copyKey]}</Badge>
                    </div>
                    {item.description && (
                      <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                        {item.description}
                      </p>
                    )}
                    {item.metadata && (
                      <p className="mt-2 text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-[var(--outline)]">
                        {item.metadata}
                      </p>
                    )}
                    {visibleAction(item.action) && (
                      <div className="mt-3">
                        <GuidanceAction action={item.action} />
                      </div>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </SurfaceCard>
  );
}
