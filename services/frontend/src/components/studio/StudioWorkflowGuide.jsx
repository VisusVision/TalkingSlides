import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Eye,
  LoaderCircle,
  PlayCircle,
  RefreshCcw,
  Save,
  UploadCloud,
} from 'lucide-react';
import Button from '../ui/Button';
import { studioWorkspaceCopy } from './studioWorkspaceCopy';

const WORKFLOW_STEPS = ['edit', 'review', 'render', 'publish', 'watch'];

function normalizeStatus(value) {
  return String(value?.status || value?.state || value || '').trim().toLowerCase();
}

function firstText(...values) {
  return values.map((value) => String(value || '').trim()).find(Boolean) || '';
}

function firstFiniteNumber(...values) {
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number)) return Math.max(0, Math.min(100, number));
  }
  return null;
}

function renderSnapshot({ renderStatus = null, projectStatus = '', renderReady = false } = {}) {
  const status = normalizeStatus(renderStatus) || normalizeStatus(projectStatus);
  const failed = status.includes('fail') || status.includes('error');
  const queued = ['queued', 'pending'].includes(status);
  const running = ['running', 'processing', 'started'].includes(status);
  const active = queued || running;
  const ready = Boolean(renderReady || ['ready', 'done', 'completed', 'published'].includes(status));
  const progress = firstFiniteNumber(
    renderStatus?.progress,
    renderStatus?.progress_pct,
    renderStatus?.percent,
    renderStatus?.percentage,
  );
  const stage = firstText(
    renderStatus?.stage,
    renderStatus?.current_stage,
    renderStatus?.status_label,
    renderStatus?.task,
  );
  const lastResult = firstText(
    renderStatus?.error_message,
    renderStatus?.error,
    renderStatus?.result_message,
    renderStatus?.message,
  );

  return {
    status,
    failed,
    queued,
    running,
    active,
    ready,
    progress,
    stage,
    lastResult,
  };
}

function pushUnique(items, item) {
  if (item && !items.includes(item)) items.push(item);
}

export function studioWorkflowState({
  hasProject = true,
  loadingProject = false,
  apiError = '',
  hasChanges = false,
  requiresRerender = false,
  renderReady = false,
  renderStatus = null,
  projectStatus = '',
  canPublish = true,
  publishBlockedReason = '',
  moderationMessage = '',
  published = false,
  hasSlides = true,
  hasNarration = true,
  avatarReady = true,
  staleProject = false,
  canRetryRender = true,
} = {}) {
  const render = renderSnapshot({ renderStatus, projectStatus, renderReady });
  const blockers = [];
  let activeStep = 'edit';
  let errorStep = '';
  let action = { id: 'review_lesson', labelKey: 'workflowActionReviewLesson', disabled: false };

  if (!hasProject) {
    pushUnique(blockers, 'workflowBlockerNoProject');
    return {
      activeStep: 'edit',
      blockedSteps: ['review', 'render', 'publish', 'watch'],
      completedSteps: [],
      errorStep: '',
      blockers,
      action: { id: 'select_project', labelKey: 'workflowActionSelectProject', disabled: false },
      render,
    };
  }

  if (loadingProject) {
    pushUnique(blockers, 'workflowBlockerProjectLoading');
    return {
      activeStep: 'edit',
      blockedSteps: ['review', 'render', 'publish', 'watch'],
      completedSteps: [],
      errorStep: '',
      blockers,
      action: { id: 'view_progress', labelKey: 'workflowActionViewProgress', disabled: false },
      render,
    };
  }

  if (apiError) pushUnique(blockers, 'workflowBlockerApiError');
  if (staleProject) pushUnique(blockers, 'workflowBlockerStaleProject');

  if (hasChanges) {
    activeStep = 'edit';
    pushUnique(blockers, 'workflowBlockerUnsavedChanges');
    action = { id: 'save_changes', labelKey: 'workflowActionSaveChanges', disabled: false };
    return {
      activeStep,
      blockedSteps: ['review', 'render', 'publish', 'watch'],
      completedSteps: [],
      errorStep: apiError ? activeStep : '',
      blockers,
      action,
      render,
    };
  }

  if (!hasSlides || !hasNarration) {
    activeStep = 'review';
    if (!hasSlides) pushUnique(blockers, 'workflowBlockerNoSlides');
    if (!hasNarration) pushUnique(blockers, 'workflowBlockerMissingNarration');
    action = { id: 'review_lesson', labelKey: 'workflowActionReviewLesson', disabled: false };
    return {
      activeStep,
      blockedSteps: ['render', 'publish', 'watch'],
      completedSteps: ['edit'],
      errorStep: '',
      blockers,
      action,
      render,
    };
  }

  if (render.active) {
    activeStep = 'render';
    pushUnique(blockers, 'workflowBlockerRenderRunning');
    if (!avatarReady) pushUnique(blockers, 'workflowBlockerAvatarNotReady');
    return {
      activeStep,
      blockedSteps: ['publish', 'watch'],
      completedSteps: ['edit', 'review'],
      errorStep: '',
      blockers,
      action: { id: 'view_progress', labelKey: 'workflowActionViewProgress', disabled: false },
      render,
    };
  }

  if (render.failed) {
    activeStep = 'render';
    pushUnique(blockers, 'workflowBlockerRenderFailed');
    return {
      activeStep,
      blockedSteps: ['publish', 'watch'],
      completedSteps: ['edit', 'review'],
      errorStep: 'render',
      blockers,
      action: { id: 'retry_render', labelKey: 'workflowActionRetryRender', disabled: !canRetryRender },
      render,
    };
  }

  if (requiresRerender || !render.ready) {
    activeStep = 'render';
    pushUnique(blockers, requiresRerender ? 'workflowBlockerRenderRequired' : 'workflowBlockerRenderUnavailable');
    return {
      activeStep,
      blockedSteps: ['publish', 'watch'],
      completedSteps: ['edit', 'review'],
      errorStep: '',
      blockers,
      action: { id: 'render_lesson', labelKey: 'workflowActionRenderLesson', disabled: false },
      render,
    };
  }

  if (!canPublish) {
    activeStep = 'publish';
    pushUnique(blockers, publishBlockedReason || moderationMessage ? 'workflowBlockerModerationRequired' : 'workflowBlockerPublishUnavailable');
    return {
      activeStep,
      blockedSteps: ['watch'],
      completedSteps: ['edit', 'review', 'render'],
      errorStep: 'publish',
      blockers,
      action: { id: 'resolve_publishing_issues', labelKey: 'workflowActionResolvePublishingIssues', disabled: false },
      render,
    };
  }

  if (!published) {
    return {
      activeStep: 'publish',
      blockedSteps: ['watch'],
      completedSteps: ['edit', 'review', 'render'],
      errorStep: '',
      blockers,
      action: { id: 'publish_lesson', labelKey: 'workflowActionPublishLesson', disabled: false },
      render,
    };
  }

  return {
    activeStep: 'watch',
    blockedSteps: [],
    completedSteps: ['edit', 'review', 'render', 'publish'],
    errorStep: '',
    blockers,
    action: { id: 'watch_lesson', labelKey: 'workflowActionWatchLesson', disabled: false },
    render,
  };
}

