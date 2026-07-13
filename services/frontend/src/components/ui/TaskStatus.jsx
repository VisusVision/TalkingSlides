import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  LoaderCircle,
  UploadCloud,
  XCircle,
} from 'lucide-react';

function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

const STATE_CONFIG = {
  idle: {
    icon: Clock3,
    tone: 'border-[var(--border-subtle)] bg-[var(--surface-container-high)]',
    iconTone: 'text-[var(--text-secondary)]',
  },
  uploading: {
    icon: UploadCloud,
    tone: 'border-[color:var(--status-info-fg)] bg-[color:var(--status-info-bg)]',
    iconTone: 'text-[color:var(--status-info-fg)]',
    active: true,
  },
  queued: {
    icon: Clock3,
    tone: 'border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)]',
    iconTone: 'text-[color:var(--status-warning-fg)]',
    active: true,
  },
  waiting: {
    icon: Clock3,
    tone: 'border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)]',
    iconTone: 'text-[color:var(--status-warning-fg)]',
    active: true,
  },
  processing: {
    icon: LoaderCircle,
    tone: 'border-[color:var(--status-info-fg)] bg-[color:var(--status-info-bg)]',
    iconTone: 'text-[color:var(--status-info-fg)]',
    active: true,
    spin: true,
  },
  completed: {
    icon: CheckCircle2,
    tone: 'border-[color:var(--status-success-fg)] bg-[color:var(--status-success-bg)] motion-task-complete',
    iconTone: 'text-[color:var(--status-success-fg)]',
  },
  failed: {
    icon: AlertTriangle,
    tone: 'border-[color:var(--status-danger-fg)] bg-[color:var(--status-danger-bg)] motion-task-failed',
    iconTone: 'text-[color:var(--status-danger-fg)]',
  },
  cancelled: {
    icon: XCircle,
    tone: 'border-[color:var(--status-danger-fg)] bg-[color:var(--status-danger-bg)]',
    iconTone: 'text-[color:var(--status-danger-fg)]',
  },
};

function clampProgress(progress, state) {
  if (progress === null || progress === undefined || progress === '') return null;
  if (!Number.isFinite(Number(progress))) return null;
  const value = Math.max(0, Math.min(100, Number(progress)));
  return state === 'completed' ? value : Math.min(value, 99);
}

export default function TaskStatus({
  state = 'idle',
  title,
  description = '',
  progress = null,
  stage = '',
  action = null,
  className = '',
  progressLabel = '',
  live = 'polite',
  ...props
}) {
  const normalizedState = STATE_CONFIG[state] ? state : 'idle';
  const config = STATE_CONFIG[normalizedState];
  const Icon = config.icon;
  const displayProgress = clampProgress(progress, normalizedState);
  const hasDeterminateProgress = displayProgress !== null;
  const showIndeterminate = config.active && !hasDeterminateProgress;
  const resolvedProgressLabel = progressLabel || `${Math.round(displayProgress ?? 0)}%`;

  return (
    <section
      aria-live={live}
      data-state={normalizedState}
      className={joinClasses(
        'motion-task-active flex min-w-0 flex-col gap-3 rounded-xl border px-3 py-3 text-start',
        config.tone,
        className,
      )}
      {...props}
    >
      <div className="flex min-w-0 items-start gap-3">
        <Icon
          size={18}
          aria-hidden="true"
          className={joinClasses(
            'mt-0.5 shrink-0',
            config.iconTone,
            config.spin && 'animate-spin',
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <p className="text-sm font-semibold leading-tight text-[var(--text-primary)]">
              {title}
            </p>
            {stage && (
              <span className="max-w-full rounded-full bg-[var(--surface-container-lowest)] px-2 py-0.5 text-[0.68rem] font-semibold text-[var(--text-secondary)] [overflow-wrap:anywhere]">
                {stage}
              </span>
            )}
          </div>
          {description && (
            <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
              {description}
            </p>
          )}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>

      {hasDeterminateProgress && (
        <div className="space-y-1">
          <div className="flex items-center justify-between gap-3 text-[0.68rem] font-semibold text-[var(--text-secondary)]">
            <span>{stage || title}</span>
            <span>{resolvedProgressLabel}</span>
          </div>
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(displayProgress)}
            aria-label={title}
            className="h-2 overflow-hidden rounded-full bg-[var(--surface-container-lowest)]"
          >
            <span
              className="block h-full w-full origin-left rounded-full bg-[var(--accent-primary)] transition-transform duration-fast ease-standard rtl:origin-right"
              style={{ transform: `scaleX(${displayProgress / 100})` }}
            />
          </div>
        </div>
      )}

      {showIndeterminate && (
        <div
          className="motion-task-progress h-2 rounded-full bg-[var(--surface-container-lowest)]"
          aria-hidden="true"
        />
      )}
    </section>
  );
}
