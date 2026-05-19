import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
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
  fetchProjects,
  getProjectModeration,
  generateSubtitleTrack,
  fetchSubtitleTrackBundle,
  requestProjectAdminReview,
  rerenderProjectAvatar,
  rerenderProject,
  rescanProjectModeration,
  updateTranscriptPageScene,
  updateProjectPublished,
  fetchSubtitleTracks,
  fetchAuthenticatedAssetBlobUrl,
  previewTranscriptPageHighlight,
  uploadProjectCover,
  uploadTranscriptPageBackground,
  updateProjectAvatarVisible,
} from '../api';
import { canAccessStudio } from '../lib/auth';
import { avatarRuntimeStatusMessage } from '../utils/avatarRuntimeSettings';
import Button from '../components/ui/Button';
import SurfaceCard from '../components/ui/SurfaceCard';
import CreateLessonModal from '../components/studio/CreateLessonModal';
import PlaylistManager from '../components/studio/PlaylistManager';
import TranscriptEditorPanel from '../components/studio/TranscriptEditorPanel';
import TtsSettingsPanel from '../components/studio/TtsSettingsPanel';
import VideoStage from '../components/player/VideoStage';
import { copyTextToClipboard } from '../utils/clipboard';

const LESSON_TABS = ['overview'];
const EDITOR_PANELS = ['transcript', 'slides', 'moderation', 'intelligence', 'notes', 'tts'];
const SOURCE_TYPES_ACCEPT = '.pptx,.pdf,.docx,.txt,.png,.jpg,.jpeg,.webp,.gif';
const STUDIO_POLL_INTERVAL_MS = 4000;
const LESSON_INTELLIGENCE_ENHANCEMENT_POLL_INTERVAL_MS = 6000;
const UNSTABLE_JOB_STATUSES = new Set(['pending', 'running', 'processing', 'queued', 'started']);
const STABLE_MODERATION_STATUSES = new Set(['approved', 'admin_approved', 'revision_required', 'needs_admin_review', 'admin_rejected', 'failed']);

function normalizeProjectList(payload) {
  return Array.isArray(payload) ? payload : payload.results || [];
}

