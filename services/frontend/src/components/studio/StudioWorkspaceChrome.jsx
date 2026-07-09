import { AlertTriangle, CheckCircle2, Clock3, LoaderCircle, PanelsTopLeft } from 'lucide-react';
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
}) {
  const copy = studioWorkspaceCopy(useDocumentLocale());

  return (
    <aside
      aria-label={copy.slides}
      aria-busy={loading}
      data-testid="studio-slide-rail"
      className="min-w-0 xl:sticky xl:top-4 xl:self-start"
    >
      <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 shadow-soft">
        <div className="flex items-center gap-2">
          <PanelsTopLeft size={16} className="text-[var(--accent-primary)]" />
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">{copy.slides}</h2>
            <p className="text-[0.68rem] text-[var(--text-secondary)]">{copy.slidesHint}</p>
          </div>
          <span className="ml-auto rounded-full bg-[var(--surface-container-high)] px-2 py-0.5 text-xs text-[var(--text-secondary)]">
            {scenes.length}
          </span>
        </div>

        {loading ? (
          <div className="mt-3 flex gap-2 overflow-hidden xl:block xl:space-y-2" role="status">
            <span className="sr-only">{copy.loadingSlides}</span>
            {[0, 1, 2].map((item) => (
              <div
                key={item}
                className="h-24 min-w-36 animate-pulse rounded-xl bg-[var(--surface-container-high)] xl:min-w-0"
              />
            ))}
          </div>
        ) : scenes.length === 0 ? (
          <div className="mt-3 rounded-xl border border-dashed border-[var(--border-subtle)] p-4 text-center">
            <p className="text-sm font-semibold text-[var(--text-primary)]">{copy.noSlides}</p>
            <p className="mt-1 text-xs text-[var(--text-secondary)]">{copy.noSlidesHint}</p>
          </div>
        ) : (
          <div className="rail-scroll mt-3 flex gap-2 overflow-x-auto pb-1 xl:max-h-[calc(100vh-12rem)] xl:flex-col xl:overflow-y-auto xl:overflow-x-hidden">
            {scenes.map((scene, index) => {
              const selected = scene.key === selectedSceneKey;
              return (
                <button
                  key={scene.key}
                  type="button"
                  aria-current={selected ? 'true' : undefined}
                  onClick={() => onSelect?.(scene, index)}
                  className={`focus-ring group min-w-36 rounded-xl border p-2 text-left transition xl:min-w-0 ${
                    selected
                      ? 'border-[var(--outline-variant)] bg-[var(--surface-container-highest)] shadow-sm'
                      : 'border-transparent bg-[var(--surface-container-high)] hover:border-[var(--border-subtle)]'
                  }`}
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
                    <span className="absolute bottom-1 left-1 rounded bg-black/65 px-1.5 py-0.5 text-[0.62rem] font-semibold text-white">
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
              );
            })}
          </div>
        )}
      </div>
    </aside>
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
