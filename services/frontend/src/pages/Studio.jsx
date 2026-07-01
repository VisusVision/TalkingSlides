import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  BookOpenText,
  Check,
  ChevronDown,
  Copy,
  Eye,
  EyeOff,
  FileText,
  ImagePlus,
  LayoutPanelTop,
  LogIn,
  RefreshCcw,
  Save,
  Sparkles,
  Trash2,
  Upload,
  Volume2,
  X,
} from 'lucide-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  createProject,
  deleteProject,
  discardProjectDraft,
  analyzeProjectLessonIntelligence,
  applyProjectBackgroundToAll,
  fetchCategories,
  fetchPlaybackToken,
  fetchStudioPreviewToken,
  fetchProjectTranscript,
  fetchProjectLessonIntelligence,
  fetchProject,
  fetchProjects,
  previewPartialRenderImpact,
  getModerationReviewRequest,
  getProjectModeration,
  generateSubtitleTrack,
  fetchSubtitleTrackBundle,
  promoteProjectDraft,
  requestProjectAdminReview,
  removeProjectCover,
  removeTranscriptPageBackground,
  rerenderProjectAvatar,
  rerenderProject,
  rescanProjectModeration,
  updateTranscriptPageScene,
  updateProjectPublished,
  fetchSubtitleTracks,
  fetchAuthenticatedAssetBlobUrl,
  runAdminProjectModerationAction,
  previewTranscriptPageHighlight,
  uploadProjectCover,
  uploadTranscriptPageBackground,
  updateProjectAvatarVisible,
} from '../api';
import { canAccessStudio, isStaffOrAdmin } from '../lib/auth';
import { avatarRuntimeStatusMessage } from '../utils/avatarRuntimeSettings';
import { adminReviewBackLabel, visualModerationRerenderMessage } from '../utils/studioModeration';
import Button from '../components/ui/Button';
import SurfaceCard from '../components/ui/SurfaceCard';
import { usePageLoading } from '../components/ui/PageLoading';
import CreateLessonModal from '../components/studio/CreateLessonModal';
import PlaylistManager from '../components/studio/PlaylistManager';
import TranscriptEditorPanel from '../components/studio/TranscriptEditorPanel';
import TtsSettingsPanel from '../components/studio/TtsSettingsPanel';
import VideoStage from '../components/player/VideoStage';
import { copyTextToClipboard } from '../utils/clipboard';
import { featureEnabled, useCapabilities } from '../lib/capabilities';
import { onRouteReset, safeInternalReturnTo } from '../utils/routeSession';

const LESSON_TABS = ['overview', 'transcript', 'slides'];
const EDITOR_PANELS = ['transcript', 'slides', 'moderation', 'intelligence', 'notes', 'tts'];
const SOURCE_TYPES_ACCEPT = '.pptx,.pdf,.docx,.txt,.png,.jpg,.jpeg,.webp,.gif';
const STUDIO_POLL_INTERVAL_MS = 4000;
const LESSON_INTELLIGENCE_ENHANCEMENT_POLL_INTERVAL_MS = 6000;
const STUDIO_PROJECT_PAGE_SIZE = 12;
const STUDIO_PROJECT_CACHE_TTL_MS = 45 * 1000;
const STUDIO_PROJECT_LIST_CACHE_MAX = 8;
const STUDIO_PROJECT_DETAIL_CACHE_MAX = 24;
const STUDIO_PROJECT_CACHE_WINDOW = 5;
const UNSTABLE_JOB_STATUSES = new Set(['pending', 'running', 'processing', 'queued', 'started']);
const STABLE_MODERATION_STATUSES = new Set(['approved', 'admin_approved', 'revision_required', 'needs_admin_review', 'admin_rejected', 'failed']);

function normalizeProjectList(payload) {
  return Array.isArray(payload) ? payload : payload.results || [];
}

function projectPaginationMeta(payload, fallbackLimit = STUDIO_PROJECT_PAGE_SIZE) {
  if (Array.isArray(payload)) {
    return {
      totalCount: payload.length,
      limit: fallbackLimit,
      offset: 0,
      nextOffset: null,
      hasNext: false,
    };
  }
  const nextOffset = Number(payload?.next_offset);
  return {
    totalCount: Number.isFinite(Number(payload?.count)) ? Number(payload.count) : null,
    limit: Number.isFinite(Number(payload?.limit)) ? Number(payload.limit) : fallbackLimit,
    offset: Number.isFinite(Number(payload?.offset)) ? Number(payload.offset) : 0,
    nextOffset: Number.isFinite(nextOffset) ? nextOffset : null,
    hasNext: Boolean(payload?.has_next),
  };
}

function cloneCacheValue(value) {
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return value;
  }
}

function projectListCacheKey({ limit = STUDIO_PROJECT_PAGE_SIZE, offset = 0, q = '' } = {}) {
  return JSON.stringify({
    limit: Number(limit) || STUDIO_PROJECT_PAGE_SIZE,
    offset: Number(offset) || 0,
    q: String(q || '').trim(),
  });
}

function readStudioCacheEntry(cache, key, ttlMs = STUDIO_PROJECT_CACHE_TTL_MS) {
  if (!cache || !key) return null;
  const entry = cache.get(key);
  if (!entry) return null;
  if ((Date.now() - Number(entry.cachedAt || 0)) > ttlMs) {
    cache.delete(key);
    return null;
  }
  entry.lastAccessedAt = Date.now();
  return cloneCacheValue(entry.value);
}

function writeStudioCacheEntry(cache, key, value, maxEntries) {
  if (!cache || !key) return;
  cache.set(key, {
    value: cloneCacheValue(value),
    cachedAt: Date.now(),
    lastAccessedAt: Date.now(),
  });
  if (cache.size <= maxEntries) return;
  const entriesByAge = [...cache.entries()].sort((left, right) => {
    return Number(left[1]?.lastAccessedAt || 0) - Number(right[1]?.lastAccessedAt || 0);
  });
  while (cache.size > maxEntries && entriesByAge.length) {
    cache.delete(entriesByAge.shift()[0]);
  }
}

function readProjectListCache(cache, request) {
  return readStudioCacheEntry(cache, projectListCacheKey(request));
}

function writeProjectListCache(cache, request, payload) {
  writeStudioCacheEntry(cache, projectListCacheKey(request), payload, STUDIO_PROJECT_LIST_CACHE_MAX);
}

function readProjectDetailCache(cache, projectId) {
  return readStudioCacheEntry(cache, String(projectId || ''));
}

function writeProjectDetailCache(cache, project) {
  if (!project?.id) return;
  writeStudioCacheEntry(cache, String(project.id), project, STUDIO_PROJECT_DETAIL_CACHE_MAX);
}

function cacheProjectWindow(cache, projects, selectedId) {
  if (!Array.isArray(projects) || !projects.length) return;
  const selectedIndex = selectedId
    ? projects.findIndex((project) => String(project.id) === String(selectedId))
    : 0;
  const center = selectedIndex >= 0 ? selectedIndex : 0;
  const start = Math.max(0, center - STUDIO_PROJECT_CACHE_WINDOW);
  const end = Math.min(projects.length, center + STUDIO_PROJECT_CACHE_WINDOW + 1);
  projects.slice(start, end).forEach((project) => writeProjectDetailCache(cache, project));
}

function invalidateProjectCaches(listCache, detailCache, projectId) {
  if (detailCache && projectId) {
    detailCache.delete(String(projectId));
  }
  if (!listCache) return;
  if (!projectId) {
    listCache.clear();
    return;
  }
  for (const [key, entry] of listCache.entries()) {
    const projects = normalizeProjectList(entry?.value);
    if (projects.some((project) => String(project.id) === String(projectId))) {
      listCache.delete(key);
    }
  }
}

function mergeProjectsPreservingLocalModeration(previousProjects, nextProjects, { append = false } = {}) {
  if (!Array.isArray(previousProjects) || !previousProjects.length) {
    return nextProjects;
  }
  const previousById = new Map(previousProjects.map((project) => [String(project.id), project]));
  const mergedNextProjects = nextProjects.map((project) => {
    const previous = previousById.get(String(project.id));
    if (!previous) return project;
    const previousIsStale = projectHasModerationStaleMarkers(previous);
    const incomingIsStale = projectHasModerationStaleMarkers(project);
    const incomingStatus = normalizedStatus(project?.moderation_status);
    const sameRun = moderationRunId(previous) === moderationRunId(project);
    const previousCoverIsStale = visualMarkerTargetsCover(projectVisualStaleMarker(previous));
    const nextProject = previousCoverIsStale ? {
      ...project,
      cover_url: preserveCacheBustedMediaUrl(previous.cover_url, project.cover_url),
      thumbnail_url: preserveCacheBustedMediaUrl(previous.thumbnail_url, project.thumbnail_url),
    } : project;
    if (
      previousIsStale
      && !incomingIsStale
      && sameRun
      && (incomingStatus === 'approved' || incomingStatus === 'admin_approved')
    ) {
      return {
        ...nextProject,
        moderation_status: previous.moderation_status,
        moderation_summary: previous.moderation_summary,
      };
    }
    return nextProject;
  });
  if (!append) return mergedNextProjects;
  const mergedById = new Map(mergedNextProjects.map((project) => [String(project.id), project]));
  const appended = mergedNextProjects.filter((project) => !previousById.has(String(project.id)));
  return [
    ...previousProjects.map((project) => mergedById.get(String(project.id)) || project),
    ...appended,
  ];
}

function normalizedStatus(value) {
  if (value && typeof value === 'object') {
    return String(value.status || value.state || '').trim().toLowerCase();
  }
  return String(value || '').trim().toLowerCase();
}

function timestampMs(value) {
  const timestamp = Date.parse(String(value || ''));
  return Number.isFinite(timestamp) ? timestamp : null;
}

function currentManualApprovalCoversDraft(project, draftMetadata) {
  const manualStatus = normalizedStatus(project?.manual_moderation_status);
  const moderationStatus = normalizedStatus(project?.moderation_status);
  if (manualStatus !== 'approved' && moderationStatus !== 'admin_approved') return false;
  const manualAt = timestampMs(project?.manual_moderation_at);
  if (manualAt === null) return false;
  const moderation = draftMetadata?.moderation && typeof draftMetadata.moderation === 'object'
    ? draftMetadata.moderation
    : {};
  const draftTimes = [
    draftMetadata?.moderation_failed_at,
    draftMetadata?.updated_at,
    draftMetadata?.created_at,
    moderation?.changed_at,
    moderation?.completed_at,
  ].map(timestampMs).filter((value) => value !== null);
  return draftTimes.length > 0 && draftTimes.every((value) => value <= manualAt);
}

function projectLatestJobStatus(project) {
  return normalizedStatus(project?.latest_job?.status);
}

function projectRawStatus(project) {
  return normalizedStatus(project?.status);
}

function projectStatusLabel(project) {
  const raw = String(project?.latest_job?.status || project?.status || '').trim().toLowerCase();
  if (!raw) return 'Draft';
  if (raw === 'done' || raw === 'ready') return 'Ready';
  if (raw === 'running' || raw === 'processing') return 'Processing';
  if (raw === 'pending') return 'Queued';
  if (raw.includes('fail') || raw.includes('error')) return 'Failed';
  return raw;
}

function projectStatusTone(project) {
  const label = projectStatusLabel(project).toLowerCase();
  if (label === 'ready') {
    return 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]';
  }
  if (label === 'failed') {
    return 'bg-[color:var(--status-danger-bg)] text-[color:var(--status-danger-fg)]';
  }
  if (label === 'processing') {
    return 'bg-[color:var(--status-info-bg)] text-[color:var(--status-info-fg)]';
  }
  return 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]';
}

function projectPublicationLabel(project) {
  return project?.is_published ? 'Published' : 'Draft';
}

function projectPublicationTone(project) {
  return project?.is_published
    ? 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]'
    : 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]';
}

const MODERATION_STATUS_LABELS = {
  not_scanned: 'Not scanned',
  pending: 'Scanning',
  approved: 'Approved',
  revision_required: 'Needs revision',
  needs_admin_review: 'Needs admin review',
  admin_approved: 'Admin approved',
  admin_rejected: 'Admin rejected',
  request_changes: 'Changes requested',
  failed: 'Scan failed',
};

// Statuses where moderation BLOCKS publishing (mirrors server-side BLOCKED_MODERATION_STATUSES).
// All other statuses (not_scanned, pending, failed, approved, needs_admin_review, admin_approved)
// are allowed — the publish button is enabled and the backend will accept the request.
const MODERATION_BLOCKED_STATUSES = new Set([
  'admin_rejected',
  'revision_required',
  'needs_admin_review',
  'failed',
  'not_scanned',
  'not_scanned_required',
  'pending',
  'processing',
  'running',
]);

function plainObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}

const RENDER_ANALYSIS_ACTION_LABELS = {
  reuse_all: 'Reuse existing assets',
  metadata_only_future: 'Metadata-only update',
  recompose_visual_only_future: 'Visual-only recomposition',
  rerun_avatar_future: 'Rerun avatar',
  rerun_tts_avatar_future: 'Rerun narration/avatar',
  rerender_page_future: 'Rerender page',
  full_rerender_required_future: 'Full rerender required',
  unknown_requires_full: 'Unknown, safest full rerender',
};

const PARTIAL_RENDER_PREVIEW_SUMMARY_KEYS = [
  'reuse_all',
  'recompose_visual_only_future',
  'rerun_tts_avatar_future',
  'rerun_avatar_future',
  'metadata_only_future',
  'rerender_page_future',
  'full_rerender_required_future',
  'unknown_requires_full',
];

const PARTIAL_RENDER_PREVIEW_SOURCE_LABELS = {
  request_payload: 'Current editor payload',
  dirty_draft: 'Saved draft',
  active_project: 'Active project',
  unavailable: 'Unavailable',
};

export function renderAnalysisActionLabel(action) {
  const key = String(action || '').trim();
  if (RENDER_ANALYSIS_ACTION_LABELS[key]) return RENDER_ANALYSIS_ACTION_LABELS[key];
  return key
    ? key.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
    : 'No action reported';
}

export function partialRenderPreviewSourceLabel(source) {
  const key = String(source || '').trim();
  return PARTIAL_RENDER_PREVIEW_SOURCE_LABELS[key] || 'Saved draft/current project';
}