function mergeProjectsPreservingLocalModeration(previousProjects, nextProjects) {
  if (!Array.isArray(previousProjects) || !previousProjects.length) {
    return nextProjects;
  }
  const previousById = new Map(previousProjects.map((project) => [String(project.id), project]));
  return nextProjects.map((project) => {
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
}

function normalizedStatus(value) {
  if (value && typeof value === 'object') {
    return String(value.status || value.state || '').trim().toLowerCase();
  }
  return String(value || '').trim().toLowerCase();
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
  failed: 'Scan failed',
};

// Statuses where moderation BLOCKS publishing (mirrors server-side BLOCKED_MODERATION_STATUSES).
// All other statuses (not_scanned, pending, failed, approved, needs_admin_review, admin_approved)
// are allowed — the publish button is enabled and the backend will accept the request.
const MODERATION_BLOCKED_STATUSES = new Set(['admin_rejected', 'revision_required']);

function plainObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
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
  if (normalized === 'revision_required' || normalized === 'admin_rejected' || normalized === 'failed' || normalized === 'block') {
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
    return 'This lesson is awaiting admin review. You can still publish — admin review does not block publication.';
  }
  if (normalized === 'approved' || normalized === 'admin_approved') {
    return 'Moderation approved. This lesson can be published when rendering is complete.';
  }
  if (normalized === 'pending') {
    return 'Moderation scan is running. You can publish once rendering finishes — scan results will not block publishing unless content is rejected.';
  }
  if (normalized === 'failed') {
    return 'Moderation scan failed. You can still publish — resubmit a scan at any time.';
  }
  // not_scanned
  return 'Moderation has not scanned this lesson. You can publish once rendering is complete.';
}

function moderationMessage(project, moderation = null) {
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
 * Returns true when the FRONTEND considers publishing allowed.
 * Mirrors the server-side project_can_publish() rule:
 *   - blocked only by admin_rejected or revision_required
 *   - render readiness is checked separately via projectRenderReady()
 * The server is the authoritative source; this is a pre-flight UI guard only.
 */
function projectCanPublishFromModeration(project, moderation = null) {
  // Trust explicit server-side can_publish if present.
  if (moderation && Object.prototype.hasOwnProperty.call(moderation, 'can_publish')) {
    return Boolean(moderation.can_publish);
  }
  // Fall back to client-side policy: blocked only by explicit rejection.
  const modStatus = projectModerationStatus(project, moderation);
  return !MODERATION_BLOCKED_STATUSES.has(modStatus);
}

function findingHaystack(finding) {
  return [
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

function findingAssetKind(finding) {
  const haystack = findingHaystack(finding);
  if (haystack.includes('cover')) return 'cover';
  if (haystack.includes('avatar') || haystack.includes('profile')) return 'avatar';
  if (haystack.includes('video') || haystack.includes('frame')) return 'video';
  if (haystack.includes('background') || haystack.includes('custom_background')) return 'background';
  if (
    haystack.includes('transcript')
    || haystack.includes('slide')
    || haystack.includes('page_key')
    || haystack.includes('original')
    || haystack.includes('narration')
  ) {
    return 'transcript';
  }
  return 'project';
}

function findingFieldKey(finding) {
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

function findingLocationLabel(finding) {
  const kind = findingAssetKind(finding);
  if (kind === 'cover') return 'Cover image';
  if (kind === 'background') return 'Custom background';
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

function findingMatchesTranscriptPage(finding, page, index) {
  if (!finding || !page) return false;
  const haystack = findingHaystack(finding);
  const pageId = textValue(page?.id);
  const objectId = textValue(finding?.object_id);
  if (pageId && objectId && pageId === objectId && findingAssetKind(finding) === 'transcript') return true;
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
  const assetWarnings = {
    cover: false,
    background: false,
    avatar: false,
    video: false,
  };

  (Array.isArray(findings) ? findings : []).forEach((finding) => {
    const kind = findingAssetKind(finding);
    if (Object.prototype.hasOwnProperty.call(assetWarnings, kind)) {
      assetWarnings[kind] = true;
    }
    if (kind !== 'transcript') return;
    const pageIndex = (Array.isArray(pages) ? pages : []).findIndex((page, index) => (
      findingMatchesTranscriptPage(finding, page, index)
    ));
    if (pageIndex < 0) return;
    const page = pages[pageIndex];
    const key = pageIdentity(page, pageIndex);
    const field = findingFieldKey(finding) || 'page';
    const existing = pageWarnings[key] || { fields: [], findings: [] };
    if (!existing.fields.includes(field)) existing.fields.push(field);
    existing.findings.push(finding);
    pageWarnings[key] = existing;
  });

  return { pageWarnings, assetWarnings };
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

function editorDraftKey(projectId) {
  return `visus-studio-editor-draft-${projectId || 'new'}`;
}

function textValue(value) {
  return value === null || value === undefined ? '' : String(value);
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
}) {
  if (!project) return null;

  const status = projectModerationStatus(project, moderation);
  const findings = Array.isArray(moderation?.findings) ? moderation.findings : [];
  const canRequestAdminReview = Boolean(moderation?.can_request_admin_review);
  const canPublish = projectCanPublishFromModeration(project, moderation);
  const visualScan = projectVisualStaleMarker(project, moderation);
  const visualNeedsRescan = moderationMarkerIsStale(visualScan);
  const adminResponse = textValue(moderation?.admin_review?.admin_response).trim();

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
              className={`rounded-full px-3 py-1 text-xs font-semibold ${
                canPublish
                  ? 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]'
                  : 'bg-[color:var(--status-danger-bg)] text-[color:var(--status-danger-fg)]'
              }`}
            >
              {canPublish ? 'Publish allowed' : 'Publish blocked by moderation'}
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
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={onRefresh} disabled={loading || Boolean(actionBusy)}>
            <RefreshCcw size={14} />
            <span>Refresh moderation status</span>
          </Button>
          <Button size="sm" variant="secondary" onClick={onRescan} disabled={loading || Boolean(actionBusy)}>
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
      {adminResponse && (
        <div className="mt-3 rounded-xl border border-[color:var(--status-info-fg)] bg-[color:var(--status-info-bg)] p-3">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[color:var(--status-info-fg)]">
            Admin response
          </p>
          <p className="mt-1 whitespace-pre-wrap text-sm text-[var(--text-primary)]">{adminResponse}</p>
        </div>
      )}
      {visualNeedsRescan && (
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          {visualScan?.message || 'A Studio image changed after the last visual moderation scan.'}
        </p>
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
                    {finding.category || 'unknown'}
                  </span>
                  <span className={`rounded-full px-2 py-0.5 ${moderationStatusTone(finding.decision)}`}>
                    {finding.decision || 'review'}
                  </span>
                  <span className="rounded-full bg-[color:var(--surface-container-high)] px-2 py-0.5 text-[var(--text-secondary)]">
                    {finding.severity || 'low'}
                  </span>
                  </div>
                  {clickable && (
                    <span className="text-xs font-semibold text-[var(--accent-primary)]">View in editor</span>
                  )}
                </div>
                <p className="mt-2 text-sm text-[var(--text-primary)]">
                  {finding.user_message || 'This content needs moderation attention.'}
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

function lessonIntelligenceProviderLabel(report) {
  if (report?.enabled === false) return 'Disabled';
  if (report?.fallback_used) return 'Fallback heuristic used';
  const provider = String(report?.provider || '').toLowerCase();
  if (provider === 'ollama') return 'Ollama enhanced';
  if (provider === 'heuristic') return 'Heuristic analysis';
  if (provider) return `${provider.charAt(0).toUpperCase()}${provider.slice(1)} analysis`;
  return 'No analysis yet';
}

function lessonIntelligenceEnhancementStatus(report) {
  return String(report?.enhancement_status || '').trim().toLowerCase();
}

function lessonIntelligenceEnhancementPending(report) {
  return Boolean(report?.enhancement_pending || ['pending', 'running'].includes(lessonIntelligenceEnhancementStatus(report)));
}

function lessonIntelligenceEnhancementLabel(report) {
  const status = lessonIntelligenceEnhancementStatus(report);
  const provider = String(report?.enhancement_provider || '').toLowerCase();
  if (provider !== 'ollama') return '';
  if (['pending', 'running'].includes(status)) return 'Ollama enhancement running';
  if (status === 'done') return 'Ollama enhanced';
  if (['failed', 'unavailable', 'disabled', 'stale'].includes(status)) return 'Ollama unavailable; heuristic kept';
  return '';
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

function getCleanSuggestionCopyText(item) {
  const draft = lessonIntelligenceDraftNarration(item);
  if (draft) return draft;
  if (typeof item === 'string') return item.trim();
  if (!item || typeof item !== 'object') return '';
  return textValue(item.copy_text || item.advice || item.suggestion || item.message || item.text || '').trim();
}

function lessonIntelligenceCopyText(report) {
  if (!report) return '';
  const sections = [];
  if (report.summary) sections.push(`Summary\n${report.summary}`);
  const complexity = report.complexity || {};
  if (complexity.level) {
    sections.push(`Complexity\n${complexity.level} (${complexity.score || 0}/100)`);
  }
  const appendList = (title, items) => {
    if (!Array.isArray(items) || !items.length) return;
    const lines = items.map((item) => {
      const meta = lessonIntelligenceItemMeta(item);
      const text = lessonIntelligenceItemText(item);
      return [meta, text].filter(Boolean).join(': ');
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
  const enhancementFailed = ['failed', 'unavailable', 'disabled', 'stale'].includes(lessonIntelligenceEnhancementStatus(report));
  const copyDisabled = !hasReport || actionBusy || loading;
  const stale = lessonIntelligenceIsStale(report);

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
          <Button size="sm" onClick={onAnalyze} disabled={!enabled || loading || Boolean(actionBusy) || enhancementPending}>
            <Sparkles size={14} className={enhancementPending ? 'animate-spin' : ''} />
            <span>{actionBusy ? 'Analyzing...' : enhancementPending ? 'Enhancing...' : stale ? 'Re-analyze' : 'Analyze lesson'}</span>
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
          {report?.enhancement_error_safe || 'Ollama unavailable; heuristic kept.'}
        </p>
      )}

      {!loading && enabled && !hasReport && !error && (
        <div className="mt-4 rounded-xl bg-[color:var(--surface-muted)] p-4">
          <p className="text-sm font-semibold text-[var(--text-primary)]">No analysis yet</p>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Run an analysis to review summary, complexity, warnings, and narration suggestions.
          </p>
        </div>
      )}

      {hasReport && (
        <div className="mt-4 space-y-4">
          <div className="rounded-xl bg-[color:var(--surface-muted)] p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">Summary</p>
                <p className="mt-2 text-sm leading-6 text-[var(--text-primary)]">{report.summary}</p>
                {report.short_description && (
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
                    {item?.ai_generated && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-[color:rgba(208,188,255,0.16)] px-2.5 py-1 text-xs font-semibold text-[var(--accent-primary)]">
                        <Sparkles size={12} />
                        <span>AI draft</span>
                      </span>
                    )}
                  </div>
                  {advice && (
                    <p className="text-sm leading-6 text-[var(--text-secondary)]">{advice}</p>
                  )}
                  {draftNarration ? (
                    <div className="rounded-xl border border-[color:rgba(208,188,255,0.32)] bg-[color:rgba(208,188,255,0.08)] p-3">
                      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.1em] text-[var(--accent-primary)]">Draft narration</p>
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
  const previewVideoRef = useRef(null);
  const previewSectionRef = useRef(null);
  const transcriptEditorRef = useRef(null);
  const ttsSettingsRef = useRef(null);
  const selectedLessonIdRef = useRef(null);
  const [searchParams, setSearchParams] = useSearchParams();

  const [projects, setProjects] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [loadingTranscript, setLoadingTranscript] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [selectedLessonId, setSelectedLessonId] = useState(null);
  const [activeTab, setActiveTab] = useState('overview');
  const [activeEditorPanel, setActiveEditorPanel] = useState('transcript');
  const [transcriptPages, setTranscriptPages] = useState([]);
  const [selectedLessonDraftMetadata, setSelectedLessonDraftMetadata] = useState({});
  const [selectedPageKey, setSelectedPageKey] = useState('');
  const [selectedPageIndex, setSelectedPageIndex] = useState(0);
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
  const [avatarVisibilitySaving, setAvatarVisibilitySaving] = useState(false);
  const [avatarRerendering, setAvatarRerendering] = useState(false);
  const [avatarRerenderMessage, setAvatarRerenderMessage] = useState('');

  const filteredProjects = useMemo(() => {
    if (!searchQuery) return projects;
    const q = searchQuery.toLowerCase();
    return projects.filter((project) => {
      const title = String(project.title || `Project #${project.id}`).toLowerCase();
      const desc = String(project.description || '').toLowerCase();
      const cat = String(project.category_name || '').toLowerCase();
      const statusLabel = projectStatusLabel(project).toLowerCase();
      const pubLabel = projectPublicationLabel(project).toLowerCase();
      return (
        title.includes(q)
        || desc.includes(q)
        || cat.includes(q)
        || statusLabel.includes(q)
        || pubLabel.includes(q)
      );
    });
  }, [projects, searchQuery]);

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
  const [lessonNotesSavedAt, setLessonNotesSavedAt] = useState('');

  const requestedStudioView = searchParams.get('view');
  const studioView = requestedStudioView === 'editor'
    ? 'editor'
    : requestedStudioView === 'playlists'
      ? 'playlists'
      : 'lessons';
  const requestedLessonId = Number(searchParams.get('lesson') || 0) || null;
  const isStudioUser = canAccessStudio(user);

  const refreshProjects = useCallback(async ({ showLoading = true, preserveOnError = false } = {}) => {
    if (!user || !isStudioUser) return;

    if (showLoading) setLoadingProjects(true);
    try {
      const payload = await fetchProjects();
      const nextProjects = normalizeProjectList(payload);
      setProjects((previous) => mergeProjectsPreservingLocalModeration(previous, nextProjects));
      return nextProjects;
    } catch {
      if (!preserveOnError) {
        setProjects([]);
      }
      return null;
    } finally {
      if (showLoading) setLoadingProjects(false);
    }
  }, [isStudioUser, user]);

  useEffect(() => {
    if (!user || !isStudioUser) return;

    fetchCategories()
      .then((data) => setCategories(Array.isArray(data) ? data : []))
      .catch(() => setCategories([]));

    refreshProjects();
  }, [isStudioUser, refreshProjects, user]);

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
    selectedLessonIdRef.current = selectedLesson?.id || null;
  }, [selectedLesson?.id]);

  const selectedModeration = selectedLesson?.id ? moderationByProject[selectedLesson.id] || null : null;
  const selectedLessonIntelligence = selectedLesson?.id ? lessonIntelligenceByProject[selectedLesson.id] || null : null;
  const selectedModerationFindings = useMemo(
    () => (Array.isArray(selectedModeration?.findings) ? selectedModeration.findings : []),
    [selectedModeration],
  );
  const selectedDraftModeration = plainObject(selectedLessonDraftMetadata?.moderation) || {};
  const selectedDraftModerationFindings = useMemo(
    () => (Array.isArray(selectedDraftModeration?.findings) ? selectedDraftModeration.findings : []),
    [selectedDraftModeration],
  );
  const selectedDraftModerationStatus = normalizedStatus(
    selectedLessonDraftMetadata?.moderation_status
      || selectedDraftModeration?.moderation_status
      || selectedDraftModeration?.final_decision,
  );
  const selectedDraftBlocked = ['revision_required', 'needs_admin_review', 'admin_rejected', 'block'].includes(selectedDraftModerationStatus);
  const draftRerenderInProgress = Boolean(
    selectedLessonDraftMetadata?.dirty
      && ['pending', 'running', 'processing'].includes(normalizedStatus(activeRerenderStatus)),
  );
  const selectedLessonHasDraft = Boolean(selectedLessonDraftMetadata?.dirty);
  const selectedDraftStatusMessage = selectedDraftBlocked
    ? 'Draft blocked by moderation. Edit the highlighted content or discard draft. Public lesson was not changed.'
    : draftRerenderInProgress
      ? 'Draft rerender in progress.'
      : 'Draft changes saved. Public version is unchanged until Save & Rerender succeeds.';
  const moderationFindingsForStudio = useMemo(
    () => (selectedDraftBlocked
      ? [...selectedModerationFindings, ...selectedDraftModerationFindings]
      : selectedModerationFindings),
    [selectedDraftBlocked, selectedDraftModerationFindings, selectedModerationFindings],
  );
  const { pageWarnings: moderationPageWarnings, assetWarnings: moderationAssetWarnings } = useMemo(
    () => buildModerationWarningMaps(moderationFindingsForStudio, transcriptPages),
    [moderationFindingsForStudio, transcriptPages],
  );
  const draftCoverUrl = textValue(selectedLesson?.draft_cover_url || selectedLesson?.draft_thumbnail_url);
  const hasDraftCover = Boolean(draftCoverUrl);
  const selectedVisualMarker = projectVisualStaleMarker(selectedLesson, selectedModeration);
  const coverVisualNeedsRecheck = visualMarkerTargetsCover(selectedVisualMarker)
    && moderationMarkerIsStale(selectedVisualMarker);
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
  }, []);

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

  const refreshSelectedLessonState = useCallback(async (projectId, { showLoading = false } = {}) => {
    if (!projectId) return;
    await Promise.all([
      refreshProjects({ showLoading, preserveOnError: true }),
      refreshProjectModeration(projectId, { showLoading: false, preserveError: true }),
      refreshLessonIntelligence(projectId, { showLoading: false, preserveError: true }),
      refreshProjectTranscript(projectId, { showLoading: false, preserveOnError: true }),
    ]);
  }, [refreshLessonIntelligence, refreshProjectModeration, refreshProjectTranscript, refreshProjects]);

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
    if (!selectedLesson?.id || !lessonIntelligenceEnhancementPending(selectedLessonIntelligence)) return undefined;

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
  }, [selectedLesson?.id, selectedLessonIntelligence]);

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
  }, [refreshLessonIntelligence, selectedLesson?.id]);

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
      setLessonNotesSavedAt('');
      return;
    }

    const stored = window.localStorage.getItem(lessonNotesKey(selectedLesson.id)) || '';
    setLessonNotes(stored);
    setLessonNotesSavedAt(stored ? 'Loaded saved lesson notes' : 'No lesson notes yet');
  }, [selectedLesson?.id]);

  useEffect(() => {
    const handleCreateLessonRequest = () => setCreateModalOpen(true);
    window.addEventListener('visus:create-lesson-request', handleCreateLessonRequest);
    return () => window.removeEventListener('visus:create-lesson-request', handleCreateLessonRequest);
  }, []);

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
        setAvatarEnabled(draft.avatarEnabled === true);
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
    setAvatarEnabled(Boolean(selectedLesson?.avatar_enabled_override ?? selectedLesson?.avatar_active ?? false));
    setEditorSavedAtLabel('');
  }, [selectedLesson?.avatar_active, selectedLesson?.avatar_enabled_override, selectedLesson?.category_name, selectedLesson?.description, selectedLesson?.id, selectedLesson?.title]);

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
    formData.append('avatar_enabled', avatarEnabled ? '1' : '0');

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
      avatarEnabled,
    };
    window.localStorage.setItem(editorDraftKey(selectedLesson?.id), JSON.stringify(draftPayload));
    setEditorSavedAtLabel(`Draft saved at ${new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`);
  };

  const saveLessonNotes = () => {
    if (!selectedLesson?.id) return;
    window.localStorage.setItem(lessonNotesKey(selectedLesson.id), lessonNotes);
    setLessonNotesSavedAt(`Saved at ${new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`);
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
    const hasAvatarOverride = Object.prototype.hasOwnProperty.call(options, 'avatarEnabled');
    const avatarQueueExpected = projectAvatarEnabled(project) && !(hasAvatarOverride && options.avatarEnabled === false);
    const queueNote = avatarQueueExpected
      ? ' Avatar will continue in the background after the base render is ready.'
      : '';
    if (!window.confirm(`Rerender ${project.title || `project #${project.id}`}?${queueNote}`)) return false;
    try {
      await rerenderProject(project.id, hasAvatarOverride ? { avatarEnabled: options.avatarEnabled } : {});
      await refreshSelectedLessonState(project.id, { showLoading: false });
      return true;
    } catch (err) {
      window.alert(err.message || 'Rerender failed.');
      return false;
    }
  };

  const handleAvatarVisibilityToggle = async (project, nextVisible) => {
    if (!project?.id || avatarVisibilitySaving) return;
    setAvatarVisibilitySaving(true);
    try {
      const updated = await updateProjectAvatarVisible(project.id, nextVisible);
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
    if (!selectedLesson?.id || avatarRerendering) return;
    setAvatarRerendering(true);
    setAvatarRerenderMessage('');
    try {
      const result = await rerenderProjectAvatar(selectedLesson.id);
      handleProjectUpdated({
        ...selectedLesson,
        avatar_processing_status: result.avatar_processing_status || 'queued',
        avatar_processing_message: result.message || 'Avatar rerender queued.',
        avatar_last_job_id: String(result.avatar_job_id || selectedLesson.avatar_last_job_id || ''),
        avatar_runtime_settings: result.avatar_runtime_settings || selectedLesson.avatar_runtime_settings,
      });
      setAvatarRerenderMessage(result.message || 'Avatar rerender queued.');
      refreshSelectedLessonState(selectedLesson.id, { showLoading: false, preserveOnError: true }).catch(() => {});
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
          latest_run_id: current.latest_run_id
            ?? payload.latest_run_id
            ?? payload.last_moderation_run_id
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
  }, [selectedLesson?.id, selectedLesson?.last_moderation_run_id]);

  const handleModerationRescan = async (project) => {
    if (!project?.id) return;
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
      await refreshSelectedLessonState(project.id, { showLoading: false });
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
      await refreshSelectedLessonState(project.id, { showLoading: false });
    } catch (err) {
      setModerationError(err.message || 'Admin review request failed.');
    } finally {
      setModerationActionBusy('');
    }
  };

  const handleAnalyzeLessonIntelligence = useCallback(async (project, { auto = false } = {}) => {
    if (!project?.id || lessonIntelligenceActionBusy) return null;
    if (lessonIntelligenceEnhancementPending(lessonIntelligenceByProject[project.id])) return null;
    setLessonIntelligenceActionBusy('analyze');
    setLessonIntelligenceError('');
    setLessonIntelligenceCopied(false);
    setLessonIntelligenceNotice('');
    try {
      const payload = await analyzeProjectLessonIntelligence(project.id, { force: !auto });
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
  }, [lessonIntelligenceActionBusy, lessonIntelligenceByProject]);

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
      if (!result?.cancelled) {
        setLessonIntelligenceError(result?.message || 'Could not apply this suggestion.');
      }
      return;
    }
    setLessonIntelligenceError('');
    setLessonIntelligenceNotice('AI draft applied. Review it, then save changes when ready.');
  };

  useEffect(() => {
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
    lessonIntelligenceActionBusy,
    lessonIntelligenceByProject,
    loadingLessonIntelligence,
    selectedLesson,
    selectedLessonIntelligence,
  ]);

  const handlePublishToggle = async (project, nextPublished) => {
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
      handleProjectUpdated(updated);
      await refreshSelectedLessonState(project.id, { showLoading: false });
    } catch (err) {
      const message = err.message || 'Publication update failed.';
      setModerationError(message);
      await refreshSelectedLessonState(project.id, { showLoading: false });
      window.alert(message);
    }
  };

  const setStudioLocation = useCallback((nextView, lessonId = null) => {
    const nextParams = new URLSearchParams();
    nextParams.set('view', ['editor', 'playlists'].includes(nextView) ? nextView : 'lessons');

    const targetLessonId = lessonId || selectedLessonId;
    if (targetLessonId) {
      nextParams.set('lesson', String(targetLessonId));
    }

    setSearchParams(nextParams);
  }, [selectedLessonId, setSearchParams]);

  const openEditorForProject = (project) => {
    setSelectedLessonId(project.id);
    setStudioLocation('editor', project.id);
  };

  const openPreviewForProject = (project) => {
    if (!project?.id || !projectRenderReady(project)) return;
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
          moderationWarning: moderationPageWarnings[key] || null,
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
  }, [editorCanvas, moderationPageWarnings, sceneDraftStatus, transcriptPages]);

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
    !selectedLesson
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
    const pageIndex = transcriptPages.findIndex((page, index) => (
      findingMatchesTranscriptPage(finding, page, index)
    ));
    if (pageIndex >= 0) {
      handleSelectTranscriptPage(transcriptPages[pageIndex], pageIndex);
      setActiveEditorPanel('transcript');
      return;
    }
    const kind = findingAssetKind(finding);
    if (kind === 'cover' || kind === 'background') {
      setActiveEditorPanel('slides');
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
    if (!selectedLesson?.id || !selectedScene?.page?.id) return;
    setSceneActionBusy('scene');
    setSceneActionError('');
    setSceneActionMessage('');
    try {
      const payload = await updateTranscriptPageScene(selectedLesson.id, selectedScene.page.id, patch);
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
  }, [replaceTranscriptPage, selectedLesson?.id, selectedScene?.page]);

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
    if (!selectedLesson?.id || !selectedScene?.page?.id || highlightPreviewBusy) return;
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
  ]);

  const handleSceneBackgroundUpload = useCallback(async (file) => {
    if (!file || !selectedLesson?.id || !selectedScene?.page?.id) return;
    setSceneActionBusy('background');
    setSceneActionError('');
    setSceneActionMessage('');
    try {
      const payload = await uploadTranscriptPageBackground(selectedLesson.id, selectedScene.page.id, file, {
        backgroundFit: selectedSceneFit,
        textScale: selectedSceneTextScale,
      });
      replaceTranscriptPage(payload?.page);
      if (payload?.has_draft) {
        setSelectedLessonDraftMetadata(payload?.draft_metadata || {});
      }
      await refreshSelectedLessonState(selectedLesson.id, { showLoading: false });
      setSceneActionMessage('Draft background saved. Public background is unchanged until Save & Rerender succeeds.');
    } catch (err) {
      setSceneActionError(err.message || 'Could not upload slide background.');
    } finally {
      setSceneActionBusy('');
    }
  }, [refreshSelectedLessonState, replaceTranscriptPage, selectedLesson?.id, selectedScene?.page, selectedSceneFit, selectedSceneTextScale]);

  const handleApplyBackgroundToAll = useCallback(async () => {
    if (!selectedLesson?.id || !selectedScene?.page?.id) return;
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
      await refreshSelectedLessonState(selectedLesson.id, { showLoading: false });
      setSceneActionMessage('Draft background settings applied to all slides.');
    } catch (err) {
      setSceneActionError(err.message || 'Could not apply background settings to all slides.');
    } finally {
      setSceneActionBusy('');
    }
  }, [handleTranscriptPagesUpdated, refreshProjectTranscript, refreshSelectedLessonState, selectedLesson?.id, selectedPageIndex, selectedScene?.page, selectedSceneFit, selectedSceneHasCustomBackground, selectedSceneMode, selectedSceneOriginalAvailable, selectedSceneSourceBackgroundAvailable, selectedSceneSourceBackgroundMessage, selectedSceneTextScale]);

  const handleCoverUpload = useCallback(async (file) => {
    if (!file || !selectedLesson?.id) return;
    setSceneActionBusy('cover');
    setSceneActionError('');
    setSceneActionMessage('');
    try {
      const updatedProject = await uploadProjectCover(selectedLesson.id, file);
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
        nextProject.cover_url = preserveCacheBustedMediaUrl(selectedLesson.cover_url, updatedProject.cover_url);
      }
      if (updatedProject?.thumbnail_url) {
        nextProject.thumbnail_url = preserveCacheBustedMediaUrl(selectedLesson.thumbnail_url, updatedProject.thumbnail_url);
      }
      handleProjectUpdated(nextProject);
      setSelectedLessonDraftMetadata(updatedProject?.draft_metadata || {});
      setSceneActionMessage('Draft cover saved. Public cover is unchanged until Save & Rerender succeeds.');
    } catch (err) {
      setSceneActionError(err.message || 'Could not update lesson cover.');
    } finally {
      setSceneActionBusy('');
    }
  }, [handleProjectUpdated, selectedLesson?.cover_url, selectedLesson?.id, selectedLesson?.thumbnail_url]);

  const handleDraftStatusChange = useCallback((nextStatus) => {
    setSceneDraftStatus((previous) => {
      const previousJson = JSON.stringify(previous || {});
      const nextJson = JSON.stringify(nextStatus || {});
      return previousJson === nextJson ? previous : (nextStatus || {});
    });
  }, []);

  const handleGlobalEditorSave = useCallback(async ({ triggerRerender = false } = {}) => {
    if (!selectedLesson?.id) {
      persistEditorDraft();
      return;
    }

    setGlobalEditorActionBusy(triggerRerender ? 'rerender' : 'save');
    setGlobalEditorMessage('');
    setGlobalEditorError('');

    try {
      const hadTranscriptChanges = Boolean(transcriptEditorRef.current?.hasUnsavedChanges?.());
      const ttsResult = await ttsSettingsRef.current?.save?.();
      if (ttsResult?.id) {
        handleProjectUpdated(ttsResult);
      }

      const transcriptResult = await transcriptEditorRef.current?.save?.({ triggerRerender });
      applyProjectModerationPayload(transcriptResult, 'not_scanned');
      saveLessonNotes();
      await refreshSelectedLessonState(selectedLesson.id, { showLoading: false });
      if (hadTranscriptChanges) {
        if (activeEditorPanel === 'intelligence') {
          await handleAnalyzeLessonIntelligence(selectedLesson, { auto: true });
        } else {
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
      setGlobalEditorMessage(triggerRerender ? 'Saved all changes and queued rerender.' : 'Saved all changes.');
    } catch (err) {
      setGlobalEditorError(err.message || 'Could not save all editor changes.');
    } finally {
      setGlobalEditorActionBusy('');
    }
  }, [
    activeEditorPanel,
    applyProjectModerationPayload,
    handleAnalyzeLessonIntelligence,
    handleProjectUpdated,
    refreshSelectedLessonState,
    saveLessonNotes,
    selectedLesson,
  ]);

  const handleDiscardDraft = useCallback(async () => {
    if (!selectedLesson?.id || globalEditorActionBusy) return;
    if (!window.confirm('Discard all draft changes and return to the current public version?')) return;

    setGlobalEditorActionBusy('discard');
    setGlobalEditorMessage('');
    setGlobalEditorError('');

    try {
      const payload = await discardProjectDraft(selectedLesson.id);
      if (payload?.project?.id) {
        handleProjectUpdated(payload.project);
      }
      if (Array.isArray(payload?.pages)) {
        setTranscriptPages(payload.pages);
      }
      setSelectedLessonDraftMetadata(payload?.has_draft ? (payload?.draft_metadata || {}) : {});
      setSceneDraftStatus({});
      setActiveRerenderStatus(null);
      await refreshSelectedLessonState(selectedLesson.id, { showLoading: false });
      setGlobalEditorMessage('Draft discarded. Studio is showing the current public version.');
    } catch (err) {
      setGlobalEditorError(err.message || 'Could not discard draft.');
    } finally {
      setGlobalEditorActionBusy('');
    }
  }, [globalEditorActionBusy, handleProjectUpdated, refreshSelectedLessonState, selectedLesson?.id]);

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
      avatarEnabled,
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
    <div className="space-y-5">
      <SurfaceCard className="token-surface-elevated flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="label-sm">Studio Workspace</p>
          <h1 className="headline-md mt-1 text-[var(--text-primary)]">Teacher Publishing Console</h1>
        </div>

        <div className="inline-flex rounded-full token-surface p-1">
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
      </SurfaceCard>

      {submitError && (
        <SurfaceCard className="rounded-2xl bg-[color:var(--feedback-danger-bg)] p-4">
          <p className="text-sm text-[color:var(--feedback-danger-fg)]">{submitError}</p>
        </SurfaceCard>
      )}

      {studioView === 'playlists' ? (
        <PlaylistManager projects={projects} />
      ) : studioView === 'lessons' ? (
        <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_21.5rem]">
          <div className="space-y-5">
            <SurfaceCard elevated className="overflow-hidden p-0">
              <div className="relative min-h-[320px] overflow-hidden rounded-[1.5rem] bg-[var(--hero-fallback)] sm:min-h-[360px]">
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
                    {selectedLesson && (
                      <span className="rounded-full bg-[color:var(--media-pill-bg)] px-3 py-1.5">
                        {avatarStatusLabel(selectedLesson)}
                      </span>
                    )}
                  </div>

                  {selectedLesson && (
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
                    <Button onClick={() => selectedLesson && openEditorForProject(selectedLesson)} disabled={!selectedLesson}>
                      <LayoutPanelTop size={16} />
                      <span>Open Lesson Workspace</span>
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => selectedLesson && openPreviewForProject(selectedLesson)}
                      disabled={!selectedLesson || !projectRenderReady(selectedLesson)}
                    >
                      <Eye size={16} />
                      <span>{selectedLesson?.is_published ? 'Preview In Watch' : 'Preview Draft'}</span>
                    </Button>
                    {selectedLesson && projectRenderReady(selectedLesson) && (
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
                    <Button size="sm" onClick={() => selectedLesson && openEditorForProject(selectedLesson)} disabled={!selectedLesson}>
                      <LayoutPanelTop size={14} />
                      <span>Edit in Studio</span>
                    </Button>
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
                        const hasModerationWarning = Boolean(scene.moderationWarning);
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
                                {hasModerationWarning && (
                                  <span className="inline-flex items-center gap-1 rounded-full bg-[color:var(--status-warning-bg)] px-2 py-0.5 text-[0.64rem] font-semibold text-[color:var(--status-warning-fg)]">
                                    <AlertTriangle size={11} />
                                    Review
                                  </span>
                                )}
                                <span className={`rounded-full px-2 py-0.5 text-[0.64rem] font-semibold ${sceneStatusTone(scene.status)}`}>
                                  {scene.status}
                                </span>
                              </div>
                            </div>
                            <p className={`mt-2 whitespace-pre-wrap text-sm text-[var(--text-secondary)] ${expanded ? '' : 'line-clamp-3'}`}>{slideText}</p>
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
                  <textarea
                    value={lessonNotes}
                    onChange={(event) => setLessonNotes(event.target.value)}
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

          <aside>
            <SurfaceCard className="max-h-[72vh] space-y-3 overflow-y-auto p-4">
              <div className="flex items-center justify-between">
                <p className="label-sm">My Lessons</p>
                <span className="text-xs text-[var(--text-secondary)]">{filteredProjects.length}</span>
              </div>

              {loadingProjects ? (
                <p className="text-sm text-[var(--text-secondary)]">Loading lessons...</p>
              ) : (
                filteredProjects.map((project) => {
                  const projectModeration = moderationByProject[project.id] || null;
                  return (
                  <article
                    key={project.id}
                    className={`rounded-2xl p-3 transition ${
                      project.id === selectedLesson?.id
                        ? 'border border-[color:rgba(208,188,255,0.3)] bg-[color:rgba(208,188,255,0.1)]'
                        : 'token-surface'
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => selectLesson(project)}
                      className="focus-ring w-full text-left"
                    >
                      <p className="title-lg text-[var(--text-primary)]">{project.title || `Project #${project.id}`}</p>
                      <p className="mt-1 text-xs text-[var(--text-secondary)]">{safeDateLabel(project.created_at)}</p>
                      <div className="mt-2 flex flex-wrap gap-1.5 text-[0.68rem] font-semibold">
                        <span className={`rounded-full px-2 py-0.5 ${projectStatusTone(project)}`}>
                          {projectStatusLabel(project)}
                        </span>
                        <span className={`rounded-full px-2 py-0.5 ${projectPublicationTone(project)}`}>
                          {projectPublicationLabel(project)}
                        </span>
                        <span className={`rounded-full px-2 py-0.5 ${moderationStatusTone(projectModerationStatus(project, projectModeration))}`}>
                          Moderation: {moderationStatusLabel(projectModerationStatus(project, projectModeration))}
                        </span>
                        {(projectAvatarEnabled(project) || avatarProcessingStatus(project) !== 'none') && (
                          <span className="rounded-full bg-[color:var(--surface-muted)] px-2 py-0.5 text-[var(--text-secondary)]">
                            {avatarStatusLabel(project)}
                          </span>
                        )}
                      </div>
                    </button>

                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button size="sm" onClick={() => openEditorForProject(project)}>
                        <BookOpenText size={14} />
                        <span>Open</span>
                      </Button>
                      {projectRenderReady(project) && (
                        <Button variant="secondary" size="sm" onClick={() => openPreviewForProject(project)}>
                          <Eye size={14} />
                          <span>{project.is_published ? 'Preview' : 'Draft Preview'}</span>
                        </Button>
                      )}
                      <Button variant="secondary" size="sm" onClick={() => handleRerenderProject(project)}>
                        <RefreshCcw size={14} />
                        <span>Rerender</span>
                      </Button>
                      {projectRenderReady(project) && (
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
                      <Button variant="ghost" size="sm" onClick={() => handleDeleteProject(project)}>
                        <Trash2 size={14} />
                        <span>Delete</span>
                      </Button>
                    </div>
                  </article>
                  );
                })
              )}
            </SurfaceCard>
          </aside>
        </section>
      ) : (
        <>
          <section className="grid gap-5 xl:grid-cols-2">
            <div className="space-y-5">
              <SurfaceCard elevated className="space-y-4 p-4 sm:p-5">
                <div className="grid gap-3 md:grid-cols-2">
                  <label className="block text-sm text-[var(--text-secondary)]">
                    Lesson title
                    <input
                      value={editorTitle}
                      onChange={(event) => setEditorTitle(event.target.value)}
                      type="text"
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
                      const hasModerationWarning = Boolean(scene.moderationWarning);
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
                              {hasModerationWarning && (
                                <span className="inline-flex items-center gap-1 rounded-full bg-[color:var(--status-warning-bg)] px-2 py-0.5 text-[0.6rem] font-semibold text-[color:var(--status-warning-fg)]">
                                  <AlertTriangle size={10} />
                                  Review
                                </span>
                              )}
                              <span className={`rounded-full px-2 py-0.5 text-[0.6rem] font-semibold ${modeTone}`}>
                                {sceneModeLabel(scene.backgroundMode)}
                              </span>
                            </div>
                          </div>
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

            <aside>
              <SurfaceCard
                elevated
                className="flex min-h-[72vh] flex-col gap-4 overflow-hidden xl:max-h-[calc(100vh-9rem)]"
              >
                <div className="flex shrink-0 flex-wrap items-start justify-between gap-3">
                  <div>
                    <h3 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Editor Workspace</h3>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                      {selectedLesson ? selectedLesson.title || 'Selected lesson' : 'Local draft'}
                    </p>
                  </div>
                  <div className="flex flex-wrap justify-end gap-2">
                    {selectedLesson ? (
                      <>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => handleGlobalEditorSave({ triggerRerender: false })}
                          disabled={Boolean(globalEditorActionBusy)}
                        >
                          <Save size={14} />
                          <span>{globalEditorActionBusy === 'save' ? 'Saving...' : 'Save'}</span>
                        </Button>
                        <Button
                          size="sm"
                          onClick={() => handleGlobalEditorSave({ triggerRerender: true })}
                          disabled={Boolean(globalEditorActionBusy)}
                        >
                          <RefreshCcw size={14} />
                          <span>{globalEditorActionBusy === 'rerender' ? 'Saving...' : 'Save & Rerender'}</span>
                        </Button>
                        {selectedLessonHasDraft && (
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={handleDiscardDraft}
                            disabled={Boolean(globalEditorActionBusy)}
                          >
                            <Trash2 size={14} />
                            <span>{globalEditorActionBusy === 'discard' ? 'Discarding...' : 'Discard Draft'}</span>
                          </Button>
                        )}
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

                {(globalEditorMessage || globalEditorError || (!selectedLesson && editorSavedAtLabel)) && (
                  <p className={`shrink-0 rounded-xl px-3 py-2 text-xs font-semibold ${
                    globalEditorError
                      ? 'bg-[color:var(--feedback-danger-bg)] text-[color:var(--feedback-danger-fg)]'
                      : 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]'
                  }`}>
                    {globalEditorError || globalEditorMessage || editorSavedAtLabel}
                  </p>
                )}

                <div className="rail-scroll relative z-10 -mx-1 flex shrink-0 gap-2 overflow-x-auto bg-[var(--bg-elevated)] px-1 py-1">
                  {EDITOR_PANELS.map((panel) => {
                    const selected = activeEditorPanel === panel;
                    const hasModerationWarning = (
                      (panel === 'transcript' && Object.keys(moderationPageWarnings).length > 0)
                      || (panel === 'slides' && (moderationAssetWarnings.cover || moderationAssetWarnings.background))
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

                <div className="rail-scroll min-h-0 flex-1 overflow-y-auto px-1">
                  <div className={activeEditorPanel === 'transcript' ? 'space-y-3' : 'hidden'}>
                      {selectedLesson ? (
                        <TranscriptEditorPanel
                          ref={transcriptEditorRef}
                          project={selectedLesson}
                          pages={transcriptPages}
                          loading={loadingTranscript}
                          selectedPageKey={selectedPageKey}
                          selectedPageIndex={selectedPageIndex}
                          moderationPageWarnings={moderationPageWarnings}
                          showLocalActions={false}
                          onSelectPage={handleSelectTranscriptPage}
                          onPagesUpdated={handleTranscriptPagesUpdated}
                          onProjectRefresh={() => selectedLesson && refreshSelectedLessonState(selectedLesson.id, { showLoading: false })}
                          onModerationUpdated={applyProjectModerationPayload}
                          onDraftStatusChange={handleDraftStatusChange}
                          onJobStatusChange={setActiveRerenderStatus}
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

                      <div className="space-y-3 rounded-2xl token-surface p-3">
                        <label className="block text-sm text-[var(--text-secondary)]">
                          Pause between slides (sec)
                          <input
                            type="number"
                            min="0"
                            step="0.1"
                            value={pauseSec}
                            onChange={(event) => setPauseSec(event.target.value)}
                            className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-[var(--text-primary)]"
                          />
                        </label>

                        <label className="inline-flex items-center gap-2 rounded-xl px-2 py-1 text-sm text-[var(--text-secondary)]">
                          <input
                            type="checkbox"
                            checked={whiteboardModeAll}
                            onChange={(event) => setWhiteboardModeAll(event.target.checked)}
                          />
                          <span>Whiteboard mode all slides</span>
                        </label>

                        <label className="inline-flex items-center gap-2 rounded-xl px-2 py-1 text-sm text-[var(--text-secondary)]">
                          <input
                            type="checkbox"
                            checked={avatarEnabled}
                            onChange={(event) => setAvatarEnabled(event.target.checked)}
                          />
                          <span>Render with avatar</span>
                        </label>
                      </div>
                  </div>

                  <div className={activeEditorPanel === 'slides' ? 'space-y-3' : 'hidden'}>
                      <div>
                        <p className="title-lg text-[var(--text-primary)]">Slides</p>
                        <p className="text-xs text-[var(--text-secondary)]">Adjust the selected slide background and lesson cover. Select slides from the timeline below the preview.</p>
                      </div>
                      {selectedLesson && (
                        <div className={`space-y-3 rounded-2xl p-3 ${
                          moderationAssetWarnings.cover
                            ? 'border border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)]'
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
                              {hasDraftCover && !moderationAssetWarnings.cover && (
                                <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                  Public cover is unchanged until Save & Rerender succeeds.
                                </p>
                              )}
                              {moderationAssetWarnings.cover && (
                                <p className="mt-1 inline-flex items-center gap-1 text-xs font-semibold text-[color:var(--status-warning-fg)]">
                                  <AlertTriangle size={12} />
                                  Cover image has a moderation finding. Public cover/background was not changed.
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
                                  <img
                                    src={selectedLesson.cover_url}
                                    alt="Public lesson cover"
                                    className="h-14 w-20 rounded-lg object-cover"
                                  />
                                  {hasDraftCover && <span className="block text-[0.65rem] text-[var(--text-muted)]">Public</span>}
                                </div>
                              )}
                              {hasDraftCover && (
                                <div className="space-y-1 text-right">
                                  <img
                                    src={draftCoverUrl}
                                    alt="Draft lesson cover"
                                    className="h-14 w-20 rounded-lg object-cover"
                                  />
                                  <span className="block text-[0.65rem] font-semibold text-[var(--text-secondary)]">Draft</span>
                                </div>
                              )}
                            </div>
                          </div>
                          <label className="block text-xs font-medium text-[var(--text-secondary)]">
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
                        </div>
                      )}

                      {selectedScene?.page && (
                        <div className={`space-y-3 rounded-2xl p-3 ${
                          selectedScene?.moderationWarning || moderationAssetWarnings.background
                            ? 'border border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)]'
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
                            {(selectedScene?.moderationWarning || moderationAssetWarnings.background) && (
                              <p className="mt-1 inline-flex items-center gap-1 text-xs font-semibold text-[color:var(--status-warning-fg)]">
                                <AlertTriangle size={12} />
                                This scene has moderation findings. Public cover/background was not changed.
                              </p>
                            )}
                          </div>

                          <label className="block text-xs font-medium text-[var(--text-secondary)]">
                            Mode
                            <select
                              value={selectedSceneMode}
                              onChange={handleSceneModeChange}
                              disabled={Boolean(sceneActionBusy)}
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

                          <label className="block text-xs font-medium text-[var(--text-secondary)]">
                            Background fit
                            <select
                              value={selectedSceneFit}
                              onChange={(event) => handleScenePatch({ background_fit: event.target.value }, 'Background fit updated.')}
                              disabled={Boolean(sceneActionBusy)}
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
                              disabled={Boolean(sceneActionBusy)}
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
                                disabled={Boolean(sceneActionBusy) || highlightPreviewBusy}
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
                                      disabled={Boolean(sceneActionBusy) || highlightPreviewBusy}
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
                                disabled={Boolean(sceneActionBusy) || highlightPreviewBusy}
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
                              disabled={Boolean(sceneActionBusy) || highlightPreviewBusy}
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
                        onAnalyze={() => handleAnalyzeLessonIntelligence(selectedLesson)}
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

                  <div className={activeEditorPanel === 'notes' ? 'space-y-3' : 'hidden'}>
                      <div>
                        <p className="title-lg text-[var(--text-primary)]">Notes</p>
                        <p className="text-xs text-[var(--text-secondary)]">Local publisher notes for this browser only; backend note persistence is not implemented yet.</p>
                      </div>
                      <textarea
                        value={lessonNotes}
                        onChange={(event) => setLessonNotes(event.target.value)}
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
                      ref={ttsSettingsRef}
                      project={selectedLesson}
                      transcriptPages={transcriptPages}
                      selectedPageKey={selectedPageKey}
                      showLocalActions={false}
                      onProjectUpdated={handleProjectUpdated}
                      onRerender={handleRerenderProject}
                    />
                  </div>
                </div>
              </SurfaceCard>
            </aside>
          </section>

        </>
      )}

      <CreateLessonModal
        open={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        categories={categories}
        submitting={submitting}
        submitError={submitError}
        onSubmit={handleCreateLessonFromModal}
      />
    </div>
  );
}