function stepLabel(copy, step) {
  return copy[`workflowStep${step.charAt(0).toUpperCase()}${step.slice(1)}`] || step;
}

function statusKeyForStep(state, step) {
  if (state.errorStep === step) return 'workflowStatusError';
  if (state.activeStep === step) return 'workflowStatusActive';
  if (state.completedSteps.includes(step)) return 'workflowStatusCompleted';
  if (state.blockedSteps.includes(step)) return 'workflowStatusBlocked';
  return 'workflowStatusUpcoming';
}

function statusClass(statusKey) {
  if (statusKey === 'workflowStatusCompleted') return 'border-[color:var(--status-success-fg)] bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]';
  if (statusKey === 'workflowStatusActive') return 'border-[var(--accent-primary)] bg-[color:var(--hover-accent-soft)] text-[var(--accent-primary)]';
  if (statusKey === 'workflowStatusError') return 'border-[color:var(--status-danger-fg)] bg-[color:var(--status-danger-bg)] text-[color:var(--status-danger-fg)]';
  if (statusKey === 'workflowStatusBlocked') return 'border-[var(--border-subtle)] bg-[var(--surface-container-high)] text-[var(--text-secondary)] opacity-80';
  return 'border-[var(--border-subtle)] bg-[var(--surface-container-low)] text-[var(--text-secondary)]';
}

function iconForAction(actionId) {
  if (actionId === 'save_changes') return <Save size={16} />;
  if (actionId === 'render_lesson' || actionId === 'retry_render') return <RefreshCcw size={16} />;
  if (actionId === 'publish_lesson') return <UploadCloud size={16} />;
  if (actionId === 'watch_lesson') return <PlayCircle size={16} />;
  if (actionId === 'resolve_publishing_issues') return <AlertTriangle size={16} />;
  if (actionId === 'select_project') return <Eye size={16} />;
  return <ChevronRight size={16} />;
}

