import {
  AlertTriangle,
  ArrowDown,
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
  Trash2,
} from 'lucide-react';
import { createPortal } from 'react-dom';
import { useEffect, useState } from 'react';
import { studioWorkspaceCopy } from './studioWorkspaceCopy';

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
      className="fixed z-50 w-52 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-1.5 shadow-xl"
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
      className="min-w-0 lg:max-xl:col-span-2 xl:sticky xl:top-4 xl:self-start"
    >
      <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 shadow-soft">
        <div className="flex items-center gap-2">
          <PanelsTopLeft size={16} className="text-[var(--accent-primary)]" />
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">{copy.slides}</h2>
            <p className="text-[0.68rem] text-[var(--text-secondary)]">{copy.slidesHint}</p>
          </div>
          <span className="ml-auto rounded-full bg-[var(--surface-container-high)] px-2 py-0.5 text-xs text-[var(--text-secondary)]">
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
                  className={`group relative min-w-40 rounded-xl border transition xl:min-w-0 ${
                    selected
                      ? 'border-[var(--accent-primary)] bg-[var(--surface-container-highest)] shadow-sm ring-2 ring-[color:var(--hover-accent-soft)]'
                      : dropTarget
                        ? 'border-[var(--accent-primary)] bg-[color:var(--hover-accent-soft)]'
                        : 'border-transparent bg-[var(--surface-container-high)] hover:border-[var(--border-subtle)]'
                  }`}
                  title={copy.dragToReorder}
                >
                  {selected && (
                    <span className="absolute bottom-2 left-0 top-2 w-1 rounded-r-full bg-[var(--accent-primary)]" aria-hidden="true" />
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
                    className="focus-ring block w-full rounded-xl p-2 pl-3 text-left"
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
                      <span className="absolute left-1 top-1 rounded bg-black/70 px-1.5 py-0.5 text-[0.62rem] font-bold text-white">
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
                  <div className="absolute right-2 top-2 flex gap-1 opacity-0 transition group-hover:opacity-100 group-focus-within:opacity-100">
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
      className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-full border border-white/25 bg-black/70 text-white shadow-sm transition hover:bg-black/85 disabled:opacity-40"
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
      className={`focus-ring flex min-h-9 w-full items-center gap-2 rounded-lg px-2 text-left text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
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

export function StudioRenderStatus({ renderStatus, projectStatus = '' }) {
  const copy = studioWorkspaceCopy(useDocumentLocale());
  const status = normalizedRenderStatus(renderStatus) || String(projectStatus || '').toLowerCase();
  const failed = status.includes('fail') || status.includes('error');
  const active = ['running', 'processing', 'started'].includes(status);
  const queued = ['queued', 'pending'].includes(status);
  const ready = ['ready', 'done', 'completed', 'published'].includes(status);
  const Icon = failed ? AlertTriangle : active ? LoaderCircle : queued ? Clock3 : CheckCircle2;
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

  return (
    <section
      aria-label={copy.renderStatus}
      aria-live="polite"
      data-testid="studio-render-status"
      className={`flex min-w-0 items-center gap-3 rounded-xl border px-3 py-2 ${
        failed
          ? 'border-[color:var(--status-danger-fg)] bg-[color:var(--status-danger-bg)]'
          : active || queued
            ? 'border-[color:var(--status-info-fg)] bg-[color:var(--status-info-bg)]'
            : 'border-[var(--border-subtle)] bg-[var(--surface-container-high)]'
      }`}
    >
      <Icon size={16} className={active ? 'animate-spin text-[color:var(--status-info-fg)]' : 'text-[var(--accent-primary)]'} />
      <div className="min-w-0">
        <p className="text-xs font-semibold text-[var(--text-primary)]">
          {copy.renderStatus}: {label}
        </p>
        <p className="truncate text-[0.68rem] text-[var(--text-secondary)]">{hint}</p>
      </div>
    </section>
  );
}

export function StudioInspectorHeading({ projectTitle = '' }) {
  const copy = studioWorkspaceCopy(useDocumentLocale());
  return (
    <div className="min-w-0">
      <h2 className="text-base font-bold tracking-[-0.01em] text-[var(--text-primary)]">{copy.inspector}</h2>
      <p className="mt-0.5 text-xs text-[var(--text-secondary)]">
        {projectTitle || copy.inspectorHint}
      </p>
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
      className={`flex min-w-0 items-center gap-2 rounded-xl border px-3 py-2 text-xs ${
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
  title,
  summary = '',
  defaultOpen = true,
  className = '',
  children,
}) {
  return (
    <details
      open={defaultOpen}
      className={`group rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-container-low)] ${className}`}
    >
      <summary className="focus-ring flex cursor-pointer list-none items-center justify-between gap-3 rounded-xl px-3 py-3">
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-[var(--text-primary)]">{title}</span>
          {summary && <span className="mt-0.5 block text-xs text-[var(--text-secondary)]">{summary}</span>}
        </span>
        <ChevronDown size={16} className="shrink-0 text-[var(--text-secondary)] transition group-open:rotate-180" />
      </summary>
      <div className="space-y-3 border-t border-[var(--border-subtle)] px-3 py-3">
        {children}
      </div>
    </details>
  );
}

export function StudioToolbarGroup({ label, children }) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl bg-[var(--surface-container-low)] p-1.5" aria-label={label}>
      {children}
    </div>
  );
}
