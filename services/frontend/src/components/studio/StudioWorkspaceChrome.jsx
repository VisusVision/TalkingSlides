import {
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  CheckCircle2,
  ChevronDown,
  Clipboard,
  Copy,
  Clock3,
  LoaderCircle,
  MoreVertical,
  PanelsTopLeft,
  Pencil,
  Sparkles,
  Trash2,
} from 'lucide-react';
import { createPortal } from 'react-dom';
import { useEffect, useState } from 'react';
import { studioWorkspaceCopy } from './studioWorkspaceCopy';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import TaskStatus from '../ui/TaskStatus';

function useDocumentLocale() {
  const readLocale = () => (
    typeof document === 'undefined' ? 'en' : document.documentElement.lang || 'en'
  );
  const [locale, setLocale] = useState(readLocale);

  useEffect(() => {
    if (typeof document === 'undefined' || typeof MutationObserver === 'undefined') return undefined;
    const observer = new MutationObserver(() => setLocale(readLocale()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['lang'] });
    return () => observer.disconnect();
  }, []);

  return locale;
}

export function useStudioWorkspaceCopy() {
  return studioWorkspaceCopy(useDocumentLocale());
}

function sceneStatusTone(status) {
  const value = String(status || '').toLowerCase();
  if (value === 'ready' || value === 'done' || value === 'completed') return 'text-[color:var(--status-success-fg)]';
  if (value.includes('fail') || value.includes('error')) return 'text-[color:var(--status-danger-fg)]';
  if (value === 'running' || value === 'processing' || value === 'queued' || value === 'pending') {
    return 'text-[color:var(--status-info-fg)]';
  }
  return 'text-[var(--text-secondary)]';
}