function RenderWorkflowCard({ copy, state, onAction }) {
  const { render } = state;
  const tone = render.failed
    ? 'border-[color:var(--status-danger-fg)] bg-[color:var(--status-danger-bg)]'
    : render.active
      ? 'border-[color:var(--status-info-fg)] bg-[color:var(--status-info-bg)]'
      : 'border-[var(--border-subtle)] bg-[var(--surface-container-high)]';
  const Icon = render.failed ? AlertTriangle : render.active ? LoaderCircle : render.ready ? CheckCircle2 : Clock3;
  const label = render.failed
    ? copy.renderFailed
    : render.queued
      ? copy.renderQueued
      : render.running
        ? copy.renderProcessing
        : render.ready
          ? copy.renderReady
          : copy.renderDraft;

  return (
    <section
      aria-label={copy.workflowRenderPanel}
      aria-live="polite"
      data-testid="studio-workflow-render-card"
      className={`grid gap-3 rounded-xl border p-3 text-sm sm:grid-cols-[auto_minmax(0,1fr)] ${tone}`}
    >
      <Icon size={18} className={render.active ? 'mt-0.5 animate-spin text-[color:var(--status-info-fg)]' : 'mt-0.5 text-[var(--accent-primary)]'} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p data-testid="studio-render-status" className="font-semibold text-[var(--text-primary)]">
            {copy.renderStatus}: {label}
          </p>
          {render.failed && state.action?.id === 'retry_render' && (
            <button
              type="button"
              className="focus-ring rounded-full px-2 py-1 text-xs font-semibold text-[color:var(--status-danger-fg)] underline-offset-2 hover:underline disabled:opacity-60"
              disabled={state.action.disabled}
              onClick={() => onAction?.(state.action.id)}
            >
              {copy.workflowActionRetryRender}
            </button>
          )}
        </div>
        <dl className="mt-2 grid gap-2 text-xs text-[var(--text-secondary)] sm:grid-cols-2">
          <div>
            <dt className="font-semibold text-[var(--text-primary)]">{copy.workflowRenderStage}</dt>
            <dd>{render.stage || label}</dd>
          </div>
          <div>
            <dt className="font-semibold text-[var(--text-primary)]">{copy.workflowRenderProgress}</dt>
            <dd>{render.progress === null ? copy.workflowNoEta : `${Math.round(render.progress)}%`}</dd>
          </div>
          <div>
            <dt className="font-semibold text-[var(--text-primary)]">{copy.workflowRenderLastResult}</dt>
            <dd>{render.lastResult || (render.ready ? copy.renderReadyHint : copy.renderIdleHint)}</dd>
          </div>
          <div>
            <dt className="font-semibold text-[var(--text-primary)]">{copy.workflowRenderPartial}</dt>
            <dd>{copy.workflowNoEta}</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}

export default function StudioWorkflowGuide({
  locale = '',
  onAction,
  ...props
}) {
  const copy = studioWorkspaceCopy(locale || (typeof document === 'undefined' ? 'en' : document.documentElement.lang));
  const state = props.state || studioWorkflowState(props);
  const actionLabel = copy[state.action?.labelKey] || '';
  const blockers = state.blockers || [];

  return (
    <section
      data-testid="studio-workflow-guide"
      aria-label={copy.workflowLabel}
      className="grid min-w-0 gap-3 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-container-low)] p-3 lg:grid-cols-[minmax(0,1.4fr)_minmax(18rem,0.9fr)]"
    >
      <div className="min-w-0 space-y-3">
        <ol className="flex min-w-0 flex-wrap items-center gap-1 text-xs font-semibold" aria-label={copy.workflowLabel}>
          {WORKFLOW_STEPS.map((step, index) => {
            const statusKey = statusKeyForStep(state, step);
            const statusLabel = copy[statusKey];
            const label = stepLabel(copy, step);
            return (
              <li key={step} className="inline-flex items-center gap-1">
                {index > 0 && <ChevronRight size={13} aria-hidden="true" className="text-[var(--outline)]" />}
                <button
                  type="button"
                  aria-current={state.activeStep === step ? 'step' : undefined}
                  aria-label={`${label}: ${statusLabel}`}
                  className={`focus-ring inline-flex min-h-9 items-center gap-1 rounded-full border px-3 py-1 ${statusClass(statusKey)}`}
                >
                  {statusKey === 'workflowStatusCompleted' && <CheckCircle2 size={13} aria-hidden="true" />}
                  {statusKey === 'workflowStatusError' && <AlertTriangle size={13} aria-hidden="true" />}
                  <span>{label}</span>
                </button>
              </li>
            );
          })}
        </ol>

        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-start">
          <div className="min-w-0 rounded-xl bg-[var(--surface-container-high)] p-3">
            <p className="text-xs font-semibold uppercase text-[var(--text-secondary)]">{copy.workflowBlockers}</p>
            {blockers.length ? (
              <ul className="mt-2 space-y-1 text-sm text-[var(--text-primary)]">
                {blockers.map((blocker) => (
                  <li key={blocker} className="flex gap-2">
                    <AlertTriangle size={15} className="mt-0.5 shrink-0 text-[color:var(--status-warning-fg)]" />
                    <span>{copy[blocker] || blocker}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-[var(--text-secondary)]">
                {state.activeStep === 'watch' ? copy.workflowPublishedState : copy.workflowNoBlockers}
              </p>
            )}
            {props.published && props.hasChanges && (
              <p className="mt-2 text-xs font-semibold text-[color:var(--status-warning-fg)]">{copy.workflowEditRequiresRerender}</p>
            )}
          </div>

          <div className="min-w-0 rounded-xl bg-[var(--surface-container-high)] p-3 md:min-w-56">
            <p className="text-xs font-semibold uppercase text-[var(--text-secondary)]">{copy.workflowNextAction}</p>
            <Button
              className="mt-2 w-full"
              disabled={!state.action || state.action.disabled}
              onClick={() => state.action && onAction?.(state.action.id)}
            >
              {iconForAction(state.action?.id)}
              <span>{actionLabel}</span>
            </Button>
          </div>
        </div>
      </div>

      <RenderWorkflowCard copy={copy} state={state} onAction={onAction} />
    </section>
  );
}