function renderAnalysisCount(summary, key) {
  const value = Number(summary?.[key] || 0);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

export function renderAnalysisSummaryItems(analysis) {
  const summary = plainObject(analysis?.plan?.summary) || {};
  const items = [
    {
      key: 'recompose_visual_only_future',
      label: 'Visual-only recompose',
      value: renderAnalysisCount(summary, 'recompose_visual_only_future'),
    },
    {
      key: 'full_rerender_required_future',
      label: 'Full rerender required',
      value: renderAnalysisCount(summary, 'full_rerender_required_future'),
    },
    {
      key: 'unknown_requires_full',
      label: 'Unknown/full fallback',
      value: renderAnalysisCount(summary, 'unknown_requires_full'),
    },
  ];
  const pageRerenderCount = renderAnalysisCount(summary, 'rerender_page_future');
  if (pageRerenderCount > 0) {
    items.push({
      key: 'rerender_page_future',
      label: 'Page rerender',
      value: pageRerenderCount,
    });
  }
  return items;
}

export function renderAnalysisPageRows(analysis, limit = 6) {
  const planPages = plainObject(analysis?.plan?.pages) || {};
  const classifierPages = plainObject(analysis?.classifier?.pages) || {};
  return Object.entries(planPages)
    .map(([pageKey, page], index) => {
      const safePage = plainObject(page) || {};
      const classifierPage = plainObject(classifierPages[pageKey]) || {};
      return {
        pageKey: String(safePage.page_key || pageKey || ''),
        index: Number.isFinite(Number(classifierPage.index)) ? Number(classifierPage.index) : index,
        classification: String(safePage.classification || classifierPage.classification || ''),
        recommendedAction: String(safePage.recommended_action || ''),
        recommendedLabel: renderAnalysisActionLabel(safePage.recommended_action),
        reasons: Array.isArray(safePage.reasons) ? safePage.reasons.map((item) => String(item)) : [],
      };
    })
    .filter((row) => row.pageKey)
    .sort((left, right) => left.index - right.index || left.pageKey.localeCompare(right.pageKey))
    .slice(0, Math.max(0, limit));
}

export function RenderAnalysisPanel({ analysis }) {
  if (!plainObject(analysis)) return null;
  const summaryItems = renderAnalysisSummaryItems(analysis);
  const pageRows = renderAnalysisPageRows(analysis);
  const classifierAvailable = Boolean(analysis?.classifier?.available);
  const hiddenPageCount = Math.max(0, Object.keys(plainObject(analysis?.plan?.pages) || {}).length - pageRows.length);

  return (
    <section
      data-testid="render-analysis-panel"
      className="shrink-0 border-y border-[var(--border-subtle)] bg-[var(--surface-container-low)] px-3 py-3"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-[var(--text-primary)]">Last render analysis</p>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            These are diagnostic recommendations from the last completed render. Actual rendering may safely fall back.
          </p>
        </div>
        <span className="shrink-0 rounded-full bg-[var(--surface-elevated)] px-2 py-1 text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">
          Diagnostic only
        </span>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {summaryItems.map((item) => (
          <div key={item.key} className="border-l border-[var(--border-subtle)] pl-3">
            <p className="text-lg font-semibold text-[var(--text-primary)]">{item.value}</p>
            <p className="text-xs text-[var(--text-secondary)]">{item.label}</p>
          </div>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-xs text-[var(--text-secondary)]">
        <span>Classifier {classifierAvailable ? 'available' : 'unavailable'}</span>
        <span>Plan mode: {textValue(analysis?.plan?.mode || analysis?.mode || 'report_only')}</span>
      </div>

      {pageRows.length > 0 && (
        <div className="mt-3 space-y-2">
          {pageRows.map((row) => (
            <div key={row.pageKey} className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--border-subtle)] pt-2 text-xs">
              <span className="font-semibold text-[var(--text-primary)]">{row.pageKey}</span>
              <span className="text-[var(--text-secondary)]">
                Recommended future action: {row.recommendedLabel}
              </span>
            </div>
          ))}
          {hiddenPageCount > 0 && (
            <p className="text-xs text-[var(--text-secondary)]">+{hiddenPageCount} more pages in the analysis</p>
          )}
        </div>
      )}
    </section>
  );
}

export function partialRenderPreviewSummaryItems(prediction) {
  const summary = plainObject(prediction?.summary) || {};
  return PARTIAL_RENDER_PREVIEW_SUMMARY_KEYS.map((key) => ({
    key,
    label: renderAnalysisActionLabel(key),
    value: renderAnalysisCount(summary, key),
  }));
}

export function partialRenderPreviewPageRows(prediction, limit = 6) {
  const pages = Array.isArray(prediction?.pages) ? prediction.pages : [];
  return pages
    .map((page, index) => {
      const safePage = plainObject(page) || {};
      return {
        pageKey: String(safePage.page_key || ''),
        index: Number.isFinite(Number(safePage.index)) ? Number(safePage.index) : index,
        classification: String(safePage.classification || ''),
        recommendedAction: String(safePage.recommended_action || ''),
        recommendedLabel: renderAnalysisActionLabel(safePage.recommended_action),
        requiresFull: Boolean(safePage.requires_full),
      };
    })
    .filter((row) => row.pageKey)
    .sort((left, right) => left.index - right.index || left.pageKey.localeCompare(right.pageKey))
    .slice(0, Math.max(0, limit));
}

export function PreviewRerenderImpactButton({ busy = false, disabled = false, onClick }) {
  return (
    <Button
      size="sm"
      variant="secondary"
      onClick={onClick}
      disabled={disabled || busy}
      title="Preview which pages are expected to need rerender work."
      data-testid="partial-render-preview-button"
    >
      <Eye size={14} />
      <span>{busy ? 'Previewing...' : 'Preview rerender impact'}</span>
    </Button>
  );
}

export function PredictedRerenderImpactPanel({ prediction, error = '' }) {
  if (!plainObject(prediction) && !error) return null;
  const summaryItems = partialRenderPreviewSummaryItems(prediction);
  const pageRows = partialRenderPreviewPageRows(prediction);
  const hiddenPageCount = Math.max(0, (Array.isArray(prediction?.pages) ? prediction.pages.length : 0) - pageRows.length);
  const source = String(prediction?.source || 'unavailable');
  const sourceLabel = partialRenderPreviewSourceLabel(source);
  const sourceCopy = source === 'request_payload'
    ? 'Prediction uses the current editor transcript payload and saved project settings.'
    : 'Prediction uses the saved draft/current project state.';

  return (
    <section
      data-testid="partial-render-preview-panel"
      className="shrink-0 border-y border-[var(--border-subtle)] bg-[var(--surface-container-low)] px-3 py-3"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-[var(--text-primary)]">Predicted rerender impact</p>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            Prediction only. Actual rendering may safely fall back.
          </p>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">{sourceCopy}</p>
        </div>
        <span className="shrink-0 rounded-full bg-[var(--surface-elevated)] px-2 py-1 text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">
          Prediction only
        </span>
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-xs text-[var(--text-secondary)]">
        <span>Source: {sourceLabel}</span>
        {plainObject(prediction) && (
          <span>Classifier {prediction.available ? 'available' : 'unavailable'}</span>
        )}
      </div>

      {error && (
        <p className="mt-3 rounded-xl bg-[color:var(--status-warning-bg)] px-3 py-2 text-xs font-semibold text-[color:var(--status-warning-fg)]">
          {error}
        </p>
      )}

      {plainObject(prediction) && (
        <>
          <div className="mt-3 grid gap-2 sm:grid-cols-4">
            {summaryItems.map((item) => (
              <div key={item.key} className="border-l border-[var(--border-subtle)] pl-3">
                <p className="text-lg font-semibold text-[var(--text-primary)]">{item.value}</p>
                <p className="text-xs text-[var(--text-secondary)]">{item.label}</p>
              </div>
            ))}
          </div>

          {pageRows.length > 0 && (
            <div className="mt-3 space-y-2">
              {pageRows.map((row) => (
                <div key={row.pageKey} className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--border-subtle)] pt-2 text-xs">
                  <span className="font-semibold text-[var(--text-primary)]">{row.pageKey}</span>
                  <span className="text-[var(--text-secondary)]">{row.recommendedLabel}</span>
                </div>
              ))}
              {hiddenPageCount > 0 && (
                <p className="text-xs text-[var(--text-secondary)]">+{hiddenPageCount} more pages in the prediction</p>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function partialRenderPreviewEditorDocument(document) {
  if (!plainObject(document)) return {};
  const next = { ...document };
  const scene = plainObject(document.scene);
  if (scene) {
    const safeScene = {};
    [
      'background_mode',
      'background_fit',
      'text_scale',
      'highlight_enabled',
      'highlight_style',
      'highlight_detector',
      'overlay_layout',
      'font',
    ].forEach((key) => {
      if (Object.prototype.hasOwnProperty.call(scene, key)) {
        safeScene[key] = scene[key];
      }
    });
    next.scene = safeScene;
  }
  return next;
}

function partialRenderPreviewPagePayload(page, index) {
  return {
    id: page?.id,
    page_key: page?.page_key,
    order: page?.order ?? index,
    source_slide_index: page?.source_slide_index ?? index,
    split_index: page?.split_index ?? 0,
    original_text: textValue(page?.original_text ?? page?.display_text),
    display_text: textValue(page?.display_text ?? page?.original_text),
    narration_text: textValue(page?.narration_text ?? page?.original_text ?? page?.display_text),
    rich_text_html: textValue(page?.rich_text_html),
    editor_document: partialRenderPreviewEditorDocument(page?.editor_document),
    subtitle_chunks: Array.isArray(page?.subtitle_chunks) ? page.subtitle_chunks.map((item) => textValue(item)) : [],
    whiteboard_mode: Boolean(page?.whiteboard_mode),
  };
}

function partialRenderPreviewPayload({ transcriptPages, avatarFeatureEnabled, avatarEnabled, selectedLesson }) {
  const payload = {
    pages: Array.isArray(transcriptPages)
      ? transcriptPages.map((page, index) => partialRenderPreviewPagePayload(page, index))
      : [],
  };
  if (avatarFeatureEnabled) {
    payload.avatar_enabled = avatarEnabled ? '1' : '0';
    payload.render_with_avatar = Boolean(avatarEnabled);
  }
  if (plainObject(selectedLesson?.tts_settings)) {
    payload.tts_settings = selectedLesson.tts_settings;
  }
  return payload;
}

function projectModerationSummary(project, moderation = null) {
  if (moderation && Object.prototype.hasOwnProperty.call(moderation, 'moderation_summary')) {
    return plainObject(moderation.moderation_summary) || {};
  }
  if (moderation && Object.prototype.hasOwnProperty.call(moderation, 'summary')) {
    return plainObject(moderation.summary) || {};
  }
  if (moderation) {
    return {};
  }
  return plainObject(project?.moderation_summary) || {};
}

function projectEditorTextStaleMarker(project, moderation = null) {
  const summary = projectModerationSummary(project, moderation);
  return (
    plainObject(moderation?.editor_text_changed)
    || plainObject(summary.editor_text_changed)
    || plainObject(summary.stale_text)
    || null
  );
}

function projectVisualStaleMarker(project, moderation = null) {
  const summary = projectModerationSummary(project, moderation);
  return (
    plainObject(moderation?.visual_asset_scan)
    || plainObject(summary.visual_asset_scan)
    || null
  );
}

function visualMarkerTargetsCover(marker) {
  if (!plainObject(marker)) return false;
  const haystack = [
    marker.asset_type,
    marker.asset,
    marker.kind,
    marker.source,
    marker.message,
  ].map((value) => String(value || '').toLowerCase());
  return haystack.some((value) => value.includes('cover'));
}

function moderationMarkerIsStale(marker) {
  if (!plainObject(marker)) return false;
  const status = normalizedStatus(marker);
  return Boolean(
    marker.needs_rescan
      || marker.needs_recheck
      || marker.stale
      || marker.stale_text
      || ['needs_rescan', 'needs_recheck', 'stale', 'pending', 'not_scanned'].includes(status),
  );
}

function projectHasModerationStaleMarkers(project, moderation = null) {
  return moderationMarkerIsStale(projectEditorTextStaleMarker(project, moderation))
    || moderationMarkerIsStale(projectVisualStaleMarker(project, moderation));
}

function moderationRunId(payload) {
  const rawId = payload?.latest_run_id ?? payload?.last_moderation_run_id ?? payload?.run_id ?? null;
  return rawId === null || rawId === undefined ? '' : String(rawId);
}

function projectModerationStatus(project, moderation = null) {
  const rawStatus = String(moderation?.moderation_status || project?.moderation_status || 'not_scanned').trim().toLowerCase() || 'not_scanned';
  if ((rawStatus === 'approved' || rawStatus === 'admin_approved') && moderation?.publish_blocked_by_moderation) {
    const manualStatus = String(moderation?.manual_moderation_status || project?.manual_moderation_status || '').trim().toLowerCase();
    if (manualStatus === 'request_changes') return 'revision_required';
    if (manualStatus === 'blocked' || manualStatus === 'rejected') return 'admin_rejected';
    return 'needs_admin_review';
  }
  if (rawStatus === 'pending') return 'pending';
  if (MODERATION_BLOCKED_STATUSES.has(rawStatus) || rawStatus === 'needs_admin_review' || rawStatus === 'failed') {
    return rawStatus;
  }

  const textMarker = projectEditorTextStaleMarker(project, moderation);
  const visualMarker = projectVisualStaleMarker(project, moderation);
  if (moderationMarkerIsStale(textMarker) || moderationMarkerIsStale(visualMarker)) {
    const markerStatus = normalizedStatus(textMarker) || normalizedStatus(visualMarker);
    return markerStatus === 'pending' ? 'pending' : 'not_scanned';
  }
  return rawStatus;
}

function moderationStatusLabel(status) {
  const normalized = String(status || 'not_scanned').trim().toLowerCase();
  return MODERATION_STATUS_LABELS[normalized] || normalized.replace(/_/g, ' ');
}

function moderationStatusTone(status) {
  const normalized = String(status || 'not_scanned').trim().toLowerCase();
  if (normalized === 'approved' || normalized === 'admin_approved' || normalized === 'allow') {
    return 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]';
  }
  if (normalized === 'pending' || normalized === 'warn') {
    return 'bg-[color:var(--status-info-bg)] text-[color:var(--status-info-fg)]';
  }
  if (normalized === 'revision_required' || normalized === 'admin_rejected' || normalized === 'failed' || normalized === 'block' || normalized === 'blocked' || normalized === 'rejected') {
    return 'bg-[color:var(--status-danger-bg)] text-[color:var(--status-danger-fg)]';
  }
  return 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]';
}

function moderationSuggestedMessage(status) {
  const normalized = String(status || 'not_scanned').trim().toLowerCase();
  if (normalized === 'revision_required') {
    return 'Moderation flagged this lesson. Publishing is blocked until content is revised or an admin approves it.';
  }
  if (normalized === 'admin_rejected') {
    return 'An admin has rejected this lesson. Publishing is blocked. Contact support if you believe this is a mistake.';
  }
  if (normalized === 'needs_admin_review') {
    return 'This lesson is awaiting admin review. Publishing is blocked until moderation approves it.';
  }
  if (normalized === 'approved' || normalized === 'admin_approved') {
    return 'Moderation approved. This lesson can be published when rendering is complete.';
  }
  if (normalized === 'pending') {
    return 'Moderation in progress. Publishing is temporarily blocked.';
  }
  if (normalized === 'failed') {
    return 'Moderation scan failed. Resubmit a scan or request admin review before publishing.';
  }
  // not_scanned
  return 'This lesson must pass moderation before publishing.';
}

function moderationMessage(project, moderation = null) {
  const adminNote = textValue(
    moderation?.admin_note
    || moderation?.manual_moderation_reason
    || project?.manual_moderation_reason
    || '',
  ).trim();
  const manualStatus = String(moderation?.manual_moderation_status || project?.manual_moderation_status || '').trim();
  if (adminNote && manualStatus === 'request_changes') {
    return `Admin requested changes. Reason: ${adminNote}. Update the lesson and request review.`;
  }
  if (adminNote && ['blocked', 'rejected'].includes(manualStatus)) {
    return `Publish blocked by moderation. Reason: ${adminNote}`;
  }
  const textMarker = projectEditorTextStaleMarker(project, moderation);
  if (moderationMarkerIsStale(textMarker)) {
    return textMarker.message || 'Text changed in Studio. Moderation needs to scan the updated text.';
  }
  const visualMarker = projectVisualStaleMarker(project, moderation);
  if (moderationMarkerIsStale(visualMarker)) {
    return visualMarker.message || 'A Studio image changed after the last visual moderation scan.';
  }
  return moderation?.message || moderationSuggestedMessage(projectModerationStatus(project, moderation));
}

/**
 * Returns true when the frontend pre-flight considers publishing allowed.
 * The server is authoritative.
 */
function projectCanPublishFromModeration(project, moderation = null) {
  // Trust explicit server-side can_publish if present.
  if (moderation && Object.prototype.hasOwnProperty.call(moderation, 'can_publish')) {
    return Boolean(moderation.can_publish);
  }
  const modStatus = projectModerationStatus(project, moderation);
  return modStatus === 'approved' || modStatus === 'admin_approved';
}

function findingHaystack(finding) {
  return [
    finding?.asset_kind,
    finding?.asset_label,
    finding?.source_kind,
    finding?.source_label,
    finding?.location_label,
    finding?.ui_anchor,
    finding?.content_type,
    finding?.object_type,
    finding?.object_id,
    finding?.page_key,
  ]
    .map((value) => textValue(value))
    .join(' ')
    .toLowerCase();
}

const STUDIO_VISUAL_ASSET_KINDS = new Set([
  'cover',
  'custom_background',
  'slide_image',
  'draft_visual_asset',
  'video_frame',
  'profile_image',
  'channel_logo',
  'channel_banner',
]);

const STUDIO_EDITOR_VISUAL_WARNING_KINDS = new Set([
  'cover',
  'custom_background',
  'slide_image',
  'draft_visual_asset',
]);
const STUDIO_EDITOR_SOURCE_KINDS = new Set([
  'lesson_cover',
  'scene_background',
  'slide_image',
]);
const STUDIO_SOURCE_KIND_TO_ASSET_KIND = {
  lesson_cover: 'cover',
  scene_background: 'custom_background',
  slide_image: 'slide_image',
};

const STUDIO_TEXT_CONTENT_TYPES = new Set(['text', 'ocr', 'transcript', 'subtitle', 'language']);
const STUDIO_TEXT_CATEGORIES = new Set([
  'abusive_language',
  'copyright_text',
  'dangerous_instruction',
  'hate_or_harassment',
  'inappropriate_language',
  'language',
  'profanity',
  'self_harm_instruction',
  'sexual_text',
  'text_moderation',
  'violence_text',
]);
const STUDIO_VISUAL_CATEGORIES = new Set(['sexual', 'violence', 'graphic_content', 'self_harm', 'provider_unavailable']);
const STUDIO_RESOLVED_MODERATION_STATES = new Set([
  'allow',
  'allowed',
  'approve',
  'approved',
  'admin_approved',
  'clear',
  'cleared',
  'dismissed',
  'pass',
  'passed',
  'resolved',
  'safe',
  'scan_passed',
]);
const STUDIO_VISUAL_ASSET_KIND_ALIASES = {
  background: 'custom_background',
  custom_background: 'custom_background',
  'custom-background': 'custom_background',
  cover: 'cover',
  lesson_cover: 'cover',
  slide: 'slide_image',
  slide_image: 'slide_image',
  draft_visual_asset: 'draft_visual_asset',
  frame: 'video_frame',
  video_frame: 'video_frame',
  avatar_image: 'profile_image',
  profile_image: 'profile_image',
  profile_logo: 'channel_logo',
  channel_logo: 'channel_logo',
  profile_banner: 'channel_banner',
  channel_banner: 'channel_banner',
};

function rawIssueAssetKind(finding) {
  return textValue(finding?.asset_kind).trim().toLowerCase();
}

function getExplicitIssueAssetKind(finding) {
  if (!finding || isTextModerationIssue(finding)) return '';
  const assetKind = rawIssueAssetKind(finding);
  if (assetKind) return STUDIO_VISUAL_ASSET_KIND_ALIASES[assetKind] || assetKind;
  const objectType = textValue(finding?.object_type).trim().toLowerCase();
  if (objectType) return STUDIO_VISUAL_ASSET_KIND_ALIASES[objectType] || '';
  return '';
}

function getEditorVisualIssueAssetKind(issue) {
  if (!issue || isTextModerationIssue(issue)) return '';
  const sourceKind = textValue(issue?.source_kind).trim().toLowerCase();
  if (!STUDIO_EDITOR_SOURCE_KINDS.has(sourceKind)) return '';
  return STUDIO_SOURCE_KIND_TO_ASSET_KIND[sourceKind] || '';
}

function isTextModerationIssue(finding) {
  const sourceKind = textValue(finding?.source_kind).trim().toLowerCase();
  const contentType = textValue(finding?.content_type).trim().toLowerCase();
  const provider = textValue(finding?.provider).trim().toLowerCase();
  const objectType = textValue(finding?.object_type).trim().toLowerCase();
  const category = textValue(finding?.category).trim().toLowerCase();
  const issueType = textValue(finding?.issue_type || finding?.finding_type || finding?.type).trim().toLowerCase();
  const textSignal = issueType === 'text'
    || sourceKind === 'transcript_text'
    || STUDIO_TEXT_CONTENT_TYPES.has(contentType)
    || provider.includes('ocr')
    || provider.includes('text')
    || objectType.includes('ocr')
    || STUDIO_TEXT_CATEGORIES.has(category);
  if (textSignal) return true;
  if (sourceKind && sourceKind !== 'unknown') return false;
  return false;
}

function getIssueAssetKind(finding) {
  if (!finding || isTextModerationIssue(finding)) return 'text';
  const explicit = STUDIO_VISUAL_ASSET_KIND_ALIASES[rawIssueAssetKind(finding)] || '';
  if (explicit) return explicit;
  const objectType = textValue(finding?.object_type).trim().toLowerCase();
  const objectKind = STUDIO_VISUAL_ASSET_KIND_ALIASES[objectType] || '';
  if (objectKind) return objectKind;
  const haystack = findingHaystack(finding);
  if (haystack.includes('cover')) return 'cover';
  if (haystack.includes('custom_background') || haystack.includes('custom background') || haystack.includes('background')) return 'custom_background';
  if (haystack.includes('video') || haystack.includes('frame')) return 'video_frame';
  if (haystack.includes('avatar') || haystack.includes('profile')) return 'profile_image';
  if (haystack.includes('logo')) return 'channel_logo';
  if (haystack.includes('banner')) return 'channel_banner';
  if (haystack.includes('slide') || finding?.slide_order !== undefined || finding?.slide_index !== undefined || finding?.slide_number !== undefined) return 'slide_image';
  return '';
}

function isVisualModerationIssue(finding) {
  if (!finding || isTextModerationIssue(finding)) return false;
  const explicit = getIssueAssetKind(finding);
  const contentType = textValue(finding?.content_type).trim().toLowerCase();
  const provider = textValue(finding?.provider).trim().toLowerCase();
  const category = textValue(finding?.category).trim().toLowerCase();
  return STUDIO_VISUAL_ASSET_KINDS.has(explicit)
    || ['image', 'video_frame'].includes(contentType)
    || provider.includes('visual')
    || STUDIO_VISUAL_CATEGORIES.has(category);
}

function isUnresolvedModerationIssue(finding) {
  const states = [
    finding?.decision,
    finding?.status,
    finding?.moderation_status,
    finding?.final_decision,
    finding?.manual_moderation_status,
  ]
    .map((value) => textValue(value).trim().toLowerCase())
    .filter(Boolean);
  return !states.some((state) => STUDIO_RESOLVED_MODERATION_STATES.has(state));
}

function isRealVisualIssue(finding) {
  return isUnresolvedVisualIssue(finding);
}

function isUnresolvedVisualIssue(issue) {
  return Boolean(
    issue
      && !isTextModerationIssue(issue)
      && isUnresolvedModerationIssue(issue)
      && getEditorVisualIssueAssetKind(issue),
  );
}

function issueModerationState(issue) {
  return textValue(issue?.moderation_state || issue?.decision || issue?.status || issue?.moderation_status)
    .trim()
    .toLowerCase();
}

function visualIssueWarningState(issue) {
  const state = issueModerationState(issue);
  if (['pending_scan', 'pending', 'processing', 'running'].includes(state)) return 'pending';
  if (['needs_admin_review', 'blocked', 'block', 'rejected', 'reject', 'revision_required', 'admin_rejected'].includes(state)) {
    return 'flagged';
  }
  return '';
}

function moderationWarningIsFlagged(warning) {
  return warning?.state === 'flagged';
}

function moderationWarningIsPending(warning) {
  return warning?.state === 'pending';
}

function findingAssetKind(finding) {
  if (isTextModerationIssue(finding)) return 'transcript';
  const explicit = getIssueAssetKind(finding);
  if (explicit === 'cover') return 'cover';
  if (explicit === 'custom_background') return 'background';
  if (explicit === 'slide_image' || explicit === 'draft_visual_asset') return 'slide';
  if (explicit === 'video_frame') return 'video';
  if (explicit === 'profile_image' || explicit === 'channel_logo' || explicit === 'channel_banner') return 'avatar';
  const haystack = findingHaystack(finding);
  if (isVisualModerationIssue(finding)) {
    if (haystack.includes('cover')) return 'cover';
    if (haystack.includes('avatar') || haystack.includes('profile')) return 'avatar';
    if (haystack.includes('video') || haystack.includes('frame')) return 'video';
    if (haystack.includes('background') || haystack.includes('custom_background')) return 'background';
    if (haystack.includes('slide') || finding?.slide_order !== undefined || finding?.slide_index !== undefined) return 'slide';
  }
  if (
    haystack.includes('transcript')
    || haystack.includes('page_key')
    || haystack.includes('original')
    || haystack.includes('narration')
  ) {
    return 'transcript';
  }
  return 'project';
}

function findingFieldKey(finding) {
  if (findingAssetKind(finding) === 'background') return 'background';
  if (findingAssetKind(finding) === 'slide') return 'slide_image';
  const haystack = findingHaystack(finding);
  if (haystack.includes('narration')) return 'narration_text';
  if (haystack.includes('original') || haystack.includes('display')) return 'original_text';
  if (findingAssetKind(finding) === 'transcript') return 'page';
  return '';
}

function findingFieldLabel(finding) {
  const key = findingFieldKey(finding);
  if (key === 'narration_text') return 'Narration text';
  if (key === 'original_text') return 'Original text';
  return '';
}

function findingSlideNumber(finding) {
  if (finding?.slide_number !== undefined && finding?.slide_number !== null) {
    const slideNumber = Number(finding.slide_number);
    if (Number.isFinite(slideNumber) && slideNumber > 0) return slideNumber;
  }
  if (finding?.slide_index !== undefined && finding?.slide_index !== null) {
    const slideNumber = Number(finding.slide_index) + 1;
    if (Number.isFinite(slideNumber) && slideNumber > 0) return slideNumber;
  }
  if (finding?.slide_order !== undefined && finding?.slide_order !== null) {
    const slideNumber = Number(finding.slide_order) + 1;
    if (Number.isFinite(slideNumber) && slideNumber > 0) return slideNumber;
  }
  const match = textValue(finding?.location_label).match(/slide\s*(\d+)/i);
  if (match) {
    const slideNumber = Number(match[1]);
    if (Number.isFinite(slideNumber) && slideNumber > 0) return slideNumber;
  }
  return null;
}

function issueSlideIndex(issue) {
  for (const key of ['slide_index', 'slide_order']) {
    if (issue?.[key] !== undefined && issue?.[key] !== null && issue?.[key] !== '') {
      const value = Number(issue[key]);
      if (Number.isFinite(value) && value >= 0) return value;
    }
  }
  if (issue?.slide_number !== undefined && issue?.slide_number !== null && issue?.slide_number !== '') {
    const value = Number(issue.slide_number);
    if (Number.isFinite(value) && value > 0) return value - 1;
  }
  return null;
}

function issueHasPageLocator(issue) {
  return Boolean(
    textValue(issue?.transcript_page_id).trim()
      || textValue(issue?.slide_id).trim()
      || textValue(issue?.page_key).trim()
      || textValue(issue?.slide_index).trim()
      || textValue(issue?.slide_order).trim()
      || textValue(issue?.slide_number).trim(),
  );
}

function pageMatchesIssueTarget(issue, page, index) {
  if (!issue || !page) return false;
  const slideId = textValue(issue?.slide_id).trim();
  if (slideId && slideId === textValue(page?.id).trim()) return true;
  const transcriptPageId = textValue(issue?.transcript_page_id).trim();
  if (transcriptPageId && transcriptPageId === textValue(page?.id).trim()) return true;
  const pageKey = textValue(issue?.page_key).trim().toLowerCase();
  if (pageKey && pageKey === textValue(page?.page_key).trim().toLowerCase()) return true;
  const objectId = textValue(issue?.object_id).trim();
  if (objectId && (objectId === textValue(page?.id).trim() || objectId === textValue(page?.page_key).trim())) return true;
  const slideIndex = issueSlideIndex(issue);
  if (slideIndex === null) return false;
  const candidates = [
    page?.source_slide_index,
    page?.order,
    index,
  ]
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value));
  return candidates.includes(slideIndex);
}

function issueHasClearSlideLocator(issue) {
  return Boolean(textValue(issue?.slide_id).trim() || textValue(issue?.transcript_page_id).trim())
    || issueSlideIndex(issue) !== null;
}

function getIssueEditorTarget(issue, pages = []) {
  const assetKind = getEditorVisualIssueAssetKind(issue);
  if (!isRealVisualIssue(issue)) {
    if (isTextModerationIssue(issue)) return { type: 'transcript' };
    return { type: 'moderation_panel' };
  }
  if (assetKind === 'cover') {
    return { type: 'cover' };
  }
  if (assetKind === 'custom_background' || assetKind === 'slide_image' || assetKind === 'draft_visual_asset') {
    if ((assetKind === 'slide_image' || assetKind === 'draft_visual_asset') && !issueHasClearSlideLocator(issue)) {
      return { type: 'moderation_panel' };
    }
    if (!issueHasPageLocator(issue)) return { type: 'moderation_panel' };
    const pageIndex = (Array.isArray(pages) ? pages : []).findIndex((page, index) => (
      pageMatchesIssueTarget(issue, page, index)
    ));
    if (pageIndex < 0) return { type: 'moderation_panel' };
    const page = pages[pageIndex];
    return {
      type: assetKind === 'custom_background' ? 'background' : 'slide',
      page,
      pageIndex,
      key: pageIdentity(page, pageIndex),
    };
  }
  return { type: 'moderation_panel' };
}

function getIssuesForCover(findings = []) {
  return getCoverVisualIssues(findings);
}

function getCoverVisualIssues(findings = []) {
  return (Array.isArray(findings) ? findings : []).filter((issue) => (
    isRealVisualIssue(issue) && getEditorVisualIssueAssetKind(issue) === 'cover'
  ));
}

function getIssuesForBackground(findings = [], pages = []) {
  return getBackgroundVisualIssues(findings, pages);
}

function getBackgroundVisualIssues(findings = [], pages = []) {
  return (Array.isArray(findings) ? findings : []).filter((issue) => (
    isRealVisualIssue(issue)
      && getEditorVisualIssueAssetKind(issue) === 'custom_background'
      && getIssueEditorTarget(issue, pages).type === 'background'
  ));
}

function getSlideVisualIssues(findings = [], pages = []) {
  return (Array.isArray(findings) ? findings : []).filter((issue) => {
    const assetKind = getEditorVisualIssueAssetKind(issue);
    return isRealVisualIssue(issue)
      && (assetKind === 'slide_image' || assetKind === 'draft_visual_asset')
      && getIssueEditorTarget(issue, pages).type === 'slide';
  });
}

function getIssuesForSlide(findings = [], pages = []) {
  return getSlideVisualIssues(findings, pages);
}

function findingLocationLabel(finding) {
  if (finding?.asset_label) return finding.asset_label;
  const kind = findingAssetKind(finding);
  if (kind === 'cover') return 'Cover image';
  if (kind === 'background') return 'Custom background';
  if (kind === 'slide') {
    const slideNumber = findingSlideNumber(finding);
    return slideNumber ? `Slide ${slideNumber} image` : 'Slide image';
  }
  if (kind === 'avatar') return 'Avatar image';
  if (kind === 'video') return finding?.timestamp_label || 'Video frame';
  if (kind === 'transcript') {
    const slideNumber = findingSlideNumber(finding);
    if (slideNumber) return `Slide ${slideNumber}`;
    return 'Lesson text';
  }
  if (finding?.timestamp_label) return finding.timestamp_label;
  return 'Project';
}

function isProviderUnavailableVisualIssue(issue) {
  if (!issue || isTextModerationIssue(issue)) return false;
  const category = textValue(issue?.category).trim().toLowerCase();
  const title = textValue(issue?.reason_title).trim().toLowerCase();
  const technicalReason = textValue(issue?.technical_reason || issue?.evidence_excerpt).trim().toLowerCase();
  const provider = textValue(issue?.provider).trim().toLowerCase();
  return category === 'provider_unavailable'
    || title === 'visual safety scan unavailable'
    || technicalReason.includes('semantic_visual_provider_unavailable')
    || provider.includes('provider_unavailable');
}

function visualScanUnavailableMessage(warning) {
  const issue = (Array.isArray(warning?.findings) ? warning.findings : []).find(isProviderUnavailableVisualIssue);
  if (!issue) return '';
  return `${findingReasonTitle(issue) || 'Visual safety scan unavailable'}. ${visualIssueStatusLabel(issue) || 'Needs admin review'}.`;
}

function sceneModerationWarningMessage(warning) {
  if (warning?.state === 'pending') {
    return 'Visual scan pending';
  }
  const unavailableMessage = visualScanUnavailableMessage(warning);
  if (unavailableMessage) return unavailableMessage;
  const fields = Array.isArray(warning?.fields) ? warning.fields : [];
  if (fields.includes('background')) {
    return 'This scene background was blocked by visual moderation. Replace it before publishing.';
  }
  if (fields.includes('slide_image')) {
    return 'This slide image was blocked by visual moderation. Replace it before publishing.';
  }
  return 'This slide has a moderation finding.';
}

function findingMetaLabel(finding) {
  const parts = [];
  if (finding?.ui_anchor) parts.push(finding.ui_anchor);
  if (finding?.content_type) parts.push(finding.content_type);
  if (finding?.object_type) parts.push(finding.object_type);
  if (finding?.slide_order !== undefined && finding?.slide_order !== null) {
    const slideNumber = Number(finding.slide_order) + 1;
    parts.push(Number.isFinite(slideNumber) ? `Slide ${slideNumber}` : 'Slide');
  }
  if (finding?.page_key) parts.push(`Page key: ${finding.page_key}`);
  if (finding?.timestamp_label) parts.push(finding.timestamp_label);
  if (finding?.timestamp_seconds !== undefined && finding?.timestamp_seconds !== null && !finding?.timestamp_label) {
    parts.push(`${Number(finding.timestamp_seconds).toFixed(1)}s`);
  }
  return parts.join(' · ');
}

function findingReasonTitle(finding) {
  return textValue(finding?.reason_title || finding?.category || 'Moderation finding').replace(/_/g, ' ');
}

function findingPrimaryMessage(finding, { admin = false } = {}) {
  return textValue(
    (admin ? finding?.admin_reason_message : finding?.publisher_reason_message)
      || finding?.reason_message
      || finding?.user_message
      || finding?.admin_message
      || 'This content needs moderation attention.',
  );
}

function moderationVisualIssues(moderation, findings = []) {
  const explicitIssues = Array.isArray(moderation?.visual_issues) ? moderation.visual_issues : [];
  if (explicitIssues.length > 0) return explicitIssues.filter(isVisualModerationIssue);
  return (Array.isArray(findings) ? findings : []).filter(isVisualModerationIssue);
}

function moderationIssueDedupKey(issue, index) {
  const id = issue?.finding_id || issue?.id;
  if (id) return `id-${id}`;
  return [
    getIssueAssetKind(issue),
    issue?.source_kind || '',
    issue?.transcript_page_id || '',
    issue?.page_key || '',
    issue?.slide_index ?? issue?.slide_order ?? issue?.slide_number ?? '',
    issue?.timestamp_seconds || '',
    issue?.category || '',
    index,
  ].join('|');
}

function mergeModerationIssues(...groups) {
  const merged = [];
  const seen = new Set();
  groups.forEach((group) => {
    (Array.isArray(group) ? group : []).forEach((issue, index) => {
      const key = moderationIssueDedupKey(issue, index);
      if (seen.has(key)) return;
      seen.add(key);
      merged.push(issue);
    });
  });
  return merged;
}

function getStudioVisualIssues(project) {
  const summary = plainObject(project?.moderation_summary) || {};
  const moderation = plainObject(project?.moderation) || {};
  return mergeModerationIssues(
    project?.visual_issues,
    moderation?.visual_issues,
    summary?.visual_issues,
    summary?.findings,
    project?.findings,
  ).filter(isUnresolvedVisualIssue);
}

function visualIssueStatusLabel(issue) {
  const decision = String(issue?.decision || '').trim().toLowerCase();
  if (decision === 'blocked' || decision === 'block' || decision === 'rejected') return 'Blocked';
  if (decision === 'approved' || decision === 'approve' || decision === 'allow') return 'Approved';
  return 'Needs admin review';
}

function publisherMessageForModeration(moderation) {
  const findings = Array.isArray(moderation?.findings) ? moderation.findings : [];
  const visualIssue = moderationVisualIssues(moderation, findings)[0] || null;
  const issue = visualIssue || findings[0] || null;
  const kind = rawIssueAssetKind(issue);
  if (kind === 'cover') return 'Please replace the lesson cover image.';
  if (kind === 'custom_background') return 'Please replace the custom background image.';
  if (kind === 'slide_image' || kind === 'draft_visual_asset') {
    const slideNumber = Number(issue?.slide_number || (Number(issue?.slide_order) + 1));
    if (Number.isFinite(slideNumber) && slideNumber > 0) return `Please replace Slide ${slideNumber} image.`;
    return 'Please replace the flagged slide image.';
  }
  if (isTextModerationIssue(issue)) return 'Please remove or rewrite the highlighted transcript text.';
  return textValue(issue?.publisher_reason_message || issue?.reason_message || moderation?.message).trim();
}

function visualIssueKey(issue, index) {
  return `${issue?.asset_kind || findingAssetKind(issue)}-${issue?.object_id || issue?.page_key || issue?.timestamp_seconds || index}`;
}

function publishAvailabilityLabel(moderation, canPublish) {
  if (canPublish) return 'Publish allowed';
  if (moderation?.publish_blocked_by_moderation) return 'Publish blocked by moderation';
  const reason = String(moderation?.publish_block_reason || moderation?.publish_block?.reason || '').trim();
  if (reason === 'render_not_ready') return 'Waiting for render';
  if (reason === 'draft_render_required') return 'Rerender required';
  return 'Publish unavailable';
}

function publishAvailabilityTone(moderation, canPublish) {
  if (canPublish) return 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]';
  if (moderation?.publish_blocked_by_moderation) return 'bg-[color:var(--status-danger-bg)] text-[color:var(--status-danger-fg)]';
  return 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]';
}

function VisualIssuePreview({ issue }) {
  const previewUrl = textValue(issue?.preview_url || issue?.asset_url).trim();
  const [blobUrl, setBlobUrl] = useState('');
  const [failed, setFailed] = useState(!previewUrl);

  useEffect(() => {
    let cancelled = false;
    let objectUrl = '';
    setBlobUrl('');
    setFailed(!previewUrl);
    if (!previewUrl) return () => {};
    fetchAuthenticatedAssetBlobUrl(previewUrl)
      .then((url) => {
        if (cancelled) {
          if (url) URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        setBlobUrl(url);
        setFailed(!url);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [previewUrl]);

  if (failed || !blobUrl) {
    return (
      <div className="flex h-16 w-24 shrink-0 items-center justify-center rounded-lg bg-[color:var(--surface-container-high)] text-[color:var(--status-warning-fg)]">
        <AlertTriangle size={18} />
      </div>
    );
  }

  return (
    <img
      src={blobUrl}
      alt={findingLocationLabel(issue)}
      className="h-16 w-24 shrink-0 rounded-lg object-cover"
      onError={() => setFailed(true)}
    />
  );
}

function AuthenticatedMediaThumbnail({
  src,
  alt,
  className = 'h-14 w-20 rounded-lg object-cover',
  fallbackLabel = 'Image unavailable',
}) {
  const previewUrl = textValue(src).trim();
  const [blobUrl, setBlobUrl] = useState('');
  const [failed, setFailed] = useState(!previewUrl);

  useEffect(() => {
    let cancelled = false;
    let objectUrl = '';
    setBlobUrl('');
    setFailed(!previewUrl);
    if (!previewUrl) return () => {};
    if (/^(blob:|data:)/i.test(previewUrl)) {
      setBlobUrl(previewUrl);
      setFailed(false);
      return () => {};
    }
    fetchAuthenticatedAssetBlobUrl(previewUrl)
      .then((url) => {
        if (cancelled) {
          if (url) URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        setBlobUrl(url);
        setFailed(!url);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [previewUrl]);

  if (failed || !blobUrl) {
    return (
      <div className="flex h-14 w-20 items-center justify-center rounded-lg border border-dashed border-[color:var(--border-subtle)] bg-[color:var(--surface-container-high)] px-2 text-center text-[0.65rem] leading-tight text-[var(--text-muted)]">
        {fallbackLabel}
      </div>
    );
  }

  return (
    <img
      src={blobUrl}
      alt={alt}
      className={className}
      onError={() => setFailed(true)}
    />
  );
}

function findingMatchesTranscriptPage(finding, page, index) {
  if (!finding || !page) return false;
  if (isVisualModerationIssue(finding)) {
    return pageMatchesIssueTarget(finding, page, index);
  }
  const haystack = findingHaystack(finding);
  const pageId = textValue(page?.id);
  const objectId = textValue(finding?.object_id);
  const kind = findingAssetKind(finding);
  if (pageId && objectId && pageId === objectId && (kind === 'transcript' || kind === 'slide' || kind === 'background')) return true;
  if (pageId && haystack.includes(`transcript-page-${pageId}`)) return true;
  const pageKey = textValue(page?.page_key).toLowerCase();
  if (pageKey && haystack.includes(pageKey)) return true;
  const slideOrder = Number(finding?.slide_order);
  const pageSlideOrder = page?.source_slide_index !== undefined && page?.source_slide_index !== null
    ? Number(page.source_slide_index)
    : Number(index);
  if (Number.isFinite(slideOrder) && Number.isFinite(pageSlideOrder) && slideOrder === pageSlideOrder) return true;
  const slideNumber = findingSlideNumber(finding);
  return Boolean(slideNumber && slideNumber === index + 1);
}

function buildModerationWarningMaps(findings, pages) {
  const pageWarnings = {};
  const slideWarnings = {};
  const backgroundWarnings = {};
  const assetWarnings = {
    cover: null,
    background: null,
    avatar: null,
    video: null,
  };
  const unidentifiedWarnings = [];
  const warningEntry = (finding) => ({
    state: visualIssueWarningState(finding) || 'flagged',
    findings: [finding],
  });

  (Array.isArray(findings) ? findings : []).forEach((finding) => {
    if (isRealVisualIssue(finding)) {
      const target = getIssueEditorTarget(finding, pages);
      if (target.type === 'cover') {
        const existing = assetWarnings.cover;
        if (existing) {
          existing.findings.push(finding);
          if (existing.state !== 'flagged' && visualIssueWarningState(finding) === 'flagged') existing.state = 'flagged';
        } else {
          assetWarnings.cover = warningEntry(finding);
        }
        return;
      }
      if (target.type === 'background' && target.key) {
        const existing = backgroundWarnings[target.key] || { fields: [], findings: [], state: visualIssueWarningState(finding) || 'flagged' };
        if (!existing.fields.includes('background')) existing.fields.push('background');
        existing.findings.push(finding);
        if (existing.state !== 'flagged' && visualIssueWarningState(finding) === 'flagged') existing.state = 'flagged';
        backgroundWarnings[target.key] = existing;
        return;
      }
      if (target.type === 'slide' && target.key) {
        const existing = slideWarnings[target.key] || { fields: [], findings: [], state: visualIssueWarningState(finding) || 'flagged' };
        if (!existing.fields.includes('slide_image')) existing.fields.push('slide_image');
        existing.findings.push(finding);
        if (existing.state !== 'flagged' && visualIssueWarningState(finding) === 'flagged') existing.state = 'flagged';
        slideWarnings[target.key] = existing;
        return;
      }
      unidentifiedWarnings.push(finding);
      return;
    }

    if (!isTextModerationIssue(finding)) {
      unidentifiedWarnings.push(finding);
      return;
    }

    const pageIndex = (Array.isArray(pages) ? pages : []).findIndex((page, index) => (
      findingMatchesTranscriptPage(finding, page, index)
    ));

    if (pageIndex < 0) {
      unidentifiedWarnings.push(finding);
      return;
    }
    const page = pages[pageIndex];
    const key = pageIdentity(page, pageIndex);
    const field = findingFieldKey(finding) || 'page';
    const existing = pageWarnings[key] || { fields: [], findings: [] };
    if (!existing.fields.includes(field)) existing.fields.push(field);
    existing.findings.push(finding);
    pageWarnings[key] = existing;
  });

  return { pageWarnings, slideWarnings, backgroundWarnings, assetWarnings, unidentifiedWarnings };
}

function findingDisplayLabel(finding) {
  const fieldLabel = findingFieldLabel(finding);
  const locationLabel = findingLocationLabel(finding);
  return fieldLabel ? `${locationLabel} - ${fieldLabel}` : locationLabel;
}

function projectAvatarEnabled(project) {
  return Boolean(project?.avatar_active || project?.avatar_enabled_override === true);
}

function avatarProcessingStatus(project) {
  return String(project?.avatar_processing_status || 'none').trim().toLowerCase() || 'none';
}

function avatarVisible(project) {
  return project?.avatar_visible !== false;
}

function avatarStatusLabel(project) {
  if (!projectAvatarEnabled(project)) return 'Avatar disabled.';
  return avatarRuntimeStatusMessage(project);
}

function projectRenderReady(project) {
  const projectStatus = normalizedStatus(project?.status);
  const jobStatus = projectLatestJobStatus(project);
  return projectStatus === 'ready' || jobStatus === 'done' || jobStatus === 'ready';
}

function studioLessonNeedsPolling({
  project,
  moderation,
  transcriptPages,
  moderationActionBusy,
  activeRerenderStatus,
  pendingSubtitleGeneration,
  generatingSubtitleTrack,
}) {
  if (!project?.id) return false;

  const projectStatus = projectRawStatus(project);
  const jobStatus = projectLatestJobStatus(project);
  const moderationStatus = projectModerationStatus(project, moderation);
  const moderationStale = projectHasModerationStaleMarkers(project, moderation);
  const transcriptPageCount = Array.isArray(transcriptPages) ? transcriptPages.length : 0;
  const jobInFlight = UNSTABLE_JOB_STATUSES.has(jobStatus);
  const projectInFlight = (
    jobInFlight
    || ['processing', 'queued', 'running', 'pending'].includes(projectStatus)
    || (projectStatus === 'draft' && transcriptPageCount === 0 && jobStatus !== 'failed')
  );
  const moderationInFlight = moderationStatus === 'pending'
    || moderationStale
    || Boolean(moderationActionBusy)
    || (moderationStatus === 'not_scanned' && projectInFlight);
  const transcriptWaiting = projectInFlight && transcriptPageCount === 0;
  const rerenderInFlight = UNSTABLE_JOB_STATUSES.has(normalizedStatus(activeRerenderStatus));
  const avatarInFlight = ['queued', 'processing'].includes(avatarProcessingStatus(project));
  const subtitleInFlight = Boolean(pendingSubtitleGeneration) || Boolean(generatingSubtitleTrack);

  return Boolean(
    projectInFlight
      || moderationInFlight
      || transcriptWaiting
      || rerenderInFlight
      || avatarInFlight
      || subtitleInFlight
      || moderationStale
      || (!STABLE_MODERATION_STATUSES.has(moderationStatus) && moderationStatus !== 'not_scanned')
  );
}

function subtitleTrackSummary(tracks, lesson) {
  const byKey = new Map();
  for (const track of tracks || []) {
    const rawCode = String(track?.language_code || '').trim().toLowerCase();
    const isOriginal = track?.is_original === true || track?.type === 'original' || track?.id === 'original' || rawCode === 'original';
    const key = isOriginal ? 'original' : rawCode;
    const label = isOriginal ? 'Original' : String(track?.language_label || rawCode.toUpperCase()).trim();
    const status = String(track?.status || '').trim().toLowerCase();
    const vttUrl = String(track?.vtt_url || track?.subtitle_vtt_url || '').trim();
    if (!key || !label || !vttUrl || (status && status !== 'ready')) continue;
    byKey.set(key, { key, label, isOriginal });
  }
  if (!byKey.has('original') && (lesson?.vtt_url || lesson?.subtitle_vtt_url)) {
    byKey.set('original', { key: 'original', label: 'Original', isOriginal: true });
  }
  const original = byKey.get('original');
  const translated = Array.from(byKey.values())
    .filter((track) => !track.isOriginal)
    .sort((a, b) => a.label.localeCompare(b.label));
  const ordered = original ? [original, ...translated] : translated;
  return {
    labels: ordered.map((track) => track.label),
    hasEnglish: ordered.some((track) => track.key === 'en'),
  };
}

function subtitleTrackCode(track) {
  const raw = String(track?.language_code || '').trim().toLowerCase();
  if (!raw || raw === 'original' || track?.is_original === true) return '';
  return raw;
}

function isReadySubtitleTrack(track) {
  return String(track?.status || '').trim().toLowerCase() === 'ready' && Boolean(track?.vtt_url);
}

function activeSubtitleTrackCodes(tracks) {
  const codes = new Set();
  for (const track of tracks || []) {
    const code = subtitleTrackCode(track);
    const status = String(track?.status || '').trim().toLowerCase();
    if (code && ['pending', 'processing', 'ready'].includes(status)) codes.add(code);
  }
  return codes;
}

function subtitleProviderMessage(track) {
  const providerUsed = String(track?.metadata?.provider_used || track?.provider || '').trim().toLowerCase();
  const providerNames = { ollama: 'Ollama', libretranslate: 'LibreTranslate', argos: 'Argos', mock: 'mock', api: 'API provider' };
  const providerLabel = providerUsed ? ` via ${providerNames[providerUsed] || providerUsed}` : '';
  const mockNote = providerUsed === 'mock' ? ' Mock provider used; this is not a real translation.' : '';
  return { providerLabel, mockNote };
}

function safeDateLabel(value) {
  if (!value) return 'Recent';
  return new Date(value).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function lessonNotesKey(projectId) {
  return `visus-studio-notes-${projectId || 'none'}`;
}

function localDraftScope(user) {
  return encodeURIComponent(
    textValue(user?.id || user?.pk || user?.email || user?.username || 'anonymous').trim() || 'anonymous',
  );
}

function lessonNotesDraftKey(userScope, projectId) {
  return `visus-studio-local-draft-${userScope || 'anonymous'}-${projectId || 'none'}-notes`;
}

function readLessonNotesDraft(userScope, projectId) {
  if (typeof window === 'undefined' || !projectId) return null;
  const key = lessonNotesDraftKey(userScope, projectId);
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || 'null');
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch {
    window.localStorage.removeItem(key);
    return null;
  }
}

function writeLessonNotesDraft(userScope, projectId, value) {
  if (typeof window === 'undefined' || !projectId) return;
  const key = lessonNotesDraftKey(userScope, projectId);
  window.localStorage.setItem(key, JSON.stringify({
    version: 1,
    value: textValue(value),
    updatedAt: Date.now(),
  }));
}

function clearLessonNotesDraft(userScope, projectId) {
  if (typeof window === 'undefined' || !projectId) return;
  window.localStorage.removeItem(lessonNotesDraftKey(userScope, projectId));
}

function editorDraftKey(projectId) {
  return `visus-studio-editor-draft-${projectId || 'new'}`;
}

function textValue(value) {
  return value === null || value === undefined ? '' : String(value);
}

function studioSessionKey(user) {
  const userId = textValue(user?.id || user?.pk || user?.email || user?.username).trim();
  return userId ? `visus-studio-position-${userId}` : '';
}

function readStudioSession(key) {
  if (!key || typeof window === 'undefined') return {};
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(key) || '{}');
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    window.sessionStorage.removeItem(key);
    return {};
  }
}

function writeStudioSession(key, patch) {
  if (!key || typeof window === 'undefined') return;
  try {
    const previous = readStudioSession(key);
    window.sessionStorage.setItem(key, JSON.stringify({
      ...previous,
      ...patch,
      updatedAt: Date.now(),
    }));
  } catch {
    // Ignore storage quota/privacy mode failures; position memory is best-effort.
  }
}

function clearStudioSession(key) {
  if (!key || typeof window === 'undefined') return;
  window.sessionStorage.removeItem(key);
}

function positiveIdParam(searchParams, key) {
  const value = Number(searchParams.get(key) || 0);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function safeCssBackgroundUrl(value) {
  const rawUrl = textValue(value).trim();
  if (!rawUrl) return '';
  const lower = rawUrl.toLowerCase();
  if (lower.startsWith('javascript:') || lower.startsWith('data:') || lower.startsWith('file:')) return '';
  try {
    const parsed = new URL(rawUrl, window.location.origin);
    if (!['http:', 'https:', 'blob:'].includes(parsed.protocol)) return '';
    return parsed.href.replace(/"/g, '%22');
  } catch {
    return '';
  }
}

function cacheBustedMediaUrl(url, token = Date.now()) {
  const rawUrl = textValue(url).trim();
  if (!rawUrl || rawUrl.startsWith('blob:') || rawUrl.startsWith('data:')) return rawUrl;
  const separator = rawUrl.includes('?') ? '&' : '?';
  return `${rawUrl}${separator}v=${encodeURIComponent(token)}`;
}

function mediaUrlWithoutStudioCacheBuster(url) {
  const rawUrl = textValue(url).trim();
  if (!rawUrl) return rawUrl;
  return rawUrl
    .replace(/([?&])v=[^&]*&?/g, '$1')
    .replace(/[?&]$/, '');
}

function preserveCacheBustedMediaUrl(previousUrl, incomingUrl) {
  const previous = textValue(previousUrl).trim();
  const incoming = textValue(incomingUrl).trim();
  if (!previous || !incoming) return incoming || previous;
  if (!previous.includes('v=')) return incoming;
  return mediaUrlWithoutStudioCacheBuster(previous) === mediaUrlWithoutStudioCacheBuster(incoming)
    ? previous
    : incoming;
}

function pageIdentity(page, index) {
  return String(page?.page_key || page?.id || `page-${index}`);
}

function pageNarration(page) {
  if (page && Object.prototype.hasOwnProperty.call(page, 'narration_text')) {
    return textValue(page.narration_text);
  }
  return textValue(page?.original_text);
}

function pageDisplayText(page) {
  if (page && Object.prototype.hasOwnProperty.call(page, 'original_text')) {
    return textValue(page.original_text);
  }
  const paragraphs = Array.isArray(page?.editor_document?.paragraphs)
    ? page.editor_document.paragraphs
    : [];
  const fromDocument = paragraphs
    .map((item) => textValue(item?.text))
    .join('\n')
    .trim();
  return fromDocument || pageNarration(page);
}

function hasDoubleBlankLine(value) {
  return /\n\s*\n/.test(textValue(value).replace(/\r\n/g, '\n').replace(/\r/g, '\n'));
}

function textPreview(value, maxLength = 110) {
  const normalized = textValue(value).replace(/\s+/g, ' ').trim();
  if (!normalized) return 'No narration text yet';
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}...` : normalized;
}

function isProbablyRtlText(value) {
  return /[\u0591-\u07FF\uFB1D-\uFDFD\uFE70-\uFEFC]/.test(textValue(value));
}

function sceneLabel(page, index) {
  if (page?.source_slide_index !== undefined && page?.source_slide_index !== null) {
    const slideNumber = Number(page.source_slide_index) + 1;
    const splitNumber = Number(page.split_index || 0);
    return splitNumber > 0 ? `Slide ${slideNumber}.${splitNumber + 1}` : `Slide ${slideNumber}`;
  }
  return `Slide ${index + 1}`;
}

function sceneStatusFromPage(page) {
  const narration = pageNarration(page);
  if (!narration.trim()) return 'empty';
  if (hasDoubleBlankLine(narration)) return 'split candidate';
  const textFlags = page?.editor_document?.text && typeof page.editor_document.text === 'object'
    ? page.editor_document.text
    : {};
  if (textFlags.display_text_customized || textFlags.narration_customized) {
    return 'edited';
  }
  if (narration.trim() && narration.trim() !== textValue(page?.original_text).trim()) {
    return 'edited';
  }
  return 'unchanged';
}

function sceneStatusTone(status) {
  const value = String(status || '').toLowerCase();
  if (value === 'empty') {
    return 'bg-[color:var(--status-danger-bg)] text-[color:var(--status-danger-fg)]';
  }
  if (value === 'edited' || value === 'split candidate' || value === 'draft') {
    return 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]';
  }
  return 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]';
}

function formatSeconds(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return '';
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60).toString().padStart(2, '0');
  return `${minutes}:${rest}`;
}

function sceneTimingLabel(page) {
  const duration = Number(page?.duration_seconds);
  if (Number.isFinite(duration) && duration > 0) return `${duration.toFixed(duration >= 10 ? 0 : 1)}s`;
  const start = formatSeconds(page?.start_seconds);
  const end = formatSeconds(page?.end_seconds);
  if (start && end) return `${start}-${end}`;
  return 'No timing yet';
}

function firstAvailableUrl(...values) {
  return values.map(textValue).find(Boolean) || '';
}

const SCENE_BACKGROUND_MODES = new Set(['original', 'whiteboard', 'custom', 'source_background']);
const SCENE_BACKGROUND_FITS = new Set(['contain', 'cover', 'stretch']);
const SCENE_TEXT_SCALE_MIN = 0.75;
const SCENE_TEXT_SCALE_MAX = 2;
const SCENE_TEXT_PREVIEW_BASE_REM = 2.35;

function pageSceneSettings(page) {
  const scene = page?.editor_document?.scene && typeof page.editor_document.scene === 'object'
    ? page.editor_document.scene
    : {};
  const rawMode = String(scene.background_mode || '').trim().toLowerCase();
  const backgroundMode = SCENE_BACKGROUND_MODES.has(rawMode)
    ? rawMode
    : (page?.whiteboard_mode ? 'whiteboard' : 'original');
  const rawFit = String(scene.background_fit || '').trim().toLowerCase();
  const backgroundFit = SCENE_BACKGROUND_FITS.has(rawFit) ? rawFit : 'contain';
  const sourceType = textValue(scene.source_type).trim().toLowerCase().replace(/^\./, '');
  const numericScale = Number(scene.text_scale);
  const textScale = Number.isFinite(numericScale)
    ? Math.min(SCENE_TEXT_SCALE_MAX, Math.max(SCENE_TEXT_SCALE_MIN, numericScale))
    : 1;
  const hasSource = Boolean(scene.has_source_background || scene.source_background_url);
  const sourceBackgroundAvailable = Boolean(scene.source_background_available || (sourceType === 'pptx' && hasSource));
  const rawHighlightStyle = String(scene.highlight_style || 'none').trim().toLowerCase();
  const highlightStyle = ['none', 'box', 'bold'].includes(rawHighlightStyle) ? rawHighlightStyle : 'none';
  const rawHighlightDetector = String(scene.highlight_detector || 'auto').trim().toLowerCase();
  const highlightDetector = rawHighlightDetector === 'auto' ? 'auto' : 'auto';
  return {
    backgroundMode,
    backgroundFit,
    textScale,
    sourceType,
    originalUrl: textValue(scene.original_background_url),
    customUrl: textValue(scene.custom_background_url),
    sourceUrl: sourceBackgroundAvailable ? textValue(scene.source_background_url) : '',
    hasOriginal: Boolean(scene.has_original_background || scene.original_background_url),
    hasCustom: Boolean(scene.has_custom_background || scene.custom_background_url),
    hasSource,
    sourceBackgroundAvailable,
    sourceWarnings: Array.isArray(scene.source_background_warnings) ? scene.source_background_warnings : [],
    highlightEnabled: Boolean(scene.highlight_enabled),
    highlightStyle,
    highlightDetector,
    highlightPreviewUrl: textValue(scene.highlight_preview_url),
  };
}

function scenePreviewTextLayout(scale, text) {
  const numericScale = Number(scale);
  const normalizedScale = Number.isFinite(numericScale)
    ? Math.min(SCENE_TEXT_SCALE_MAX, Math.max(SCENE_TEXT_SCALE_MIN, numericScale))
    : 1;
  const normalizedText = textValue(text).replace(/\s+/g, ' ').trim();
  const hardLineCount = textValue(text).split(/\r\n|\r|\n/).filter(Boolean).length || 1;
  const density = Math.max(normalizedText.length / 190, hardLineCount / 4, 1);
  const preferredSize = normalizedScale * SCENE_TEXT_PREVIEW_BASE_REM;
  const fittedSize = preferredSize / Math.sqrt(density);
  return {
    fontSize: `${Math.min(4.25, Math.max(1.05, fittedSize))}rem`,
    lineHeight: density > 2.6 ? 1.06 : density > 1.5 ? 1.1 : 1.18,
    maxWidth: density > 2.4 ? '96%' : density > 1.35 ? '90%' : '82%',
    padding: density > 2.6 ? '0.45rem 0.35rem' : density > 1.5 ? '0.75rem 0.85rem' : '1.25rem 1.5rem',
  };
}

function sceneBackgroundUrl(page) {
  const settings = pageSceneSettings(page);
  if (settings.backgroundMode === 'whiteboard') return '';
  if (settings.backgroundMode === 'custom') return settings.customUrl;
  if (settings.backgroundMode === 'source_background') return settings.sourceUrl;
  return settings.originalUrl || settings.customUrl;
}

function backgroundObjectFit(fit) {
  if (fit === 'cover') return 'cover';
  if (fit === 'stretch') return 'fill';
  return 'contain';
}

function sceneModeLabel(mode) {
  if (mode === 'whiteboard') return 'Whiteboard';
  if (mode === 'custom') return 'Custom';
  if (mode === 'source_background') return 'Source Background';
  return 'Original';
}

function ModerationPanel({
  project,
  moderation,
  loading,
  error,
  actionBusy,
  reviewDialogOpen,
  reviewMessage,
  onReviewMessageChange,
  onRefresh,
  onRescan,
  onOpenReview,
  onCloseReview,
  onSubmitReview,
  onSelectFinding,
  visualModerationEnabled = true,
}) {
  if (!project) return null;

  const status = projectModerationStatus(project, moderation);
  const findings = Array.isArray(moderation?.findings) ? moderation.findings : [];
  const visualIssues = moderationVisualIssues(moderation, findings);
  const visualBlockWithoutIssue = visualIssues.length === 0
    && ['visual_moderation_rejected', 'video_frame_audit_rejected'].includes(
      String(moderation?.publish_block_reason || moderation?.publish_block?.reason || '').trim(),
    );
  const canRequestAdminReview = Boolean(moderation?.can_request_admin_review);
  const hasOpenAdminReview = String(moderation?.admin_review?.status || '').trim().toLowerCase() === 'open';
  const canPublish = projectCanPublishFromModeration(project, moderation);
  const visualScan = projectVisualStaleMarker(project, moderation);
  const visualNeedsRescan = visualModerationEnabled && moderationMarkerIsStale(visualScan);
  const adminResponse = textValue(moderation?.admin_review?.admin_response).trim();
  const adminNote = textValue(
    moderation?.admin_note
    || moderation?.manual_moderation_reason
    || project?.manual_moderation_reason
    || adminResponse,
  ).trim();
  const manualStatus = String(moderation?.manual_moderation_status || project?.manual_moderation_status || '').trim();
  const rescanBlockedByManualDecision = ['blocked', 'rejected', 'needs_review'].includes(manualStatus);

  return (
    <div className="rounded-2xl token-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="label-sm">Moderation</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className={`rounded-full px-3 py-1 text-xs font-semibold ${moderationStatusTone(status)}`}>
              {moderationStatusLabel(status)}
            </span>
            <span
              className={`rounded-full px-3 py-1 text-xs font-semibold ${publishAvailabilityTone(moderation, canPublish)}`}
            >
              {publishAvailabilityLabel(moderation, canPublish)}
            </span>
            {moderation?.latest_run_id && (
              <span className="rounded-full bg-[color:var(--surface-muted)] px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
                Run #{moderation.latest_run_id}
              </span>
            )}
            {visualNeedsRescan && (
              <span className="rounded-full bg-[color:var(--status-info-bg)] px-3 py-1 text-xs font-semibold text-[color:var(--status-info-fg)]">
                Visual recheck needed
              </span>
            )}
            {!visualModerationEnabled && (
              <span className="rounded-full bg-[color:var(--surface-muted)] px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
                Visual scan disabled
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={onRefresh} disabled={loading || Boolean(actionBusy)}>
            <RefreshCcw size={14} />
            <span>Refresh moderation status</span>
          </Button>
          <Button size="sm" variant="secondary" onClick={onRescan} disabled={loading || Boolean(actionBusy) || rescanBlockedByManualDecision}>
            <RefreshCcw size={14} />
            <span>Resubmit moderation scan</span>
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={onOpenReview}
            disabled={!canRequestAdminReview || loading || Boolean(actionBusy)}
          >
            <FileText size={14} />
            <span>Ask admin for review</span>
          </Button>
        </div>
      </div>

      <p className="mt-3 text-sm text-[var(--text-secondary)]">
        {loading ? 'Loading moderation status...' : moderationMessage(project, moderation)}
      </p>
      {hasOpenAdminReview && (
        <p className="mt-2 rounded-xl bg-[color:var(--status-info-bg)] px-3 py-2 text-sm font-medium text-[color:var(--status-info-fg)]">
          A review request is already open. Please wait for an admin response.
        </p>
      )}
      {adminNote && (
        <div className="mt-3 rounded-xl border border-[color:var(--status-info-fg)] bg-[color:var(--status-info-bg)] p-3">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[color:var(--status-info-fg)]">
            {manualStatus === 'request_changes' ? 'Admin requested changes' : 'Admin moderation message'}
          </p>
          <p className="mt-1 whitespace-pre-wrap text-sm text-[var(--text-primary)]">{adminNote}</p>
          {manualStatus === 'request_changes' && (
            <p className="mt-2 text-sm text-[var(--text-secondary)]">Update the lesson and request review.</p>
          )}
        </div>
      )}
      {visualNeedsRescan && (
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          {visualScan?.message || 'A Studio image changed after the last visual moderation scan.'}
        </p>
      )}
      {(visualIssues.length > 0 || visualBlockWithoutIssue) && (
        <div className="mt-4 space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">
            Visual issue
          </p>
          {visualIssues.length > 0 ? (
            visualIssues.map((issue, index) => {
              const clickable = typeof onSelectFinding === 'function' && findingAssetKind(issue) !== 'project';
              return (
                <article
                  key={visualIssueKey(issue, index)}
                  className={`flex gap-3 rounded-xl border border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)] p-3 ${clickable ? 'cursor-pointer transition hover:bg-[color:var(--hover-surface)]' : ''}`}
                  onClick={() => {
                    if (clickable) onSelectFinding(issue);
                  }}
                >
                  <VisualIssuePreview issue={issue} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-semibold text-[var(--text-primary)]">{findingLocationLabel(issue)}</p>
                      <span className={`rounded-full px-2 py-0.5 text-[0.68rem] font-semibold ${moderationStatusTone(issue?.decision)}`}>
                        {visualIssueStatusLabel(issue)}
                      </span>
                    </div>
                    <p className="mt-1 text-sm font-medium text-[var(--text-primary)]">{findingReasonTitle(issue)}</p>
                    <p className="mt-1 text-sm text-[var(--text-secondary)]">{findingPrimaryMessage(issue)}</p>
                    {clickable && (
                      <p className="mt-2 text-xs font-semibold text-[var(--accent-primary)]">View in editor</p>
                    )}
                  </div>
                </article>
              );
            })
          ) : (
            <div className="rounded-xl border border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)] p-3 text-sm text-[var(--text-secondary)]">
              A moderation issue needs review, but the exact editor location could not be identified.
            </div>
          )}
        </div>
      )}

      {error && (
        <p className="mt-3 rounded-xl bg-[color:var(--feedback-danger-bg)] px-3 py-2 text-sm text-[color:var(--feedback-danger-fg)]">
          {error}
        </p>
      )}

      {reviewDialogOpen && (
        <div className="mt-4 space-y-3 rounded-xl border border-[var(--border-subtle)] p-3">
          <label className="block text-sm text-[var(--text-secondary)]">
            Review message
            <textarea
              value={reviewMessage}
              onChange={(event) => onReviewMessageChange(event.target.value)}
              maxLength={1000}
              placeholder="Explain why this lesson should receive human review..."
              className="focus-ring mt-2 min-h-[92px] w-full resize-y rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 text-sm text-[var(--text-primary)]"
            />
          </label>
          <div className="flex flex-wrap justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={onCloseReview} disabled={Boolean(actionBusy)}>
              Cancel
            </Button>
            <Button size="sm" onClick={onSubmitReview} disabled={Boolean(actionBusy)}>
              Submit review request
            </Button>
          </div>
        </div>
      )}

      <div className="mt-4 space-y-2">
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">Findings</p>
        {findings.length === 0 ? (
          <p className="text-sm text-[var(--text-secondary)]">No visible moderation findings.</p>
        ) : (
          findings.map((finding, index) => {
            const meta = findingMetaLabel(finding);
            const clickable = typeof onSelectFinding === 'function' && findingAssetKind(finding) !== 'project';
            return (
              <article
                key={`${finding.category || 'finding'}-${finding.object_id || index}`}
                className={`rounded-xl bg-[color:var(--surface-muted)] p-3 ${clickable ? 'cursor-pointer transition hover:bg-[color:var(--hover-surface)]' : ''}`}
                onClick={() => {
                  if (clickable) onSelectFinding(finding);
                }}
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="flex flex-wrap gap-1.5 text-[0.68rem] font-semibold">
                  <span className="rounded-full bg-[var(--surface-container-highest)] px-2 py-0.5 text-[var(--text-primary)]">
                    {findingReasonTitle(finding)}
                  </span>
                  <span className={`rounded-full px-2 py-0.5 ${moderationStatusTone(finding.decision)}`}>
                    {visualIssueStatusLabel(finding)}
                  </span>
                  <span className="rounded-full bg-[color:var(--surface-container-high)] px-2 py-0.5 text-[var(--text-secondary)]">
                    {finding.category || 'unknown'} / {finding.severity || 'low'}
                  </span>
                  </div>
                  {clickable && (
                    <span className="text-xs font-semibold text-[var(--accent-primary)]">View in editor</span>
                  )}
                </div>
                <p className="mt-2 text-sm text-[var(--text-primary)]">
                  {findingPrimaryMessage(finding)}
                </p>
                <p className="mt-1 text-xs font-semibold text-[var(--text-secondary)]">{findingDisplayLabel(finding)}</p>
                {meta && (
                  <details className="mt-2 text-xs text-[var(--text-secondary)]">
                    <summary className="cursor-pointer font-semibold text-[var(--accent-primary)]">Details</summary>
                    <p className="mt-1 break-words">{meta}</p>
                  </details>
                )}
              </article>
            );
          })
        )}
      </div>
    </div>
  );
}

function AdminReviewActionPanel({
  response,
  onResponseChange,
  onAction,
  onBack,
  backLabel = 'Back to moderation',
  contextLabel,
  contextError,
  busy,
  message,
  error,
}) {
  const disabled = Boolean(busy);

  return (
    <div className="sticky top-20 z-30 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-4 shadow-xl">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="label-sm">Staff decision</p>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            {contextLabel || 'Moderation review context'}
          </p>
        </div>
        <Button size="sm" variant="secondary" onClick={onBack}>
          <ArrowLeft size={14} />
          <span>{backLabel}</span>
        </Button>
      </div>

      <label className="mt-4 block text-sm text-[var(--text-secondary)]">
        Admin response
        <textarea
          value={response}
          onChange={(event) => onResponseChange(event.target.value)}
          maxLength={4000}
          placeholder="Add a short response for the publisher..."
          className="focus-ring mt-2 min-h-[88px] w-full resize-y rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 text-sm text-[var(--text-primary)]"
        />
      </label>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex items-center gap-2 text-xs text-[var(--text-secondary)]">
          <AlertTriangle size={14} />
          <span>Approving clears moderation blockers. Requesting changes keeps publishing blocked.</span>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Button size="sm" variant="secondary" onClick={() => onAction('request_changes')} disabled={disabled}>
            <FileText size={14} />
            <span>{busy === 'request_changes' ? 'Requesting...' : 'Request changes'}</span>
          </Button>
          <Button size="sm" variant="secondary" onClick={() => onAction('reject')} disabled={disabled}>
            <AlertTriangle size={14} />
            <span>{busy === 'reject' ? 'Rejecting...' : 'Reject'}</span>
          </Button>
          <Button size="sm" onClick={() => onAction('approve')} disabled={disabled}>
            <Check size={14} />
            <span>{busy === 'approve' ? 'Approving...' : 'Approve'}</span>
          </Button>
        </div>
      </div>

      {message && (
        <p className="mt-3 rounded-xl bg-[color:var(--status-success-bg)] px-3 py-2 text-sm text-[color:var(--status-success-fg)]">
          {message}
        </p>
      )}
      {contextError && (
        <p className="mt-3 rounded-xl bg-[color:var(--status-warning-bg)] px-3 py-2 text-sm text-[color:var(--status-warning-fg)]">
          {contextError}
        </p>
      )}
      {error && (
        <p className="mt-3 rounded-xl bg-[color:var(--feedback-danger-bg)] px-3 py-2 text-sm text-[color:var(--feedback-danger-fg)]">
          {error}
        </p>
      )}
    </div>
  );
}

export function lessonIntelligenceProviderLabel(report) {
  if (report?.enabled === false) return 'Disabled';
  const provider = String(report?.provider || '').toLowerCase();
  if (provider === 'ollama' && report?.fallback_used) return 'Partial Ollama insight with heuristic fallback';
  if (provider === 'ollama') return 'Ollama insight completed';
  if (provider === 'heuristic' && report?.fallback_used) return 'Heuristic fallback shown';
  if (provider === 'heuristic') return 'Heuristic suggestion';
  if (report?.fallback_used) return 'Heuristic fallback shown';
  if (provider) return `${provider.charAt(0).toUpperCase()}${provider.slice(1)} analysis`;
  return 'No analysis yet';
}

function lessonIntelligenceEnhancementStatus(report) {
  return String(report?.enhancement_status || '').trim().toLowerCase();
}

const LESSON_INTELLIGENCE_ACTIVE_ENHANCEMENT_STATUSES = new Set([
  'pending',
  'running',
  'analyzing_chunks',
  'chunk_processing',
  'synthesizing',
  'final_synthesis',
  'final_aggregation',
]);
const LESSON_INTELLIGENCE_FAILED_ENHANCEMENT_STATUSES = new Set([
  'failed',
  'unavailable',
  'disabled',
  'stale',
  'superseded',
  'degraded',
]);

function lessonIntelligenceEnhancementPending(report) {
  return Boolean(
    report?.enhancement_pending
    || LESSON_INTELLIGENCE_ACTIVE_ENHANCEMENT_STATUSES.has(lessonIntelligenceEnhancementStatus(report)),
  );
}

function lessonIntelligenceOllamaFallbackFailed(report) {
  return Boolean(
    report?.fallback_used
    && String(report?.provider || '').toLowerCase() === 'heuristic'
    && String(report?.enhancement_provider || '').toLowerCase() === 'ollama'
    && LESSON_INTELLIGENCE_FAILED_ENHANCEMENT_STATUSES.has(lessonIntelligenceEnhancementStatus(report)),
  );
}

function lessonIntelligenceRetryOnCooldown(report) {
  if (!lessonIntelligenceOllamaFallbackFailed(report)) return false;
  const availableAt = Date.parse(report?.retry_available_at || report?.metadata?.progressive_enhancement?.retry_available_at || '');
  return Number.isFinite(availableAt) && Date.now() < availableAt;
}

function lessonIntelligenceUpToDate(report) {
  return Boolean(
    report?.id
    && !lessonIntelligenceIsStale(report)
    && !lessonIntelligenceEnhancementPending(report)
    && !lessonIntelligenceOllamaFallbackFailed(report)
    && String(report?.provider || '').toLowerCase() === 'ollama',
  );
}

function lessonIntelligenceEnhancementMeta(report) {
  return report?.metadata?.progressive_enhancement || {};
}

function lessonIntelligenceReportHasUsableResult(report) {
  return Boolean(report?.id && String(report?.status || '').toLowerCase() === 'done');
}

export function lessonIntelligenceEnhancementLabel(report) {
  const status = lessonIntelligenceEnhancementStatus(report);
  const provider = String(report?.enhancement_provider || '').toLowerCase();
  if (provider !== 'ollama') return '';
  const meta = lessonIntelligenceEnhancementMeta(report);
  const phase = String(meta.phase || '').toLowerCase();
  const chunkCount = Number(meta.chunk_count || report?.metadata?.chunk_count || 0);
  const completedChunks = Number(meta.completed_chunks || report?.metadata?.completed_chunks || 0);
  const failedChunks = Number(meta.failed_chunks || report?.metadata?.failed_chunks || 0);
  const degradedReason = String(meta.degraded_reason || '').toLowerCase();
  const chunkAnalysisTimedOut = LESSON_INTELLIGENCE_FAILED_ENHANCEMENT_STATUSES.has(status)
    && chunkCount > 0
    && completedChunks <= 0
    && degradedReason === 'chunk_timeout';
  const partialProgressTimedOut = LESSON_INTELLIGENCE_FAILED_ENHANCEMENT_STATUSES.has(status)
    && completedChunks > 0
    && degradedReason === 'ollama_no_progress_timeout';
  const finalAggregationTimedOut = LESSON_INTELLIGENCE_FAILED_ENHANCEMENT_STATUSES.has(status)
    && chunkCount > 0
    && completedChunks >= chunkCount
    && degradedReason === 'final_aggregation_timeout';
  const processedChunks = Math.min(chunkCount, completedChunks + failedChunks);
  if (LESSON_INTELLIGENCE_ACTIVE_ENHANCEMENT_STATUSES.has(status)) {
    if (phase === 'synthesizing' || phase === 'final_synthesis' || phase === 'final_aggregation') return 'Synthesizing final insight';
    const currentChunk = Number(meta.current_chunk_index || meta.current_chunk?.index || 0);
    const visibleProgress = Math.max(processedChunks, currentChunk);
    if (chunkCount > 1) return `Ollama analyzing ${visibleProgress}/${chunkCount} chunks`;
    return 'Ollama enhancement running';
  }
  const usableReport = lessonIntelligenceReportHasUsableResult(report);
  const reportProvider = String(report?.provider || '').toLowerCase();
  const partialWithFallback = Boolean(
    usableReport
    && reportProvider === 'ollama'
    && report?.fallback_used
    && (status === 'partial' || status === 'degraded' || failedChunks > 0 || completedChunks > 0),
  );
  const heuristicFallback = Boolean(
    usableReport
    && reportProvider === 'heuristic'
    && report?.fallback_used
    && LESSON_INTELLIGENCE_FAILED_ENHANCEMENT_STATUSES.has(status),
  );
  if (status === 'done') {
    if (failedChunks > 0 || report?.fallback_used) return 'Partial Ollama insight with heuristic fallback';
    return 'Ollama insight completed';
  }
  if (status === 'partial' || partialWithFallback) return 'Partial Ollama insight with heuristic fallback';
  if (heuristicFallback) return 'Heuristic fallback shown';
  if (LESSON_INTELLIGENCE_FAILED_ENHANCEMENT_STATUSES.has(status)) {
    if (usableReport) return 'Heuristic fallback shown';
    if (finalAggregationTimedOut) return 'Ollama enhancement failed during final summary';
    if (chunkAnalysisTimedOut) return 'Ollama enhancement failed during chunk analysis';
    if (partialProgressTimedOut) return 'Ollama enhancement timed out after partial progress';
    if (lessonIntelligenceOllamaFallbackFailed(report)) return 'Heuristic fallback shown';
    return 'Ollama enhancement failed';
  }
  return '';
}

const LESSON_INTELLIGENCE_SECTION_LABELS = {
  summary: 'Summary',
  clarity: 'Clarity',
  page_suggestions: 'Page suggestions',
  expanded_narration: 'Narration suggestions',
  tags: 'Tags',
};

function lessonIntelligenceSectionEntries(report) {
  const progressive = report?.metadata?.progressive_enhancement?.sections;
  const topLevel = report?.metadata?.sections;
  const sections = progressive && typeof progressive === 'object' ? progressive : topLevel;
  if (!sections || typeof sections !== 'object') return [];
  return Object.entries(LESSON_INTELLIGENCE_SECTION_LABELS)
    .map(([key, label]) => {
      const meta = sections[key] && typeof sections[key] === 'object' ? sections[key] : {};
      const status = String(meta.status || '').trim().toLowerCase();
      const provider = String(meta.provider || '').trim().toLowerCase();
      if (!status && !provider) return null;
      return { key, label, status, provider };
    })
    .filter(Boolean);
}

function lessonIntelligenceSectionText(entry) {
  if (!entry) return '';
  if (LESSON_INTELLIGENCE_ACTIVE_ENHANCEMENT_STATUSES.has(entry.status)) return `${entry.label} analyzing...`;
  if (entry.status === 'done' && entry.provider === 'ollama') return `${entry.label} enhanced`;
  if (entry.status === 'done') return `${entry.label} ready`;
  if (entry.status === 'failed') return `${entry.label} heuristic kept`;
  return `${entry.label} ${entry.status || 'ready'}`;
}

function LessonIntelligenceSectionStatusList({ report }) {
  const entries = lessonIntelligenceSectionEntries(report);
  if (!entries.length) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {entries.map((entry) => {
        const pending = LESSON_INTELLIGENCE_ACTIVE_ENHANCEMENT_STATUSES.has(entry.status);
        const enhanced = entry.status === 'done' && entry.provider === 'ollama';
        const failed = entry.status === 'failed';
        return (
          <span
            key={entry.key}
            className={`rounded-full px-3 py-1 text-xs font-semibold ${
              failed
                ? 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]'
                : pending
                  ? 'bg-[color:var(--status-info-bg)] text-[color:var(--status-info-fg)]'
                  : enhanced
                    ? 'bg-[color:var(--feedback-success-bg)] text-[color:var(--feedback-success-fg)]'
                    : 'bg-[color:var(--surface-muted)] text-[var(--text-secondary)]'
            }`}
          >
            {lessonIntelligenceSectionText(entry)}
          </span>
        );
      })}
    </div>
  );
}

function lessonIntelligenceLanguageLabel(report) {
  const language = String(report?.output_language || report?.metadata?.output_language || '').toLowerCase();
  const detected = String(report?.detected_language || report?.metadata?.detected_language || '').toLowerCase();
  if (language === 'tr') return 'Turkish analysis';
  if (language === 'en') return detected === 'unknown' ? 'English analysis' : 'English analysis';
  return 'Language uncertain';
}

function lessonIntelligenceInputWasCompacted(report) {
  return Boolean(report?.metadata?.input_truncated);
}

function lessonIntelligenceIsStale(report) {
  return Boolean(report?.is_stale);
}

function lessonIntelligenceItemText(item) {
  if (typeof item === 'string') return item;
  if (!item || typeof item !== 'object') return '';
  return textValue(item.message || item.advice || item.suggestion || item.reason || item.text || item.title);
}

function lessonIntelligenceItemMeta(item) {
  if (!item || typeof item !== 'object') return '';
  const page = item.page_number ? `Page ${item.page_number}` : '';
  const type = textValue(item.type).replace(/_/g, ' ');
  return [page, type].filter(Boolean).join(' - ');
}

function lessonIntelligenceItemKey(item, index = 0) {
  if (!item || typeof item !== 'object') return `item-${index}`;
  return [
    item.page_key || item.page_number || 'item',
    item.type || 'suggestion',
    index,
  ].map((value) => textValue(value).replace(/\s+/g, '-')).join(':');
}

function lessonIntelligenceDraftNarration(item) {
  if (!item || typeof item !== 'object') return '';
  return textValue(item.draft_narration || item.copy_text).trim();
}

export function lessonIntelligenceDraftLabel(item) {
  if (!item || typeof item !== 'object') return '';
  const provider = textValue(item.generated_by || item.provider).trim().toLowerCase();
  if (provider === 'heuristic' || provider === 'local_heuristic' || item.ai_generated === false) {
    return 'Heuristic suggestion';
  }
  return 'AI draft';
}

function getCleanSuggestionCopyText(item) {
  const draft = lessonIntelligenceDraftNarration(item);
  if (draft) return draft;
  if (typeof item === 'string') return item.trim();
  if (!item || typeof item !== 'object') return '';
  return textValue(item.copy_text || item.advice || item.suggestion || item.message || item.text || '').trim();
}

function lessonPublicSummary(report) {
  return textValue(report?.public_lesson_summary || report?.lesson_summary || report?.summary).trim();
}

function lessonImprovementSummary(report) {
  return textValue(report?.improvement_summary || report?.editorial_summary).trim();
}

function lessonIntelligenceCopyText(report) {
  if (!report) return '';
  const sections = [];
  const improvementSummary = lessonImprovementSummary(report);
  const publicSummary = lessonPublicSummary(report);
  if (improvementSummary) sections.push(`Improvement summary\n${improvementSummary}`);
  if (publicSummary) sections.push(`Lesson overview\n${publicSummary}`);
  const complexity = report.complexity || {};
  if (complexity.level) {
    sections.push(`Complexity\n${complexity.level} (${complexity.score || 0}/100)`);
  }
  const appendList = (title, items) => {
    if (!Array.isArray(items) || !items.length) return;
    const lines = items.map((item) => {
      const text = lessonIntelligenceItemText(item);
      return text;
    }).filter(Boolean);
    if (lines.length) sections.push(`${title}\n${lines.map((line) => `- ${line}`).join('\n')}`);
  };
  appendList('Clarity warnings', report.clarity_warnings);
  appendList('Page suggestions', report.page_suggestions);
  appendList('Expanded narration suggestions', report.expanded_narration_suggestions);
  return sections.join('\n\n');
}

function CollapsibleIntelligenceSection({ title, count = 0, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="rounded-xl border border-[var(--border-subtle)] bg-[color:var(--surface-muted)]/35">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="focus-ring flex w-full items-center justify-between gap-3 rounded-xl px-3 py-3 text-left"
      >
        <span className="min-w-0">
          <span className="block text-xs font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">{title}</span>
          <span className="mt-0.5 block text-xs text-[var(--text-secondary)]">{count} item{count === 1 ? '' : 's'}</span>
        </span>
        <ChevronDown
          size={16}
          className={`shrink-0 text-[var(--text-secondary)] transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>
      {open && <div className="space-y-2 px-3 pb-3">{children}</div>}
    </section>
  );
}

function LessonIntelligenceList({ title, items, emptyText, renderActions, renderItem }) {
  const rows = Array.isArray(items) ? items : [];
  return (
    <CollapsibleIntelligenceSection title={title} count={rows.length}>
      {rows.length === 0 ? (
        <p className="text-sm text-[var(--text-secondary)]">{emptyText}</p>
      ) : (
        rows.map((item, index) => {
          if (renderItem) {
            return renderItem(item, index);
          }
          const meta = lessonIntelligenceItemMeta(item);
          const text = lessonIntelligenceItemText(item);
          return (
            <article key={`${title}-${index}`} className="rounded-xl bg-[color:var(--surface-muted)] p-3">
              {meta && <p className="text-[0.68rem] font-semibold uppercase tracking-[0.1em] text-[var(--text-secondary)]">{meta}</p>}
              <p className="mt-1 text-sm text-[var(--text-primary)]">{text || 'Review this item.'}</p>
              {renderActions && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {renderActions(item, index)}
                </div>
              )}
            </article>
          );
        })
      )}
    </CollapsibleIntelligenceSection>
  );
}

function LessonIntelligencePanel({
  project,
  report,
  loading,
  error,
  actionBusy,
  copied,
  onAnalyze,
  onRefresh,
  onCopy,
  onCopySuggestion,
  onApplyNarrationSuggestion,
  copiedSuggestionKey,
  notice,
}) {
  if (!project) return null;

  const enabled = report?.enabled !== false;
  const status = String(report?.status || '').toLowerCase();
  const hasReport = Boolean(report?.id && status !== 'empty');
  const complexity = report?.complexity || {};
  const score = Number.isFinite(Number(complexity.score)) ? Number(complexity.score) : 0;
  const providerLabel = lessonIntelligenceProviderLabel(report);
  const enhancementLabel = lessonIntelligenceEnhancementLabel(report);
  const enhancementPending = lessonIntelligenceEnhancementPending(report);
  const enhancementFailed = LESSON_INTELLIGENCE_FAILED_ENHANCEMENT_STATUSES.has(lessonIntelligenceEnhancementStatus(report))
    && !lessonIntelligenceReportHasUsableResult(report);
  const enhancementFallback = /fallback/i.test(enhancementLabel);
  const copyDisabled = !hasReport || actionBusy || loading;
  const stale = lessonIntelligenceIsStale(report);
  const retryOllama = lessonIntelligenceOllamaFallbackFailed(report);
  const retryCooldown = lessonIntelligenceRetryOnCooldown(report);
  const upToDate = lessonIntelligenceUpToDate(report);
  const improvementSummary = lessonImprovementSummary(report);
  const publicSummary = lessonPublicSummary(report);
  const analyzeDisabled = !enabled || loading || Boolean(actionBusy) || enhancementPending || retryCooldown || upToDate;
  const analyzeLabel = actionBusy
    ? 'Analyzing...'
    : enhancementPending
      ? 'Enhancing...'
      : retryCooldown
        ? 'Retry available soon'
        : retryOllama
          ? 'Retry Ollama'
          : upToDate
            ? 'Up to date'
            : stale
              ? 'Re-analyze'
              : 'Analyze lesson';

  return (
    <div className="rounded-2xl token-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="label-sm">Lesson Intelligence</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className={`rounded-full px-3 py-1 text-xs font-semibold ${
              enabled
                ? 'bg-[color:var(--status-info-bg)] text-[color:var(--status-info-fg)]'
                : 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]'
            }`}>
              {providerLabel}
            </span>
            {hasReport && (
              <span className="rounded-full bg-[color:var(--surface-muted)] px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
                {lessonIntelligenceLanguageLabel(report)}
              </span>
            )}
            {hasReport && (
              <span className="rounded-full bg-[color:var(--surface-muted)] px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
                Report #{report.id}
              </span>
            )}
            {stale && (
              <span className="rounded-full bg-[color:var(--status-warning-bg)] px-3 py-1 text-xs font-semibold text-[color:var(--status-warning-fg)]">
                Stale
              </span>
            )}
            {enhancementLabel && (
              <span className={`rounded-full px-3 py-1 text-xs font-semibold ${
                enhancementFailed
                  ? 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]'
                  : enhancementPending
                    ? 'bg-[color:var(--status-info-bg)] text-[color:var(--status-info-fg)]'
                    : enhancementFallback
                      ? 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]'
                      : 'bg-[color:var(--feedback-success-bg)] text-[color:var(--feedback-success-fg)]'
              }`}>
                {enhancementLabel}
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={onRefresh} disabled={loading || Boolean(actionBusy)}>
            <RefreshCcw size={14} />
            <span>Refresh</span>
          </Button>
          <Button size="sm" onClick={onAnalyze} disabled={analyzeDisabled}>
            <Sparkles size={14} className={enhancementPending ? 'animate-spin' : ''} />
            <span>{analyzeLabel}</span>
          </Button>
        </div>
      </div>

      <p className="mt-3 text-sm text-[var(--text-secondary)]">
        Suggestions are advisory. They do not change your lesson until you edit it.
      </p>

      {!enabled && (
        <p className="mt-3 rounded-xl bg-[color:var(--status-warning-bg)] px-3 py-2 text-sm text-[color:var(--status-warning-fg)]">
          {report?.message || 'Lesson Intelligence is disabled.'}
        </p>
      )}
      {loading && (
        <p className="mt-3 text-sm text-[var(--text-secondary)]">Loading latest analysis...</p>
      )}
      {error && (
        <p className="mt-3 rounded-xl bg-[color:var(--feedback-danger-bg)] px-3 py-2 text-sm text-[color:var(--feedback-danger-fg)]">
          {error}
        </p>
      )}
      {notice && (
        <p className="mt-3 rounded-xl bg-[color:var(--feedback-success-bg)] px-3 py-2 text-sm text-[color:var(--feedback-success-fg)]">
          {notice}
        </p>
      )}
      {enhancementFailed && (
        <p className="mt-3 rounded-xl bg-[color:var(--status-warning-bg)] px-3 py-2 text-sm text-[color:var(--status-warning-fg)]">
          {retryOllama ? 'Heuristic fallback shown. Retry Ollama when available. ' : ''}
          {report?.enhancement_last_failure_reason || report?.enhancement_error_safe || 'Ollama enhancement failed; heuristic analysis kept.'}
        </p>
      )}
      {hasReport && <LessonIntelligenceSectionStatusList report={report} />}

      {!loading && enabled && !hasReport && !error && (
        <div className="mt-4 rounded-xl bg-[color:var(--surface-muted)] p-4">
          <p className="text-sm font-semibold text-[var(--text-primary)]">Analysis preparing...</p>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            A quick summary appears here when transcript text is available.
          </p>
        </div>
      )}

      {hasReport && (
        <div className="mt-4 space-y-4">
          <div className="rounded-xl bg-[color:var(--surface-muted)] p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">Studio guidance</p>
                <p className="mt-2 text-sm leading-6 text-[var(--text-primary)]">
                  {improvementSummary || 'Review clarity, examples, narration depth, and lesson flow before publishing.'}
                </p>
                {publicSummary && (
                  <div className="mt-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-container-high)] p-3">
                    <p className="text-[0.68rem] font-semibold uppercase tracking-[0.1em] text-[var(--text-secondary)]">Lesson overview</p>
                    <p className="mt-1 text-xs leading-relaxed text-[var(--text-secondary)]">{publicSummary}</p>
                  </div>
                )}
                {report.short_description && report.short_description !== publicSummary && (
                  <p className="mt-2 text-xs text-[var(--text-secondary)]">{report.short_description}</p>
                )}
              </div>
              <div className="grid h-16 w-16 shrink-0 place-items-center rounded-full border-4 border-[var(--accent-primary)] bg-[var(--surface-elevated)]">
                <div className="text-center">
                  <p className="text-lg font-bold text-[var(--text-primary)]">{score}</p>
                  <p className="text-[0.62rem] uppercase text-[var(--text-secondary)]">score</p>
                </div>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="rounded-full bg-[var(--surface-container-highest)] px-3 py-1 text-xs font-semibold text-[var(--text-primary)]">
                {complexity.display_label || complexity.level || 'unknown'}
              </span>
              {(Array.isArray(complexity.reasons) ? complexity.reasons : []).slice(0, 3).map((reason, index) => (
                <span key={`complexity-reason-${index}`} className="rounded-full bg-[color:var(--surface-container-high)] px-3 py-1 text-xs text-[var(--text-secondary)]">
                  {reason}
                </span>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" onClick={onCopy} disabled={copyDisabled}>
              {copied ? <Check size={14} /> : <Copy size={14} />}
              <span>{copied ? 'Copied' : 'Copy suggestions'}</span>
            </Button>
          </div>

          {(stale || lessonIntelligenceInputWasCompacted(report) || (Array.isArray(report.limitations) && report.limitations.length > 0)) && (
            <div className="rounded-xl bg-[color:var(--surface-muted)] p-3 text-sm text-[var(--text-secondary)]">
              {stale && (
                <p className="font-semibold text-[color:var(--status-warning-fg)]">This analysis is out of date for the current transcript.</p>
              )}
              {lessonIntelligenceInputWasCompacted(report) && (
                <p>Large lesson text was summarized before analysis.</p>
              )}
              {Array.isArray(report.limitations) && report.limitations.slice(0, 3).map((item, index) => (
                <p key={`lesson-intelligence-limitation-${index}`} className="mt-1">{textValue(item)}</p>
              ))}
            </div>
          )}

          <LessonIntelligenceList
            title="Clarity warnings"
            items={report.clarity_warnings}
            emptyText="No clarity warnings in the latest report."
          />
          <LessonIntelligenceList
            title="Slide/page suggestions"
            items={report.page_suggestions}
            emptyText="No slide or page suggestions in the latest report."
          />
          <LessonIntelligenceList
            title="Expanded narration suggestions"
            items={report.expanded_narration_suggestions}
            emptyText="No expanded narration suggestions in the latest report."
            renderItem={(item, index) => {
              const key = lessonIntelligenceItemKey(item, index);
              const pageLabel = item?.page_number ? `Page ${item.page_number}` : 'Page';
              const title = textValue(item?.title || 'Expand narration');
              const advice = textValue(item?.advice || item?.suggestion || lessonIntelligenceItemText(item));
              const draftNarration = lessonIntelligenceDraftNarration(item);
              const draftLabel = lessonIntelligenceDraftLabel(item);
              const copiedThis = copiedSuggestionKey === key;
              return (
                <article key={key} className="space-y-3 rounded-xl bg-[color:var(--surface-muted)] p-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <span className="inline-flex rounded-full bg-[var(--surface-container-highest)] px-2.5 py-1 text-[0.68rem] font-semibold uppercase tracking-[0.1em] text-[var(--text-primary)]">
                        {pageLabel}
                      </span>
                      <p className="mt-2 text-sm font-semibold text-[var(--text-primary)]">{title}</p>
                    </div>
                    {draftLabel && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-[color:rgba(208,188,255,0.16)] px-2.5 py-1 text-xs font-semibold text-[var(--accent-primary)]">
                        {draftLabel === 'AI draft' ? <Sparkles size={12} /> : <FileText size={12} />}
                        <span>{draftLabel}</span>
                      </span>
                    )}
                  </div>
                  {advice && (
                    <p className="text-sm leading-6 text-[var(--text-secondary)]">{advice}</p>
                  )}
                  {draftNarration ? (
                    <div className="rounded-xl border border-[color:rgba(208,188,255,0.32)] bg-[color:rgba(208,188,255,0.08)] p-3">
                      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.1em] text-[var(--accent-primary)]">{draftLabel || 'Draft narration'}</p>
                      <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-[var(--text-primary)]">{draftNarration}</p>
                    </div>
                  ) : (
                    <p className="rounded-xl bg-[color:var(--status-warning-bg)] px-3 py-2 text-sm text-[color:var(--status-warning-fg)]">
                      This suggestion does not include draft narration to apply.
                    </p>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="secondary" onClick={() => onCopySuggestion?.(item, index)} disabled={Boolean(actionBusy) || !getCleanSuggestionCopyText(item)}>
                      {copiedThis ? <Check size={14} /> : <Copy size={14} />}
                      <span>{copiedThis ? 'Copied' : 'Copy'}</span>
                    </Button>
                    <Button size="sm" onClick={() => onApplyNarrationSuggestion?.(item)} disabled={Boolean(actionBusy) || !draftNarration}>
                      <Sparkles size={14} />
                      <span>Apply to page draft</span>
                    </Button>
                  </div>
                </article>
              );
            }}
          />

          {(Array.isArray(report.suggested_tags) && report.suggested_tags.length > 0) && (
            <CollapsibleIntelligenceSection title="Suggested tags" count={report.suggested_tags.length}>
              <div className="flex flex-wrap gap-2">
                {report.suggested_tags.map((tag, index) => {
                  const tagLabel = textValue(tag);
                  return (
                    <span key={`${tagLabel}-${index}`} className="rounded-full bg-[var(--surface-container-highest)] px-3 py-1 text-xs font-semibold text-[var(--text-primary)]">
                      {tagLabel}
                    </span>
                  );
                })}
              </div>
            </CollapsibleIntelligenceSection>
          )}
        </div>
      )}
    </div>
  );
}

export default function Studio({ user, searchQuery = '', onLoginRequest }) {
  const navigate = useNavigate();
  const { capabilities } = useCapabilities();
  const avatarFeatureEnabled = featureEnabled(capabilities, 'avatar');
  const intelligenceFeatureEnabled = featureEnabled(capabilities, 'intelligence');
  const visualModerationEnabled = featureEnabled(capabilities, 'visual_moderation');
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedMode = String(searchParams.get('mode') || '').trim().toLowerCase();
  const requestedReviewParam = String(searchParams.get('review') || '').trim();
  const readOnlyReviewRequested = (
    requestedMode === 'review'
    || searchParams.has('review')
    || searchParams.has('report')
  ) && isStaffOrAdmin(user);
  const requestedReviewId = requestedReviewParam && (requestedMode === 'review' || requestedReviewParam !== '1')
    ? positiveIdParam(searchParams, 'review')
    : null;
  const requestedReportId = positiveIdParam(searchParams, 'report');
  const requestedLessonId = positiveIdParam(searchParams, 'lesson');
  const requestedStudioView = searchParams.get('view');
  const requestedReviewReturnTo = safeInternalReturnTo(searchParams.get('returnTo'), '/moderation');
  const requestedSourceItem = textValue(searchParams.get('sourceItem'));
  const requestedSource = textValue(searchParams.get('source'));
  const hasDirectStudioLocation = searchParams.has('view')
    || searchParams.has('lesson')
    || searchParams.has('review')
    || searchParams.has('mode')
    || searchParams.has('report')
    || searchParams.has('returnTo');
  const studioPositionStorageKey = useMemo(() => studioSessionKey(user), [user]);
  const storedStudioPosition = useMemo(
    () => (hasDirectStudioLocation ? {} : readStudioSession(studioPositionStorageKey)),
    [hasDirectStudioLocation, studioPositionStorageKey],
  );
  const storedSelectedLessonId = Number(storedStudioPosition.selectedLessonId || 0) || null;
  const visibleEditorPanels = useMemo(
    () => EDITOR_PANELS
      .filter((panel) => panel !== 'intelligence' || intelligenceFeatureEnabled)
      .filter((panel) => !readOnlyReviewRequested || ['transcript', 'slides', 'moderation', 'intelligence'].includes(panel)),
    [intelligenceFeatureEnabled, readOnlyReviewRequested],
  );
  const previewVideoRef = useRef(null);
  const previewSectionRef = useRef(null);
  const transcriptEditorRef = useRef(null);
  const ttsSettingsRef = useRef(null);
  const selectedLessonIdRef = useRef(null);
  const projectListCacheRef = useRef(new Map());
  const projectDetailCacheRef = useRef(new Map());
  const lessonNotesHydratedProjectRef = useRef(null);
  const studioLocalDraftScope = useMemo(() => localDraftScope(user), [user]);

  const [projects, setProjects] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [loadingMoreProjects, setLoadingMoreProjects] = useState(false);
  const [projectsError, setProjectsError] = useState('');
  const [projectPageMeta, setProjectPageMeta] = useState({
    totalCount: null,
    limit: STUDIO_PROJECT_PAGE_SIZE,
    offset: 0,
    nextOffset: null,
    hasNext: false,
  });
  usePageLoading(loadingProjects && projects.length === 0, 'studio-projects');
  const [loadingTranscript, setLoadingTranscript] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [selectedLessonId, setSelectedLessonId] = useState(
    () => requestedLessonId || storedSelectedLessonId,
  );
  const [activeTab, setActiveTab] = useState(
    () => (LESSON_TABS.includes(storedStudioPosition.activeTab) ? storedStudioPosition.activeTab : 'overview'),
  );
  const [activeEditorPanel, setActiveEditorPanel] = useState(
    () => (
      visibleEditorPanels.includes(storedStudioPosition.activeEditorPanel)
        ? storedStudioPosition.activeEditorPanel
        : 'transcript'
    ),
  );
  const [transcriptPages, setTranscriptPages] = useState([]);
  const [selectedLessonDraftMetadata, setSelectedLessonDraftMetadata] = useState({});
  const [selectedPageKey, setSelectedPageKey] = useState(() => textValue(storedStudioPosition.selectedPageKey));
  const [selectedPageIndex, setSelectedPageIndex] = useState(() => (
    Number.isFinite(Number(storedStudioPosition.selectedPageIndex))
      ? Number(storedStudioPosition.selectedPageIndex)
      : 0
  ));
  const [sceneDraftStatus, setSceneDraftStatus] = useState({});
  const [activeRerenderStatus, setActiveRerenderStatus] = useState(null);
  const [expandedSlideKeys, setExpandedSlideKeys] = useState({});
  const [previewLesson, setPreviewLesson] = useState(null);
  const [previewSubtitleTracks, setPreviewSubtitleTracks] = useState([]);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [generatingSubtitleTrack, setGeneratingSubtitleTrack] = useState(false);
  const [subtitleGenerationMessage, setSubtitleGenerationMessage] = useState('');
  const [pendingSubtitleGeneration, setPendingSubtitleGeneration] = useState(null);
  const [previewRequestableSubtitleLanguages, setPreviewRequestableSubtitleLanguages] = useState([]);
  const [previewRequestLanguageCode, setPreviewRequestLanguageCode] = useState('en');
  const [moderationByProject, setModerationByProject] = useState({});
  const [loadingModeration, setLoadingModeration] = useState(false);
  const [moderationActionBusy, setModerationActionBusy] = useState('');
  const [moderationError, setModerationError] = useState('');
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false);
  const [reviewMessage, setReviewMessage] = useState('');
  const [adminReviewResponse, setAdminReviewResponse] = useState('');
  const [adminReviewActionBusy, setAdminReviewActionBusy] = useState('');
  const [adminReviewActionMessage, setAdminReviewActionMessage] = useState('');
  const [adminReviewActionError, setAdminReviewActionError] = useState('');
  const [adminReviewContext, setAdminReviewContext] = useState(null);
  const [adminReviewContextError, setAdminReviewContextError] = useState('');
  const [lessonIntelligenceByProject, setLessonIntelligenceByProject] = useState({});
  const [loadingLessonIntelligence, setLoadingLessonIntelligence] = useState(false);
  const [lessonIntelligenceActionBusy, setLessonIntelligenceActionBusy] = useState('');
  const [lessonIntelligenceError, setLessonIntelligenceError] = useState('');
  const [lessonIntelligenceCopied, setLessonIntelligenceCopied] = useState(false);
  const [lessonIntelligenceCopiedItemKey, setLessonIntelligenceCopiedItemKey] = useState('');
  const [lessonIntelligenceNotice, setLessonIntelligenceNotice] = useState('');
  const lessonIntelligenceAutoRunKeysRef = useRef(new Set());
  const [sceneActionBusy, setSceneActionBusy] = useState('');
  const [sceneActionMessage, setSceneActionMessage] = useState('');
  const [sceneActionError, setSceneActionError] = useState('');
  const [highlightPreviewBusy, setHighlightPreviewBusy] = useState(false);
  const [highlightPreviewMessage, setHighlightPreviewMessage] = useState('');
  const [highlightPreviewImageUrl, setHighlightPreviewImageUrl] = useState('');
  const highlightPreviewObjectUrlRef = useRef('');
  const [selectedSceneBackgroundImageUrl, setSelectedSceneBackgroundImageUrl] = useState('');
  const selectedSceneBackgroundObjectUrlRef = useRef('');
  const [globalEditorActionBusy, setGlobalEditorActionBusy] = useState('');
  const [globalEditorMessage, setGlobalEditorMessage] = useState('');
  const [globalEditorError, setGlobalEditorError] = useState('');
  const [partialRenderPreview, setPartialRenderPreview] = useState(null);
  const [partialRenderPreviewBusy, setPartialRenderPreviewBusy] = useState(false);
  const [partialRenderPreviewError, setPartialRenderPreviewError] = useState('');
  const [transcriptDirty, setTranscriptDirty] = useState(false);
  const [ttsDirty, setTtsDirty] = useState(false);
  const [editorResetNonce, setEditorResetNonce] = useState(0);
  const [avatarVisibilitySaving, setAvatarVisibilitySaving] = useState(false);
  const [avatarRerendering, setAvatarRerendering] = useState(false);
  const [avatarRerenderMessage, setAvatarRerenderMessage] = useState('');

  useEffect(() => {
    if (!intelligenceFeatureEnabled && activeEditorPanel === 'intelligence') {
      setActiveEditorPanel('transcript');
    }
  }, [activeEditorPanel, intelligenceFeatureEnabled]);

  useEffect(() => {
    if (!visibleEditorPanels.includes(activeEditorPanel)) {
      setActiveEditorPanel(visibleEditorPanels[0] || 'transcript');
    }
  }, [activeEditorPanel, visibleEditorPanels]);

  const activeProjectSearchQuery = useMemo(() => String(searchQuery || '').trim(), [searchQuery]);
  const filteredProjects = projects;

  const [sourceFile, setSourceFile] = useState(null);
  const [coverFile, setCoverFile] = useState(null);
  const [coverPreviewUrl, setCoverPreviewUrl] = useState('');
  const [editorTitle, setEditorTitle] = useState('');
  const [editorCategory, setEditorCategory] = useState('');
  const [editorCanvas, setEditorCanvas] = useState('');
  const [pauseSec, setPauseSec] = useState('0.2');
  const [whiteboardModeAll, setWhiteboardModeAll] = useState(false);
  const [avatarEnabled, setAvatarEnabled] = useState(false);
  const [editorSavedAtLabel, setEditorSavedAtLabel] = useState('');

  const [lessonNotes, setLessonNotes] = useState('');
  const [lessonNotesSavedValue, setLessonNotesSavedValue] = useState('');
  const [lessonNotesSavedAt, setLessonNotesSavedAt] = useState('');
  const [lessonNotesLocalDraft, setLessonNotesLocalDraft] = useState(null);

  const isReviewMode = readOnlyReviewRequested;
  const storedStudioView = textValue(storedStudioPosition.studioView);
  const effectiveStudioView = requestedStudioView || storedStudioView;
  const studioView = isReviewMode
    ? (effectiveStudioView === 'editor' ? 'editor' : 'lessons')
    : effectiveStudioView === 'editor'
      ? 'editor'
      : effectiveStudioView === 'playlists'
        ? 'playlists'
        : 'lessons';
  const isStudioUser = canAccessStudio(user);

  useEffect(() => {
    if (hasDirectStudioLocation || loadingProjects || !projects.length || !storedStudioPosition.scrollY) return undefined;
    const restoreId = window.requestAnimationFrame(() => {
      window.scrollTo({ top: Number(storedStudioPosition.scrollY) || 0, behavior: 'auto' });
    });
    return () => window.cancelAnimationFrame(restoreId);
  }, [hasDirectStudioLocation, loadingProjects, projects.length, storedStudioPosition.scrollY]);

  useEffect(() => {
    if (!studioPositionStorageKey || !isStudioUser || isReviewMode) return;
    writeStudioSession(studioPositionStorageKey, {
      studioView,
      selectedLessonId: selectedLessonId || null,
      activeTab,
      activeEditorPanel,
      selectedPageKey,
      selectedPageIndex,
      scrollY: typeof window !== 'undefined' ? window.scrollY : 0,
    });
  }, [
    activeEditorPanel,
    activeTab,
    isStudioUser,
    isReviewMode,
    selectedLessonId,
    selectedPageIndex,
    selectedPageKey,
    studioPositionStorageKey,
    studioView,
  ]);

  useEffect(() => {
    if (!studioPositionStorageKey || !isStudioUser || isReviewMode) return undefined;
    const persistScrollPosition = () => {
      writeStudioSession(studioPositionStorageKey, {
        studioView,
        selectedLessonId: selectedLessonId || null,
        activeTab,
        activeEditorPanel,
        selectedPageKey,
        selectedPageIndex,
        scrollY: window.scrollY,
      });
    };
    window.addEventListener('pagehide', persistScrollPosition);
    window.addEventListener('beforeunload', persistScrollPosition);
    return () => {
      persistScrollPosition();
      window.removeEventListener('pagehide', persistScrollPosition);
      window.removeEventListener('beforeunload', persistScrollPosition);
    };
  }, [
    activeEditorPanel,
    activeTab,
    isStudioUser,
    isReviewMode,
    selectedLessonId,
    selectedPageIndex,
    selectedPageKey,
    studioPositionStorageKey,
    studioView,
  ]);

  const refreshProjects = useCallback(async ({
    showLoading = true,
    preserveOnError = false,
    append = false,
    offset = 0,
  } = {}) => {
    if (!user || !isStudioUser) return;

    const request = { limit: STUDIO_PROJECT_PAGE_SIZE, offset, q: activeProjectSearchQuery };
    if (append) {
      setLoadingMoreProjects(true);
    } else if (showLoading) {
      setLoadingProjects(true);
    }
    setProjectsError('');
    try {
      const cachedPayload = readProjectListCache(projectListCacheRef.current, request);
      const payload = cachedPayload || await fetchProjects(request);
      if (!cachedPayload) {
        writeProjectListCache(projectListCacheRef.current, request, payload);
      }
      let nextProjects = normalizeProjectList(payload);
      const projectIdToPreserve = requestedLessonId || storedSelectedLessonId;
      if (
        projectIdToPreserve
        && !activeProjectSearchQuery
        && offset === 0
        && !nextProjects.some((project) => project.id === projectIdToPreserve)
      ) {
        const reviewProject = readProjectDetailCache(projectDetailCacheRef.current, projectIdToPreserve)
          || await fetchProject(projectIdToPreserve);
        writeProjectDetailCache(projectDetailCacheRef.current, reviewProject);
        nextProjects = [reviewProject, ...nextProjects.filter((project) => project.id !== reviewProject.id)];
      }
      cacheProjectWindow(projectDetailCacheRef.current, nextProjects, selectedLessonIdRef.current || projectIdToPreserve);
      setProjectPageMeta(projectPaginationMeta(payload, STUDIO_PROJECT_PAGE_SIZE));
      setProjects((previous) => mergeProjectsPreservingLocalModeration(previous, nextProjects, { append }));
      return nextProjects;
    } catch (projectError) {
      if (isReviewMode && requestedLessonId) {
        try {
          const reviewProject = await fetchProject(requestedLessonId);
          setProjects((previous) => mergeProjectsPreservingLocalModeration(previous, [reviewProject], { append }));
          return [reviewProject];
        } catch {
          // Fall through to the standard error handling below.
        }
      }
      if (!preserveOnError) {
        setProjects([]);
      }
      setProjectsError(projectError.message || 'Could not load lessons.');
      return null;
    } finally {
      if (append) {
        setLoadingMoreProjects(false);
      } else if (showLoading) {
        setLoadingProjects(false);
      }
    }
  }, [activeProjectSearchQuery, isReviewMode, isStudioUser, requestedLessonId, storedSelectedLessonId, user]);

  useEffect(() => {
    if (!user || !isStudioUser) return;

    fetchCategories()
      .then((data) => setCategories(Array.isArray(data) ? data : []))
      .catch(() => setCategories([]));

    refreshProjects();
  }, [isStudioUser, refreshProjects, user]);

  const loadMoreProjects = useCallback(() => {
    if (loadingProjects || loadingMoreProjects || !projectPageMeta.hasNext || projectPageMeta.nextOffset === null) {
      return null;
    }
    return refreshProjects({
      showLoading: false,
      preserveOnError: true,
      append: true,
      offset: projectPageMeta.nextOffset,
    });
  }, [loadingMoreProjects, loadingProjects, projectPageMeta.hasNext, projectPageMeta.nextOffset, refreshProjects]);

  const handleMyLessonsScroll = useCallback((event) => {
    const target = event.currentTarget;
    if (!target) return;
    const remaining = target.scrollHeight - target.scrollTop - target.clientHeight;
    if (remaining < 180) {
      loadMoreProjects();
    }
  }, [loadMoreProjects]);

  useEffect(() => {
    if (!projects.length) {
      setSelectedLessonId(null);
      return;
    }

    if (requestedLessonId && projects.some((project) => project.id === requestedLessonId)) {
      setSelectedLessonId(requestedLessonId);
      return;
    }

    setSelectedLessonId((previous) => {
      if (previous && projects.some((project) => project.id === previous)) {
        return previous;
      }
      return projects[0].id;
    });
  }, [projects, requestedLessonId]);

  const selectedLesson = useMemo(() => {
    if (!projects.length) return null;
    if (selectedLessonId && projects.some((project) => project.id === selectedLessonId)) {
      return projects.find((project) => project.id === selectedLessonId) || projects[0];
    }
    return projects[0];
  }, [projects, selectedLessonId]);

  useEffect(() => {
    if (!selectedLesson?.id || Object.prototype.hasOwnProperty.call(selectedLesson, 'latest_render_analysis')) {
      return undefined;
    }

    let active = true;
    fetchProject(selectedLesson.id)
      .then((project) => {
        if (!active) return;
        writeProjectDetailCache(projectDetailCacheRef.current, project);
        setProjects((previous) => mergeProjectsPreservingLocalModeration(previous, [project], { append: true }));
      })
      .catch(() => {});

    return () => {
      active = false;
    };
  }, [selectedLesson?.id, selectedLesson?.latest_render_analysis]);

  const readOnlyReview = Boolean(isReviewMode && selectedLesson);

  useEffect(() => {
    selectedLessonIdRef.current = selectedLesson?.id || null;
  }, [selectedLesson?.id]);

  useEffect(() => {
    setPartialRenderPreview(null);
    setPartialRenderPreviewError('');
    setPartialRenderPreviewBusy(false);
  }, [selectedLesson?.id]);

  useEffect(() => {
    setPartialRenderPreview(null);
    setPartialRenderPreviewError('');
  }, [avatarEnabled, transcriptPages]);

  useEffect(() => {
    setAdminReviewResponse('');
    setAdminReviewActionMessage('');
    setAdminReviewActionError('');
    setAdminReviewActionBusy('');
    setAdminReviewContext(null);
    setAdminReviewContextError('');
  }, [requestedReviewId, selectedLesson?.id]);

  const selectedModeration = selectedLesson?.id ? moderationByProject[selectedLesson.id] || null : null;
  const selectedLessonIntelligence = selectedLesson?.id ? lessonIntelligenceByProject[selectedLesson.id] || null : null;

  useEffect(() => {
    if (!readOnlyReview || !requestedReviewId) return undefined;
    let active = true;
    setAdminReviewContextError('');
    getModerationReviewRequest(requestedReviewId)
      .then((detail) => {
        if (!active) return;
        setAdminReviewContext(detail);
        const suggestedResponse = textValue(
          detail?.admin_response
          || detail?.publisher_message
          || detail?.project_moderation?.admin_review?.publisher_message,
        ).trim();
        if (suggestedResponse) {
          setAdminReviewResponse((current) => (current.trim() ? current : suggestedResponse));
        }
      })
      .catch((error) => {
        if (!active) return;
        setAdminReviewContextError(error.message || 'Could not load the moderation review context.');
      });
    return () => {
      active = false;
    };
  }, [readOnlyReview, requestedReviewId]);

  useEffect(() => {
    if (!readOnlyReview || adminReviewResponse.trim()) return;
    const suggested = publisherMessageForModeration(selectedModeration);
    if (suggested) setAdminReviewResponse(suggested);
  }, [adminReviewResponse, readOnlyReview, selectedModeration]);

  const selectedModerationFindings = useMemo(
    () => mergeModerationIssues(selectedModeration?.findings, selectedModeration?.visual_issues),
    [selectedModeration],
  );
  const selectedProjectModerationFindings = useMemo(
    () => getStudioVisualIssues(selectedLesson),
    [selectedLesson],
  );
  const selectedDraftModeration = plainObject(selectedLessonDraftMetadata?.moderation) || {};
  const selectedDraftModerationFindings = useMemo(
    () => mergeModerationIssues(selectedDraftModeration?.findings, selectedDraftModeration?.visual_issues),
    [selectedDraftModeration],
  );
  const selectedDraftModerationStatus = normalizedStatus(
    selectedLessonDraftMetadata?.moderation_status
      || selectedDraftModeration?.moderation_status
      || selectedDraftModeration?.final_decision,
  );
  const selectedDraftManualApprovalCurrent = currentManualApprovalCoversDraft(selectedLesson, selectedLessonDraftMetadata);
  const selectedDraftApproved = !selectedDraftManualApprovalCurrent
    && ['approved', 'admin_approved', 'allow'].includes(selectedDraftModerationStatus);
  const selectedDraftBlocked = !selectedDraftManualApprovalCurrent
    && ['revision_required', 'needs_admin_review', 'admin_rejected', 'failed', 'block', 'blocked', 'rejected'].includes(selectedDraftModerationStatus);
  const draftRerenderInProgress = Boolean(
    selectedLessonDraftMetadata?.dirty
      && ['pending', 'running', 'processing'].includes(normalizedStatus(activeRerenderStatus)),
  );
  const selectedLessonHasDraft = Boolean(selectedLessonDraftMetadata?.dirty);
  const selectedDraftRenderRequired = Boolean(
    selectedLessonDraftMetadata?.render_required
      || selectedLessonDraftMetadata?.background_dirty
      || selectedLessonDraftMetadata?.transcript_dirty
      || selectedLessonDraftMetadata?.tts_dirty
      || selectedLessonDraftMetadata?.source_dirty
  );
  const selectedDraftStatusMessage = selectedDraftBlocked
    ? 'Draft blocked by moderation. Edit the highlighted content or discard draft. Public lesson was not changed.'
    : draftRerenderInProgress
      ? 'Draft rerender in progress.'
      : selectedLessonHasDraft && !selectedDraftRenderRequired
        ? (selectedLessonDraftMetadata?.cover_dirty
          ? (selectedDraftApproved
            ? 'Draft cover passed visual moderation. Save changes to make it public.'
            : 'Cover is pending visual review before it becomes public.')
          : 'Draft changes saved. Video rerender is not required.')
      : 'Draft changes saved. Public version is unchanged until Save & Rerender succeeds.';
  const moderationFindingsForStudio = useMemo(
    () => (selectedDraftBlocked
      ? [...selectedProjectModerationFindings, ...selectedModerationFindings, ...selectedDraftModerationFindings]
      : [...selectedProjectModerationFindings, ...selectedModerationFindings]),
    [selectedDraftBlocked, selectedDraftModerationFindings, selectedModerationFindings, selectedProjectModerationFindings],
  );
  const {
    pageWarnings: moderationPageWarnings,
    slideWarnings: moderationSlideWarnings,
    backgroundWarnings: moderationBackgroundWarnings,
    assetWarnings: moderationAssetWarnings,
    unidentifiedWarnings: moderationUnidentifiedWarnings,
  } = useMemo(
    () => buildModerationWarningMaps(moderationFindingsForStudio, transcriptPages),
    [moderationFindingsForStudio, transcriptPages],
  );
  const draftCoverUrl = textValue(selectedLesson?.draft_cover_url || selectedLesson?.draft_thumbnail_url);
  const hasDraftCover = Boolean(draftCoverUrl);
  const draftCoverRemoved = Boolean(selectedLessonDraftMetadata?.cover_removed && !hasDraftCover);
  const hasSelectedLessonCover = Boolean(
    draftCoverUrl || selectedLesson?.cover_url || selectedLesson?.thumbnail_url || draftCoverRemoved,
  );
  const selectedLessonCoverBackgroundUrl = safeCssBackgroundUrl(
    draftCoverRemoved ? '' : draftCoverUrl || selectedLesson?.cover_url || selectedLesson?.thumbnail_url,
  );
  const selectedVisualMarker = projectVisualStaleMarker(selectedLesson, selectedModeration);
  const coverVisualNeedsRecheck = visualModerationEnabled
    && visualMarkerTargetsCover(selectedVisualMarker)
    && moderationMarkerIsStale(selectedVisualMarker);
  const coverModerationWarningFlagged = moderationWarningIsFlagged(moderationAssetWarnings.cover);
  const coverModerationWarningPending = moderationWarningIsPending(moderationAssetWarnings.cover);
  const lessonNotesDirty = Boolean(selectedLesson?.id && lessonNotes !== lessonNotesSavedValue);
  const selectedLessonDirtyScope = useMemo(() => {
    if (!selectedLesson?.id) {
      return {
        hasChanges: false,
        requiresRerender: false,
        canSaveChanges: false,
        canDiscardChanges: false,
        canSaveRerender: false,
        moderationMessage: '',
        saveDisabledReason: 'No lesson selected.',
        discardDisabledReason: 'No lesson selected.',
        rerenderDisabledReason: 'No lesson selected.',
      };
    }

    const savedDraftDirty = Boolean(selectedLessonHasDraft);
    const savedRenderDraftDirty = Boolean(savedDraftDirty && selectedDraftRenderRequired);
    const savedNonRenderDraftDirty = Boolean(savedDraftDirty && !selectedDraftRenderRequired);
    const requiresRerender = Boolean(transcriptDirty || ttsDirty || savedRenderDraftDirty);
    const hasChanges = Boolean(requiresRerender || savedNonRenderDraftDirty || lessonNotesDirty);
    const moderationStatus = projectModerationStatus(selectedLesson, selectedModeration);
    const visualModerationMessage = visualModerationRerenderMessage({
      issues: [
        ...selectedProjectModerationFindings,
        ...selectedModerationFindings,
        ...selectedDraftModerationFindings,
      ],
      moderationStatus,
      draftModerationStatus: selectedDraftModerationStatus,
      visualMarker: selectedVisualMarker,
    });
    const draftModerationUnlocksRerender = Boolean(
      (savedRenderDraftDirty && selectedDraftApproved && !visualModerationMessage)
      || (transcriptDirty && !selectedDraftBlocked && !visualModerationMessage)
    );
    const rerenderBlockedByModeration = requiresRerender && (
      Boolean(visualModerationMessage)
      || selectedDraftBlocked
      || (!draftModerationUnlocksRerender
        && ['pending', 'processing', 'running', 'revision_required', 'needs_admin_review', 'admin_rejected', 'failed', 'block', 'blocked', 'rejected'].includes(moderationStatus))
    );
    const moderationBlockedMessage = selectedDraftBlocked
      ? (visualModerationMessage || selectedDraftStatusMessage)
      : visualModerationMessage
        ? visualModerationMessage
      : moderationMessage(selectedLesson, selectedModeration);
    const moderationMessageForRerender = rerenderBlockedByModeration ? moderationBlockedMessage : '';
    const canSaveChanges = true;
    const canDiscardChanges = hasChanges;
    const canSaveRerender = Boolean(requiresRerender && !moderationMessageForRerender);

    return {
      hasChanges,
      requiresRerender,
      canSaveChanges,
      canDiscardChanges,
      canSaveRerender,
      moderationMessage: moderationMessageForRerender,
      saveDisabledReason: '',
      discardDisabledReason: hasChanges ? '' : 'No editor changes to discard.',
      rerenderDisabledReason: !hasChanges
        ? 'No editor changes to rerender.'
        : !requiresRerender
          ? 'Current changes do not affect the video.'
          : moderationMessageForRerender,
      notesDirty: lessonNotesDirty,
      savedNonRenderDraftDirty,
      savedRenderDraftDirty,
      transcriptDirty,
      ttsDirty,
    };
  }, [
    lessonNotesDirty,
    selectedDraftBlocked,
    selectedDraftModerationFindings,
    selectedDraftModerationStatus,
    selectedDraftRenderRequired,
    selectedDraftStatusMessage,
    selectedDraftApproved,
    selectedLesson,
    selectedLessonHasDraft,
    selectedModeration,
    selectedModerationFindings,
    selectedProjectModerationFindings,
    selectedVisualMarker,
    transcriptDirty,
    ttsDirty,
  ]);
  const previewSubtitleSummary = useMemo(
    () => subtitleTrackSummary(previewSubtitleTracks, previewLesson),
    [previewLesson, previewSubtitleTracks],
  );
  const previewActiveSubtitleCodes = useMemo(
    () => activeSubtitleTrackCodes(previewSubtitleTracks),
    [previewSubtitleTracks],
  );
  const missingPreviewSubtitleLanguages = useMemo(
    () => previewRequestableSubtitleLanguages.filter((language) => !previewActiveSubtitleCodes.has(language.code)),
    [previewActiveSubtitleCodes, previewRequestableSubtitleLanguages],
  );
  const selectedPreviewRequestLanguage = useMemo(
    () => (
      pendingSubtitleGeneration
      || missingPreviewSubtitleLanguages.find((language) => language.code === previewRequestLanguageCode)
      || missingPreviewSubtitleLanguages[0]
      || previewRequestableSubtitleLanguages.find((language) => language.code === previewRequestLanguageCode)
      || previewRequestableSubtitleLanguages[0]
    ),
    [missingPreviewSubtitleLanguages, pendingSubtitleGeneration, previewRequestLanguageCode, previewRequestableSubtitleLanguages],
  );

  const refreshProjectModeration = useCallback(async (projectId, { showLoading = true, preserveError = false } = {}) => {
    if (!projectId) return null;
    if (showLoading) setLoadingModeration(true);
    if (!preserveError) setModerationError('');
    try {
      const payload = await getProjectModeration(projectId);
      setModerationByProject((previous) => {
        const current = previous[projectId] || null;
        const currentIsStale = projectHasModerationStaleMarkers(null, current);
        const incomingIsStale = projectHasModerationStaleMarkers(null, payload);
        const incomingStatus = normalizedStatus(payload?.moderation_status);
        const sameRun = moderationRunId(current) === moderationRunId(payload);
        if (
          currentIsStale
          && !incomingIsStale
          && sameRun
          && (incomingStatus === 'approved' || incomingStatus === 'admin_approved')
        ) {
          return previous;
        }
        return {
          ...previous,
          [projectId]: payload,
        };
      });
      setProjects((previous) => previous.map((project) => {
        if (String(project.id) !== String(projectId)) return project;
        const projectIsStale = projectHasModerationStaleMarkers(project);
        const incomingIsStale = projectHasModerationStaleMarkers(null, payload);
        const incomingStatus = normalizedStatus(payload?.moderation_status);
        const sameRun = moderationRunId(project) === moderationRunId(payload);
        if (
          projectIsStale
          && !incomingIsStale
          && sameRun
          && (incomingStatus === 'approved' || incomingStatus === 'admin_approved')
        ) {
          return project;
        }
        const nextProject = { ...project };
        if (payload && Object.prototype.hasOwnProperty.call(payload, 'moderation_status')) {
          nextProject.moderation_status = payload.moderation_status;
        }
        if (payload && Object.prototype.hasOwnProperty.call(payload, 'moderation_summary')) {
          nextProject.moderation_summary = plainObject(payload.moderation_summary) || {};
        } else if (payload && Object.prototype.hasOwnProperty.call(payload, 'summary')) {
          nextProject.moderation_summary = plainObject(payload.summary) || {};
        }
        return nextProject;
      }));
      return payload;
    } catch (err) {
      if (!preserveError) {
        setModerationError(err.message || 'Moderation status is unavailable.');
      }
      return null;
    } finally {
      if (showLoading) setLoadingModeration(false);
    }
  }, []);

  const refreshLessonIntelligence = useCallback(async (projectId, { showLoading = true, preserveError = false } = {}) => {
    if (!projectId) return null;
    if (!intelligenceFeatureEnabled) {
      setLessonIntelligenceByProject((previous) => {
        if (!Object.prototype.hasOwnProperty.call(previous, projectId)) return previous;
        const next = { ...previous };
        delete next[projectId];
        return next;
      });
      setLessonIntelligenceError('');
      setLoadingLessonIntelligence(false);
      return null;
    }
    if (showLoading) setLoadingLessonIntelligence(true);
    if (!preserveError) setLessonIntelligenceError('');
    try {
      const payload = await fetchProjectLessonIntelligence(projectId);
      setLessonIntelligenceByProject((previous) => ({
        ...previous,
        [projectId]: payload,
      }));
      return payload;
    } catch (err) {
      if (!preserveError) {
        setLessonIntelligenceError(err.message || 'Lesson Intelligence is unavailable.');
      }
      return null;
    } finally {
      if (showLoading) setLoadingLessonIntelligence(false);
    }
  }, [intelligenceFeatureEnabled]);

  const refreshProjectTranscript = useCallback(async (projectId, { showLoading = true, preserveOnError = false } = {}) => {
    if (!projectId) return [];
    if (showLoading) setLoadingTranscript(true);
    try {
      const payload = await fetchProjectTranscript(projectId);
      const pages = Array.isArray(payload?.pages) ? payload.pages : [];
      if (String(selectedLessonIdRef.current || '') === String(projectId || '')) {
        setTranscriptPages(pages);
        setSelectedLessonDraftMetadata(payload?.has_draft ? (payload?.draft_metadata || {}) : {});
      }
      return pages;
    } catch {
      if (!preserveOnError && String(selectedLessonIdRef.current || '') === String(projectId || '')) {
        setTranscriptPages([]);
        setSelectedLessonDraftMetadata({});
      }
      return null;
    } finally {
      if (showLoading) setLoadingTranscript(false);
    }
  }, []);

  const refreshSelectedLessonState = useCallback(async (projectId, { preserveOnError = true, bypassCache = false } = {}) => {
    if (!projectId) return null;
    if (!bypassCache) {
      const cachedProject = readProjectDetailCache(projectDetailCacheRef.current, projectId);
      if (cachedProject) {
        setProjects((previous) => mergeProjectsPreservingLocalModeration(previous, [cachedProject], { append: true }));
      }
    }
    const refreshProjectSummary = fetchProject(projectId)
      .then((updatedProject) => {
        writeProjectDetailCache(projectDetailCacheRef.current, updatedProject);
        setProjects((previous) => mergeProjectsPreservingLocalModeration(previous, [updatedProject], { append: true }));
        return updatedProject;
      })
      .catch((projectError) => {
        if (!preserveOnError) {
          setProjects((previous) => previous.filter((project) => project.id !== projectId));
        }
        setProjectsError(projectError.message || 'Could not refresh the selected lesson.');
        return null;
      });
    const refreshes = [
      refreshProjectSummary,
      refreshProjectModeration(projectId, { showLoading: false, preserveError: true }),
      refreshProjectTranscript(projectId, { showLoading: false, preserveOnError: true }),
    ];
    if (intelligenceFeatureEnabled) {
      refreshes.push(refreshLessonIntelligence(projectId, { showLoading: false, preserveError: true }));
    }
    const [updatedProject] = await Promise.all(refreshes);
    return updatedProject || null;
  }, [intelligenceFeatureEnabled, refreshLessonIntelligence, refreshProjectModeration, refreshProjectTranscript]);

  const invalidateSelectedLessonCache = useCallback((projectId) => {
    invalidateProjectCaches(projectListCacheRef.current, projectDetailCacheRef.current, projectId || selectedLessonIdRef.current);
  }, []);

  const selectedLessonNeedsPolling = useMemo(() => studioLessonNeedsPolling({
    project: selectedLesson,
    moderation: selectedModeration,
    transcriptPages,
    moderationActionBusy,
    activeRerenderStatus,
    pendingSubtitleGeneration,
    generatingSubtitleTrack,
  }), [
    activeRerenderStatus,
    generatingSubtitleTrack,
    moderationActionBusy,
    pendingSubtitleGeneration,
    selectedLesson,
    selectedModeration,
    transcriptPages,
  ]);

  useEffect(() => {
    if (!selectedLesson?.id || !selectedLessonNeedsPolling) return undefined;

    let active = true;
    const poll = () => {
      if (!active) return;
      refreshSelectedLessonState(selectedLesson.id, { showLoading: false }).catch(() => {});
    };

    const intervalId = window.setInterval(poll, STUDIO_POLL_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [refreshSelectedLessonState, selectedLesson?.id, selectedLessonNeedsPolling]);

  useEffect(() => {
    if (!intelligenceFeatureEnabled || !selectedLesson?.id || !lessonIntelligenceEnhancementPending(selectedLessonIntelligence)) return undefined;

    let active = true;
    const poll = async () => {
      try {
        const payload = await fetchProjectLessonIntelligence(selectedLesson.id);
        if (!active) return;
        setLessonIntelligenceByProject((previous) => ({
          ...previous,
          [selectedLesson.id]: payload,
        }));
      } catch {
        // Keep the heuristic report visible if a polling read fails.
      }
    };

    const intervalId = window.setInterval(poll, LESSON_INTELLIGENCE_ENHANCEMENT_POLL_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [intelligenceFeatureEnabled, selectedLesson?.id, selectedLessonIntelligence]);

  useEffect(() => {
    if (!activeRerenderStatus || !selectedLesson?.id) return;
    const jobStatus = projectLatestJobStatus(selectedLesson);
    const status = projectRawStatus(selectedLesson);
    if (jobStatus === 'done' || jobStatus === 'failed' || status === 'ready' || status === 'failed') {
      setActiveRerenderStatus(null);
    }
  }, [activeRerenderStatus, selectedLesson?.id, selectedLesson?.latest_job?.status, selectedLesson?.status]);

  useEffect(() => {
    if (!selectedLesson?.id) {
      setModerationError('');
      setReviewDialogOpen(false);
      setReviewMessage('');
      return;
    }

    setReviewDialogOpen(false);
    setReviewMessage('');
    refreshProjectModeration(selectedLesson.id);
  }, [refreshProjectModeration, selectedLesson?.id]);

  useEffect(() => {
    if (!intelligenceFeatureEnabled) {
      setLessonIntelligenceError('');
      setLessonIntelligenceCopied(false);
      setLessonIntelligenceCopiedItemKey('');
      setLessonIntelligenceNotice('');
      setLessonIntelligenceByProject({});
      return;
    }
    if (!selectedLesson?.id) {
      setLessonIntelligenceError('');
      setLessonIntelligenceCopied(false);
      setLessonIntelligenceCopiedItemKey('');
      setLessonIntelligenceNotice('');
      return;
    }

    setLessonIntelligenceError('');
    setLessonIntelligenceCopied(false);
    setLessonIntelligenceCopiedItemKey('');
    setLessonIntelligenceNotice('');
    refreshLessonIntelligence(selectedLesson.id);
  }, [intelligenceFeatureEnabled, refreshLessonIntelligence, selectedLesson?.id]);

  useEffect(() => {
    if (!selectedLesson?.id) {
      setTranscriptPages([]);
      setSceneActionMessage('');
      setSceneActionError('');
      setHighlightPreviewMessage('');
      setHighlightPreviewImageUrl('');
      return;
    }

    setSceneDraftStatus({});
    setActiveRerenderStatus(null);
    setSceneActionMessage('');
    setSceneActionError('');
    setHighlightPreviewMessage('');
    setHighlightPreviewImageUrl('');

    refreshProjectTranscript(selectedLesson.id);
  }, [refreshProjectTranscript, selectedLesson?.id]);

  useEffect(() => {
    if (!selectedLesson?.id || !projectRenderReady(selectedLesson)) {
      setPreviewLesson(null);
      setLoadingPreview(false);
      setPreviewError('');
      return undefined;
    }

    let active = true;
    setLoadingPreview(true);
    setPreviewError('');

    Promise.all([
      fetchStudioPreviewToken(selectedLesson.id),
      fetchSubtitleTrackBundle(selectedLesson.id).catch(() => ({ tracks: [], requestableLanguages: [] }))
    ])
      .then(([payload, tracksPayload]) => {
        if (!active) return;
        setPreviewLesson(payload ? { ...payload, stream_url: payload.video_url } : null);
        setPreviewSubtitleTracks(tracksPayload?.tracks || []);
        setPreviewRequestableSubtitleLanguages(tracksPayload?.requestableLanguages || []);
      })
      .catch((err) => {
        if (!active) return;
        setPreviewLesson(null);
        setPreviewError(err.message || 'Preview is not available yet.');
      })
      .finally(() => {
        if (active) {
          setLoadingPreview(false);
        }
      });

    return () => {
      active = false;
    };
  }, [selectedLesson?.id, selectedLesson?.is_published, selectedLesson?.latest_job?.status, selectedLesson?.status]);

  useEffect(() => {
    setSubtitleGenerationMessage('');
    setGeneratingSubtitleTrack(false);
    setPendingSubtitleGeneration(null);
  }, [selectedLesson?.id]);

  useEffect(() => {
    if (pendingSubtitleGeneration) return;
    if (
      missingPreviewSubtitleLanguages.length
      && !missingPreviewSubtitleLanguages.some((language) => language.code === previewRequestLanguageCode)
    ) {
      setPreviewRequestLanguageCode(missingPreviewSubtitleLanguages[0].code);
    }
  }, [missingPreviewSubtitleLanguages, pendingSubtitleGeneration, previewRequestLanguageCode]);

  useEffect(() => {
    if (!selectedLesson?.id || !pendingSubtitleGeneration?.code) return undefined;

    let active = true;
    let timeoutId;

    const pollSubtitleTracks = async () => {
      try {
        const tracks = await fetchSubtitleTracks(selectedLesson.id);
        if (!active) return;
        setPreviewSubtitleTracks(tracks || []);
        const track = (tracks || []).find((item) => subtitleTrackCode(item) === pendingSubtitleGeneration.code);
        const status = String(track?.status || '').trim().toLowerCase();
        if (track && isReadySubtitleTrack(track)) {
          const { providerLabel, mockNote } = subtitleProviderMessage(track);
          setSubtitleGenerationMessage(`${pendingSubtitleGeneration.label} subtitles are ready${providerLabel}. Select them from the player menu.${mockNote}`);
          setPendingSubtitleGeneration(null);
          setGeneratingSubtitleTrack(false);
          return;
        }
        if (status === 'failed') {
          setSubtitleGenerationMessage(track?.error_message || `Could not generate ${pendingSubtitleGeneration.label} subtitles.`);
          setPendingSubtitleGeneration(null);
          setGeneratingSubtitleTrack(false);
        }
      } catch (err) {
        if (!active) return;
        setSubtitleGenerationMessage(err.message || `Could not refresh ${pendingSubtitleGeneration.label} subtitle status.`);
      }
    };

    pollSubtitleTracks();
    timeoutId = window.setInterval(pollSubtitleTracks, 3000);

    return () => {
      active = false;
      if (timeoutId) window.clearInterval(timeoutId);
    };
  }, [pendingSubtitleGeneration, selectedLesson?.id]);

  useEffect(() => {
    if (!selectedLesson?.id) {
      setLessonNotes('');
      setLessonNotesSavedValue('');
      setLessonNotesSavedAt('');
      setLessonNotesLocalDraft(null);
      lessonNotesHydratedProjectRef.current = null;
      return;
    }

    const stored = window.localStorage.getItem(lessonNotesKey(selectedLesson.id)) || '';
    const localDraft = readLessonNotesDraft(studioLocalDraftScope, selectedLesson.id);
    const localDraftValue = textValue(localDraft?.value);
    setLessonNotes(stored);
    setLessonNotesSavedValue(stored);
    setLessonNotesSavedAt(stored ? 'Loaded saved lesson notes' : 'No lesson notes yet');
    lessonNotesHydratedProjectRef.current = selectedLesson.id;
    if (localDraft && localDraftValue !== stored) {
      setLessonNotesLocalDraft({
        ...localDraft,
        value: localDraftValue,
      });
    } else {
      clearLessonNotesDraft(studioLocalDraftScope, selectedLesson.id);
      setLessonNotesLocalDraft(null);
    }
  }, [selectedLesson?.id, studioLocalDraftScope]);

  useEffect(() => {
    if (!selectedLesson?.id || lessonNotesHydratedProjectRef.current !== selectedLesson.id) return;
    if (lessonNotesLocalDraft) return;
    if (lessonNotes !== lessonNotesSavedValue) {
      writeLessonNotesDraft(studioLocalDraftScope, selectedLesson.id, lessonNotes);
    } else {
      clearLessonNotesDraft(studioLocalDraftScope, selectedLesson.id);
    }
  }, [lessonNotes, lessonNotesLocalDraft, lessonNotesSavedValue, selectedLesson?.id, studioLocalDraftScope]);

  useEffect(() => {
    setTranscriptDirty(false);
    setTtsDirty(false);
  }, [selectedLesson?.id]);

  useEffect(() => {
    const handleCreateLessonRequest = () => {
      if (!readOnlyReview) setCreateModalOpen(true);
    };
    window.addEventListener('visus:create-lesson-request', handleCreateLessonRequest);
    return () => window.removeEventListener('visus:create-lesson-request', handleCreateLessonRequest);
  }, [readOnlyReview]);

  useEffect(() => {
    if (!coverFile) {
      setCoverPreviewUrl('');
      return undefined;
    }

    const objectUrl = URL.createObjectURL(coverFile);
    setCoverPreviewUrl(objectUrl);

    return () => {
      URL.revokeObjectURL(objectUrl);
    };
  }, [coverFile]);

  useEffect(() => {
    const projectId = selectedLesson?.id;
    const key = editorDraftKey(projectId);
    const stored = window.localStorage.getItem(key);

    if (stored) {
      try {
        const draft = JSON.parse(stored);
        setEditorTitle(String(draft.title || selectedLesson?.title || ''));
        setEditorCategory(String(draft.category || selectedLesson?.category_name || ''));
        setEditorCanvas(String(draft.canvas || selectedLesson?.description || ''));
        setPauseSec(String(draft.pauseSec || '0.2'));
        setWhiteboardModeAll(Boolean(draft.whiteboardModeAll));
        setAvatarEnabled(avatarFeatureEnabled && draft.avatarEnabled === true);
        setEditorSavedAtLabel('Draft restored');
        return;
      } catch {
        window.localStorage.removeItem(key);
      }
    }

    setEditorTitle(selectedLesson?.title || '');
    setEditorCategory(selectedLesson?.category_name || '');
    setEditorCanvas(selectedLesson?.description || '');
    setPauseSec('0.2');
    setWhiteboardModeAll(false);
    setAvatarEnabled(avatarFeatureEnabled && Boolean(selectedLesson?.avatar_enabled_override ?? selectedLesson?.avatar_active ?? false));
    setEditorSavedAtLabel('');
  }, [avatarFeatureEnabled, selectedLesson?.avatar_active, selectedLesson?.avatar_enabled_override, selectedLesson?.category_name, selectedLesson?.description, selectedLesson?.id, selectedLesson?.title]);

  useEffect(() => {
    setAvatarRerenderMessage('');
    setAvatarRerendering(false);
  }, [selectedLesson?.id]);

  const handleCreateProject = async ({
    file,
    coverFile,
    title,
    category,
    pauseSec,
    whiteboardModeAll,
    avatarEnabled,
  }) => {
    if (readOnlyReview) return null;
    if (!file) return;

    setSubmitError('');
    setSubmitting(true);

    const formData = new FormData();
    formData.append('lesson_file', file);
    if (coverFile) formData.append('cover_file', coverFile);
    if (title) formData.append('title', title);
    if (category) formData.append('category', category);
    if (user?.id) formData.append('user_id', user.id);
    if (pauseSec) formData.append('pause_sec', pauseSec);
    if (whiteboardModeAll) formData.append('whiteboard_mode_all', '1');
    formData.append('avatar_enabled', avatarFeatureEnabled && avatarEnabled ? '1' : '0');

    try {
      const createdJob = await createProject(formData);
      const createdProjectId = Number(createdJob?.project_id || createdJob?.project?.id || 0) || null;
      await refreshProjects({ preserveOnError: true });
      if (createdProjectId) {
        selectedLessonIdRef.current = createdProjectId;
        setSelectedLessonId(createdProjectId);
        await Promise.all([
          refreshProjectModeration(createdProjectId, { showLoading: false, preserveError: true }),
          refreshProjectTranscript(createdProjectId, { showLoading: false, preserveOnError: true }),
        ]);
      }
      return createdProjectId || true;
    } catch (err) {
      setSubmitError(err.message || 'Project upload failed.');
      return false;
    } finally {
      setSubmitting(false);
    }
  };

  const persistEditorDraft = () => {
    const draftPayload = {
      title: editorTitle,
      category: editorCategory,
      canvas: editorCanvas,
      pauseSec,
      whiteboardModeAll,
      avatarEnabled: avatarFeatureEnabled && avatarEnabled,
    };
    window.localStorage.setItem(editorDraftKey(selectedLesson?.id), JSON.stringify(draftPayload));
    setEditorSavedAtLabel(`Draft saved at ${new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`);
  };

  const saveLessonNotes = () => {
    if (!selectedLesson?.id) return;
    window.localStorage.setItem(lessonNotesKey(selectedLesson.id), lessonNotes);
    clearLessonNotesDraft(studioLocalDraftScope, selectedLesson.id);
    setLessonNotesLocalDraft(null);
    setLessonNotesSavedValue(lessonNotes);
    setLessonNotesSavedAt(`Saved at ${new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`);
  };

  const restoreLessonNotesDraft = () => {
    if (!selectedLesson?.id || !lessonNotesLocalDraft) return;
    setLessonNotes(textValue(lessonNotesLocalDraft.value));
    setLessonNotesLocalDraft(null);
    setLessonNotesSavedAt('Unsaved local draft restored');
  };

  const discardLessonNotesDraft = () => {
    if (!selectedLesson?.id) return;
    clearLessonNotesDraft(studioLocalDraftScope, selectedLesson.id);
    setLessonNotesLocalDraft(null);
    setLessonNotesSavedAt(lessonNotesSavedValue ? 'Loaded saved lesson notes' : 'No lesson notes yet');
  };

  const handleDeleteProject = async (project) => {
    if (!window.confirm(`Delete ${project.title || `project #${project.id}`}?`)) return;
    try {
      await deleteProject(project.id);
      await refreshProjects();
    } catch (err) {
      window.alert(err.message || 'Delete failed.');
    }
  };

  const handleRerenderProject = async (project, options = {}) => {
    const hasAvatarOverride =
      Object.prototype.hasOwnProperty.call(options, 'avatarEnabled') ||
      Object.prototype.hasOwnProperty.call(options, 'renderWithAvatar');
    const shouldUseEditorAvatarOption = Boolean(
      avatarFeatureEnabled &&
      selectedLesson?.id &&
      project?.id === selectedLesson.id &&
      !hasAvatarOverride
    );
    const renderWithAvatar = shouldUseEditorAvatarOption
      ? Boolean(avatarEnabled)
      : Boolean(options.avatarEnabled ?? options.renderWithAvatar);
    const avatarQueueExpected = (hasAvatarOverride || shouldUseEditorAvatarOption)
      ? Boolean(avatarFeatureEnabled && renderWithAvatar)
      : Boolean(avatarFeatureEnabled && projectAvatarEnabled(project));
    const queueNote = avatarQueueExpected
      ? ' Avatar will continue in the background after the base render is ready.'
      : '';
    if (!window.confirm(`Rerender ${project.title || `project #${project.id}`}?${queueNote}`)) return false;
    try {
      await rerenderProject(
        project.id,
        (hasAvatarOverride || shouldUseEditorAvatarOption)
          ? { renderWithAvatar }
          : {},
      );
      invalidateSelectedLessonCache(project.id);
      await refreshSelectedLessonState(project.id, { showLoading: false, bypassCache: true });
      return true;
    } catch (err) {
      window.alert(err.message || 'Rerender failed.');
      return false;
    }
  };

  const handleAvatarVisibilityToggle = async (project, nextVisible) => {
    if (!avatarFeatureEnabled || !project?.id || avatarVisibilitySaving) return;
    setAvatarVisibilitySaving(true);
    try {
      const updated = await updateProjectAvatarVisible(project.id, nextVisible);
      invalidateSelectedLessonCache(project.id);
      handleProjectUpdated(updated);
      if (selectedLesson?.id === project.id) {
        setSelectedLessonId(project.id);
      }
    } catch (err) {
      window.alert(err.message || 'Avatar visibility update failed.');
    } finally {
      setAvatarVisibilitySaving(false);
    }
  };

  const handleProjectUpdated = useCallback((updatedProject) => {
    if (!updatedProject?.id) return;
    setProjects((prev) => prev.map((project) => {
      if (project.id !== updatedProject.id) return project;
      const previousSummary = plainObject(project.moderation_summary) || {};
      const incomingSummary = plainObject(updatedProject.moderation_summary) || {};
      return {
        ...project,
        ...updatedProject,
        moderation_summary: {
          ...previousSummary,
          ...incomingSummary,
        },
        moderation_status: updatedProject.moderation_status || project.moderation_status,
      };
    }));
    setSelectedLessonId((previous) => previous || updatedProject.id);
  }, []);

  const handleAvatarOnlyRerender = async () => {
    if (readOnlyReview || !avatarFeatureEnabled || !selectedLesson?.id || avatarRerendering) return;
    setAvatarRerendering(true);
    setAvatarRerenderMessage('');
    try {
      const result = await rerenderProjectAvatar(selectedLesson.id);
      invalidateSelectedLessonCache(selectedLesson.id);
      handleProjectUpdated({
        ...selectedLesson,
        avatar_processing_status: result.avatar_processing_status || 'queued',
        avatar_processing_message: result.message || 'Avatar rerender queued.',
        avatar_last_job_id: String(result.avatar_job_id || selectedLesson.avatar_last_job_id || ''),
        avatar_runtime_settings: result.avatar_runtime_settings || selectedLesson.avatar_runtime_settings,
      });
      setAvatarRerenderMessage(result.message || 'Avatar rerender queued.');
      refreshSelectedLessonState(selectedLesson.id, { showLoading: false, preserveOnError: true, bypassCache: true }).catch(() => {});
    } catch (err) {
      setAvatarRerenderMessage(err.message || 'Avatar rerender failed to start.');
    } finally {
      setAvatarRerendering(false);
    }
  };

  const handleGeneratePreviewSubtitles = async () => {
    const projectId = selectedLesson?.id;
    const language = selectedPreviewRequestLanguage;
    if (!projectId || !language || generatingSubtitleTrack) return;
    setGeneratingSubtitleTrack(true);
    setSubtitleGenerationMessage('');

    try {
      const track = await generateSubtitleTrack(projectId, {
        language_code: language.code,
        language_label: language.label,
        provider: 'auto',
      });
      if (isReadySubtitleTrack(track)) {
        const tracksPayload = await fetchSubtitleTrackBundle(projectId);
        setPreviewSubtitleTracks(tracksPayload?.tracks || []);
        setPreviewRequestableSubtitleLanguages(tracksPayload?.requestableLanguages || []);
        const { providerLabel, mockNote } = subtitleProviderMessage(track);
        setSubtitleGenerationMessage(`${track?.language_label || language.label} subtitles are ready${providerLabel}. Select them from the player menu.${mockNote}`);
        setPendingSubtitleGeneration(null);
        setGeneratingSubtitleTrack(false);
        return;
      }
      if (String(track?.status || '').trim().toLowerCase() === 'failed') {
        setSubtitleGenerationMessage(track?.error_message || 'Subtitle generation failed.');
        setPendingSubtitleGeneration(null);
        setGeneratingSubtitleTrack(false);
        return;
      }
      setPendingSubtitleGeneration(language);
      setSubtitleGenerationMessage(`Generating ${language.label} subtitles...`);
    } catch (err) {
      setSubtitleGenerationMessage(err.message || 'Subtitle generation failed.');
      setGeneratingSubtitleTrack(false);
    }
  };

  const updateProjectModerationStatus = useCallback((projectId, moderationStatus) => {
    if (!projectId || !moderationStatus) return;
    setProjects((prev) => prev.map((project) => (
      project.id === projectId ? { ...project, moderation_status: moderationStatus } : project
    )));
  }, []);

  const applyProjectModerationPayload = useCallback((payload, fallbackStatus = 'not_scanned') => {
    if (!payload) return;
    const projectId = Number(payload.project_id || payload.id || payload.project?.id || selectedLesson?.id || 0) || null;
    if (!projectId) return;
    invalidateSelectedLessonCache(projectId);

    const payloadSummary = plainObject(payload.moderation_summary)
      || plainObject(payload.summary)
      || plainObject(payload.project?.moderation_summary)
      || {};
    const textMarker = plainObject(payload.editor_text_changed)
      || plainObject(payloadSummary.editor_text_changed)
      || plainObject(payloadSummary.stale_text)
      || null;
    const visualMarker = plainObject(payload.visual_asset_scan)
      || plainObject(payloadSummary.visual_asset_scan)
      || null;
    const payloadStatus = String(
      payload.moderation_status
        || payload.project?.moderation_status
        || fallbackStatus
        || 'not_scanned',
    ).trim().toLowerCase();
    const hasStaleMarker = moderationMarkerIsStale(textMarker) || moderationMarkerIsStale(visualMarker);
    const effectiveStatus = hasStaleMarker && (payloadStatus === 'approved' || payloadStatus === 'admin_approved')
      ? 'not_scanned'
      : payloadStatus;

    setModerationByProject((previous) => {
      const current = plainObject(previous[projectId]) || {};
      const currentSummary = plainObject(current.moderation_summary) || {};
      const nextSummary = {
        ...currentSummary,
        ...payloadSummary,
      };
      if (textMarker) nextSummary.editor_text_changed = textMarker;
      if (visualMarker) nextSummary.visual_asset_scan = visualMarker;

      return {
        ...previous,
        [projectId]: {
          ...current,
          moderation_status: effectiveStatus || current.moderation_status || fallbackStatus || 'not_scanned',
          moderation_summary: nextSummary,
          latest_run_id: payload.latest_run_id
            ?? payload.last_moderation_run_id
            ?? payload.run_id
            ?? current.latest_run_id
            ?? selectedLesson?.last_moderation_run_id
            ?? null,
          ...(textMarker ? { editor_text_changed: textMarker } : {}),
          ...(visualMarker ? { visual_asset_scan: visualMarker } : {}),
          ...(payload.message ? { message: payload.message } : {}),
        },
      };
    });

    setProjects((previous) => previous.map((project) => {
      if (String(project.id) !== String(projectId)) return project;
      const currentSummary = plainObject(project.moderation_summary) || {};
      const nextSummary = {
        ...currentSummary,
        ...payloadSummary,
      };
      if (textMarker) nextSummary.editor_text_changed = textMarker;
      if (visualMarker) nextSummary.visual_asset_scan = visualMarker;
      return {
        ...project,
        moderation_status: effectiveStatus || project.moderation_status || fallbackStatus || 'not_scanned',
        moderation_summary: nextSummary,
      };
    }));
  }, [invalidateSelectedLessonCache, selectedLesson?.id, selectedLesson?.last_moderation_run_id]);

  const handleModerationRescan = async (project) => {
    if (!project?.id) return;
    if (String(project.id) === String(selectedLesson?.id || '') && transcriptEditorRef.current?.hasUnsavedChanges?.()) {
      setModerationError('Save transcript changes before rerunning moderation.');
      return;
    }
    setModerationActionBusy('rescan');
    setModerationError('');
    try {
      const payload = await rescanProjectModeration(project.id);
      setModerationByProject((previous) => {
        const current = previous[project.id] || {};
        return {
          ...previous,
          [project.id]: {
            ...current,
            ...payload,
            project_id: payload?.project_id || project.id,
            moderation_status: payload?.moderation_status || 'pending',
            message: payload?.message || moderationSuggestedMessage('pending'),
            findings: Array.isArray(payload?.findings) ? payload.findings : current.findings || [],
          },
        };
      });
      updateProjectModerationStatus(project.id, payload?.moderation_status || 'pending');
      invalidateSelectedLessonCache(project.id);
      await refreshSelectedLessonState(project.id, { showLoading: false, bypassCache: true });
    } catch (err) {
      setModerationError(err.message || 'Moderation rescan failed.');
    } finally {
      setModerationActionBusy('');
    }
  };

  const handleRequestAdminReview = async (project) => {
    if (!project?.id) return;
    setModerationActionBusy('review');
    setModerationError('');
    try {
      await requestProjectAdminReview(project.id, reviewMessage);
      setReviewDialogOpen(false);
      setReviewMessage('');
      setModerationByProject((previous) => {
        const current = previous[project.id] || {};
        return {
          ...previous,
          [project.id]: {
            ...current,
            moderation_status: 'needs_admin_review',
            can_request_admin_review: false,
            can_publish: false,
            message: moderationSuggestedMessage('needs_admin_review'),
          },
        };
      });
      updateProjectModerationStatus(project.id, 'needs_admin_review');
      invalidateSelectedLessonCache(project.id);
      await refreshSelectedLessonState(project.id, { showLoading: false, bypassCache: true });
    } catch (err) {
      const duplicateOpenRequest = err?.status === 409 || /review request is already open/i.test(err?.message || '');
      if (duplicateOpenRequest) {
        setReviewDialogOpen(false);
        setReviewMessage('');
        setModerationByProject((previous) => {
          const current = previous[project.id] || {};
          return {
            ...previous,
            [project.id]: {
              ...current,
              moderation_status: 'needs_admin_review',
              can_request_admin_review: false,
              can_publish: false,
              message: err.message || 'A review request is already open. Please wait for an admin response.',
            },
          };
        });
        updateProjectModerationStatus(project.id, 'needs_admin_review');
        invalidateSelectedLessonCache(project.id);
        await refreshSelectedLessonState(project.id, { showLoading: false, bypassCache: true });
        return;
      }
      setModerationError(err.message || 'Admin review request failed.');
    } finally {
      setModerationActionBusy('');
    }
  };

  const handleAdminReviewAction = async (action) => {
    if (!selectedLesson?.id || adminReviewActionBusy) return;
    const reason = adminReviewResponse.trim() || (action === 'request_changes'
      ? publisherMessageForModeration(selectedModeration)
      : '');
    setAdminReviewActionBusy(action);
    setAdminReviewActionMessage('');
    setAdminReviewActionError('');
    try {
      let nextStatus = '';
      let apiAction = action;
      if (action === 'approve') {
        apiAction = 'approve';
        nextStatus = 'admin_approved';
      } else if (action === 'reject' || action === 'block') {
        apiAction = 'block';
        nextStatus = 'admin_rejected';
      } else {
        apiAction = 'request_changes';
        nextStatus = 'revision_required';
      }
      const payload = await runAdminProjectModerationAction(
        selectedLesson.id,
        apiAction,
        reason,
        'manual_admin_review',
        { unpublish: true },
      );

      if (nextStatus) updateProjectModerationStatus(selectedLesson.id, nextStatus);
      invalidateSelectedLessonCache(selectedLesson.id);
      await refreshSelectedLessonState(selectedLesson.id, { showLoading: false, preserveOnError: true, bypassCache: true });
      await refreshProjectModeration(selectedLesson.id, { showLoading: false, preserveError: true });
      setAdminReviewActionMessage(payload?.message || 'Moderation action saved.');
    } catch (err) {
      setAdminReviewActionError(err.message || 'Could not update moderation state.');
    } finally {
      setAdminReviewActionBusy('');
    }
  };

  const handleAnalyzeLessonIntelligence = useCallback(async (project, { auto = false, force = false } = {}) => {
    if (!intelligenceFeatureEnabled) return null;
    if (!project?.id || lessonIntelligenceActionBusy) return null;
    if (lessonIntelligenceEnhancementPending(lessonIntelligenceByProject[project.id])) return null;
    setLessonIntelligenceActionBusy('analyze');
    setLessonIntelligenceError('');
    setLessonIntelligenceCopied(false);
    setLessonIntelligenceNotice('');
    try {
      const payload = await analyzeProjectLessonIntelligence(project.id, { force: Boolean(force) && !auto });
      setLessonIntelligenceByProject((previous) => ({
        ...previous,
        [project.id]: payload,
      }));
      return payload;
    } catch (err) {
      setLessonIntelligenceError(err.message || 'Lesson analysis failed.');
      return null;
    } finally {
      setLessonIntelligenceActionBusy('');
    }
  }, [intelligenceFeatureEnabled, lessonIntelligenceActionBusy, lessonIntelligenceByProject]);

  const handleCopyLessonIntelligence = async () => {
    const text = lessonIntelligenceCopyText(selectedLessonIntelligence);
    if (!text) return;
    try {
      await copyTextToClipboard(text);
      setLessonIntelligenceCopied(true);
      window.setTimeout(() => setLessonIntelligenceCopied(false), 2200);
    } catch {
      setLessonIntelligenceError('Could not copy suggestions.');
    }
  };

  const handleCopyLessonIntelligenceItem = async (item, index = 0) => {
    const copyText = getCleanSuggestionCopyText(item);
    if (!copyText) return;
    const itemKey = lessonIntelligenceItemKey(item, index);
    try {
      await copyTextToClipboard(copyText);
      setLessonIntelligenceCopiedItemKey(itemKey);
      setLessonIntelligenceNotice('Suggestion copied.');
      window.setTimeout(() => {
        setLessonIntelligenceCopiedItemKey('');
        setLessonIntelligenceNotice('');
      }, 2200);
    } catch {
      setLessonIntelligenceError('Could not copy this suggestion.');
    }
  };

  const handleApplyLessonNarrationSuggestion = (item) => {
    const apply = transcriptEditorRef.current?.applyNarrationSuggestion;
    if (!apply) {
      setLessonIntelligenceError('Transcript editor is not ready yet.');
      return;
    }
    const result = apply(item);
    if (!result?.ok) {
      if (result?.pendingConfirmation) {
        setLessonIntelligenceError('');
        setLessonIntelligenceNotice('');
      } else if (!result?.cancelled) {
        setLessonIntelligenceError(result?.message || 'Could not apply this suggestion.');
      }
      return;
    }
    setLessonIntelligenceError('');
    setLessonIntelligenceNotice(`${lessonIntelligenceDraftLabel(item) || 'Draft narration'} applied. Review it, then save changes when ready.`);
  };

  useEffect(() => {
    if (!intelligenceFeatureEnabled) return;
    if (!selectedLesson?.id || loadingLessonIntelligence || lessonIntelligenceActionBusy) return;
    const hasFetchedReport = Object.prototype.hasOwnProperty.call(lessonIntelligenceByProject, selectedLesson.id);
    if (!hasFetchedReport) return;

    const report = selectedLessonIntelligence || null;
    if (report?.enabled === false || report?.status === 'disabled' || report?.status === 'failed') return;
    if (lessonIntelligenceEnhancementPending(report)) return;
    if (transcriptEditorRef.current?.hasUnsavedChanges?.()) return;

    const status = String(report?.status || '').toLowerCase();
    const missingReport = !report?.id || status === 'empty';
    const staleReport = lessonIntelligenceIsStale(report);
    if (!missingReport && !(staleReport && activeEditorPanel === 'intelligence')) return;

    const sourceKey = report?.current_source_hash || report?.report_source_hash || report?.source_hash || 'empty';
    const autoRunKey = `${selectedLesson.id}:${sourceKey}:${missingReport ? 'missing' : 'stale'}`;
    if (lessonIntelligenceAutoRunKeysRef.current.has(autoRunKey)) return;
    lessonIntelligenceAutoRunKeysRef.current.add(autoRunKey);
    handleAnalyzeLessonIntelligence(selectedLesson, { auto: true });
  }, [
    activeEditorPanel,
    handleAnalyzeLessonIntelligence,
    intelligenceFeatureEnabled,
    lessonIntelligenceActionBusy,
    lessonIntelligenceByProject,
    loadingLessonIntelligence,
    selectedLesson,
    selectedLessonIntelligence,
  ]);

  const handlePublishToggle = async (project, nextPublished) => {
    if (readOnlyReview) return;
    const moderation = moderationByProject[project.id] || null;
    if (nextPublished && !projectCanPublishFromModeration(project, moderation)) {
      const message = moderationMessage(project, moderation);
      setModerationError(message);
      if (project.id !== selectedLesson?.id) {
        setSelectedLessonId(project.id);
      }
      window.alert(message);
      return;
    }

    try {
      const updated = await updateProjectPublished(project.id, nextPublished);
      invalidateSelectedLessonCache(project.id);
      handleProjectUpdated(updated);
      await refreshSelectedLessonState(project.id, { showLoading: false, bypassCache: true });
    } catch (err) {
      const message = err.message || 'Publication update failed.';
      setModerationError(message);
      invalidateSelectedLessonCache(project.id);
      await refreshSelectedLessonState(project.id, { showLoading: false, bypassCache: true });
      window.alert(message);
    }
  };

  const setStudioLocation = useCallback((nextView, lessonId = null) => {
    const nextParams = new URLSearchParams();
    nextParams.set('view', ['editor', 'playlists'].includes(nextView) ? nextView : 'lessons');
    if (isReviewMode) {
      nextParams.set('mode', 'review');
      nextParams.set('review', requestedReviewId ? String(requestedReviewId) : '1');
      if (requestedReportId) nextParams.set('report', String(requestedReportId));
      if (requestedSource) nextParams.set('source', requestedSource);
      if (requestedSourceItem) nextParams.set('sourceItem', requestedSourceItem);
      if (requestedReviewReturnTo) nextParams.set('returnTo', requestedReviewReturnTo);
    }

    const targetLessonId = lessonId || selectedLessonId;
    if (targetLessonId) {
      nextParams.set('lesson', String(targetLessonId));
    }

    setSearchParams(nextParams);
  }, [
    isReviewMode,
    requestedReportId,
    requestedReviewId,
    requestedReviewReturnTo,
    requestedSource,
    requestedSourceItem,
    selectedLessonId,
    setSearchParams,
  ]);

  useEffect(() => onRouteReset('studio', () => {
    clearStudioSession(studioPositionStorageKey);
    setSearchParams(new URLSearchParams());
    setSelectedLessonId(null);
    setActiveTab('overview');
    setActiveEditorPanel(visibleEditorPanels[0] || 'transcript');
    setSelectedPageKey('');
    setSelectedPageIndex(0);
    setExpandedSlideKeys({});
    setReviewDialogOpen(false);
    setReviewMessage('');
    setAdminReviewResponse('');
    setAdminReviewActionMessage('');
    setAdminReviewActionError('');
    setAdminReviewContext(null);
    setAdminReviewContextError('');
    window.scrollTo({ top: 0, behavior: 'auto' });
  }), [setSearchParams, studioPositionStorageKey, visibleEditorPanels]);

  const handleBackToReviewContext = useCallback(() => {
    navigate(requestedReviewReturnTo || '/moderation');
  }, [navigate, requestedReviewReturnTo]);

  const adminReviewBackLabelText = useMemo(
    () => adminReviewBackLabel({
      reportId: requestedReportId,
      source: requestedSource,
      sourceItem: requestedSourceItem,
      returnTo: requestedReviewReturnTo,
    }),
    [requestedReportId, requestedReviewReturnTo, requestedSource, requestedSourceItem],
  );

  const adminReviewContextLabel = useMemo(() => {
    const parts = [];
    if (selectedLesson?.id) parts.push(`Project #${selectedLesson.id}`);
    if (requestedReviewId) parts.push(`Review #${requestedReviewId}`);
    if (requestedReportId) parts.push(`Report #${requestedReportId}`);
    if (requestedSourceItem && !parts.includes(requestedSourceItem)) {
      parts.push(requestedSourceItem.replace(/:/g, ' #'));
    }
    return parts.length ? parts.join(' - ') : 'Moderation review context';
  }, [requestedReportId, requestedReviewId, requestedSourceItem, selectedLesson?.id]);

  const openEditorForProject = (project) => {
    if (readOnlyReview) return;
    writeProjectDetailCache(projectDetailCacheRef.current, project);
    cacheProjectWindow(projectDetailCacheRef.current, projects, project.id);
    setSelectedLessonId(project.id);
    setStudioLocation('editor', project.id);
  };

  const openPreviewForProject = (project) => {
    if (!project?.id) return;
    if (readOnlyReview) {
      navigate(`/watch?lesson=${project.id}&review=1`);
      return;
    }
    if (!projectRenderReady(project)) return;
    if (project.is_published) {
      navigate(`/watch?lesson=${project.id}`);
      return;
    }
    setSelectedLessonId(project.id);
    setActiveTab('overview');
    setStudioLocation('lessons', project.id);
    window.requestAnimationFrame(() => {
      previewSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  };

  const selectLesson = (project) => {
    writeProjectDetailCache(projectDetailCacheRef.current, project);
    cacheProjectWindow(projectDetailCacheRef.current, projects, project.id);
    setSelectedLessonId(project.id);
    setStudioLocation(studioView, project.id);
  };

  const sceneItems = useMemo(() => {
    if (transcriptPages.length > 0) {
      return transcriptPages.map((page, index) => {
        const key = pageIdentity(page, index);
        const draft = sceneDraftStatus[key] || {};
        const status = draft.status || sceneStatusFromPage(page);
        const displayText = draft.display_text ?? pageDisplayText(page);
        const sceneSettings = pageSceneSettings(page);
        const backgroundUrl = sceneBackgroundUrl(page);
        const thumbnailUrl = sceneSettings.backgroundMode === 'whiteboard'
          ? ''
          : firstAvailableUrl(backgroundUrl, page.thumbnail_url, page.slide_image_url, page.image_url, page.image_file_url);
        return {
          id: page.id || key,
          key,
          index,
          type: 'transcript',
          label: sceneLabel(page, index),
          pageKey: page.page_key || '',
          text: textPreview(displayText),
          fullText: textValue(displayText).replace(/\s+$/g, ''),
          status,
          isDirty: Boolean(draft.dirty),
          timing: sceneTimingLabel(page),
          subtitleCount: Array.isArray(page.subtitle_chunks) ? page.subtitle_chunks.length : 0,
          thumbnailUrl,
          backgroundUrl,
          customBackgroundUrl: sceneSettings.customUrl,
          backgroundMode: sceneSettings.backgroundMode,
          backgroundFit: sceneSettings.backgroundFit,
          textScale: sceneSettings.textScale,
          sourceType: sceneSettings.sourceType,
          hasOriginalBackground: sceneSettings.hasOriginal,
          hasCustomBackground: sceneSettings.hasCustom,
          hasSourceBackground: sceneSettings.hasSource,
          sourceBackgroundAvailable: sceneSettings.sourceBackgroundAvailable,
          sourceBackgroundWarnings: sceneSettings.sourceWarnings,
          highlightEnabled: sceneSettings.highlightEnabled,
          highlightStyle: sceneSettings.highlightStyle,
          highlightDetector: sceneSettings.highlightDetector,
          highlightPreviewUrl: sceneSettings.highlightPreviewUrl,
          draftBackgroundDirty: Boolean(page?.draft_background_dirty || page?.draft_scene_dirty),
          moderationWarning: moderationSlideWarnings[key] || null,
          page,
        };
      });
    }

    const draftBlocks = String(editorCanvas || '')
      .split(/\n+/)
      .map((value) => value.trim())
      .filter(Boolean)
      .slice(0, 10);

    if (draftBlocks.length > 0) {
      return draftBlocks.map((text, index) => ({
        id: `draft-${index + 1}`,
        key: `draft-${index + 1}`,
        index,
        type: 'draft',
        label: `Draft Scene ${index + 1}`,
        text: textPreview(text),
        fullText: text,
        status: 'draft',
        isDirty: false,
        timing: 'Draft only',
        subtitleCount: 0,
        thumbnailUrl: '',
      }));
    }

    return Array.from({ length: 4 }, (_, index) => ({
      id: `placeholder-${index + 1}`,
      key: `placeholder-${index + 1}`,
      index,
      type: 'placeholder',
      label: `Scene ${index + 1}`,
      text: 'Import a source file or select a rendered lesson transcript',
      fullText: 'Import a source file or select a rendered lesson transcript',
      status: 'draft',
      isDirty: false,
      timing: 'Not created',
      subtitleCount: 0,
      thumbnailUrl: '',
    }));
  }, [editorCanvas, moderationSlideWarnings, sceneDraftStatus, transcriptPages]);

  const sceneKeysSignature = useMemo(
    () => sceneItems.map((scene) => scene.key).join('|'),
    [sceneItems],
  );

  useEffect(() => {
    if (!sceneItems.length) {
      setSelectedPageKey('');
      setSelectedPageIndex(0);
      return;
    }

    const currentIndex = sceneItems.findIndex((scene) => scene.key === selectedPageKey);
    if (currentIndex >= 0) {
      setSelectedPageIndex(currentIndex);
      return;
    }

    setSelectedPageKey(sceneItems[0].key);
    setSelectedPageIndex(0);
  }, [sceneItems, sceneKeysSignature, selectedPageKey]);

  const selectedScene = useMemo(() => {
    if (!sceneItems.length) return null;
    return sceneItems.find((scene) => scene.key === selectedPageKey) || sceneItems[selectedPageIndex] || sceneItems[0];
  }, [sceneItems, selectedPageIndex, selectedPageKey]);

  const selectedSceneFullText = textValue(selectedScene?.fullText || selectedScene?.text);
  const selectedSceneMode = selectedScene?.backgroundMode || 'original';
  const selectedSceneFit = selectedScene?.backgroundFit || 'contain';
  const selectedSceneTextScale = selectedScene?.textScale ?? 1;
  const selectedSceneBackgroundUrl = selectedScene?.backgroundUrl || '';
  const selectedSceneCustomBackgroundUrl = selectedScene?.customBackgroundUrl || '';
  const selectedSceneSourceBackgroundAvailable = Boolean(selectedScene?.sourceBackgroundAvailable);
  const selectedSceneHasCustomBackground = Boolean(selectedScene?.hasCustomBackground);
  const selectedSceneOriginalAvailable = selectedScene?.sourceType !== 'txt'
    || Boolean(selectedScene?.hasOriginalBackground);
  const selectedSceneSourceBackgroundMessage = selectedScene?.sourceType === 'pptx'
    ? 'Source Background is not available for this slide.'
    : 'Source Background is currently available for PPTX lessons only.';
  const selectedSceneSourceWarnings = Array.isArray(selectedScene?.sourceBackgroundWarnings)
    ? selectedScene.sourceBackgroundWarnings
    : [];
  const selectedSceneHighlightEnabled = Boolean(selectedScene?.highlightEnabled);
  const selectedSceneHighlightStyle = selectedScene?.highlightStyle || 'none';
  const selectedSceneActiveHighlightStyle = selectedSceneHighlightEnabled ? selectedSceneHighlightStyle : 'none';
  const selectedSceneHighlightDetector = selectedScene?.highlightDetector || 'auto';
  const selectedSceneBackgroundWarning = selectedScene?.key
    ? moderationBackgroundWarnings[selectedScene.key] || null
    : null;
  const selectedSceneBackgroundWarningFlagged = moderationWarningIsFlagged(selectedSceneBackgroundWarning);
  const selectedSceneBackgroundWarningPending = moderationWarningIsPending(selectedSceneBackgroundWarning);
  const selectedSceneHasRenderDependencyWarning = selectedSceneSourceWarnings.some((warning) => (
    warning === 'slide_render_dependency_missing_libreoffice'
    || warning === 'slide_render_dependency_missing_pdftoppm'
    || warning === 'original_fidelity_reconstructed'
    || warning === 'source_background_reconstructed'
    || warning === 'source_background_generation_failed'
  ));
  const selectedSceneTextDirection = isProbablyRtlText(selectedSceneFullText) ? 'rtl' : 'ltr';
  const selectedSceneTextLayout = useMemo(
    () => scenePreviewTextLayout(selectedSceneTextScale, selectedSceneFullText),
    [selectedSceneFullText, selectedSceneTextScale],
  );
  useEffect(() => {
    let cancelled = false;
    const nextUrl = textValue(selectedScene?.highlightPreviewUrl);
    setHighlightPreviewMessage('');
    if (!nextUrl) {
      if (highlightPreviewObjectUrlRef.current) {
        URL.revokeObjectURL(highlightPreviewObjectUrlRef.current);
        highlightPreviewObjectUrlRef.current = '';
      }
      setHighlightPreviewImageUrl('');
      return () => {};
    }
    (async () => {
      try {
        const blobUrl = await fetchAuthenticatedAssetBlobUrl(nextUrl);
        if (cancelled) {
          if (blobUrl) URL.revokeObjectURL(blobUrl);
          return;
        }
        if (highlightPreviewObjectUrlRef.current) {
          URL.revokeObjectURL(highlightPreviewObjectUrlRef.current);
        }
        highlightPreviewObjectUrlRef.current = blobUrl;
        setHighlightPreviewImageUrl(blobUrl);
      } catch {
        if (!cancelled) setHighlightPreviewImageUrl('');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedScene?.key, selectedScene?.highlightPreviewUrl]);

  useEffect(() => {
    let cancelled = false;
    const nextUrl = textValue(selectedSceneBackgroundUrl);
    if (!nextUrl) {
      if (selectedSceneBackgroundObjectUrlRef.current) {
        URL.revokeObjectURL(selectedSceneBackgroundObjectUrlRef.current);
        selectedSceneBackgroundObjectUrlRef.current = '';
      }
      setSelectedSceneBackgroundImageUrl('');
      return () => {};
    }
    (async () => {
      try {
        const blobUrl = await fetchAuthenticatedAssetBlobUrl(nextUrl);
        if (cancelled) {
          if (blobUrl) URL.revokeObjectURL(blobUrl);
          return;
        }
        if (selectedSceneBackgroundObjectUrlRef.current) {
          URL.revokeObjectURL(selectedSceneBackgroundObjectUrlRef.current);
        }
        selectedSceneBackgroundObjectUrlRef.current = blobUrl;
        setSelectedSceneBackgroundImageUrl(blobUrl);
      } catch {
        if (!cancelled) setSelectedSceneBackgroundImageUrl('');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedSceneBackgroundUrl, fetchAuthenticatedAssetBlobUrl]);

  const latestRenderStatus = activeRerenderStatus || selectedLesson?.latest_job || null;
  const avatarJobInFlight = ['queued', 'processing'].includes(avatarProcessingStatus(selectedLesson));
  const avatarOnlyRerenderDisabled = (
    !avatarFeatureEnabled
    || !selectedLesson
    || avatarRerendering
    || avatarJobInFlight
    || !projectRenderReady(selectedLesson)
    || !projectAvatarEnabled(selectedLesson)
  );

  const handleSelectScene = useCallback((scene, index) => {
    if (!scene) return;
    setSelectedPageKey(scene.key);
    setSelectedPageIndex(index);
  }, []);

  const handleSelectTranscriptPage = useCallback((page, index) => {
    const key = pageIdentity(page, index);
    const scene = sceneItems.find((item) => item.key === key) || sceneItems[index] || { key, index };
    handleSelectScene(scene, index);
  }, [handleSelectScene, sceneItems]);

  const handleSelectModerationFinding = useCallback((finding) => {
    if (isRealVisualIssue(finding)) {
      const target = getIssueEditorTarget(finding, transcriptPages);
      if ((target.type === 'background' || target.type === 'slide') && target.page) {
        handleSelectTranscriptPage(target.page, target.pageIndex);
        setActiveEditorPanel('slides');
        return;
      }
      if (target.type === 'cover') {
        setActiveEditorPanel('slides');
      }
      return;
    }
    if (!isTextModerationIssue(finding)) return;
    const pageIndex = transcriptPages.findIndex((page, index) => (
      findingMatchesTranscriptPage(finding, page, index)
    ));
    if (pageIndex >= 0) {
      handleSelectTranscriptPage(transcriptPages[pageIndex], pageIndex);
      setActiveEditorPanel('transcript');
    }
  }, [handleSelectTranscriptPage, transcriptPages]);

  const handleTranscriptPagesUpdated = useCallback((updatedPages) => {
    const normalized = Array.isArray(updatedPages) ? updatedPages : [];
    setTranscriptPages(normalized);
  }, []);

  const replaceTranscriptPage = useCallback((updatedPage) => {
    if (!updatedPage?.id) return;
    setTranscriptPages((previous) => previous.map((page) => (
      page.id === updatedPage.id ? updatedPage : page
    )));
  }, []);

  const handleScenePatch = useCallback(async (patch, message = 'Scene settings updated.') => {
    if (readOnlyReview || !selectedLesson?.id || !selectedScene?.page?.id) return;
    setSceneActionBusy('scene');
    setSceneActionError('');
    setSceneActionMessage('');
    try {
      const payload = await updateTranscriptPageScene(selectedLesson.id, selectedScene.page.id, patch);
      invalidateSelectedLessonCache(selectedLesson.id);
      replaceTranscriptPage(payload?.page);
      if (payload?.has_draft) {
        setSelectedLessonDraftMetadata(payload?.draft_metadata || {});
      }
      setSceneActionMessage(message);
    } catch (err) {
      setSceneActionError(err.message || 'Could not update scene settings.');
    } finally {
      setSceneActionBusy('');
    }
  }, [invalidateSelectedLessonCache, readOnlyReview, replaceTranscriptPage, selectedLesson?.id, selectedScene?.page]);

  const handleSceneModeChange = useCallback((event) => {
    const nextMode = event.target.value;
    if (nextMode === 'source_background' && !selectedSceneSourceBackgroundAvailable) {
      setSceneActionMessage('');
      setSceneActionError(selectedSceneSourceBackgroundMessage);
      return;
    }
    if (nextMode === 'original' && !selectedSceneOriginalAvailable) {
      setSceneActionMessage('');
      setSceneActionError('Original mode is not available for this source.');
      return;
    }
    if (nextMode === 'custom' && !selectedSceneHasCustomBackground) {
      setSceneActionMessage('');
      setSceneActionError('Upload/select a custom background first.');
      return;
    }
    handleScenePatch({ background_mode: nextMode }, 'Background mode updated.');
  }, [handleScenePatch, selectedSceneHasCustomBackground, selectedSceneOriginalAvailable, selectedSceneSourceBackgroundAvailable, selectedSceneSourceBackgroundMessage]);

  const handleHighlightPreview = useCallback(async () => {
    if (readOnlyReview || !selectedLesson?.id || !selectedScene?.page?.id || highlightPreviewBusy) return;
    setHighlightPreviewBusy(true);
    setHighlightPreviewMessage('');
    setSceneActionError('');
    try {
      const style = selectedSceneHighlightEnabled ? selectedSceneHighlightStyle : 'none';
      const payload = await previewTranscriptPageHighlight(
        selectedLesson.id,
        selectedScene.page.id,
        {
          style,
          detector: selectedSceneHighlightDetector || 'auto',
          draft_only: true,
          forensic_debug: false,
        },
      );
      if (payload?.page) {
        replaceTranscriptPage(payload.page);
      }
      const previewUrl = textValue(payload?.preview_image_url || payload?.page?.editor_document?.scene?.highlight_preview_url);
      if (previewUrl) {
        const blobUrl = await fetchAuthenticatedAssetBlobUrl(previewUrl);
        if (highlightPreviewObjectUrlRef.current) {
          URL.revokeObjectURL(highlightPreviewObjectUrlRef.current);
        }
        highlightPreviewObjectUrlRef.current = blobUrl;
        setHighlightPreviewImageUrl(blobUrl);
      } else {
        setHighlightPreviewImageUrl('');
      }
      if (payload?.fallback_used) {
        setHighlightPreviewMessage('Highlight preview generated with fallback.');
      } else {
        setHighlightPreviewMessage('Highlight preview generated.');
      }
    } catch (err) {
      setHighlightPreviewMessage('');
      setSceneActionError(err.message || 'Highlight preview failed.');
    } finally {
      setHighlightPreviewBusy(false);
    }
  }, [
    highlightPreviewBusy,
    replaceTranscriptPage,
    selectedLesson?.id,
    selectedScene?.page?.id,
    selectedSceneHighlightDetector,
    selectedSceneHighlightEnabled,
    selectedSceneHighlightStyle,
    fetchAuthenticatedAssetBlobUrl,
    readOnlyReview,
  ]);

  const handleSceneBackgroundUpload = useCallback(async (file) => {
    if (readOnlyReview || !file || !selectedLesson?.id || !selectedScene?.page?.id) return;
    setSceneActionBusy('background');
    setSceneActionError('');
    setSceneActionMessage('');
    try {
      const payload = await uploadTranscriptPageBackground(selectedLesson.id, selectedScene.page.id, file, {
        backgroundFit: selectedSceneFit,
        textScale: selectedSceneTextScale,
      });
      invalidateSelectedLessonCache(selectedLesson.id);
      replaceTranscriptPage(payload?.page);
      if (payload?.has_draft) {
        setSelectedLessonDraftMetadata(payload?.draft_metadata || {});
      }
      await refreshSelectedLessonState(selectedLesson.id, { showLoading: false, bypassCache: true });
      setSceneActionMessage('Draft background saved. Public background is unchanged until Save & Rerender succeeds.');
    } catch (err) {
      setSceneActionError(err.message || 'Could not upload slide background.');
    } finally {
      setSceneActionBusy('');
    }
  }, [invalidateSelectedLessonCache, readOnlyReview, refreshSelectedLessonState, replaceTranscriptPage, selectedLesson?.id, selectedScene?.page, selectedSceneFit, selectedSceneTextScale]);

  const handleSceneBackgroundRemove = useCallback(async () => {
    if (readOnlyReview || !selectedLesson?.id || !selectedScene?.page?.id || !selectedSceneHasCustomBackground) return;
    setSceneActionBusy('background-remove');
    setSceneActionError('');
    setSceneActionMessage('');
    try {
      const payload = await removeTranscriptPageBackground(selectedLesson.id, selectedScene.page.id, { draftOnly: true });
      invalidateSelectedLessonCache(selectedLesson.id);
      replaceTranscriptPage(payload?.page);
      if (payload?.has_draft) {
        setSelectedLessonDraftMetadata(payload?.draft_metadata || {});
      }
      await refreshSelectedLessonState(selectedLesson.id, { showLoading: false, bypassCache: true });
      setSceneActionMessage(payload?.message || 'Draft custom background removed. Public background is unchanged until Save & Rerender succeeds.');
    } catch (err) {
      setSceneActionError(err.message || 'Could not remove slide background.');
    } finally {
      setSceneActionBusy('');
    }
  }, [invalidateSelectedLessonCache, readOnlyReview, refreshSelectedLessonState, replaceTranscriptPage, selectedLesson?.id, selectedScene?.page, selectedSceneHasCustomBackground]);

  const handleApplyBackgroundToAll = useCallback(async () => {
    if (readOnlyReview || !selectedLesson?.id || !selectedScene?.page?.id) return;
    if (selectedSceneMode === 'source_background' && !selectedSceneSourceBackgroundAvailable) {
      setSceneActionMessage('');
      setSceneActionError(selectedSceneSourceBackgroundMessage);
      return;
    }
    if (selectedSceneMode === 'original' && !selectedSceneOriginalAvailable) {
      setSceneActionMessage('');
      setSceneActionError('Original mode is not available for this source.');
      return;
    }
    if (selectedSceneMode === 'custom' && !selectedSceneHasCustomBackground) {
      setSceneActionMessage('');
      setSceneActionError('Upload/select a custom background first.');
      return;
    }
    setSceneActionBusy('apply-all');
    setSceneActionError('');
    setSceneActionMessage('');
    try {
      const payload = await applyProjectBackgroundToAll(selectedLesson.id, {
        source_page_id: selectedScene.page.id,
        background_mode: selectedSceneMode,
        background_fit: selectedSceneFit,
        text_scale: selectedSceneTextScale,
      });
      invalidateSelectedLessonCache(selectedLesson.id);
      const nextPages = Array.isArray(payload?.pages)
        ? payload.pages
        : await refreshProjectTranscript(selectedLesson.id, { showLoading: false, preserveOnError: true });
      handleTranscriptPagesUpdated(nextPages);
      if (payload?.has_draft) {
        setSelectedLessonDraftMetadata(payload?.draft_metadata || {});
      }
      if (Array.isArray(nextPages) && nextPages.length > 0) {
        const nextIndex = nextPages.findIndex((page) => page.id === selectedScene.page.id);
        const selectedIndex = nextIndex >= 0 ? nextIndex : Math.min(selectedPageIndex, nextPages.length - 1);
        const nextPage = nextPages[selectedIndex];
        setSelectedPageIndex(selectedIndex);
        setSelectedPageKey(pageIdentity(nextPage, selectedIndex));
      }
      await refreshSelectedLessonState(selectedLesson.id, { showLoading: false, bypassCache: true });
      setSceneActionMessage('Draft background settings applied to all slides.');
    } catch (err) {
      setSceneActionError(err.message || 'Could not apply background settings to all slides.');
    } finally {
      setSceneActionBusy('');
    }
  }, [handleTranscriptPagesUpdated, invalidateSelectedLessonCache, readOnlyReview, refreshProjectTranscript, refreshSelectedLessonState, selectedLesson?.id, selectedPageIndex, selectedScene?.page, selectedSceneFit, selectedSceneHasCustomBackground, selectedSceneMode, selectedSceneOriginalAvailable, selectedSceneSourceBackgroundAvailable, selectedSceneSourceBackgroundMessage, selectedSceneTextScale]);

  const handleCoverUpload = useCallback(async (file) => {
    if (readOnlyReview || !file || !selectedLesson?.id) return;
    setSceneActionBusy('cover');
    setSceneActionError('');
    setSceneActionMessage('');
    try {
      const updatedProject = await uploadProjectCover(selectedLesson.id, file, { draftOnly: true });
      invalidateSelectedLessonCache(selectedLesson.id);
      const cacheToken = Date.now();
      const nextProject = {
        ...updatedProject,
      };
      if (updatedProject?.draft_cover_url) {
        nextProject.draft_cover_url = cacheBustedMediaUrl(updatedProject.draft_cover_url, cacheToken);
      }
      if (updatedProject?.draft_thumbnail_url) {
        nextProject.draft_thumbnail_url = cacheBustedMediaUrl(updatedProject.draft_thumbnail_url, cacheToken);
      }
      if (updatedProject?.cover_url) {
        nextProject.cover_url = cacheBustedMediaUrl(updatedProject.cover_url, cacheToken);
      }
      if (updatedProject?.thumbnail_url) {
        nextProject.thumbnail_url = cacheBustedMediaUrl(updatedProject.thumbnail_url, cacheToken);
      }
      handleProjectUpdated(nextProject);
      setSelectedLessonDraftMetadata(updatedProject?.draft_metadata || {});
      setSceneActionMessage(updatedProject?.message || 'Draft cover saved. Public cover is unchanged until Save changes succeeds.');
    } catch (err) {
      setSceneActionError(err.message || 'Could not update lesson cover.');
    } finally {
      setSceneActionBusy('');
    }
  }, [handleProjectUpdated, invalidateSelectedLessonCache, readOnlyReview, selectedLesson?.id]);

  const handleCoverRemove = useCallback(async () => {
    if (readOnlyReview || !selectedLesson?.id || !hasSelectedLessonCover) return;
    setSceneActionBusy('cover-remove');
    setSceneActionError('');
    setSceneActionMessage('');
    try {
      const updatedProject = await removeProjectCover(selectedLesson.id, { draftOnly: true });
      invalidateSelectedLessonCache(selectedLesson.id);
      const cacheToken = Date.now();
      const nextProject = {
        ...updatedProject,
        draft_cover_url: '',
        draft_thumbnail_url: '',
      };
      if (updatedProject?.cover_url) {
        nextProject.cover_url = cacheBustedMediaUrl(updatedProject.cover_url, cacheToken);
      }
      if (updatedProject?.thumbnail_url) {
        nextProject.thumbnail_url = cacheBustedMediaUrl(updatedProject.thumbnail_url, cacheToken);
      }
      handleProjectUpdated(nextProject);
      setSelectedLessonDraftMetadata(updatedProject?.draft_metadata || {});
      setSceneActionMessage(updatedProject?.message || 'Draft cover removal saved. Public cover is unchanged until Save changes succeeds.');
    } catch (err) {
      setSceneActionError(err.message || 'Could not remove lesson cover.');
    } finally {
      setSceneActionBusy('');
    }
  }, [handleProjectUpdated, hasSelectedLessonCover, invalidateSelectedLessonCache, readOnlyReview, selectedLesson?.id]);

  const handleDraftStatusChange = useCallback((nextStatus) => {
    setSceneDraftStatus((previous) => {
      const previousJson = JSON.stringify(previous || {});
      const nextJson = JSON.stringify(nextStatus || {});
      return previousJson === nextJson ? previous : (nextStatus || {});
    });
  }, []);

  const handlePreviewRerenderImpact = useCallback(async () => {
    if (readOnlyReview || !selectedLesson?.id || partialRenderPreviewBusy) return;
    setPartialRenderPreviewBusy(true);
    setPartialRenderPreviewError('');
    try {
      const payload = partialRenderPreviewPayload({
        transcriptPages,
        avatarFeatureEnabled,
        avatarEnabled,
        selectedLesson,
      });
      const prediction = await previewPartialRenderImpact(selectedLesson.id, payload);
      setPartialRenderPreview(prediction);
    } catch (err) {
      setPartialRenderPreviewError(err.message || 'Rerender impact preview is unavailable.');
    } finally {
      setPartialRenderPreviewBusy(false);
    }
  }, [
    avatarEnabled,
    avatarFeatureEnabled,
    partialRenderPreviewBusy,
    readOnlyReview,
    selectedLesson,
    transcriptPages,
  ]);

  const handleGlobalEditorSave = useCallback(async ({ triggerRerender = false } = {}) => {
    if (readOnlyReview) return;
    if (!selectedLesson?.id) {
      persistEditorDraft();
      return;
    }

    const shouldTriggerRerender = Boolean(triggerRerender && selectedLessonDirtyScope.requiresRerender);
    if (shouldTriggerRerender && selectedLessonDirtyScope.moderationMessage) {
      setGlobalEditorMessage('');
      setGlobalEditorError(selectedLessonDirtyScope.moderationMessage);
      return;
    }

    setGlobalEditorActionBusy(shouldTriggerRerender ? 'rerender' : 'save');
    setGlobalEditorMessage('');
    setGlobalEditorError('');

    try {
      if (!selectedLessonDirtyScope.hasChanges) {
        await refreshSelectedLessonState(selectedLesson.id, { showLoading: false, bypassCache: true });
        setGlobalEditorMessage('Lesson is already up to date.');
        return;
      }
      const hadTranscriptChanges = Boolean(transcriptEditorRef.current?.hasUnsavedChanges?.());
      const shouldPromoteNonRenderDraft = Boolean(
        !shouldTriggerRerender
        && selectedLessonDirtyScope.savedNonRenderDraftDirty
        && !selectedLessonDirtyScope.requiresRerender
      );
      const ttsResult = await ttsSettingsRef.current?.save?.();
      if (ttsResult?.id) {
        invalidateSelectedLessonCache(ttsResult.id);
        handleProjectUpdated(ttsResult);
      }

      const transcriptSaveOptions = { triggerRerender: shouldTriggerRerender };
      if (shouldTriggerRerender && avatarFeatureEnabled) {
        transcriptSaveOptions.renderWithAvatar = Boolean(avatarEnabled);
      }
      const transcriptResult = await transcriptEditorRef.current?.save?.(transcriptSaveOptions);
      invalidateSelectedLessonCache(selectedLesson.id);
      applyProjectModerationPayload(transcriptResult, 'not_scanned');
      let promoteResult = null;
      if (shouldPromoteNonRenderDraft) {
        promoteResult = await promoteProjectDraft(selectedLesson.id);
        invalidateSelectedLessonCache(selectedLesson.id);
        const cacheToken = Date.now();
        const nextProject = { ...promoteResult };
        if (promoteResult?.cover_url) {
          nextProject.cover_url = cacheBustedMediaUrl(promoteResult.cover_url, cacheToken);
        }
        if (promoteResult?.thumbnail_url) {
          nextProject.thumbnail_url = cacheBustedMediaUrl(promoteResult.thumbnail_url, cacheToken);
        }
        handleProjectUpdated(nextProject);
        applyProjectModerationPayload(promoteResult, 'not_scanned');
        setSelectedLessonDraftMetadata(promoteResult?.draft_metadata || {});
      }
      saveLessonNotes();
      await refreshSelectedLessonState(selectedLesson.id, { showLoading: false, bypassCache: true });
      setTranscriptDirty(false);
      setTtsDirty(false);
      if (hadTranscriptChanges) {
        if (intelligenceFeatureEnabled && activeEditorPanel === 'intelligence') {
          await handleAnalyzeLessonIntelligence(selectedLesson, { auto: true });
        } else if (intelligenceFeatureEnabled) {
          setLessonIntelligenceByProject((previous) => {
            const current = previous[selectedLesson.id];
            if (!current) return previous;
            return {
              ...previous,
              [selectedLesson.id]: {
                ...current,
                is_stale: true,
              },
            };
          });
        }
      }
      const transcriptMessage = textValue(transcriptResult?.message);
      const promoteMessage = textValue(promoteResult?.message);
      const rerenderStrategy = textValue(transcriptResult?.rerender_strategy);
      const nextMessage = shouldTriggerRerender
        ? transcriptMessage || (
          rerenderStrategy === 'none'
            ? 'Saved all changes. Video rerender not required.'
            : 'Saved all changes and queued rerender.'
        )
        : promoteMessage || transcriptMessage || 'Saved all changes.';
      setGlobalEditorMessage(nextMessage);
    } catch (err) {
      if (err?.details) {
        applyProjectModerationPayload(err.details, 'needs_admin_review');
        if (err.details?.id) {
          handleProjectUpdated(err.details);
        } else if (err.details?.project?.id) {
          handleProjectUpdated(err.details.project);
        }
        setSelectedLessonDraftMetadata(err.details?.draft_metadata || err.details?.project?.draft_metadata || selectedLessonDraftMetadata || {});
      }
      setGlobalEditorError(err.message || 'Could not save all editor changes.');
    } finally {
      setGlobalEditorActionBusy('');
    }
  }, [
    activeEditorPanel,
    applyProjectModerationPayload,
    avatarEnabled,
    avatarFeatureEnabled,
    handleAnalyzeLessonIntelligence,
    handleProjectUpdated,
    intelligenceFeatureEnabled,
    invalidateSelectedLessonCache,
    readOnlyReview,
    refreshSelectedLessonState,
    saveLessonNotes,
    selectedLesson,
    selectedLessonDraftMetadata,
    selectedLessonDirtyScope,
  ]);

  const handleDiscardChanges = useCallback(async () => {
    if (readOnlyReview || !selectedLesson?.id || globalEditorActionBusy) return;
    if (!selectedLessonDirtyScope.hasChanges) {
      setGlobalEditorError('');
      setGlobalEditorMessage('No editor changes to discard.');
      return;
    }
    if (!window.confirm('Discard all editor changes and return to the current saved lesson state?')) return;

    setGlobalEditorActionBusy('discard');
    setGlobalEditorMessage('');
    setGlobalEditorError('');

    try {
      let discardedBackendDraft = false;
      if (selectedLessonHasDraft) {
        const payload = await discardProjectDraft(selectedLesson.id);
        discardedBackendDraft = true;
        applyProjectModerationPayload(payload, 'not_scanned');
        if (payload?.project?.id) {
          handleProjectUpdated(payload.project);
        }
        if (Array.isArray(payload?.pages)) {
          setTranscriptPages(payload.pages);
        }
        setSelectedLessonDraftMetadata(payload?.has_draft ? (payload?.draft_metadata || {}) : {});
      }
      setLessonNotes(lessonNotesSavedValue);
      setTranscriptDirty(false);
      setTtsDirty(false);
      setEditorResetNonce((previous) => previous + 1);
      setSceneDraftStatus({});
      setActiveRerenderStatus(null);
      if (discardedBackendDraft) {
        invalidateSelectedLessonCache(selectedLesson.id);
        await refreshSelectedLessonState(selectedLesson.id, { showLoading: false, bypassCache: true });
      }
      setGlobalEditorMessage(discardedBackendDraft
        ? 'Draft discarded. Studio is showing the current public version.'
        : 'Local editor changes discarded.');
    } catch (err) {
      setGlobalEditorError(err.message || 'Could not discard editor changes.');
    } finally {
      setGlobalEditorActionBusy('');
    }
  }, [
    applyProjectModerationPayload,
    globalEditorActionBusy,
    handleProjectUpdated,
    invalidateSelectedLessonCache,
    lessonNotesSavedValue,
    readOnlyReview,
    refreshSelectedLessonState,
    selectedLesson?.id,
    selectedLessonDirtyScope.hasChanges,
    selectedLessonHasDraft,
  ]);

  const toggleSlideExpanded = useCallback((sceneKey) => {
    setExpandedSlideKeys((previous) => ({
      ...previous,
      [sceneKey]: !previous[sceneKey],
    }));
  }, []);

  const categoryOptions = useMemo(
    () => categories.map((item) => item?.name).filter(Boolean),
    [categories],
  );

  const editorPanelLabel = (panel) => {
    if (panel === 'tts') return 'TTS';
    if (panel === 'intelligence') return 'Intelligence';
    return panel.charAt(0).toUpperCase() + panel.slice(1);
  };

  const editorPanelIcon = (panel) => {
    if (panel === 'slides') return <LayoutPanelTop size={14} />;
    if (panel === 'moderation') return <Eye size={14} />;
    if (panel === 'intelligence') return <Sparkles size={14} />;
    if (panel === 'notes') return <FileText size={14} />;
    if (panel === 'tts') return <Volume2 size={14} />;
    return <BookOpenText size={14} />;
  };

  const publishFromEditor = async () => {
    if (!sourceFile) {
      setSubmitError('Select a lesson source file to create.');
      return;
    }

    const created = await handleCreateProject({
      file: sourceFile,
      coverFile,
      title: editorTitle,
      category: editorCategory,
      pauseSec,
      whiteboardModeAll,
      avatarEnabled: avatarFeatureEnabled && avatarEnabled,
    });

    if (!created) {
      return;
    }

    setSourceFile(null);
    setCoverFile(null);
    setStudioLocation('lessons', typeof created === 'number' ? created : selectedLesson?.id || null);
  };

  const handleCreateLessonFromModal = async (payload) => {
    const created = await handleCreateProject(payload);
    if (!created) return;
    setCreateModalOpen(false);
    setStudioLocation('lessons', typeof created === 'number' ? created : selectedLesson?.id || null);
  };

  if (!user) {
    return (
      <SurfaceCard elevated className="mx-auto max-w-2xl space-y-4 text-center">
        <p className="label-sm">Studio Access</p>
        <h1 className="headline-md text-[var(--text-primary)]">Sign In To Author Lessons</h1>
        <p className="body-md">
          The studio is available for authenticated teachers. Sign in to upload source files and manage rendering.
        </p>
        <div className="flex flex-wrap justify-center gap-3">
          <Button onClick={onLoginRequest}>
            <LogIn size={16} />
            <span>Sign In</span>
          </Button>
          <Button variant="secondary" onClick={() => navigate('/browse')}>
            <Sparkles size={16} />
            <span>Browse Public Content</span>
          </Button>
        </div>
      </SurfaceCard>
    );
  }

  if (!isStudioUser) {
    return (
      <SurfaceCard elevated className="mx-auto max-w-2xl space-y-4 text-center">
        <p className="label-sm">Studio Access</p>
        <h1 className="headline-md text-[var(--text-primary)]">Teacher Or Publisher Access Required</h1>
        <p className="body-md">
          Your account can browse and watch lessons, but Studio authoring is available only to teacher, publisher, or staff roles.
        </p>
        <div className="flex flex-wrap justify-center gap-3">
          <Button variant="secondary" onClick={() => navigate('/')}>
            <Sparkles size={16} />
            <span>Back To Discover</span>
          </Button>
        </div>
      </SurfaceCard>
    );
  }

  return (
    <div className="min-w-0 max-w-full space-y-5 overflow-x-hidden">
      <SurfaceCard className="token-surface-elevated flex min-w-0 max-w-full flex-wrap items-center justify-between gap-3 overflow-x-hidden">
        <div className="min-w-0">
          <p className="label-sm">Studio Workspace</p>
          <h1 className="headline-md mt-1 text-[var(--text-primary)]">
            {readOnlyReview ? 'Read-only Lesson Review' : 'Teacher Publishing Console'}
          </h1>
        </div>

        {!readOnlyReview && (
        <div className="inline-flex max-w-full flex-wrap rounded-full token-surface p-1">
          <button
            type="button"
            className={`focus-ring rounded-full px-4 py-2 text-sm font-medium transition ${
              studioView === 'lessons'
                ? 'bg-[var(--surface-container-highest)] text-[var(--accent-primary)]'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
            onClick={() => setStudioLocation('lessons')}
          >
            My Lessons
          </button>
          <button
            type="button"
            className={`focus-ring rounded-full px-4 py-2 text-sm font-medium transition ${
              studioView === 'editor'
                ? 'bg-[var(--surface-container-highest)] text-[var(--accent-primary)]'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
            onClick={() => setStudioLocation('editor')}
          >
            Studio Editor
          </button>
          <button
            type="button"
            className={`focus-ring rounded-full px-4 py-2 text-sm font-medium transition ${
              studioView === 'playlists'
                ? 'bg-[var(--surface-container-highest)] text-[var(--accent-primary)]'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
            onClick={() => setStudioLocation('playlists')}
          >
            Playlists
          </button>
        </div>
        )}
      </SurfaceCard>

      {readOnlyReview && (
        <>
          <SurfaceCard className="flex flex-wrap items-center justify-between gap-3 border border-[color:var(--status-info-fg)] bg-[color:var(--status-info-bg)] p-4">
            <div className="min-w-0">
              <p className="text-sm font-semibold text-[color:var(--status-info-fg)]">Admin review mode - read only</p>
              <p className="mt-1 break-words text-xs font-medium text-[color:var(--status-info-fg)]">
                {adminReviewContextLabel}
              </p>
            </div>
          </SurfaceCard>

          {selectedLesson && (
            <AdminReviewActionPanel
              response={adminReviewResponse}
              onResponseChange={setAdminReviewResponse}
              onAction={handleAdminReviewAction}
              onBack={handleBackToReviewContext}
              backLabel={adminReviewBackLabelText}
              contextLabel={adminReviewContext?.project_title || adminReviewContextLabel}
              contextError={adminReviewContextError}
              busy={adminReviewActionBusy}
              message={adminReviewActionMessage}
              error={adminReviewActionError}
            />
          )}
        </>
      )}

      {submitError && (
        <SurfaceCard className="rounded-2xl bg-[color:var(--feedback-danger-bg)] p-4">
          <p className="text-sm text-[color:var(--feedback-danger-fg)]">{submitError}</p>
        </SurfaceCard>
      )}

      {studioView === 'playlists' ? (
        <PlaylistManager projects={projects} />
      ) : studioView === 'lessons' ? (
        <section className="grid min-w-0 max-w-full gap-5 overflow-x-hidden xl:grid-cols-[minmax(0,1fr)_minmax(24rem,29rem)] 2xl:grid-cols-[minmax(0,1fr)_30rem]">
          <div className="min-w-0 space-y-5">
            <SurfaceCard elevated className="overflow-hidden p-0">
              <div
                className="relative min-h-[320px] overflow-hidden rounded-[1.5rem] bg-[var(--hero-fallback)] bg-cover bg-center sm:min-h-[360px]"
                style={selectedLessonCoverBackgroundUrl ? { backgroundImage: `url("${selectedLessonCoverBackgroundUrl}")` } : undefined}
              >
                <div className="absolute inset-0 bg-[linear-gradient(125deg,rgba(6,10,16,0.2)_0%,rgba(6,10,16,0.62)_60%,rgba(6,10,16,0.88)_100%)]" />
                <div className="relative z-10 flex h-full flex-col justify-end gap-4 px-5 py-6 sm:px-7 sm:py-8">
                  <p className="label-sm text-[color:var(--media-text-on-image)]">Selected Lesson</p>
                  <h2 className="headline-md text-[color:var(--media-text-on-image)]">
                    {selectedLesson?.title || 'No lesson selected'}
                  </h2>
                  <p className="max-w-2xl text-sm text-[color:var(--media-text-on-image)] opacity-90">
                    {selectedLesson?.description || 'Select a lesson from the right rail to inspect transcript, notes, and publishing metadata.'}
                  </p>

                  <div className="flex flex-wrap gap-2 text-xs text-[color:var(--media-text-on-image)] opacity-90">
                    <span className="rounded-full bg-[color:var(--media-pill-bg)] px-3 py-1.5">
                      {selectedLesson?.category_name || 'Uncategorized'}
                    </span>
                    <span className="rounded-full bg-[color:var(--media-pill-bg)] px-3 py-1.5">
                      {selectedLesson ? safeDateLabel(selectedLesson.created_at) : 'Recent'}
                    </span>
                    {selectedLesson && (
                      <span className={`rounded-full px-3 py-1.5 ${projectStatusTone(selectedLesson)}`}>
                        {projectStatusLabel(selectedLesson)}
                      </span>
                    )}
                    {selectedLesson && (
                      <span className={`rounded-full px-3 py-1.5 ${projectPublicationTone(selectedLesson)}`}>
                        {projectPublicationLabel(selectedLesson)}
                      </span>
                    )}
                    {selectedLesson && (
                      <span className={`rounded-full px-3 py-1.5 ${moderationStatusTone(projectModerationStatus(selectedLesson, selectedModeration))}`}>
                        Moderation: {moderationStatusLabel(projectModerationStatus(selectedLesson, selectedModeration))}
                      </span>
                    )}
                    {avatarFeatureEnabled && selectedLesson && (
                      <span className="rounded-full bg-[color:var(--media-pill-bg)] px-3 py-1.5">
                        {avatarStatusLabel(selectedLesson)}
                      </span>
                    )}
                  </div>

                  {avatarFeatureEnabled && selectedLesson && !readOnlyReview && (
                    <div className="space-y-3 border-y border-[var(--border-subtle)] py-3">
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-[var(--text-primary)]">Avatar</p>
                          <p className="text-xs text-[var(--text-secondary)]">{avatarStatusLabel(selectedLesson)}</p>
                        </div>
                        <label className="inline-flex items-center gap-3 text-sm font-medium text-[var(--text-primary)]">
                          <span>{avatarVisible(selectedLesson) ? 'Avatar visible' : 'Avatar hidden'}</span>
                          <input
                            type="checkbox"
                            className="sr-only"
                            checked={avatarVisible(selectedLesson)}
                            disabled={avatarVisibilitySaving}
                            onChange={(event) => handleAvatarVisibilityToggle(selectedLesson, event.target.checked)}
                          />
                          <span className={`relative h-6 w-11 rounded-full transition ${
                            avatarVisible(selectedLesson)
                              ? 'bg-[var(--accent-primary)]'
                              : 'bg-[var(--surface-container-highest)]'
                          }`}>
                            <span className={`absolute top-1 h-4 w-4 rounded-full bg-white transition ${
                              avatarVisible(selectedLesson) ? 'left-6' : 'left-1'
                            }`} />
                          </span>
                        </label>
                      </div>

                      <div className="flex flex-wrap items-center gap-2">
                        <Button variant="secondary" onClick={handleAvatarOnlyRerender} disabled={avatarOnlyRerenderDisabled}>
                          <RefreshCcw size={16} />
                          <span>{avatarRerendering ? 'Queueing' : 'Rerender avatar only'}</span>
                        </Button>
                      </div>

                      {avatarRerenderMessage && (
                        <p className="text-xs font-medium text-[var(--text-primary)]">{avatarRerenderMessage}</p>
                      )}
                    </div>
                  )}

                  <div className="flex flex-wrap gap-2">
                    {!readOnlyReview && (
                      <Button onClick={() => selectedLesson && openEditorForProject(selectedLesson)} disabled={!selectedLesson}>
                        <LayoutPanelTop size={16} />
                        <span>Open Lesson Workspace</span>
                      </Button>
                    )}
                    <Button
                      variant="secondary"
                      onClick={() => selectedLesson && openPreviewForProject(selectedLesson)}
                      disabled={!selectedLesson || (!readOnlyReview && !projectRenderReady(selectedLesson))}
                    >
                      <Eye size={16} />
                      <span>{readOnlyReview ? 'Open Review Watch' : selectedLesson?.is_published ? 'Preview In Watch' : 'Preview Draft'}</span>
                    </Button>
                    {selectedLesson && projectRenderReady(selectedLesson) && !readOnlyReview && (
                      <Button
                        variant={selectedLesson.is_published ? 'secondary' : 'primary'}
                        onClick={() => handlePublishToggle(selectedLesson, !selectedLesson.is_published)}
                        disabled={!selectedLesson.is_published && !projectCanPublishFromModeration(selectedLesson, selectedModeration)}
                      >
                        {selectedLesson.is_published ? <EyeOff size={16} /> : <Eye size={16} />}
                        <span>{selectedLesson.is_published ? 'Unpublish' : 'Publish'}</span>
                      </Button>
                    )}
                  </div>
                </div>
              </div>
            </SurfaceCard>

            <SurfaceCard className="space-y-4">
              <div className="rail-scroll flex gap-2 overflow-x-auto pb-1">
                {LESSON_TABS.map((tab) => {
                  const selected = activeTab === tab;
                  const label = tab === 'tts' ? 'TTS' : tab;
                  return (
                    <button
                      key={tab}
                      type="button"
                      onClick={() => setActiveTab(tab)}
                      className={`focus-ring rounded-full px-3 py-1.5 text-sm font-medium transition ${
                        tab === 'tts' ? '' : 'capitalize'
                      } ${
                        selected
                          ? 'bg-[var(--surface-container-highest)] text-[var(--accent-primary)]'
                          : 'token-surface text-[var(--text-secondary)]'
                      }`}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>

              {activeTab === 'overview' && (
                <div className="space-y-3">
                  <p className="title-lg text-[var(--text-primary)]">Lesson Overview</p>
                  {selectedLesson && (
                    <div ref={previewSectionRef} className="rounded-2xl token-surface p-3">
                      {!projectRenderReady(selectedLesson) ? (
                        <div className="space-y-2 text-sm text-[var(--text-secondary)]">
                          <p className="font-semibold text-[var(--text-primary)]">Render: {projectStatusLabel(selectedLesson)}</p>
                          <p>Preview available after render completes.</p>
                        </div>
                      ) : loadingPreview ? (
                        <p className="text-sm text-[var(--text-secondary)]">Loading preview...</p>
                      ) : previewLesson?.stream_url ? (
                        <div className="space-y-4">
                          <VideoStage
                            lesson={{ ...selectedLesson, ...previewLesson }}
                            subtitleTracks={previewSubtitleTracks}
                            onPlaybackTimeChange={() => {}}
                            videoRef={previewVideoRef}
                            asSurface={false}
                            avatarOverlayMode={avatarFeatureEnabled ? 'floating' : 'disabled'}
                            captionMissingLabel="Captions will appear after render completes."
                          />

                          <div className="border-t border-[var(--border-subtle)] pt-3">
                            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                              <div className="min-w-0">
                                <p className="text-sm font-semibold text-[var(--text-primary)]">Subtitle tracks</p>
                                <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                  {previewSubtitleSummary.labels.length > 0
                                    ? `Current tracks: ${previewSubtitleSummary.labels.join(', ')}`
                                    : 'No caption tracks loaded yet.'}
                                </p>
                                <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                  {previewSubtitleSummary.labels.length > 1
                                    ? `Caption tracks loaded: ${previewSubtitleSummary.labels.join(', ')}.`
                                    : 'Only original captions are available. Generate translated captions in Studio.'}
                                </p>
                                {subtitleGenerationMessage && (
                                  <p className="mt-2 text-xs font-medium text-[var(--text-primary)]">{subtitleGenerationMessage}</p>
                                )}
                              </div>
                              <div className="flex flex-col gap-2 sm:min-w-[18rem]">
                                <label className="text-xs font-medium text-[var(--text-secondary)]" htmlFor="studio-subtitle-language">
                                  Generate language
                                </label>
                                <div className="flex flex-col gap-2 sm:flex-row">
                                  <select
                                    id="studio-subtitle-language"
                                    value={selectedPreviewRequestLanguage?.code || ''}
                                    onChange={(event) => setPreviewRequestLanguageCode(event.target.value)}
                                    disabled={generatingSubtitleTrack || Boolean(pendingSubtitleGeneration) || missingPreviewSubtitleLanguages.length === 0}
                                    className="focus-ring h-10 min-w-0 flex-1 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-55"
                                  >
                                    {missingPreviewSubtitleLanguages.length > 0 ? (
                                      missingPreviewSubtitleLanguages.map((language) => (
                                        <option key={language.code} value={language.code}>
                                          {language.label}
                                        </option>
                                      ))
                                    ) : (
                                      <option value="">All supported languages available</option>
                                    )}
                                  </select>
                                  <Button
                                    variant={missingPreviewSubtitleLanguages.length > 0 ? 'primary' : 'secondary'}
                                    size="sm"
                                    onClick={handleGeneratePreviewSubtitles}
                                    disabled={generatingSubtitleTrack || Boolean(pendingSubtitleGeneration) || missingPreviewSubtitleLanguages.length === 0}
                                    className="shrink-0"
                                  >
                                    <Sparkles size={14} />
                                    <span>{generatingSubtitleTrack ? 'Generating...' : 'Generate'}</span>
                                  </Button>
                                </div>
                              </div>
                            </div>
                          </div>
                        </div>
                      ) : (
                        <div className="space-y-2 text-sm text-[var(--text-secondary)]">
                          <p>{previewError || 'Video preview is not available yet.'}</p>
                          <p>Preview available after render completes.</p>
                        </div>
                      )}
                    </div>
                  )}
                  <p className="body-md">
                    {selectedLesson?.description || 'No description has been authored yet for this lesson.'}
                  </p>
                  {selectedLesson && (
                    <div className="rounded-2xl token-surface p-3 text-sm text-[var(--text-secondary)]">
                      <p className="font-semibold text-[var(--text-primary)]">Visibility: {projectPublicationLabel(selectedLesson)}</p>
                      <p className="mt-1">
                        {!projectRenderReady(selectedLesson)
                          ? 'Publishing becomes available after the render finishes.'
                          : selectedLesson.is_published
                          ? 'Published ready lessons appear in the public Home and Browse catalog.'
                          : 'Draft lessons stay in Studio only until you publish them.'}
                      </p>
                    </div>
                  )}
                  <ModerationPanel
                    project={selectedLesson}
                    moderation={selectedModeration}
                    loading={loadingModeration}
                    error={moderationError}
                    actionBusy={moderationActionBusy}
                    reviewDialogOpen={reviewDialogOpen}
                    reviewMessage={reviewMessage}
                    onReviewMessageChange={setReviewMessage}
                    onRefresh={() => selectedLesson && refreshProjectModeration(selectedLesson.id)}
                    onRescan={() => handleModerationRescan(selectedLesson)}
                    onOpenReview={() => {
                      setModerationError('');
                      setReviewDialogOpen(true);
                    }}
                    onCloseReview={() => setReviewDialogOpen(false)}
                    onSubmitReview={() => handleRequestAdminReview(selectedLesson)}
                    onSelectFinding={handleSelectModerationFinding}
                  />
                </div>
              )}

              {activeTab === 'transcript' && (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="title-lg text-[var(--text-primary)]">Transcript</p>
                      <p className="text-xs text-[var(--text-secondary)]">
                        Read-only lesson transcript. Open Studio Editor to make persistent transcript changes.
                      </p>
                    </div>
                    {!readOnlyReview && (
                      <Button size="sm" onClick={() => selectedLesson && openEditorForProject(selectedLesson)} disabled={!selectedLesson}>
                        <LayoutPanelTop size={14} />
                        <span>Edit in Studio</span>
                      </Button>
                    )}
                  </div>

                  {loadingTranscript ? (
                    <p className="text-sm text-[var(--text-secondary)]">Loading transcript pages...</p>
                  ) : transcriptPages.length === 0 ? (
                    <p className="text-sm text-[var(--text-secondary)]">No transcript pages available yet.</p>
                  ) : (
                    <div className="space-y-2">
                      {transcriptPages.map((page, index) => {
                        const sceneKey = pageIdentity(page, index);
                        const expanded = Boolean(expandedSlideKeys[sceneKey]);
                        const narration = pageNarration(page);
                        return (
                          <article key={sceneKey} className="rounded-2xl token-surface p-3">
                            <div className="flex flex-wrap items-start justify-between gap-2">
                              <div>
                                <p className="font-medium text-[var(--text-primary)]">{sceneLabel(page, index)}</p>
                                <p className="text-xs text-[var(--text-secondary)]">
                                  {page.page_key ? `Page key: ${page.page_key}` : `Page ${index + 1}`}
                                </p>
                              </div>
                              <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${sceneStatusTone(sceneStatusFromPage(page))}`}>
                                {sceneStatusFromPage(page)}
                              </span>
                            </div>
                            <p className={`mt-2 whitespace-pre-wrap text-sm text-[var(--text-secondary)] ${expanded ? '' : 'line-clamp-3'}`}>
                              {narration || 'No narration text yet'}
                            </p>
                            {narration.length > 180 && (
                              <button
                                type="button"
                                onClick={() => toggleSlideExpanded(sceneKey)}
                                className="focus-ring mt-2 text-xs font-semibold text-[var(--accent-primary)]"
                              >
                                {expanded ? 'Show less' : 'Show more'}
                              </button>
                            )}
                          </article>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {activeTab === 'slides' && (
                <div className="space-y-3">
                  <div>
                    <p className="title-lg text-[var(--text-primary)]">Slide Summary</p>
                    <p className="text-xs text-[var(--text-secondary)]">
                      Click a slide to sync the scene rail, timeline, and transcript editor selection.
                    </p>
                  </div>
                  {loadingTranscript ? (
                    <p className="text-sm text-[var(--text-secondary)]">Loading transcript pages...</p>
                  ) : sceneItems.length === 0 ? (
                    <p className="text-sm text-[var(--text-secondary)]">No slide or transcript pages available yet.</p>
                  ) : (
                    <div className="rail-scroll flex gap-2 overflow-x-auto pb-2">
                      {sceneItems.map((scene, index) => {
                        const selected = scene.key === selectedScene?.key;
                        const expanded = Boolean(expandedSlideKeys[scene.key]);
                        const slideText = expanded ? scene.fullText : scene.text;
                        const hasModerationWarning = moderationWarningIsFlagged(scene.moderationWarning);
                        const hasModerationNotice = Boolean(scene.moderationWarning);
                        return (
                          <button
                            key={scene.key}
                            type="button"
                            onClick={() => handleSelectScene(scene, index)}
                            className={`focus-ring min-w-[13rem] rounded-2xl p-3 text-left transition ${
                              selected
                                ? `border ${hasModerationWarning ? 'border-[color:var(--status-warning-fg)]' : 'border-[color:rgba(208,188,255,0.55)]'} bg-[color:rgba(208,188,255,0.12)]`
                                : hasModerationWarning
                                  ? 'border border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)] hover:bg-[color:var(--hover-surface)]'
                                  : 'token-surface hover:bg-[color:var(--hover-surface)]'
                            }`}
                          >
                            <div className="flex items-start justify-between gap-2">
                              <p className="label-sm">{scene.label}</p>
                              <div className="flex shrink-0 flex-wrap justify-end gap-1">
                                {hasModerationNotice && (
                                  <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[0.64rem] font-semibold ${
                                    hasModerationWarning
                                      ? 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]'
                                      : 'bg-[color:var(--status-info-bg)] text-[color:var(--status-info-fg)]'
                                  }`}>
                                    <AlertTriangle size={11} />
                                    {hasModerationWarning ? 'Review' : 'Pending'}
                                  </span>
                                )}
                                <span className={`rounded-full px-2 py-0.5 text-[0.64rem] font-semibold ${sceneStatusTone(scene.status)}`}>
                                  {scene.status}
                                </span>
                              </div>
                            </div>
                            <p className={`mt-2 whitespace-pre-wrap text-sm text-[var(--text-secondary)] ${expanded ? '' : 'line-clamp-3'}`}>{slideText}</p>
                            {hasModerationNotice && (
                              <p className={`mt-2 text-xs font-semibold ${
                                hasModerationWarning ? 'text-[color:var(--status-warning-fg)]' : 'text-[color:var(--status-info-fg)]'
                              }`}>
                                {sceneModerationWarningMessage(scene.moderationWarning)}
                              </p>
                            )}
                            <p className="mt-2 text-[0.68rem] text-[var(--text-secondary)]">{scene.timing}</p>
                            {textValue(scene.fullText).length > textValue(scene.text).length && (
                              <span
                                role="button"
                                tabIndex={0}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  toggleSlideExpanded(scene.key);
                                }}
                                onKeyDown={(event) => {
                                  if (event.key === 'Enter' || event.key === ' ') {
                                    event.preventDefault();
                                    event.stopPropagation();
                                    toggleSlideExpanded(scene.key);
                                  }
                                }}
                                className="mt-2 inline-flex text-xs font-semibold text-[var(--accent-primary)]"
                              >
                                {expanded ? 'Show less' : 'Show more'}
                              </span>
                            )}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {activeTab === 'notes' && (
                <div className="space-y-3">
                  <p className="title-lg text-[var(--text-primary)]">Lesson Notes</p>
                  {lessonNotesLocalDraft && (
                    <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-[color:var(--status-warning-bg)] px-3 py-2 text-xs font-semibold text-[color:var(--status-warning-fg)]">
                      <span>Unsaved local draft found. Restore or discard?</span>
                      <span className="flex gap-2">
                        <Button size="sm" variant="secondary" onClick={restoreLessonNotesDraft}>Restore</Button>
                        <Button size="sm" variant="ghost" onClick={discardLessonNotesDraft}>Discard</Button>
                      </span>
                    </div>
                  )}
                  <textarea
                    value={lessonNotes}
                    onChange={(event) => {
                      if (lessonNotesLocalDraft) setLessonNotesLocalDraft(null);
                      setLessonNotes(event.target.value);
                    }}
                    placeholder="Track publishing notes, quality checks, and post-production comments..."
                    className="focus-ring min-h-[170px] w-full resize-y rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 text-sm text-[var(--text-primary)]"
                  />
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs text-[var(--text-secondary)]">{lessonNotesSavedAt || 'Not saved yet'}</p>
                    <Button size="sm" variant="secondary" onClick={saveLessonNotes}>
                      <Save size={14} />
                      <span>Save Notes</span>
                    </Button>
                  </div>
                </div>
              )}

              {activeTab === 'tts' && (
                <TtsSettingsPanel
                  project={selectedLesson}
                  transcriptPages={transcriptPages}
                  selectedPageKey={selectedPageKey}
                  onProjectUpdated={handleProjectUpdated}
                  onRerender={handleRerenderProject}
                />
              )}
            </SurfaceCard>
          </div>

          <aside className="min-w-0">
            <SurfaceCard
              onScroll={handleMyLessonsScroll}
              className="max-h-[72vh] min-w-0 space-y-3 overflow-y-auto overflow-x-hidden p-4"
            >
              <div className="flex items-center justify-between">
                <p className="label-sm">My Lessons</p>
                <span className="text-xs text-[var(--text-secondary)]">
                  {filteredProjects.length}
                  {projectPageMeta.totalCount !== null ? ` / ${projectPageMeta.totalCount}` : ''}
                </span>
              </div>

              {loadingProjects ? (
                <div className="space-y-3" aria-label="Loading lessons">
                  {Array.from({ length: 4 }, (_, index) => (
                    <div key={`lesson-skeleton-${index}`} className="rounded-2xl token-surface p-3">
                      <div className="visus-loading-sheen h-4 w-3/4 rounded-full bg-[color:var(--surface-container-high)]" />
                      <div className="visus-loading-sheen mt-2 h-3 w-1/3 rounded-full bg-[color:var(--surface-container-high)]" />
                      <div className="mt-3 flex gap-2">
                        <div className="visus-loading-sheen h-5 w-16 rounded-full bg-[color:var(--surface-container-high)]" />
                        <div className="visus-loading-sheen h-5 w-20 rounded-full bg-[color:var(--surface-container-high)]" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : projectsError && filteredProjects.length === 0 ? (
                <div className="rounded-2xl bg-[color:var(--feedback-danger-bg)] p-3 text-sm font-medium text-[color:var(--feedback-danger-fg)]">
                  {projectsError}
                </div>
              ) : (
                <>
                  {projectsError && (
                    <div className="rounded-2xl bg-[color:var(--feedback-danger-bg)] p-3 text-xs font-medium text-[color:var(--feedback-danger-fg)]">
                      {projectsError}
                    </div>
                  )}
                  {filteredProjects.length === 0 ? (
                    <p className="rounded-2xl token-surface p-3 text-sm text-[var(--text-secondary)]">
                      No lessons match the current search.
                    </p>
                  ) : filteredProjects.map((project) => {
                    const projectModeration = moderationByProject[project.id] || null;
                    return (
                    <article
                      key={project.id}
                      className={`max-w-full overflow-hidden rounded-2xl p-3 transition ${
                        project.id === selectedLesson?.id
                          ? 'border border-[color:rgba(208,188,255,0.3)] bg-[color:rgba(208,188,255,0.1)]'
                          : 'token-surface'
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => selectLesson(project)}
                        className="focus-ring min-w-0 max-w-full text-left"
                      >
                        <p className="title-lg line-clamp-2 max-w-full break-words text-[var(--text-primary)] [overflow-wrap:anywhere]">
                          {project.title || `Project #${project.id}`}
                        </p>
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">{safeDateLabel(project.created_at)}</p>
                        <div className="mt-2 flex min-w-0 flex-wrap gap-1.5 text-[0.68rem] font-semibold">
                          <span className={`max-w-full rounded-full px-2 py-0.5 ${projectStatusTone(project)}`}>
                            {projectStatusLabel(project)}
                          </span>
                          <span className={`max-w-full rounded-full px-2 py-0.5 ${projectPublicationTone(project)}`}>
                            {projectPublicationLabel(project)}
                          </span>
                          <span className={`max-w-full break-words rounded-full px-2 py-0.5 [overflow-wrap:anywhere] ${moderationStatusTone(projectModerationStatus(project, projectModeration))}`}>
                            Moderation: {moderationStatusLabel(projectModerationStatus(project, projectModeration))}
                          </span>
                          {avatarFeatureEnabled && (projectAvatarEnabled(project) || avatarProcessingStatus(project) !== 'none') && (
                            <span className="max-w-full break-words rounded-full bg-[color:var(--surface-muted)] px-2 py-0.5 text-[var(--text-secondary)] [overflow-wrap:anywhere]">
                              {avatarStatusLabel(project)}
                            </span>
                          )}
                        </div>
                      </button>

                      <div className="mt-3 flex min-w-0 flex-wrap gap-2">
                        {!readOnlyReview && (
                          <Button size="sm" onClick={() => openEditorForProject(project)}>
                            <BookOpenText size={14} />
                            <span>Open</span>
                          </Button>
                        )}
                        {(projectRenderReady(project) || readOnlyReview) && (
                          <Button variant="secondary" size="sm" onClick={() => openPreviewForProject(project)}>
                            <Eye size={14} />
                            <span>{readOnlyReview ? 'Review' : project.is_published ? 'Preview' : 'Draft Preview'}</span>
                          </Button>
                        )}
                        {!readOnlyReview && (
                          <Button variant="secondary" size="sm" onClick={() => handleRerenderProject(project)}>
                            <RefreshCcw size={14} />
                            <span>Rerender</span>
                          </Button>
                        )}
                        {projectRenderReady(project) && !readOnlyReview && (
                          <Button
                            variant={project.is_published ? 'secondary' : 'primary'}
                            size="sm"
                            onClick={() => handlePublishToggle(project, !project.is_published)}
                            disabled={!project.is_published && !projectCanPublishFromModeration(project, projectModeration)}
                          >
                            {project.is_published ? <EyeOff size={14} /> : <Eye size={14} />}
                            <span>{project.is_published ? 'Unpublish' : 'Publish'}</span>
                          </Button>
                        )}
                        {!readOnlyReview && (
                          <Button variant="ghost" size="sm" onClick={() => handleDeleteProject(project)}>
                            <Trash2 size={14} />
                            <span>Delete</span>
                          </Button>
                        )}
                      </div>
                    </article>
                    );
                  })}

                  {projectPageMeta.hasNext && (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={loadMoreProjects}
                      disabled={loadingMoreProjects}
                      className="w-full"
                    >
                      <span>{loadingMoreProjects ? 'Loading more...' : 'Load more lessons'}</span>
                    </Button>
                  )}
                  {loadingMoreProjects && (
                    <p className="text-center text-xs font-medium text-[var(--text-secondary)]">Loading more lessons...</p>
                  )}
                </>
              )}
            </SurfaceCard>
          </aside>
        </section>
      ) : (
        <>
          <section className="grid min-w-0 max-w-full gap-5 overflow-x-hidden xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div className="min-w-0 space-y-5">
              <SurfaceCard elevated className="space-y-4 p-4 sm:p-5">
                <div className="grid gap-3 md:grid-cols-2">
                  <label className="block text-sm text-[var(--text-secondary)]">
                    Lesson title
                    <input
                      value={editorTitle}
                      onChange={(event) => setEditorTitle(event.target.value)}
                      type="text"
                      readOnly={readOnlyReview}
                      disabled={readOnlyReview}
                      placeholder="The Quantum Physics Masterclass"
                      className="focus-ring mt-1 h-11 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 text-[var(--text-primary)]"
                    />
                  </label>

                  <label className="block text-sm text-[var(--text-secondary)]">
                    Category
                    <input
                      value={editorCategory}
                      onChange={(event) => setEditorCategory(event.target.value)}
                      list="studio-editor-categories"
                      readOnly={readOnlyReview}
                      disabled={readOnlyReview}
                      placeholder="AI Product, Design, Storytelling"
                      className="focus-ring mt-1 h-11 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 text-[var(--text-primary)]"
                    />
                    <datalist id="studio-editor-categories">
                      {categoryOptions.map((name) => (
                        <option key={name} value={name} />
                      ))}
                    </datalist>
                  </label>
                </div>

                {!selectedLesson && (
                  <div className="grid gap-3 rounded-2xl token-surface p-3 md:grid-cols-2">
                    <label className="block text-sm text-[var(--text-secondary)]">
                      Source file
                      <input
                        type="file"
                        accept={SOURCE_TYPES_ACCEPT}
                        onChange={(event) => setSourceFile(event.target.files?.[0] || null)}
                        className="focus-ring mt-1 block w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-2 text-sm text-[var(--text-primary)]"
                      />
                      {sourceFile && (
                        <span className="mt-1 inline-flex items-center gap-1 text-xs text-[var(--text-secondary)]">
                          <FileText size={12} />
                          {sourceFile.name}
                        </span>
                      )}
                    </label>

                    <label className="block text-sm text-[var(--text-secondary)]">
                      Cover image
                      <input
                        type="file"
                        accept="image/*"
                        onChange={(event) => setCoverFile(event.target.files?.[0] || null)}
                        className="focus-ring mt-1 block w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-2 text-sm text-[var(--text-primary)]"
                      />
                    </label>
                  </div>
                )}

                <div
                  className={`relative mx-auto overflow-hidden rounded-2xl ${
                    selectedSceneMode === 'whiteboard'
                      ? 'bg-white'
                      : 'bg-[var(--video-stage-bg)]'
                  }`}
                  style={{
                    aspectRatio: '3 / 2',
                    maxHeight: '72vh',
                    width: 'min(100%, calc(72vh * 3 / 2))',
                  }}
                >
                  {selectedSceneBackgroundImageUrl || (!selectedLesson && coverPreviewUrl) ? (
                    <img
                      src={selectedSceneBackgroundImageUrl || coverPreviewUrl}
                      alt="Selected scene preview"
                      className={`absolute inset-0 h-full w-full ${
                        selectedSceneMode === 'custom' ? 'opacity-90' : 'opacity-100'
                      }`}
                      style={{ objectFit: backgroundObjectFit(selectedSceneFit) }}
                    />
                  ) : (
                    <div className={`absolute inset-0 flex items-center justify-center ${
                      selectedSceneMode === 'whiteboard'
                        ? 'bg-white text-slate-500'
                        : 'bg-[var(--surface-container-high)] text-[var(--text-secondary)]'
                    }`}>
                      <div className="text-center">
                        <p className="text-5xl font-bold text-[var(--accent-primary)]">{selectedPageIndex + 1}</p>
                        <p className="mt-2 text-sm font-semibold">{selectedScene?.label || 'No scene selected'}</p>
                      </div>
                    </div>
                  )}
                  {selectedSceneMode !== 'whiteboard' && (
                    <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(5,8,14,0.02)_0%,rgba(5,8,14,0.2)_64%,rgba(5,8,14,0.42)_100%)]" />
                  )}

                  <div className={`absolute inset-x-5 top-5 flex flex-wrap items-center justify-between gap-2 text-xs ${
                    selectedSceneMode === 'whiteboard' ? 'text-slate-700' : 'text-white/85'
                  }`}>
                    <span className={`rounded-full px-3 py-1.5 ${
                      selectedSceneMode === 'whiteboard' ? 'bg-slate-100' : 'bg-black/35'
                    }`}>
                      {selectedScene?.label || 'No scene selected'}
                    </span>
                    <span className={`rounded-full px-3 py-1.5 ${sceneStatusTone(selectedScene?.status || 'draft')}`}>
                      {selectedScene?.status || 'draft'}
                    </span>
                    <span className={`rounded-full px-3 py-1.5 ${
                      selectedSceneMode === 'whiteboard' ? 'bg-slate-100' : 'bg-black/35'
                    }`}>
                      {sceneModeLabel(selectedSceneMode)}
                    </span>
                  </div>

                  {selectedSceneMode !== 'original' ? (
                    <div className="absolute inset-x-6 bottom-16 top-16 flex items-center justify-center text-left">
                      <div
                        className={`max-h-full w-full overflow-hidden rounded-2xl ${
                        selectedSceneMode === 'whiteboard'
                          ? 'bg-transparent text-slate-900'
                          : 'bg-black/45 text-white shadow-lg backdrop-blur-sm'
                      }`}
                        style={{
                          maxWidth: selectedSceneTextLayout.maxWidth,
                          padding: selectedSceneTextLayout.padding,
                        }}
                      >
                        <p
                          className={`whitespace-pre-wrap leading-snug ${
                            selectedSceneMode === 'whiteboard' ? 'text-slate-900' : 'text-white'
                          } ${
                            selectedSceneActiveHighlightStyle === 'bold'
                              ? 'font-extrabold tracking-[0.01em]'
                              : 'font-semibold'
                          } ${
                            selectedSceneActiveHighlightStyle === 'box'
                              ? `inline-block rounded-xl border-2 px-3 py-2 shadow-md ${
                                selectedSceneMode === 'whiteboard'
                                  ? 'border-slate-400 bg-white/90'
                                  : 'border-white/75 bg-black/35'
                              }`
                              : ''
                          }`}
                          dir={selectedSceneTextDirection}
                          style={{
                            direction: selectedSceneTextDirection,
                            fontSize: selectedSceneTextLayout.fontSize,
                            lineHeight: selectedSceneTextLayout.lineHeight,
                            textAlign: selectedSceneTextDirection === 'rtl' ? 'right' : 'left',
                          }}
                        >
                          {selectedSceneFullText || 'Select a transcript page or import a source file to start authoring scenes.'}
                        </p>
                      </div>
                    </div>
                  ) : (
                    <div className="absolute inset-x-5 bottom-14 flex justify-center">
                      <span className="max-w-[92%] rounded-full bg-black/55 px-3 py-1.5 text-center text-xs font-medium text-white shadow-sm backdrop-blur-sm">
                        Original mode displays the source screenshot. Source Background keeps slide design but replaces source text with editable text.
                      </span>
                    </div>
                  )}

                  <div className="absolute bottom-5 left-5 right-5 space-y-3">
                    <div className={`h-1 rounded-full ${selectedSceneMode === 'whiteboard' ? 'bg-slate-200' : 'bg-white/20'}`}>
                      <div
                        className="h-full rounded-full bg-[image:var(--accent-gradient)]"
                        style={{ width: `${sceneItems.length ? ((selectedPageIndex + 1) / sceneItems.length) * 100 : 0}%` }}
                      />
                    </div>
                    <div className={`flex items-center justify-between text-xs ${
                      selectedSceneMode === 'whiteboard' ? 'text-slate-600' : 'text-white/75'
                    }`}>
                      <span>{selectedScene?.timing || 'No timing yet'}</span>
                      <span>{sceneItems.length} scenes</span>
                    </div>
                  </div>
                </div>

                <div className="rounded-2xl border border-[color:rgba(73,68,84,0.15)] bg-[var(--surface-container-high)] p-3">
                  <div className="flex items-center justify-between">
                    <p className="label-sm">Timeline</p>
                    <span className="text-xs text-[var(--text-secondary)]">{sceneItems.length} blocks</span>
                  </div>
                  <div className="rail-scroll mt-3 flex gap-2 overflow-x-auto pb-2">
                    {sceneItems.map((scene, index) => {
                      const selected = scene.key === selectedScene?.key;
                      const isWhiteboard = scene.backgroundMode === 'whiteboard';
                      const hasModerationWarning = moderationWarningIsFlagged(scene.moderationWarning);
                      const hasModerationNotice = Boolean(scene.moderationWarning);
                      const modeTone = isWhiteboard
                        ? 'bg-white text-slate-800'
                        : scene.backgroundMode === 'custom' || scene.backgroundMode === 'source_background'
                          ? 'bg-[color:var(--status-info-bg)] text-[color:var(--status-info-fg)]'
                          : 'bg-[color:var(--surface-muted)] text-[var(--text-secondary)]';
                      return (
                        <button
                          key={scene.key}
                          type="button"
                          onClick={() => handleSelectScene(scene, index)}
                          className={`focus-ring min-w-[14rem] rounded-xl p-2 text-left transition ${
                            selected
                              ? `border ${hasModerationWarning ? 'border-[color:var(--status-warning-fg)]' : 'border-[color:rgba(208,188,255,0.55)]'} bg-[color:rgba(208,188,255,0.12)]`
                              : hasModerationWarning
                                ? 'border border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)] hover:bg-[color:var(--hover-surface)]'
                                : 'token-surface hover:bg-[color:var(--hover-surface)]'
                          }`}
                        >
                          <div
                            className={`relative aspect-video overflow-hidden rounded-lg ${
                              isWhiteboard ? 'bg-white' : 'bg-[var(--card-fallback)]'
                            }`}
                            style={!isWhiteboard && scene.thumbnailUrl ? {
                              backgroundImage: `url(${scene.thumbnailUrl})`,
                              backgroundSize: scene.backgroundFit === 'stretch' ? '100% 100%' : scene.backgroundFit || 'contain',
                              backgroundRepeat: 'no-repeat',
                              backgroundPosition: 'center',
                            } : undefined}
                          >
                            {!scene.thumbnailUrl && !isWhiteboard && (
                              <div className="flex h-full items-center justify-center text-2xl font-bold text-[var(--accent-primary)]">
                                {index + 1}
                              </div>
                            )}
                            <div className={`absolute inset-x-2 bottom-2 rounded-md px-2 py-1 ${
                              isWhiteboard ? 'bg-white/90 text-slate-800' : 'bg-black/45 text-white'
                            }`}>
                              <p className="line-clamp-2 text-[0.68rem] normal-case leading-snug tracking-normal">
                                {scene.text}
                              </p>
                            </div>
                          </div>
                          <div className="mt-2 flex items-start justify-between gap-2">
                            <span className="min-w-0 text-[0.68rem] font-semibold uppercase tracking-[0.1em] text-[var(--text-primary)]">
                              {scene.label}
                            </span>
                            <div className="flex shrink-0 flex-wrap justify-end gap-1">
                              {hasModerationNotice && (
                                <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[0.6rem] font-semibold ${
                                  hasModerationWarning
                                    ? 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]'
                                    : 'bg-[color:var(--status-info-bg)] text-[color:var(--status-info-fg)]'
                                }`}>
                                  <AlertTriangle size={10} />
                                  {hasModerationWarning ? 'Review' : 'Pending'}
                                </span>
                              )}
                              <span className={`rounded-full px-2 py-0.5 text-[0.6rem] font-semibold ${modeTone}`}>
                                {sceneModeLabel(scene.backgroundMode)}
                              </span>
                            </div>
                          </div>
                          {hasModerationNotice && (
                            <p className={`mt-2 text-[0.68rem] font-semibold ${
                              hasModerationWarning ? 'text-[color:var(--status-warning-fg)]' : 'text-[color:var(--status-info-fg)]'
                            }`}>
                              {sceneModerationWarningMessage(scene.moderationWarning)}
                            </p>
                          )}
                          <span className="mt-1 block truncate text-[0.68rem] text-[var(--text-secondary)]">{scene.timing}</span>
                        </button>
                      );
                    })}
                  </div>
                  <p className="mt-2 text-xs text-[var(--text-secondary)]">
                    Selected: {selectedScene?.label || 'No scene selected'}
                  </p>
                </div>
              </SurfaceCard>
            </div>

            <aside className="min-w-0 max-w-full">
              <SurfaceCard
                elevated
                className="flex min-h-[72vh] min-w-0 max-w-full flex-col gap-4 overflow-hidden xl:max-h-[calc(100vh-9rem)]"
              >
                <div className="flex shrink-0 flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Editor Workspace</h3>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                      {selectedLesson ? selectedLesson.title || 'Selected lesson' : 'Local draft'}
                    </p>
                  </div>
                  <div className="flex min-w-0 flex-wrap justify-end gap-2">
                    {readOnlyReview ? (
                      <span className="rounded-full bg-[color:var(--status-info-bg)] px-3 py-1.5 text-xs font-semibold text-[color:var(--status-info-fg)]">
                        Read only
                      </span>
                    ) : selectedLesson ? (
                      <>
                        <Button
                          size="sm"
                          variant={selectedLessonDirtyScope.canSaveChanges ? 'primary' : 'secondary'}
                          onClick={() => handleGlobalEditorSave({ triggerRerender: false })}
                          disabled={Boolean(globalEditorActionBusy)}
                          title={selectedLessonDirtyScope.canSaveChanges
                            ? (selectedLessonDirtyScope.hasChanges
                              ? 'Save draft changes without rerendering the video.'
                              : 'Refresh the selected lesson state.')
                            : selectedLessonDirtyScope.saveDisabledReason}
                        >
                          <Save size={14} />
                          <span>
                            {globalEditorActionBusy === 'save'
                              ? 'Saving...'
                              : 'Save changes'}
                          </span>
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={handleDiscardChanges}
                          disabled={
                            Boolean(globalEditorActionBusy)
                            || !selectedLessonDirtyScope.canDiscardChanges
                          }
                          title={selectedLessonDirtyScope.canDiscardChanges
                            ? 'Discard local and draft editor changes.'
                            : selectedLessonDirtyScope.discardDisabledReason}
                          className={selectedLessonDirtyScope.canDiscardChanges
                            ? 'border border-[color:var(--outline-variant)] shadow-[0_0_0_1px_rgba(107,56,212,0.15)]'
                            : ''}
                        >
                          <Trash2 size={14} />
                          <span>{globalEditorActionBusy === 'discard' ? 'Discarding...' : 'Discard changes'}</span>
                        </Button>
                        {avatarFeatureEnabled && (
                          <label className="inline-flex min-h-9 items-center gap-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-container-high)] px-2.5 py-1 text-xs font-semibold text-[var(--text-secondary)]">
                            <input
                              type="checkbox"
                              checked={avatarEnabled}
                              onChange={(event) => setAvatarEnabled(event.target.checked)}
                              disabled={Boolean(globalEditorActionBusy)}
                              aria-label="Render with avatar"
                            />
                            <span>Render with avatar</span>
                            <span className="rounded-full bg-[var(--surface-elevated)] px-2 py-0.5 text-[0.68rem] text-[var(--text-muted)]">
                              Next rerender
                            </span>
                          </label>
                        )}
                        <PreviewRerenderImpactButton
                          busy={partialRenderPreviewBusy}
                          disabled={Boolean(globalEditorActionBusy)}
                          onClick={handlePreviewRerenderImpact}
                        />
                        <Button
                          size="sm"
                          variant={selectedLessonDirtyScope.canSaveRerender ? 'primary' : 'secondary'}
                          onClick={() => handleGlobalEditorSave({ triggerRerender: true })}
                          disabled={
                            Boolean(globalEditorActionBusy)
                            || !selectedLessonDirtyScope.canSaveRerender
                          }
                          title={selectedLessonDirtyScope.canSaveRerender
                            ? 'Save changes and queue a video rerender.'
                            : selectedLessonDirtyScope.rerenderDisabledReason}
                        >
                          <RefreshCcw size={14} />
                          <span>{globalEditorActionBusy === 'rerender' ? 'Saving...' : 'Save & Rerender'}</span>
                        </Button>
                      </>
                    ) : (
                      <>
                        <Button size="sm" variant="secondary" onClick={persistEditorDraft}>
                          <Save size={14} />
                          <span>Save Local Draft</span>
                        </Button>
                        <Button size="sm" onClick={publishFromEditor} disabled={submitting || !sourceFile}>
                          <Upload size={14} />
                          <span>{submitting ? 'Creating...' : 'Create Lesson Draft'}</span>
                        </Button>
                      </>
                    )}
                  </div>
                </div>

                {selectedLesson && selectedLessonHasDraft && (
                  <p className="shrink-0 rounded-xl bg-[color:var(--status-warning-bg)] px-3 py-2 text-xs font-semibold text-[color:var(--status-warning-fg)]">
                    {selectedDraftStatusMessage}
                  </p>
                )}

                {(globalEditorMessage || globalEditorError || selectedLessonDirtyScope.moderationMessage || (!selectedLesson && editorSavedAtLabel)) && (
                  <p className={`shrink-0 rounded-xl px-3 py-2 text-xs font-semibold ${
                    globalEditorError
                      ? 'bg-[color:var(--feedback-danger-bg)] text-[color:var(--feedback-danger-fg)]'
                      : selectedLessonDirtyScope.moderationMessage
                        ? 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]'
                      : 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]'
                  }`}>
                    {globalEditorError || selectedLessonDirtyScope.moderationMessage || globalEditorMessage || editorSavedAtLabel}
                  </p>
                )}

                <PredictedRerenderImpactPanel prediction={partialRenderPreview} error={partialRenderPreviewError} />

                <RenderAnalysisPanel analysis={selectedLesson?.latest_render_analysis} />

                <div className="rail-scroll relative z-10 -mx-1 flex max-w-full shrink-0 gap-2 overflow-x-auto bg-[var(--bg-elevated)] px-1 py-1">
                  {visibleEditorPanels.map((panel) => {
                    const selected = activeEditorPanel === panel;
                    const hasModerationWarning = (
                      (panel === 'transcript' && Object.keys(moderationPageWarnings).length > 0)
                      || (panel === 'slides' && (
                        moderationWarningIsFlagged(moderationAssetWarnings.cover)
                        || Object.values(moderationBackgroundWarnings).some(moderationWarningIsFlagged)
                        || Object.values(moderationSlideWarnings).some(moderationWarningIsFlagged)
                      ))
                    );
                    return (
                      <button
                        key={panel}
                        type="button"
                        onClick={() => setActiveEditorPanel(panel)}
                        className={`focus-ring inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium transition ${
                          selected
                            ? `border border-[var(--outline-variant)] bg-[var(--surface-container-highest)] ${hasModerationWarning ? 'text-[color:var(--status-warning-fg)] ring-1 ring-inset ring-[color:var(--status-warning-fg)]' : 'text-[var(--accent-primary)]'}`
                            : hasModerationWarning
                              ? 'border border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)] hover:text-[color:var(--status-warning-fg)]'
                              : 'token-surface text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                        }`}
                      >
                        {editorPanelIcon(panel)}
                        <span>{editorPanelLabel(panel)}</span>
                        {hasModerationWarning && <AlertTriangle size={13} />}
                      </button>
                    );
                  })}
                </div>

                <div className="rail-scroll min-h-0 min-w-0 max-w-full flex-1 overflow-y-auto overflow-x-hidden px-1">
                  <div className={activeEditorPanel === 'transcript' ? 'space-y-3' : 'hidden'}>
                      {selectedLesson ? (
                        <TranscriptEditorPanel
                          key={`transcript-${selectedLesson.id}-${editorResetNonce}`}
                          ref={transcriptEditorRef}
                          project={selectedLesson}
                          pages={transcriptPages}
                          localDraftScope={studioLocalDraftScope}
                          loading={loadingTranscript}
                          selectedPageKey={selectedPageKey}
                          selectedPageIndex={selectedPageIndex}
                          moderationPageWarnings={moderationPageWarnings}
                          showLocalActions={false}
                          readOnly={readOnlyReview}
                          onSelectPage={handleSelectTranscriptPage}
                          onPagesUpdated={handleTranscriptPagesUpdated}
                          onProjectRefresh={() => selectedLesson && refreshSelectedLessonState(selectedLesson.id, { showLoading: false, bypassCache: true })}
                          onModerationUpdated={applyProjectModerationPayload}
                          onDraftStatusChange={handleDraftStatusChange}
                          onJobStatusChange={setActiveRerenderStatus}
                          onDirtyChange={setTranscriptDirty}
                        />
                      ) : (
                        <label className="block text-sm text-[var(--text-secondary)]">
                          Local editing canvas
                          <textarea
                            value={editorCanvas}
                            onChange={(event) => setEditorCanvas(event.target.value)}
                            placeholder="Draft narration, section summaries, and production notes..."
                            className="focus-ring mt-1 min-h-[420px] w-full resize-y rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-4 text-base leading-7 text-[var(--text-primary)]"
                          />
                          <span className="mt-1 block text-xs text-[var(--text-secondary)]">
                            Local drafts are not persisted to the backend until you create a lesson draft from a source file.
                          </span>
                        </label>
                      )}

                  </div>

                  <div className={activeEditorPanel === 'slides' ? 'space-y-3' : 'hidden'}>
                      <div>
                        <p className="title-lg text-[var(--text-primary)]">Slides</p>
                        <p className="text-xs text-[var(--text-secondary)]">Adjust the selected slide background and lesson cover. Select slides from the timeline below the preview.</p>
                      </div>
                      {selectedLesson && (
                        <div className={`space-y-3 rounded-2xl p-3 ${
                          coverModerationWarningFlagged
                            ? 'border border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)]'
                            : coverModerationWarningPending
                              ? 'border border-[color:var(--status-info-fg)] bg-[color:var(--status-info-bg)]'
                            : 'token-surface'
                        }`}>
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                              <p className="text-sm font-semibold text-[var(--text-primary)]">Lesson cover</p>
                              <p className="mt-1 text-xs text-[var(--text-secondary)]">Update the cover used on lesson cards.</p>
                              {hasDraftCover && (
                                <span className="mt-2 inline-flex rounded-md border border-[var(--border-subtle)] px-2 py-0.5 text-[0.68rem] font-semibold text-[var(--text-secondary)]">
                                  Draft cover
                                </span>
                              )}
                              {draftCoverRemoved && (
                                <span className="mt-2 inline-flex rounded-md border border-[var(--border-subtle)] px-2 py-0.5 text-[0.68rem] font-semibold text-[var(--text-secondary)]">
                                  Draft removal
                                </span>
                              )}
                              {hasDraftCover && !moderationAssetWarnings.cover && (
                                <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                  Draft cover saved. Public cover is unchanged until Save changes succeeds.
                                </p>
                              )}
                              {draftCoverRemoved && !moderationAssetWarnings.cover && (
                                <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                  Draft cover removal saved. Public cover is unchanged until Save changes succeeds.
                                </p>
                              )}
                              {moderationAssetWarnings.cover && (
                                <p className={`mt-2 inline-flex items-start gap-1.5 rounded-lg px-2 py-1.5 text-xs font-semibold ${
                                  coverModerationWarningPending
                                    ? 'bg-[color:var(--status-info-bg)] text-[color:var(--status-info-fg)]'
                                    : 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]'
                                }`}>
                                  <AlertTriangle size={12} />
                                  <span>
                                    {moderationAssetWarnings.cover?.state === 'pending'
                                      ? 'Visual scan pending'
                                      : visualScanUnavailableMessage(moderationAssetWarnings.cover)
                                        || 'This cover image was blocked by visual moderation. Replace it before publishing.'}
                                  </span>
                                </p>
                              )}
                              {!hasDraftCover && !moderationAssetWarnings.cover && coverVisualNeedsRecheck && (
                                <p className="mt-1 text-xs font-semibold text-[color:var(--status-info-fg)]">
                                  {textValue(selectedVisualMarker?.message) || 'Cover changed - visual recheck needed.'}
                                </p>
                              )}
                            </div>
                            <div className="flex gap-2">
                              {selectedLesson.cover_url && (
                                <div className="space-y-1 text-right">
                                  <AuthenticatedMediaThumbnail
                                    src={selectedLesson.cover_url}
                                    alt="Public lesson cover"
                                    fallbackLabel="Cover unavailable"
                                  />
                                  {hasDraftCover && <span className="block text-[0.65rem] text-[var(--text-muted)]">Public</span>}
                                </div>
                              )}
                              {hasDraftCover && (
                                <div className="space-y-1 text-right">
                                  <AuthenticatedMediaThumbnail
                                    src={draftCoverUrl}
                                    alt="Draft lesson cover"
                                    fallbackLabel="Draft unavailable"
                                  />
                                  <span className="block text-[0.65rem] font-semibold text-[var(--text-secondary)]">Draft</span>
                                </div>
                              )}
                            </div>
                          </div>
                          {!readOnlyReview && (
                            <div className="flex flex-wrap items-end gap-2">
                              <label className="min-w-[12rem] flex-1 text-xs font-medium text-[var(--text-secondary)]">
                                Upload cover image
                                <input
                                  type="file"
                                  accept="image/*"
                                  onChange={(event) => {
                                    const file = event.target.files?.[0] || null;
                                    event.target.value = '';
                                    if (file) handleCoverUpload(file);
                                  }}
                                  disabled={Boolean(sceneActionBusy)}
                                  className="focus-ring mt-1 block w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-2 text-sm text-[var(--text-primary)]"
                                />
                              </label>
                              {hasSelectedLessonCover && (
                                <Button
                                  size="sm"
                                  variant="secondary"
                                  onClick={handleCoverRemove}
                                  disabled={Boolean(sceneActionBusy)}
                                >
                                  <X size={14} />
                                  <span>Remove cover</span>
                                </Button>
                              )}
                            </div>
                          )}
                        </div>
                      )}

                      {selectedScene?.page && (
                        <div className={`space-y-3 rounded-2xl p-3 ${
                          selectedSceneBackgroundWarningFlagged
                            ? 'border border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)]'
                            : selectedSceneBackgroundWarningPending
                              ? 'border border-[color:var(--status-info-fg)] bg-[color:var(--status-info-bg)]'
                            : 'token-surface'
                        }`}>
                          <div>
                            <p className="text-sm font-semibold text-[var(--text-primary)]">Scene background</p>
                            <p className="mt-1 text-xs text-[var(--text-secondary)]">
                              Use the exported source slide, a source background, a whiteboard, or a custom image for this page.
                            </p>
                            {selectedScene?.draftBackgroundDirty && (
                              <span className="mt-2 inline-flex rounded-md border border-[var(--border-subtle)] px-2 py-0.5 text-[0.68rem] font-semibold text-[var(--text-secondary)]">
                                Draft background
                              </span>
                            )}
                            {selectedSceneBackgroundWarning && (
                              <p className={`mt-2 inline-flex items-start gap-1.5 rounded-lg px-2 py-1.5 text-xs font-semibold ${
                                selectedSceneBackgroundWarningPending
                                  ? 'bg-[color:var(--status-info-bg)] text-[color:var(--status-info-fg)]'
                                  : 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]'
                              }`}>
                                <AlertTriangle size={12} />
                                <span>
                                  {selectedSceneBackgroundWarning?.state === 'pending'
                                    ? 'Visual scan pending'
                                    : visualScanUnavailableMessage(selectedSceneBackgroundWarning)
                                      || 'This scene background was blocked by visual moderation. Replace it before publishing.'}
                                </span>
                              </p>
                            )}
                          </div>

                          <label className="block text-xs font-medium text-[var(--text-secondary)]">
                            Mode
                            <select
                              value={selectedSceneMode}
                              onChange={handleSceneModeChange}
                              disabled={readOnlyReview || Boolean(sceneActionBusy)}
                              className="focus-ring mt-1 h-10 w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
                            >
                              <option value="original" disabled={!selectedSceneOriginalAvailable}>Original</option>
                              <option value="source_background" disabled={!selectedSceneSourceBackgroundAvailable}>Source Background</option>
                              <option value="whiteboard">Whiteboard</option>
                              <option value="custom" disabled={!selectedSceneHasCustomBackground}>Custom background</option>
                            </select>
                          </label>
                          {!selectedSceneSourceBackgroundAvailable && (
                            <p className="rounded-xl bg-[color:var(--status-info-bg)] px-3 py-2 text-xs text-[color:var(--status-info-fg)]">
                              {selectedSceneSourceBackgroundMessage}
                            </p>
                          )}
                          {!selectedSceneHasCustomBackground && (
                            <p className="rounded-xl bg-[color:var(--status-info-bg)] px-3 py-2 text-xs text-[color:var(--status-info-fg)]">
                              Upload/select a custom background first.
                            </p>
                          )}
                          {!selectedSceneOriginalAvailable && (
                            <p className="rounded-xl bg-[color:var(--status-info-bg)] px-3 py-2 text-xs text-[color:var(--status-info-fg)]">
                              Original mode is not available for this source.
                            </p>
                          )}
                          {selectedSceneMode === 'source_background' && selectedSceneSourceBackgroundAvailable && (
                            <p className="rounded-xl bg-[color:var(--status-info-bg)] px-3 py-2 text-xs text-[color:var(--status-info-fg)]">
                              Source Background keeps slide design but replaces source text with editable text.
                            </p>
                          )}
                          {selectedSceneHasRenderDependencyWarning && (
                            <p className="rounded-xl bg-[color:var(--status-warning-bg)] px-3 py-2 text-xs font-medium text-[color:var(--status-warning-fg)]">
                              High-fidelity slide rendering requires LibreOffice/Poppler. Current output may use fallback reconstruction.
                            </p>
                          )}

                          {selectedSceneHasCustomBackground && (
                            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-[var(--surface-container-high)] p-3">
                              <div className="flex min-w-0 items-center gap-3">
                                <AuthenticatedMediaThumbnail
                                  src={selectedSceneCustomBackgroundUrl}
                                  alt="Custom scene background"
                                  className="h-16 w-24 rounded-lg object-cover"
                                  fallbackLabel="Custom background unavailable"
                                />
                                <div className="min-w-0">
                                  <p className="text-xs font-semibold text-[var(--text-primary)]">Custom background</p>
                                  <p className="text-xs text-[var(--text-secondary)]">
                                    {selectedSceneMode === 'custom' ? 'Selected for this slide.' : 'Available for this slide.'}
                                  </p>
                                </div>
                              </div>
                              {!readOnlyReview && (
                                <Button
                                  size="sm"
                                  variant="secondary"
                                  onClick={handleSceneBackgroundRemove}
                                  disabled={Boolean(sceneActionBusy)}
                                >
                                  <X size={14} />
                                  <span>Remove</span>
                                </Button>
                              )}
                            </div>
                          )}

                          <label className="block text-xs font-medium text-[var(--text-secondary)]">
                            Background fit
                            <select
                              value={selectedSceneFit}
                              onChange={(event) => handleScenePatch({ background_fit: event.target.value }, 'Background fit updated.')}
                              disabled={readOnlyReview || Boolean(sceneActionBusy)}
                              className="focus-ring mt-1 h-10 w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
                            >
                              <option value="contain">Contain</option>
                              <option value="cover">Cover</option>
                              <option value="stretch">Stretch</option>
                            </select>
                          </label>

                          <label className="block text-xs font-medium text-[var(--text-secondary)]">
                            Text size
                            <input
                              type="range"
                              min={SCENE_TEXT_SCALE_MIN}
                              max={SCENE_TEXT_SCALE_MAX}
                              step="0.05"
                              value={selectedSceneTextScale}
                              onChange={(event) => handleScenePatch({ text_scale: Number(event.target.value) }, 'Text size updated.')}
                              disabled={readOnlyReview || Boolean(sceneActionBusy)}
                              className="mt-2 w-full"
                            />
                            <span className="mt-1 block text-[0.68rem]">{selectedSceneTextScale.toFixed(2)}x</span>
                          </label>

                          <div className="space-y-2 rounded-xl bg-[var(--surface-container-high)] p-3">
                            <p className="text-xs font-semibold text-[var(--text-secondary)]">Highlight preview</p>
                            <label className="inline-flex items-center gap-2 text-xs text-[var(--text-secondary)]">
                              <input
                                type="checkbox"
                                checked={selectedSceneHighlightEnabled}
                                onChange={(event) => handleScenePatch({ highlight_enabled: event.target.checked }, 'Highlight setting updated.')}
                                disabled={readOnlyReview || Boolean(sceneActionBusy) || highlightPreviewBusy}
                              />
                              <span>Enable highlight</span>
                            </label>
                            <div className="space-y-1">
                              <p className="text-[0.68rem] font-medium uppercase tracking-wide text-[var(--text-secondary)]">
                                Quick style
                              </p>
                              <div className="flex flex-wrap gap-2">
                                {[
                                  { value: 'none', label: 'None' },
                                  { value: 'box', label: 'Box' },
                                  { value: 'bold', label: 'Bold' },
                                ].map((option) => {
                                  const active = selectedSceneHighlightStyle === option.value;
                                  return (
                                    <button
                                      key={option.value}
                                      type="button"
                                      onClick={() => handleScenePatch({ highlight_style: option.value }, 'Highlight style updated.')}
                                      disabled={readOnlyReview || Boolean(sceneActionBusy) || highlightPreviewBusy}
                                      className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
                                        active
                                          ? 'border-[var(--border-strong)] bg-[var(--surface-container-high)] text-[var(--text-primary)]'
                                          : 'border-[var(--border-subtle)] bg-[var(--surface-elevated)] text-[var(--text-primary)] hover:border-[var(--border-strong)]'
                                      }`}
                                    >
                                      {option.label}
                                    </button>
                                  );
                                })}
                              </div>
                            </div>
                            <label className="block text-xs font-medium text-[var(--text-secondary)]">
                              Style
                              <select
                                value={selectedSceneHighlightStyle}
                                onChange={(event) => handleScenePatch({ highlight_style: event.target.value }, 'Highlight style updated.')}
                                disabled={readOnlyReview || Boolean(sceneActionBusy) || highlightPreviewBusy}
                                className="focus-ring mt-1 h-9 w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
                              >
                                <option value="none">None</option>
                                <option value="box">Box</option>
                                <option value="bold">Bold</option>
                              </select>
                            </label>
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={handleHighlightPreview}
                              disabled={readOnlyReview || Boolean(sceneActionBusy) || highlightPreviewBusy}
                            >
                              <Sparkles size={14} />
                              <span>{highlightPreviewBusy ? 'Generating preview...' : 'Preview Highlight'}</span>
                            </Button>
                            {highlightPreviewImageUrl && (
                              <img
                                src={highlightPreviewImageUrl}
                                alt="Highlight preview"
                                className="h-24 w-full rounded-lg object-cover"
                              />
                            )}
                            {highlightPreviewMessage && (
                              <p className="text-xs text-[var(--text-secondary)]">{highlightPreviewMessage}</p>
                            )}
                          </div>

                          {!readOnlyReview && (
                            <label className="block text-xs font-medium text-[var(--text-secondary)]">
                              Upload custom background for this slide
                              <input
                                type="file"
                                accept="image/*"
                                onChange={(event) => {
                                  const file = event.target.files?.[0] || null;
                                  event.target.value = '';
                                  if (file) handleSceneBackgroundUpload(file);
                                }}
                                disabled={Boolean(sceneActionBusy)}
                                className="focus-ring mt-1 block w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-2 text-sm text-[var(--text-primary)]"
                              />
                            </label>
                          )}

                          {!readOnlyReview && (
                          <div className="flex flex-wrap gap-2">
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => handleScenePatch({ background_mode: 'original' }, 'Reset to original background.')}
                              disabled={Boolean(sceneActionBusy) || !selectedScene.hasOriginalBackground}
                            >
                              <RefreshCcw size={14} />
                              <span>Reset to original</span>
                            </Button>
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={handleApplyBackgroundToAll}
                              disabled={Boolean(sceneActionBusy)
                                || (selectedSceneMode === 'original' && !selectedSceneOriginalAvailable)
                                || (selectedSceneMode === 'source_background' && !selectedSceneSourceBackgroundAvailable)
                                || (selectedSceneMode === 'custom' && !selectedSceneHasCustomBackground)}
                            >
                              <ImagePlus size={14} />
                              <span>Apply to all</span>
                            </Button>
                          </div>
                          )}

                          {sceneActionMessage && (
                            <p className="rounded-xl bg-[color:var(--feedback-success-bg)] px-3 py-2 text-xs text-[color:var(--feedback-success-fg)]">
                              {sceneActionMessage}
                            </p>
                          )}
                          {sceneActionError && (
                            <p className="rounded-xl bg-[color:var(--feedback-danger-bg)] px-3 py-2 text-xs text-[color:var(--feedback-danger-fg)]">
                              {sceneActionError}
                            </p>
                          )}
                        </div>
                      )}
                    </div>

                  <div className={activeEditorPanel === 'moderation' ? '' : 'hidden'}>
                    {selectedLesson ? (
                      <ModerationPanel
                        project={selectedLesson}
                        moderation={selectedModeration}
                        loading={loadingModeration}
                        error={moderationError}
                        actionBusy={moderationActionBusy}
                        reviewDialogOpen={reviewDialogOpen}
                        reviewMessage={reviewMessage}
                        onReviewMessageChange={setReviewMessage}
                        onRefresh={() => selectedLesson && refreshProjectModeration(selectedLesson.id)}
                        onRescan={() => handleModerationRescan(selectedLesson)}
                        onOpenReview={() => {
                          setModerationError('');
                          setReviewDialogOpen(true);
                        }}
                        onCloseReview={() => setReviewDialogOpen(false)}
                        onSubmitReview={() => handleRequestAdminReview(selectedLesson)}
                        onSelectFinding={handleSelectModerationFinding}
                        visualModerationEnabled={visualModerationEnabled}
                      />
                    ) : (
                      <div className="rounded-2xl token-surface p-4">
                        <p className="title-lg text-[var(--text-primary)]">Moderation</p>
                        <p className="mt-2 text-sm text-[var(--text-secondary)]">
                          Create or select a lesson draft before running moderation.
                        </p>
                      </div>
                    )}
                  </div>

                  {intelligenceFeatureEnabled && (
                  <div className={activeEditorPanel === 'intelligence' ? '' : 'hidden'}>
                    {selectedLesson ? (
                      <LessonIntelligencePanel
                        project={selectedLesson}
                        report={selectedLessonIntelligence}
                        loading={loadingLessonIntelligence}
                        error={lessonIntelligenceError}
                        actionBusy={lessonIntelligenceActionBusy}
                        copied={lessonIntelligenceCopied}
                        copiedSuggestionKey={lessonIntelligenceCopiedItemKey}
                        notice={lessonIntelligenceNotice}
                        onRefresh={() => selectedLesson && refreshLessonIntelligence(selectedLesson.id)}
                        onAnalyze={() => handleAnalyzeLessonIntelligence(
                          selectedLesson,
                          { force: lessonIntelligenceIsStale(selectedLessonIntelligence) },
                        )}
                        onCopy={handleCopyLessonIntelligence}
                        onCopySuggestion={handleCopyLessonIntelligenceItem}
                        onApplyNarrationSuggestion={handleApplyLessonNarrationSuggestion}
                      />
                    ) : (
                      <div className="rounded-2xl token-surface p-4">
                        <p className="title-lg text-[var(--text-primary)]">Lesson Intelligence</p>
                        <p className="mt-2 text-sm text-[var(--text-secondary)]">
                          Create or select a lesson draft before analyzing lesson quality.
                        </p>
                      </div>
                    )}
                  </div>
                  )}

                  <div className={activeEditorPanel === 'notes' ? 'space-y-3' : 'hidden'}>
                      <div>
                        <p className="title-lg text-[var(--text-primary)]">Notes</p>
                        <p className="text-xs text-[var(--text-secondary)]">Local publisher notes for this browser only; backend note persistence is not implemented yet.</p>
                      </div>
                      {lessonNotesLocalDraft && (
                        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-[color:var(--status-warning-bg)] px-3 py-2 text-xs font-semibold text-[color:var(--status-warning-fg)]">
                          <span>Unsaved local draft found. Restore or discard?</span>
                          <span className="flex gap-2">
                            <Button size="sm" variant="secondary" onClick={restoreLessonNotesDraft}>Restore</Button>
                            <Button size="sm" variant="ghost" onClick={discardLessonNotesDraft}>Discard</Button>
                          </span>
                        </div>
                      )}
                      <textarea
                        value={lessonNotes}
                        onChange={(event) => {
                          if (lessonNotesLocalDraft) setLessonNotesLocalDraft(null);
                          setLessonNotes(event.target.value);
                        }}
                        placeholder="Track publishing notes, quality checks, and post-production comments..."
                        className="focus-ring min-h-[340px] w-full resize-y rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-4 text-base leading-7 text-[var(--text-primary)]"
                      />
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-xs text-[var(--text-secondary)]">
                          {lessonNotesSavedAt || 'Not saved yet'} - global Save persists notes for this browser.
                        </p>
                      </div>
                  </div>

                  <div className={activeEditorPanel === 'tts' ? '' : 'hidden'}>
                    <TtsSettingsPanel
                      key={`tts-${selectedLesson?.id || 'none'}-${editorResetNonce}`}
                      ref={ttsSettingsRef}
                      project={selectedLesson}
                      transcriptPages={transcriptPages}
                      selectedPageKey={selectedPageKey}
                      showLocalActions={false}
                      onProjectUpdated={handleProjectUpdated}
                      onRerender={handleRerenderProject}
                      onDirtyChange={setTtsDirty}
                    />
                  </div>
                </div>
              </SurfaceCard>
            </aside>
          </section>

        </>
      )}

      {!readOnlyReview && (
        <CreateLessonModal
          open={createModalOpen}
          onClose={() => setCreateModalOpen(false)}
          categories={categories}
          submitting={submitting}
          submitError={submitError}
          onSubmit={handleCreateLessonFromModal}
        />
      )}
    </div>
  );
}