export function StudioSlideRail({
  scenes = [],
  selectedSceneKey = '',
  loading = false,
  onSelect,
  onReorder,
  onMove,
  onDelete,
  onCopy,
  onPaste,
  onDuplicate,
  onRename,
  readOnly = false,
  actionBusy = false,
  canPaste = false,
  supportsDuplicate = false,
  supportsRename = false,
}) {
  const copy = studioWorkspaceCopy(useDocumentLocale());
  const [contextMenu, setContextMenu] = useState(null);
  const [draggedIndex, setDraggedIndex] = useState(null);
  const [dragOverIndex, setDragOverIndex] = useState(null);

  useEffect(() => {
    if (!contextMenu) return undefined;
    const close = () => setContextMenu(null);
    window.addEventListener('click', close);
    window.addEventListener('keydown', close);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', close, true);
    return () => {
      window.removeEventListener('click', close);
      window.removeEventListener('keydown', close);
      window.removeEventListener('resize', close);
      window.removeEventListener('scroll', close, true);
    };
  }, [contextMenu]);

  const actionDisabled = Boolean(readOnly || actionBusy);
  const selectAdjacent = (index, direction) => {
    const nextIndex = direction === 'up'
      ? Math.max(0, index - 1)
      : Math.min(scenes.length - 1, index + 1);
    if (nextIndex !== index) onSelect?.(scenes[nextIndex], nextIndex);
  };

  const openContextMenu = (event, scene, index) => {
    event.preventDefault();
    onSelect?.(scene, index);
    setContextMenu({
      scene,
      index,
      x: Math.min(event.clientX, window.innerWidth - 220),
      y: Math.min(event.clientY, window.innerHeight - 280),
    });
  };

  const runContextAction = (handler) => {
    setContextMenu(null);
    handler?.();
  };

  const menu = contextMenu ? createPortal(
    <div
      role="menu"
      aria-label={copy.slideActions}
      className="motion-popover-in fixed z-50 w-52 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-1.5 shadow-xl"
      style={{ left: contextMenu.x, top: contextMenu.y }}
      onClick={(event) => event.stopPropagation()}
    >
      <SlideMenuItem
        icon={<Copy size={14} />}
        label={copy.duplicate}
        disabled={!supportsDuplicate || actionDisabled}
        title={supportsDuplicate ? copy.duplicate : copy.duplicateUnavailable}
        onClick={() => runContextAction(() => onDuplicate?.(contextMenu.scene, contextMenu.index))}
      />
      <SlideMenuItem
        icon={<Trash2 size={14} />}
        label={copy.delete}
        disabled={actionDisabled || scenes.length <= 1}
        danger
        onClick={() => runContextAction(() => onDelete?.(contextMenu.scene, contextMenu.index))}
      />
      <SlideMenuItem
        icon={<ArrowUp size={14} />}
        label={copy.moveUp}
        disabled={actionDisabled || contextMenu.index <= 0}
        onClick={() => runContextAction(() => onMove?.(contextMenu.scene, contextMenu.index, 'up'))}
      />
      <SlideMenuItem
        icon={<ArrowDown size={14} />}
        label={copy.moveDown}
        disabled={actionDisabled || contextMenu.index >= scenes.length - 1}
        onClick={() => runContextAction(() => onMove?.(contextMenu.scene, contextMenu.index, 'down'))}
      />
      <div className="my-1 h-px bg-[var(--border-subtle)]" />
      <SlideMenuItem
        icon={<Clipboard size={14} />}
        label={copy.copy}
        disabled={!onCopy}
        onClick={() => runContextAction(() => onCopy?.(contextMenu.scene, contextMenu.index))}
      />
      <SlideMenuItem
        icon={<Clipboard size={14} />}
        label={copy.paste}
        disabled={!canPaste || !onPaste || actionDisabled}
        title={canPaste ? copy.paste : copy.pasteUnavailable}
        onClick={() => runContextAction(() => onPaste?.(contextMenu.scene, contextMenu.index))}
      />
      <SlideMenuItem
        icon={<Pencil size={14} />}
        label={copy.rename}
        disabled={!supportsRename || actionDisabled}
        title={supportsRename ? copy.rename : copy.renameUnavailable}
        onClick={() => runContextAction(() => onRename?.(contextMenu.scene, contextMenu.index))}
      />
    </div>,
    document.body,
  ) : null;

  return (
    <aside
      aria-label={copy.slides}
      aria-busy={loading}
      data-testid="studio-slide-rail"
      className="min-w-0 xl:sticky xl:top-4 xl:self-start"
    >
      <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-container-lowest)] p-2.5 shadow-token-xs">
        <div className="flex items-center gap-2">
          <PanelsTopLeft size={16} className="text-[var(--accent-primary)]" />
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">{copy.slides}</h2>
            <p className="text-[0.68rem] text-[var(--text-secondary)]">{copy.slidesHint}</p>
          </div>
          <span className="ms-auto rounded-full bg-[var(--surface-container-high)] px-2 py-0.5 text-xs text-[var(--text-secondary)]">
            {scenes.length} {copy.slideCount}
          </span>
        </div>

        {loading ? (
          <div className="mt-3 flex gap-2 overflow-hidden xl:block xl:space-y-2" role="status">
            <span className="sr-only">{copy.loadingSlides}</span>
            {[0, 1, 2].map((item) => (
              <div
                key={item}
                className="visus-loading-sheen h-28 min-w-40 rounded-xl bg-[var(--surface-container-high)] xl:min-w-0"
              />
            ))}
          </div>
        ) : scenes.length === 0 ? (
          <div className="mt-3 rounded-xl border border-dashed border-[var(--border-subtle)] p-4 text-center">
            <p className="text-sm font-semibold text-[var(--text-primary)]">{copy.noSlides}</p>
            <p className="mt-1 text-xs text-[var(--text-secondary)]">{copy.noSlidesHint}</p>
          </div>
        ) : (
          <div
            className="rail-scroll mt-3 flex gap-2 overflow-x-auto pb-2 xl:max-h-[calc(100vh-12rem)] xl:flex-col xl:overflow-y-auto xl:overflow-x-hidden xl:pr-1"
            role="list"
          >
            {scenes.map((scene, index) => {
              const selected = scene.key === selectedSceneKey;
              const dropTarget = dragOverIndex === index && draggedIndex !== index;
              const canMoveUp = index > 0;
              const canMoveDown = index < scenes.length - 1;
              return (
                <div
                  key={scene.key}
                  role="listitem"
                  draggable={!actionDisabled && scenes.length > 1}
                  onDragStart={(event) => {
                    setDraggedIndex(index);
                    event.dataTransfer.effectAllowed = 'move';
                    event.dataTransfer.setData('text/plain', scene.key);
                  }}
                  onDragOver={(event) => {
                    if (actionDisabled || draggedIndex === null) return;
                    event.preventDefault();
                    setDragOverIndex(index);
                  }}
                  onDragLeave={() => setDragOverIndex(null)}
                  onDrop={(event) => {
                    event.preventDefault();
                    if (draggedIndex !== null && draggedIndex !== index) {
                      onReorder?.(scenes[draggedIndex], draggedIndex, index);
                    }
                    setDraggedIndex(null);
                    setDragOverIndex(null);
                  }}
                  onDragEnd={() => {
                    setDraggedIndex(null);
                    setDragOverIndex(null);
                  }}
                  onContextMenu={(event) => openContextMenu(event, scene, index)}
                  data-selected={selected ? 'true' : undefined}
                  data-drop-target={dropTarget ? 'true' : undefined}
                  className={`group motion-studio-selection relative min-w-40 rounded-xl border xl:min-w-0 ${
                    selected
                      ? 'border-[var(--accent-primary)] bg-[var(--surface-container-highest)] shadow-sm ring-2 ring-[color:var(--hover-accent-soft)]'
                      : dropTarget
                        ? 'border-[var(--accent-primary)] bg-[color:var(--hover-accent-soft)]'
                        : 'border-transparent bg-[var(--surface-container-high)] hover:border-[var(--border-subtle)]'
                  }`}
                  title={copy.dragToReorder}
                >
                  {selected && (
                    <span className="motion-nav-indicator absolute bottom-2 start-0 top-2 w-1 rounded-e-full bg-[var(--accent-primary)]" aria-hidden="true" />
                  )}
                  <button
                    type="button"
                    aria-current={selected ? 'true' : undefined}
                    aria-label={`${copy.selectSlide} ${index + 1}: ${scene.label || `${copy.slide} ${index + 1}`}`}
                    onClick={() => onSelect?.(scene, index)}
                    onKeyDown={(event) => {
                      if (event.key === 'ArrowUp') {
                        event.preventDefault();
                        selectAdjacent(index, 'up');
                      } else if (event.key === 'ArrowDown') {
                        event.preventDefault();
                        selectAdjacent(index, 'down');
                      }
                    }}
                    className="focus-ring block w-full rounded-xl p-2 ps-3 text-start"
                  >
                    <div
                      className="relative aspect-video overflow-hidden rounded-lg bg-[var(--card-fallback)] bg-contain bg-center bg-no-repeat"
                      style={scene.thumbnailUrl ? { backgroundImage: `url(${scene.thumbnailUrl})` } : undefined}
                    >
                      {!scene.thumbnailUrl && (
                        <span className="flex h-full items-center justify-center text-xl font-bold text-[var(--accent-primary)]">
                          {index + 1}
                        </span>
                      )}
                      <span className="absolute start-1 top-1 rounded bg-black/70 px-1.5 py-0.5 text-[0.62rem] font-bold text-white">
                        {index + 1}
                      </span>
                    </div>
                    <div className="mt-2 flex items-start justify-between gap-2">
                      <span className="line-clamp-1 text-xs font-semibold text-[var(--text-primary)]">
                        {scene.label || `${copy.slide} ${index + 1}`}
                      </span>
                      <span className={`shrink-0 text-[0.62rem] font-semibold ${sceneStatusTone(scene.status)}`}>
                        {scene.status || 'draft'}
                      </span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-[0.68rem] leading-4 text-[var(--text-secondary)]">
                      {scene.text || scene.fullText || copy.noSlides}
                    </p>
                  </button>
                  <div className="motion-studio-status absolute end-2 top-2 flex gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100">
                    <IconActionButton
                      label={copy.moveUp}
                      disabled={actionDisabled || !canMoveUp}
                      onClick={() => onMove?.(scene, index, 'up')}
                    >
                      <ArrowUp size={13} />
                    </IconActionButton>
                    <IconActionButton
                      label={copy.moveDown}
                      disabled={actionDisabled || !canMoveDown}
                      onClick={() => onMove?.(scene, index, 'down')}
                    >
                      <ArrowDown size={13} />
                    </IconActionButton>
                    <IconActionButton
                      label={copy.slideActions}
                      onClick={(event) => openContextMenu(event, scene, index)}
                    >
                      <MoreVertical size={13} />
                    </IconActionButton>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
      {menu}
    </aside>
  );
}

function IconActionButton({ label, disabled = false, onClick, children }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={(event) => {
        event.stopPropagation();
        onClick?.(event);
      }}
      className="focus-ring motion-interactive inline-flex h-7 w-7 items-center justify-center rounded-full border border-white/25 bg-black/70 text-white shadow-sm hover:bg-black/85 enabled:active:scale-[0.96] disabled:opacity-40"
    >
      {children}
    </button>
  );
}

function SlideMenuItem({ icon, label, disabled = false, title = '', danger = false, onClick }) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      title={title || label}
      onClick={onClick}
      className={`focus-ring motion-interactive flex min-h-9 w-full items-center gap-2 rounded-lg px-2 text-start text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50 ${
        danger
          ? 'text-[color:var(--status-danger-fg)] hover:bg-[color:var(--status-danger-bg)]'
          : 'text-[var(--text-primary)] hover:bg-[color:var(--hover-surface)]'
      }`}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

function normalizedRenderStatus(renderStatus) {
  return String(renderStatus?.status || renderStatus || '').trim().toLowerCase();
}

function renderProgressValue(renderStatus, state) {
  if (!renderStatus || typeof renderStatus !== 'object') return null;
  if (state !== 'processing' && state !== 'completed') return null;
  const progress = Number(renderStatus.progress);
  return Number.isFinite(progress) ? progress : null;
}

export function StudioRenderStatus({ copy: providedCopy = null, renderStatus, projectStatus = '' }) {
  const documentCopy = useStudioWorkspaceCopy();
  const copy = providedCopy || documentCopy;
  const status = normalizedRenderStatus(renderStatus) || String(projectStatus || '').toLowerCase();
  const failed = status.includes('fail') || status.includes('error');
  const active = ['running', 'processing', 'started'].includes(status);
  const queued = ['queued', 'pending'].includes(status);
  const ready = ['ready', 'done', 'completed', 'published'].includes(status);
  const state = failed ? 'failed' : active ? 'processing' : queued ? 'queued' : ready ? 'completed' : 'idle';
  const dataState = failed ? 'failed' : active ? 'active' : queued ? 'queued' : ready ? 'ready' : 'draft';
  const label = failed
    ? copy.renderFailed
    : active
      ? copy.renderProcessing
      : queued
        ? copy.renderQueued
        : ready
          ? copy.renderReady
          : copy.renderDraft;
  const hint = failed
    ? copy.renderFailedHint
    : active || queued
      ? copy.renderActiveHint
      : ready
        ? copy.renderReadyHint
        : copy.renderIdleHint;
  const progress = renderProgressValue(renderStatus, state);
  const errorMessage = failed && typeof renderStatus === 'object'
    ? String(renderStatus.error_message || '').trim()
    : '';
  const stage = status ? status.replace(/_/g, ' ') : '';

  return (
    <TaskStatus
      data-testid="studio-render-status"
      data-render-state={dataState}
      aria-label={copy.renderStatus}
      state={state}
      title={`${copy.renderStatus}: ${label}`}
      description={errorMessage || hint}
      progress={progress}
      stage={stage}
      className="motion-studio-status"
    />
  );
}

function workflowStepTone(status) {
  if (status === 'complete') {
    return {
      shell: 'border-[color:var(--status-success-fg)] bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]',
      dot: 'bg-[color:var(--status-success-fg)] text-[var(--surface-container-lowest)]',
    };
  }
  if (status === 'active') {
    return {
      shell: 'border-[color:var(--accent-primary)] bg-[color:var(--hover-accent-soft)] text-[var(--accent-primary)] shadow-token-xs',
      dot: 'bg-[var(--accent-primary)] text-[var(--accent-inverse)]',
    };
  }
  if (status === 'blocked') {
    return {
      shell: 'border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]',
      dot: 'bg-[color:var(--status-warning-fg)] text-[var(--surface-container-lowest)]',
    };
  }
  return {
    shell: 'border-transparent bg-[var(--surface-container-low)] text-[var(--text-secondary)]',
    dot: 'bg-[var(--surface-container-highest)] text-[var(--text-secondary)]',
  };
}

export function StudioWorkflowStrip({ steps = [] }) {
  return (
    <section
      aria-label="Studio workflow"
      data-testid="studio-workflow-strip"
      className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-container-lowest)] p-3 shadow-token-xs"
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <p className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.08em] text-[var(--accent-primary)]">
            <Sparkles size={13} />
            <span>AI-assisted studio flow</span>
          </p>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Write, choose avatar, preview, render, publish, and share from one workspace.
          </p>
        </div>
        <ol className="rail-scroll flex min-w-0 gap-2 overflow-x-auto pb-1 lg:justify-end lg:pb-0" aria-label="Production steps">
          {steps.map((step, index) => {
            const tone = workflowStepTone(step.status);
            const complete = step.status === 'complete';
            return (
              <li key={step.key || step.label} className="flex shrink-0 items-center gap-2">
                <div
                  className={`motion-studio-status min-w-[7.5rem] rounded-xl border px-3 py-2 ${tone.shell}`}
                  aria-current={step.status === 'active' ? 'step' : undefined}
                >
                  <span className="flex items-center gap-2">
                    <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-full text-[0.65rem] font-bold ${tone.dot}`}>
                      {complete ? <CheckCircle2 size={12} /> : index + 1}
                    </span>
                    <span className="text-sm font-semibold">{step.label}</span>
                  </span>
                  {step.detail && (
                    <span className="mt-1 block truncate text-[0.68rem] opacity-85">{step.detail}</span>
                  )}
                </div>
                {index < steps.length - 1 && (
                  <ArrowRight size={14} className="shrink-0 text-[var(--text-secondary)]" aria-hidden="true" />
                )}
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}

export function StudioCreatorHeader({
  copy: providedCopy = null,
  title = '',
  description = '',
  metadata = [],
  chips = [],
  nextActionTitle = '',
  nextActionDetail = '',
  primaryAction = null,
  secondaryActions = [],
  renderStatus = null,
  projectStatus = '',
}) {
  const documentCopy = useStudioWorkspaceCopy();
  const copy = providedCopy || documentCopy;
  const visibleMetadata = metadata.filter((item) => item?.label && item?.value);
  const visibleChips = chips.filter((chip) => chip?.label);
  const visibleSecondaryActions = secondaryActions.filter((action) => action?.label);

  return (
    <section
      data-testid="studio-creator-header"
      aria-labelledby="studio-creator-header-title"
      className="motion-studio-panel rounded-2xl bg-[var(--surface-container-lowest)] px-4 py-4 shadow-token-sm sm:px-5 sm:py-5"
    >
      <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(18rem,24rem)] xl:items-start">
        <div className="min-w-0">
          <p className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.08em] text-[var(--accent-primary)]">
            <Sparkles size={13} />
            <span>{copy.creatorEyebrow}</span>
          </p>
          <h2
            id="studio-creator-header-title"
            className="mt-2 break-words text-2xl font-bold leading-tight text-[var(--text-primary)] sm:text-3xl"
          >
            {title || copy.creatorUntitledLesson}
          </h2>
          {description && (
            <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--text-secondary)]">
              {description}
            </p>
          )}

          {visibleMetadata.length > 0 && (
            <dl
              aria-label={copy.creatorMetadataLabel}
              className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-[var(--text-secondary)]"
            >
              {visibleMetadata.map((item, index) => (
                <div
                  key={item.key || item.label}
                  className={`flex min-w-0 items-center gap-1.5 ${index > 0 ? 'border-s border-[var(--border-subtle)] ps-3' : ''}`}
                >
                  <dt className="shrink-0 font-semibold text-[var(--text-primary)]">{item.label}</dt>
                  <dd className="min-w-0 truncate">{item.value}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>

        <div className="min-w-0 space-y-3 xl:text-end">
          {visibleChips.length > 0 && (
            <div className="flex flex-wrap gap-2 xl:justify-end" aria-label={copy.creatorSummaryLabel}>
              {visibleChips.map((chip) => (
                <Badge
                  key={chip.key || chip.label}
                  variant={chip.variant || 'neutral'}
                  size="md"
                  title={chip.title || chip.label}
                >
                  {chip.icon}
                  <span>{chip.label}</span>
                </Badge>
              ))}
            </div>
          )}
          <StudioRenderStatus copy={copy} renderStatus={renderStatus} projectStatus={projectStatus} />
        </div>
      </div>

      <div className="mt-4 grid gap-3 border-t border-[var(--border-subtle)] pt-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--accent-primary)]">
            {copy.creatorNextBestAction}
          </p>
          <p className="mt-1 text-sm font-semibold text-[var(--text-primary)]">
            {nextActionTitle || copy.creatorContinueEditing}
          </p>
          {nextActionDetail && (
            <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
              {nextActionDetail}
            </p>
          )}
        </div>

        <div className="flex min-w-0 flex-wrap gap-2 lg:justify-end">
          {primaryAction?.label && (
            <Button
              variant={primaryAction.variant || 'primary'}
              onClick={primaryAction.onClick}
              disabled={primaryAction.disabled}
              title={primaryAction.title || primaryAction.label}
            >
              {primaryAction.icon}
              <span>{primaryAction.label}</span>
            </Button>
          )}
          {visibleSecondaryActions.map((action) => (
            <Button
              key={action.key || action.label}
              variant={action.variant || 'secondary'}
              onClick={action.onClick}
              disabled={action.disabled}
              title={action.title || action.label}
            >
              {action.icon}
              <span>{action.label}</span>
            </Button>
          ))}
        </div>
      </div>
    </section>
  );
}

export function StudioInspectorHeading({ projectTitle = '', ...props }) {
  return (
    <StudioInspectorContextHeading projectTitle={projectTitle} {...props} />
  );
}

export function StudioInspectorContextHeading({
  projectTitle = '',
  sceneLabel = '',
  sectionLabel = '',
  attentionCount = 0,
}) {
  const copy = studioWorkspaceCopy(useDocumentLocale());
  const attentionValue = Number(attentionCount) > 0
    ? `${copy.inspectorAttentionLabel}: ${attentionCount}`
    : copy.inspectorNoAttention;
  const contextItems = [
    sceneLabel ? { key: 'scene', label: copy.inspectorSceneLabel, value: sceneLabel } : null,
    sectionLabel ? { key: 'section', label: copy.inspectorSectionLabel, value: sectionLabel } : null,
    { key: 'attention', label: copy.inspectorAttentionLabel, value: attentionValue },
  ].filter(Boolean);

  return (
    <div data-testid="studio-inspector-heading" className="min-w-0">
      <h2 className="text-base font-bold text-[var(--text-primary)]">{copy.inspector}</h2>
      <p className="mt-0.5 text-xs text-[var(--text-secondary)]">
        {projectTitle || copy.inspectorHint}
      </p>
      <dl
        aria-label={copy.inspectorContextLabel}
        className="mt-3 flex min-w-0 flex-wrap items-center gap-2 text-xs"
      >
        {contextItems.map((item) => (
          <div
            key={item.key}
            className={`inline-flex max-w-full items-center gap-1.5 rounded-lg border px-2.5 py-1 ${
              item.key === 'attention' && Number(attentionCount) > 0
                ? 'border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]'
                : 'border-[var(--border-subtle)] bg-[var(--surface-container-low)] text-[var(--text-secondary)]'
            }`}
          >
            <dt className="sr-only">{item.label}</dt>
            <dd className="max-w-[12rem] truncate font-medium">{item.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function StudioMoreActionsLabel() {
  const copy = studioWorkspaceCopy(useDocumentLocale());
  return copy.moreActions;
}

export function StudioSaveStatus({
  saving = false,
  hasChanges = false,
  lastSavedAt = '',
  error = '',
}) {
  const copy = studioWorkspaceCopy(useDocumentLocale());
  const Icon = error ? AlertTriangle : saving ? LoaderCircle : hasChanges ? Clock3 : CheckCircle2;
  const state = error ? 'error' : saving ? 'saving' : hasChanges ? 'unsaved' : 'saved';
  const label = error
    ? error
    : saving
      ? copy.saving
      : hasChanges
        ? copy.unsavedChanges
        : copy.saved;
  const detail = lastSavedAt
    ? `${copy.lastSaved}: ${lastSavedAt}`
    : hasChanges
      ? copy.neverSaved
      : copy.upToDate;

  return (
    <div
      aria-live="polite"
      data-state={state}
      className={`motion-studio-status flex min-w-0 items-center gap-2 rounded-xl border px-3 py-2 text-xs ${
        error
          ? 'border-[color:var(--status-danger-fg)] bg-[color:var(--status-danger-bg)] text-[color:var(--status-danger-fg)]'
          : hasChanges || saving
            ? 'border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]'
            : 'border-[color:var(--status-success-fg)] bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]'
      }`}
    >
      <Icon size={15} className={saving ? 'shrink-0 animate-spin' : 'shrink-0'} />
      <div className="min-w-0">
        <p className="font-semibold">{label}</p>
        <p className="truncate opacity-85">{detail}</p>
      </div>
    </div>
  );
}

export function StudioEmptyState({ kind = 'project', title = '', hint = '', children }) {
  const copy = studioWorkspaceCopy(useDocumentLocale());
  const defaults = {
    project: [copy.noProjectTitle, copy.noProjectHint],
    slide: [copy.noSlideTitle, copy.noSlideHint],
    assets: [copy.noAssetsTitle, copy.noAssetsHint],
    avatar: [copy.noAvatarTitle, copy.noAvatarHint],
    narration: [copy.noNarrationTitle, copy.noNarrationHint],
  };
  const [defaultTitle, defaultHint] = defaults[kind] || defaults.project;

  return (
    <div className="rounded-xl border border-dashed border-[var(--border-subtle)] bg-[var(--surface-container-low)] p-4 text-sm">
      <p className="font-semibold text-[var(--text-primary)]">{title || defaultTitle}</p>
      <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">{hint || defaultHint}</p>
      {children && <div className="mt-3">{children}</div>}
    </div>
  );
}

export function StudioInspectorSection({
  icon = null,
  title,
  summary = '',
  description = '',
  status = null,
  actions = null,
  defaultOpen = true,
  className = '',
  children,
}) {
  return (
    <details
      open={defaultOpen}
      className={`group motion-studio-status rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-container-lowest)] ${className}`}
    >
      <summary className="focus-ring motion-interactive flex cursor-pointer list-none items-start justify-between gap-3 rounded-lg px-3 py-3">
        <span className="flex min-w-0 flex-1 items-start gap-2.5">
          {icon && (
            <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-[var(--surface-container-low)] text-[var(--text-secondary)]" aria-hidden="true">
              {icon}
            </span>
          )}
          <span className="min-w-0">
            <span className="block text-sm font-semibold text-[var(--text-primary)]">{title}</span>
            {(description || summary) && (
              <span className="mt-0.5 block text-xs leading-5 text-[var(--text-secondary)]">{description || summary}</span>
            )}
            {status && <span className="mt-2 flex min-w-0 flex-wrap gap-1.5">{status}</span>}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {actions}
          <ChevronDown size={16} className="motion-disclosure text-[var(--text-secondary)] group-open:rotate-180" />
        </span>
      </summary>
      <div className="motion-studio-panel space-y-3 border-t border-[var(--border-subtle)] px-3 py-3">
        {children}
      </div>
    </details>
  );
}

export function StudioToolbarGroup({ label, children }) {
  return (
    <div className="motion-studio-status flex flex-wrap items-center gap-2 rounded-xl bg-[var(--surface-container-low)] p-1.5" aria-label={label}>
      {children}
    </div>
  );
}
