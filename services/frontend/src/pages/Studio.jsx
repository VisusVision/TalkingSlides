import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BookOpenText,
  Eye,
  EyeOff,
  FileText,
  ImagePlus,
  LayoutPanelTop,
  LogIn,
  Maximize2,
  Minimize2,
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
  fetchCategories,
  fetchJobStatus,
  fetchLesson,
  fetchPlaybackToken,
  fetchProjectTranscript,
  fetchProjects,
  fetchAuthenticatedObjectUrl,
  fetchRenderCapacity,
  getToken,
  rerenderProject,
  retryJob,
  cancelJob,
  subscribeJobStatusEvents,
  updateProjectPublished,
} from '../api';
import { canAccessStudio } from '../lib/auth';
import { buildRetryErrorMessage, buildRetrySuccessMessage, isRetryVisibleForStatus } from '../lib/retryUi';
import Button from '../components/ui/Button';
import SurfaceCard from '../components/ui/SurfaceCard';
import CreateLessonModal from '../components/studio/CreateLessonModal';
import TranscriptEditorPanel from '../components/studio/TranscriptEditorPanel';
import TtsSettingsPanel from '../components/studio/TtsSettingsPanel';
import VideoStage from '../components/player/VideoStage';

const LESSON_TABS = ['overview'];
const EDITOR_PANELS = ['transcript', 'slides', 'notes', 'tts'];
const SOURCE_TYPES_ACCEPT = '.pptx,.pdf,.docx,.txt';

function normalizeProjectList(payload) {
  return Array.isArray(payload) ? payload : payload.results || [];
}

function projectStatusLabel(project) {
  if (project?.latest_job?.cancelled) return 'Cancelled';
  if (String(project?.latest_job?.error_message || '').includes('__cancelled_by_user__')) return 'Cancelled';
  const raw = String(project?.latest_job?.status || project?.status || '').trim().toLowerCase();
  if (!raw) return 'Draft';
  if (raw === 'cancelled') return 'Cancelled';
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
  if (label === 'cancelled') {
    return 'bg-[color:var(--surface-muted)] text-[color:var(--text-secondary)]';
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

function projectAvatarEnabled(project) {
  return Boolean(project?.avatar_active || project?.avatar_enabled_override === true);
}

function projectRenderReady(project) {
  const raw = String(project?.latest_job?.status || project?.status || '').trim().toLowerCase();
  return raw === 'done' || raw === 'ready';
}

function projectRetryable(project) {
  const raw = String(project?.latest_job?.status || project?.status || "").trim().toLowerCase();
  return isRetryVisibleForStatus(raw);
}

function formatEta(seconds) {
  const total = Number(seconds || 0);
  if (!Number.isFinite(total) || total <= 0) return 'under a minute';
  if (total < 60) return `${Math.round(total)}s`;
  const minutes = Math.round(total / 60);
  return `${minutes} min`;
}

function projectProgressPct(project) {
  const raw = Number(project?.latest_job?.progress);
  if (!Number.isFinite(raw)) return 0;
  return Math.min(100, Math.max(0, Math.round(raw)));
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

function pageIdentity(page, index) {
  return String(page?.page_key || page?.id || `page-${index}`);
}

function pageNarration(page) {
  if (page && Object.prototype.hasOwnProperty.call(page, 'narration_text')) {
    return textValue(page.narration_text);
  }
  return textValue(page?.original_text);
}

function hasDoubleBlankLine(value) {
  return /\n\s*\n/.test(textValue(value).replace(/\r\n/g, '\n').replace(/\r/g, '\n'));
}

function textPreview(value, maxLength = 110) {
  const normalized = textValue(value).replace(/\s+/g, ' ').trim();
  if (!normalized) return 'No narration text yet';
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}...` : normalized;
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
  if (textValue(page?.original_text).trim() && narration.trim() !== textValue(page.original_text).trim()) {
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

function withAuthToken(url) {
  const base = textValue(url).trim();
  const token = textValue(getToken()).trim();
  if (!base || !token) return base;
  return `${base}${base.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
}

export default function Studio({ user, onLoginRequest }) {
  const navigate = useNavigate();
  const previewVideoRef = useRef(null);
  const [searchParams, setSearchParams] = useSearchParams();

  const [projects, setProjects] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [projectsError, setProjectsError] = useState('');
  const [projectsDebug, setProjectsDebug] = useState('');
  const [renderCapacity, setRenderCapacity] = useState(null);
  const [loadingTranscript, setLoadingTranscript] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [submitInfo, setSubmitInfo] = useState('');
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [selectedLessonId, setSelectedLessonId] = useState(null);
  const [activeTab, setActiveTab] = useState('overview');
  const [activeEditorPanel, setActiveEditorPanel] = useState('transcript');
  const [editorFocusMode, setEditorFocusMode] = useState(false);
  const [transcriptPages, setTranscriptPages] = useState([]);
  const [selectedPageKey, setSelectedPageKey] = useState('');
  const [selectedPageIndex, setSelectedPageIndex] = useState(0);
  const [sceneDraftStatus, setSceneDraftStatus] = useState({});
  const [authedSceneThumbnails, setAuthedSceneThumbnails] = useState({});
  const [activeRerenderStatus, setActiveRerenderStatus] = useState(null);
  const [expandedSlideKeys, setExpandedSlideKeys] = useState({});
  const [previewLesson, setPreviewLesson] = useState(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [previewError, setPreviewError] = useState('');

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

  const studioView = searchParams.get('view') === 'editor' ? 'editor' : 'lessons';
  const requestedLessonId = Number(searchParams.get('lesson') || 0) || null;
  const isStudioUser = canAccessStudio(user);

  const refreshProjects = useCallback(async () => {
    if (!user || !isStudioUser) return;

    setLoadingProjects(true);
    setProjectsError('');
    setProjectsDebug('');
    try {
      const payload = await fetchProjects();
      const normalized = normalizeProjectList(payload);
      setProjects(normalized);
      setProjectsDebug(
        `Fetched ${normalized.length} projects: ${normalized
          .map((project) => `${project.id}:${project.title || 'untitled'}`)
          .join(', ')}`,
      );
    } catch (err) {
      setProjectsError(err?.message || 'Project list could not be refreshed.');
      setProjectsDebug(`Refresh failed: ${err?.message || 'unknown error'}`);
    } finally {
      setLoadingProjects(false);
    }
  }, [isStudioUser, user]);

  const refreshRenderCapacity = useCallback(async () => {
    if (!user || !isStudioUser) return;
    try {
      const payload = await fetchRenderCapacity();
      setRenderCapacity(payload);
    } catch {
      // no-op
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
    refreshRenderCapacity();
    const timer = window.setInterval(refreshRenderCapacity, 15000);
    return () => window.clearInterval(timer);
  }, [refreshRenderCapacity]);

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
    if (!selectedLesson?.id) {
      setTranscriptPages([]);
      return;
    }

    setSceneDraftStatus({});
    setActiveRerenderStatus(null);

    let active = true;
    setLoadingTranscript(true);

    fetchProjectTranscript(selectedLesson.id)
      .then((payload) => {
        if (!active) return;
        setTranscriptPages(Array.isArray(payload?.pages) ? payload.pages : []);
      })
      .catch(() => {
        if (!active) return;
        setTranscriptPages([]);
      })
      .finally(() => {
        if (active) {
          setLoadingTranscript(false);
        }
      });

    return () => {
      active = false;
    };
  }, [selectedLesson?.id, selectedLesson?.latest_job?.status, selectedLesson?.status]);

  useEffect(() => {
    let cancelled = false;
    const objectUrls = [];

    const loadSceneThumbnails = async () => {
      if (!Array.isArray(transcriptPages) || transcriptPages.length === 0) {
        setAuthedSceneThumbnails({});
        return;
      }

      const entries = await Promise.all(
        transcriptPages.map(async (page, index) => {
          const key = pageIdentity(page, index);
          const rawUrl = withAuthToken(firstAvailableUrl(page.thumbnail_url, page.slide_image_url, page.image_url, page.image_file_url));
          if (!rawUrl) return [key, ''];
          try {
            const objectUrl = await fetchAuthenticatedObjectUrl(rawUrl);
            objectUrls.push(objectUrl);
            return [key, objectUrl];
          } catch {
            return [key, rawUrl];
          }
        })
      );

      if (cancelled) {
        objectUrls.forEach((url) => URL.revokeObjectURL(url));
        return;
      }

      setAuthedSceneThumbnails(Object.fromEntries(entries));
    };

    loadSceneThumbnails();

    return () => {
      cancelled = true;
      objectUrls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [transcriptPages]);

  useEffect(() => {
    if (!selectedLesson?.id) return undefined;
    if (projectRenderReady(selectedLesson)) return undefined;

    const projectId = selectedLesson.id;
    const jobId = selectedLesson?.latest_job?.id;
    let active = true;
    let fallbackTimer = null;
    let fallbackDelayMs = 4000;
    const fallbackMinMs = 4000;
    const fallbackMaxMs = 60000;

    const clearFallbackTimer = () => {
      if (fallbackTimer) {
        window.clearTimeout(fallbackTimer);
        fallbackTimer = null;
      }
    };

    const withJitter = (baseMs) => {
      const jitterRatio = 0.2; // +/-20% jitter to avoid reconnect storms.
      const jitter = (Math.random() * 2 - 1) * jitterRatio * baseMs;
      const next = Math.round(baseMs + jitter);
      return Math.max(fallbackMinMs, Math.min(fallbackMaxMs, next));
    };

    const bumpBackoff = () => {
      fallbackDelayMs = Math.min(fallbackMaxMs, Math.round(fallbackDelayMs * 1.8));
    };

    const resetBackoff = () => {
      fallbackDelayMs = fallbackMinMs;
    };

    const applyJob = async (job) => {
      if (!active || !job || job.notfound) return;
      setProjects((previous) =>
        previous.map((project) =>
          project.id === projectId
            ? {
                ...project,
                latest_job: { ...(project.latest_job || {}), ...job },
                status: job.status || project.status,
              }
            : project,
        ),
      );
      const state = String(job.status || '').toLowerCase();
      resetBackoff();
      if (state === 'done' || state === 'failed' || state === 'cancelled') {
        clearFallbackTimer();
        await refreshProjects();
      }
    };

    const scheduleNextPoll = () => {
      if (!active) return;
      clearFallbackTimer();
      fallbackTimer = window.setTimeout(runPollCycle, withJitter(fallbackDelayMs));
    };

    const poll = async () => {
      try {
        if (jobId) {
          const job = await fetchJobStatus(projectId, jobId);
          await applyJob(job);
          scheduleNextPoll();
          return true;
        }

        await refreshProjects();
        resetBackoff();
        scheduleNextPoll();
        return true;
      } catch {
        // Keep polling silently; manual refresh is still available.
        bumpBackoff();
        scheduleNextPoll();
        return false;
      }
    };

    const runPollCycle = () => {
      void poll();
    };

    let closeStream = null;
    if (jobId) {
      closeStream = subscribeJobStatusEvents(projectId, jobId, {
        onStatus: (job) => {
          clearFallbackTimer();
          applyJob(job);
        },
        onError: () => {
          if (!active) return;
          runPollCycle();
        },
      });
    } else {
      runPollCycle();
    }

    return () => {
      active = false;
      if (typeof closeStream === 'function') {
        closeStream();
      }
      clearFallbackTimer();
    };
  }, [selectedLesson?.id, selectedLesson?.latest_job?.id, selectedLesson?.latest_job?.status, refreshProjects]);

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
      fetchLesson(selectedLesson.id),
      fetchPlaybackToken(selectedLesson.id).catch(() => null),
    ])
      .then(([lessonPayload, playbackPayload]) => {
        if (!active) return;
        const integratedPreview = playbackPayload
          ? {
              ...(lessonPayload || {}),
              stream_url: playbackPayload.video_url || lessonPayload?.stream_url || '',
              srt_url: playbackPayload.srt_url || lessonPayload?.srt_url || '',
              vtt_url: playbackPayload.vtt_url || lessonPayload?.vtt_url || '',
              subtitle_vtt_url:
                playbackPayload.subtitle_vtt_url || lessonPayload?.subtitle_vtt_url || '',
              streaming: playbackPayload.streaming || lessonPayload?.streaming || null,
              playback_status: playbackPayload.playback_status || lessonPayload?.playback_status || null,
              protection: playbackPayload.protection || lessonPayload?.protection || null,
              watermark: playbackPayload.watermark || lessonPayload?.watermark || null,
              avatar_overlay: playbackPayload.avatar_overlay || lessonPayload?.avatar_overlay || null,
            }
          : (lessonPayload || null);
        setPreviewLesson(integratedPreview);
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

  const handleCreateProject = async ({
    file,
    coverFile,
    title,
    category,
    pauseSec,
    whiteboardModeAll,
    avatarEnabled,
    renderProfile,
  }) => {
    if (!file) return;

    setSubmitError('');
    setSubmitInfo('');
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
    formData.append('render_profile', renderProfile || 'balanced');

    try {
      const createdJob = await createProject(formData);
      const createdProjectId = Number(createdJob?.project_id || createdJob?.project?.id || 0) || null;
      const queue = String(createdJob?.queue || '').trim();
      const eta = Number(createdJob?.estimated_wait_seconds || 0);
      if (queue || eta > 0) {
        setSubmitInfo(
          `Queued on ${queue || 'render'} queue. Estimated wait: ${formatEta(eta)}.`
        );
      }
      await refreshProjects();
      if (createdProjectId) {
        setSelectedLessonId(createdProjectId);
      }
      return createdProjectId || true;
    } catch (err) {
      const payload = err?.payload || {};
      const queueDepth = Number(payload?.queue_depth || 0);
      const queueLimit = Number(payload?.queue_limit || 0);
      if (err?.status === 429 && queueDepth > 0 && queueLimit > 0) {
        setSubmitError(`${err.message} (queue depth ${queueDepth}/${queueLimit})`);
      } else {
        setSubmitError(err.message || 'Project upload failed.');
      }
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
    const rerenderWithoutAvatar = hasAvatarOverride && options.avatarEnabled === false;
    const avatarQueueExpected = projectAvatarEnabled(project) && !rerenderWithoutAvatar;
    const queueNote = avatarQueueExpected
      ? ' This uses the avatar queue and can take longer.'
      : rerenderWithoutAvatar
        ? ' This bypasses the avatar queue for this rerender.'
        : '';
    if (!window.confirm(`Rerender ${project.title || `project #${project.id}`}?${queueNote}`)) return false;
    try {
      const payload = await rerenderProject(
        project.id,
        hasAvatarOverride ? { avatarEnabled: options.avatarEnabled, renderProfile: options.renderProfile } : { renderProfile: options.renderProfile },
      );
      const queue = String(payload?.queue || '').trim();
      const eta = Number(payload?.estimated_wait_seconds || 0);
      if (queue || eta > 0) {
        window.alert(`Rerender queued on ${queue || 'render'}. Estimated wait: ${formatEta(eta)}.`);
      }
      await refreshProjects();
      return true;
    } catch (err) {
      const payload = err?.payload || {};
      const queueDepth = Number(payload?.queue_depth || 0);
      const queueLimit = Number(payload?.queue_limit || 0);
      if (err?.status === 429 && queueDepth > 0 && queueLimit > 0) {
        window.alert(`${err.message} (queue depth ${queueDepth}/${queueLimit})`);
      } else {
        window.alert(err.message || 'Rerender failed.');
      }
      return false;
    }
  };

  const handleCancelRender = async (project) => {
    if (!project?.id) return false;
    const jobId = project?.latest_job?.id;
    if (!jobId) {
      window.alert('No active job found for this project.');
      return false;
    }
    if (!window.confirm(`Cancel current render for ${project.title || `project #${project.id}`}?`)) return false;
    try {
      await cancelJob(project.id, jobId, 'user_cancelled_from_studio');
      await refreshProjects();
      window.alert('Render cancelled.');
      return true;
    } catch (err) {
      window.alert(err?.message || 'Failed to cancel render.');
      return false;
    }
  };

  const handleRetryJob = async (project) => {
    if (!project?.id || !project?.latest_job?.id) {
      window.alert("No retryable job found for this project.");
      return false;
    }
    if (!projectRetryable(project)) {
      window.alert("Only failed or cancelled jobs can be retried.");
      return false;
    }
    if (!window.confirm(`Retry failed/cancelled job for ${project.title || `project #${project.id}`}?`)) return false;
    setSubmitError("");
    setSubmitInfo("");
    try {
      const payload = await retryJob(project.id, project.latest_job.id);
      setSubmitInfo(buildRetrySuccessMessage(payload, project.latest_job.id));
      await refreshProjects();
      return true;
    } catch (err) {
      const errorMessage = buildRetryErrorMessage(err);
      setSubmitError(errorMessage);
      window.alert(errorMessage);
      return false;
    }
  };

  const handleEditorRender = async () => {
    if (!selectedLesson) return;
    await handleRerenderProject(selectedLesson, { avatarEnabled });
  };

  const handleProjectUpdated = useCallback((updatedProject) => {
    if (!updatedProject?.id) return;
    setProjects((prev) => prev.map((project) => (project.id === updatedProject.id ? updatedProject : project)));
    setSelectedLessonId((previous) => previous || updatedProject.id);
  }, []);

  const handlePublishToggle = async (project, nextPublished) => {
    try {
      const updated = await updateProjectPublished(project.id, nextPublished);
      handleProjectUpdated(updated);
    } catch (err) {
      window.alert(err.message || 'Publication update failed.');
    }
  };

  const setStudioLocation = useCallback((nextView, lessonId = null) => {
    const nextParams = new URLSearchParams();
    nextParams.set('view', nextView === 'editor' ? 'editor' : 'lessons');

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
        const narration = draft.narration_text ?? pageNarration(page);
        return {
          id: page.id || key,
          key,
          index,
          type: 'transcript',
          label: sceneLabel(page, index),
          pageKey: page.page_key || '',
          text: textPreview(narration),
          fullText: textValue(narration).replace(/\s+$/g, ''),
          status,
          isDirty: Boolean(draft.dirty),
          timing: sceneTimingLabel(page),
          subtitleCount: Array.isArray(page.subtitle_chunks) ? page.subtitle_chunks.length : 0,
          thumbnailUrl: authedSceneThumbnails[key] || withAuthToken(firstAvailableUrl(page.thumbnail_url, page.slide_image_url, page.image_url, page.image_file_url)),
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
  }, [authedSceneThumbnails, editorCanvas, sceneDraftStatus, transcriptPages]);

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

  const latestRenderStatus = activeRerenderStatus || selectedLesson?.latest_job || null;
  const resumeSourceLabel = useMemo(() => {
    const source = String(latestRenderStatus?.resume_source || '').trim().toLowerCase();
    if (!source || source === 'normal') return '';
    if (source === 'resume_shortcut') return 'Recovered from existing part artifacts';
    return `Recovered path: ${source}`;
  }, [latestRenderStatus?.resume_source]);
  const recoveredPartsCount = useMemo(() => {
    const raw = Number(latestRenderStatus?.recovered_parts_count || 0);
    return Number.isFinite(raw) && raw > 0 ? Math.round(raw) : 0;
  }, [latestRenderStatus?.recovered_parts_count]);
  const renderProgressPct = useMemo(() => {
    if (!selectedLesson) return 0;
    return projectProgressPct({ latest_job: latestRenderStatus });
  }, [latestRenderStatus, selectedLesson]);

  useEffect(() => {
    if (studioView !== 'editor') {
      setEditorFocusMode(false);
    }
  }, [studioView]);

  const toggleEditorFocusMode = useCallback(() => {
    setEditorFocusMode((previous) => {
      const next = !previous;
      if (next) {
        setActiveEditorPanel('transcript');
      }
      return next;
    });
  }, []);

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

  const handleTranscriptPagesUpdated = useCallback((updatedPages) => {
    const normalized = Array.isArray(updatedPages) ? updatedPages : [];
    setTranscriptPages(normalized);
  }, []);

  const handleDraftStatusChange = useCallback((nextStatus) => {
    setSceneDraftStatus((previous) => {
      const previousJson = JSON.stringify(previous || {});
      const nextJson = JSON.stringify(nextStatus || {});
      return previousJson === nextJson ? previous : (nextStatus || {});
    });
  }, []);

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
    return panel.charAt(0).toUpperCase() + panel.slice(1);
  };

  const editorPanelIcon = (panel) => {
    if (panel === 'slides') return <LayoutPanelTop size={14} />;
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
        </div>
      </SurfaceCard>

      {submitError && (
        <SurfaceCard className="rounded-2xl bg-[color:var(--feedback-danger-bg)] p-4">
          <p className="text-sm text-[color:var(--feedback-danger-fg)]">{submitError}</p>
        </SurfaceCard>
      )}
      {submitInfo && !submitError && (
        <SurfaceCard className="rounded-2xl bg-[color:var(--surface-muted)] p-4">
          <p className="text-sm text-[var(--text-secondary)]">{submitInfo}</p>
        </SurfaceCard>
      )}
      {(projectsDebug || projectsError) && (
        <SurfaceCard className="space-y-2 rounded-2xl border border-[var(--border-subtle)] p-3">
          <p className="text-xs text-[var(--text-secondary)]">Studio debug</p>
          {projectsDebug && <p className="text-xs text-[var(--text-primary)]">{projectsDebug}</p>}
          {projectsError && <p className="text-xs text-[color:var(--feedback-danger-fg)]">{projectsError}</p>}
          {projects.length > 0 && (
            <div className="flex flex-wrap gap-2 pt-1">
              {projects.map((project) => (
                <button
                  key={`debug-project-${project.id}`}
                  type="button"
                  onClick={() => selectLesson(project)}
                  className="focus-ring rounded-lg border border-[var(--border-subtle)] px-2 py-1 text-xs text-[var(--text-primary)]"
                >
                  #{project.id} {project.title || 'untitled'}
                </button>
              ))}
            </div>
          )}
        </SurfaceCard>
      )}
      {renderCapacity?.queues && (
        <SurfaceCard className="space-y-2 rounded-2xl border border-[var(--border-subtle)] p-3">
          <p className="text-xs text-[var(--text-secondary)]">Render Capacity (live)</p>
          <div className="flex flex-wrap gap-2 text-xs text-[var(--text-primary)]">
            {Object.entries(renderCapacity.queues).map(([profile, info]) => (
              <span key={profile} className="rounded-full bg-[color:var(--surface-muted)] px-2 py-1">
                {profile}: q={info.depth} eta~{formatEta(info.estimated_wait_seconds)}
              </span>
            ))}
          </div>
        </SurfaceCard>
      )}

      {studioView === 'lessons' ? (
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
                        {projectStatusLabel(selectedLesson).toLowerCase() === 'processing' && renderProgressPct > 0
                          ? ` ${renderProgressPct}%`
                          : ''}
                      </span>
                    )}
                    {selectedLesson && (
                      <span className={`rounded-full px-3 py-1.5 ${projectPublicationTone(selectedLesson)}`}>
                        {projectPublicationLabel(selectedLesson)}
                      </span>
                    )}
                    {selectedLesson && projectAvatarEnabled(selectedLesson) && (
                      <span className="rounded-full bg-[color:var(--media-pill-bg)] px-3 py-1.5">
                        Avatar queue
                      </span>
                    )}
                  </div>

                  <div className="flex flex-wrap gap-2">
                    <Button onClick={() => selectedLesson && openEditorForProject(selectedLesson)} disabled={!selectedLesson}>
                      <LayoutPanelTop size={16} />
                      <span>Open Lesson Workspace</span>
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => selectedLesson && navigate(`/watch?lesson=${selectedLesson.id}`)}
                      disabled={!selectedLesson || !projectRenderReady(selectedLesson)}
                    >
                      <Eye size={16} />
                      <span>Preview In Watch</span>
                    </Button>
                    {selectedLesson && projectRenderReady(selectedLesson) && (
                      <Button
                        variant={selectedLesson.is_published ? 'secondary' : 'primary'}
                        onClick={() => handlePublishToggle(selectedLesson, !selectedLesson.is_published)}
                      >
                        {selectedLesson.is_published ? <EyeOff size={16} /> : <Eye size={16} />}
                        <span>{selectedLesson.is_published ? 'Unpublish' : 'Publish'}</span>
                      </Button>
                    )}
                    {selectedLesson && projectAvatarEnabled(selectedLesson) && (
                      <Button
                        variant="secondary"
                        onClick={() => handleRerenderProject(selectedLesson, { avatarEnabled: false })}
                      >
                        <RefreshCcw size={16} />
                        <span>Rerender Without Avatar</span>
                      </Button>
                    )}
                    {selectedLesson && ['processing', 'queued'].includes(projectStatusLabel(selectedLesson).toLowerCase()) && (
                      <Button variant="ghost" onClick={() => handleCancelRender(selectedLesson)}>
                        <span>Cancel Render</span>
                      </Button>
                    )}
                    {selectedLesson && projectRetryable(selectedLesson) && selectedLesson?.latest_job?.id && (
                      <Button variant="secondary" onClick={() => handleRetryJob(selectedLesson)}>
                        <RefreshCcw size={16} />
                        <span>Retry Job</span>
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
                    <div className="rounded-2xl token-surface p-3">
                      {!projectRenderReady(selectedLesson) ? (
                        <div className="space-y-2 text-sm text-[var(--text-secondary)]">
                          <p className="font-semibold text-[var(--text-primary)]">Render: {projectStatusLabel(selectedLesson)}</p>
                          {projectStatusLabel(selectedLesson).toLowerCase() === 'processing' && (
                            <>
                              <p>Progress: %{renderProgressPct}</p>
                              <div className="h-1.5 w-full overflow-hidden rounded-full bg-[color:var(--surface-container-highest)]">
                                <div
                                  className="h-full rounded-full bg-[image:var(--accent-gradient)] transition-all duration-500"
                                  style={{ width: `${Math.max(2, renderProgressPct)}%` }}
                                />
                              </div>
                            </>
                          )}
                          <p>Video and captions will appear after render completes.</p>
                        </div>
                      ) : loadingPreview ? (
                        <p className="text-sm text-[var(--text-secondary)]">Loading preview...</p>
                      ) : (previewLesson?.stream_url || previewLesson?.streaming?.hls?.manifest_url) ? (
                        <VideoStage
                          lesson={{ ...selectedLesson, ...previewLesson }}
                          onPlaybackTimeChange={() => {}}
                          videoRef={previewVideoRef}
                          asSurface={false}
                          captionMissingLabel="Captions will appear after render completes."
                        />
                      ) : (
                        <div className="space-y-2 text-sm text-[var(--text-secondary)]">
                          <p>{previewError || 'Video preview is not available yet.'}</p>
                          <p>Captions will appear after render completes.</p>
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
                        return (
                          <button
                            key={scene.key}
                            type="button"
                            onClick={() => handleSelectScene(scene, index)}
                            className={`focus-ring min-w-[13rem] rounded-2xl p-3 text-left transition ${
                              selected
                                ? 'border border-[color:rgba(208,188,255,0.55)] bg-[color:rgba(208,188,255,0.12)]'
                                : 'token-surface hover:bg-[color:var(--hover-surface)]'
                            }`}
                          >
                            <div className="flex items-start justify-between gap-2">
                              <p className="label-sm">{scene.label}</p>
                              <span className={`rounded-full px-2 py-0.5 text-[0.64rem] font-semibold ${sceneStatusTone(scene.status)}`}>
                                {scene.status}
                              </span>
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
                <span className="text-xs text-[var(--text-secondary)]">{projects.length}</span>
              </div>
              {projectsError && (
                <p className="rounded-xl bg-[color:var(--feedback-danger-bg)] px-3 py-2 text-xs text-[color:var(--feedback-danger-fg)]">
                  {projectsError}
                </p>
              )}

              {loadingProjects ? (
                <p className="text-sm text-[var(--text-secondary)]">Loading lessons...</p>
              ) : projects.length === 0 ? (
                <p className="text-sm text-[var(--text-secondary)]">No lesson drafts yet.</p>
              ) : (
                projects.map((project) => (
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
                        {projectAvatarEnabled(project) && (
                          <span className="rounded-full bg-[color:var(--surface-muted)] px-2 py-0.5 text-[var(--text-secondary)]">
                            Avatar
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
                        <Button variant="secondary" size="sm" onClick={() => navigate(`/watch?lesson=${project.id}`)}>
                          <Eye size={14} />
                          <span>Preview</span>
                        </Button>
                      )}
                      <Button variant="secondary" size="sm" onClick={() => handleRerenderProject(project)}>
                        <RefreshCcw size={14} />
                        <span>Rerender</span>
                      </Button>
                      {projectRetryable(project) && project?.latest_job?.id && (
                        <Button variant="secondary" size="sm" onClick={() => handleRetryJob(project)}>
                          <RefreshCcw size={14} />
                          <span>Retry</span>
                        </Button>
                      )}
                      {['processing', 'queued'].includes(projectStatusLabel(project).toLowerCase()) && (
                        <Button variant="ghost" size="sm" onClick={() => handleCancelRender(project)}>
                          <span>Cancel</span>
                        </Button>
                      )}
                      {projectAvatarEnabled(project) && (
                        <Button variant="secondary" size="sm" onClick={() => handleRerenderProject(project, { avatarEnabled: false })}>
                          <RefreshCcw size={14} />
                          <span>Render Only</span>
                        </Button>
                      )}
                      {projectRenderReady(project) && (
                        <Button
                          variant={project.is_published ? 'secondary' : 'primary'}
                          size="sm"
                          onClick={() => handlePublishToggle(project, !project.is_published)}
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
                ))
              )}
            </SurfaceCard>
          </aside>
        </section>
      ) : (
        <>
          <section
            className={`grid gap-5 ${
              editorFocusMode
                ? '2xl:grid-cols-[minmax(0,1fr)_minmax(28rem,0.78fr)]'
                : '2xl:grid-cols-[15.5rem_minmax(0,1fr)_24rem]'
            }`}
          >
            {!editorFocusMode && (
            <aside className="space-y-4">
              <SurfaceCard className="space-y-4">
                <div>
                  <p className="label-sm">Scene Rail</p>
                  <h2 className="title-lg mt-1 text-[var(--text-primary)]">Lesson Scenes</h2>
                  {latestRenderStatus && (
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                      Render: <span className={`rounded-full px-2 py-0.5 font-semibold ${projectStatusTone({ latest_job: latestRenderStatus })}`}>
                        {projectStatusLabel({ latest_job: latestRenderStatus })}
                      </span>
                    </p>
                  )}
                  {resumeSourceLabel && (
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                      Recovery: {resumeSourceLabel}{recoveredPartsCount > 0 ? ` (${recoveredPartsCount} parts)` : ''}
                    </p>
                  )}
                </div>

                <div className="rail-scroll max-h-[60vh] space-y-3 overflow-y-auto pr-1">
                  {loadingTranscript && selectedLesson ? (
                    <p className="text-sm text-[var(--text-secondary)]">Loading transcript pages...</p>
                  ) : (
                    sceneItems.map((scene, index) => {
                      const selected = scene.key === selectedScene?.key;
                      return (
                        <button
                          key={scene.key}
                          type="button"
                          onClick={() => handleSelectScene(scene, index)}
                          className={`focus-ring block w-full rounded-xl border p-2 text-left transition ${
                            selected
                              ? 'border-[color:rgba(208,188,255,0.55)] bg-[color:rgba(208,188,255,0.12)]'
                              : 'border-[color:rgba(73,68,84,0.2)] token-surface hover:bg-[color:var(--hover-surface)]'
                          }`}
                        >
                          {scene.thumbnailUrl ? (
                            <div
                              className="aspect-video overflow-hidden rounded-lg bg-[var(--card-fallback)]"
                              style={{
                                backgroundImage: `url(${scene.thumbnailUrl})`,
                                backgroundSize: 'cover',
                                backgroundPosition: 'center',
                              }}
                            />
                          ) : (
                            <div className="flex aspect-video items-center justify-center rounded-lg bg-[var(--surface-container-high)] text-2xl font-bold text-[var(--accent-primary)]">
                              {index + 1}
                            </div>
                          )}
                          <div className="mt-2 flex items-center justify-between gap-2">
                            <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">{scene.label}</p>
                            <span className={`rounded-full px-2 py-0.5 text-[0.62rem] font-semibold ${sceneStatusTone(scene.status)}`}>
                              {scene.status}
                            </span>
                          </div>
                          <p className="mt-1 line-clamp-2 text-xs text-[var(--text-secondary)]">{scene.text}</p>
                          <p className="mt-1 text-[0.64rem] text-[var(--text-secondary)]">
                            {scene.subtitleCount ? `${scene.subtitleCount} subtitles` : scene.timing}
                          </p>
                        </button>
                      );
                    })
                  )}
                </div>

                <button
                  type="button"
                  disabled
                  title="Split, merge, reorder, and add scene controls are planned for Phase 5C."
                  className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-dashed border-[var(--border-subtle)] text-xs font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)] opacity-80"
                >
                  <ImagePlus size={14} />
                  Add Scene Coming Later
                </button>
              </SurfaceCard>

              {!selectedLesson ? (
                <SurfaceCard className="space-y-3">
                  <label className="block text-sm text-[var(--text-secondary)]">
                    Cover image
                    <input
                      type="file"
                      accept="image/*"
                      onChange={(event) => setCoverFile(event.target.files?.[0] || null)}
                      className="focus-ring mt-1 block w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] p-2 text-sm text-[var(--text-primary)]"
                    />
                  </label>

                  <label className="block text-sm text-[var(--text-secondary)]">
                    Source file
                    <input
                      type="file"
                      accept={SOURCE_TYPES_ACCEPT}
                      onChange={(event) => setSourceFile(event.target.files?.[0] || null)}
                      className="focus-ring mt-1 block w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] p-2 text-sm text-[var(--text-primary)]"
                    />
                  </label>

                  {sourceFile && (
                    <p className="inline-flex items-center gap-1 text-xs text-[var(--text-secondary)]">
                      <FileText size={12} />
                      {sourceFile.name}
                    </p>
                  )}
                </SurfaceCard>
              ) : (
                <SurfaceCard className="space-y-2">
                  <p className="label-sm">Source Import</p>
                  <p className="text-sm text-[var(--text-secondary)]">
                    This workspace is editing the existing selected project. Source-file import creates new lessons and is kept out of this view to avoid accidental duplicate projects.
                  </p>
                </SurfaceCard>
              )}
            </aside>
            )}

            <div className="space-y-5">
              {selectedLesson && (
                <SurfaceCard elevated className="space-y-3 p-4 sm:p-5">
                  <div className="flex items-center justify-between gap-2">
                    <p className="title-lg text-[var(--text-primary)]">Render Preview</p>
                    <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${projectStatusTone(selectedLesson)}`}>
                      {projectStatusLabel(selectedLesson)}
                    </span>
                  </div>
                  {!projectRenderReady(selectedLesson) ? (
                    <p className="text-sm text-[var(--text-secondary)]">Render tamamlandığında preview burada oynatılabilir olacak.</p>
                  ) : loadingPreview ? (
                    <p className="text-sm text-[var(--text-secondary)]">Preview yükleniyor...</p>
                  ) : (previewLesson?.stream_url || previewLesson?.streaming?.hls?.manifest_url) ? (
                    <VideoStage
                      lesson={{ ...selectedLesson, ...previewLesson }}
                      onPlaybackTimeChange={() => {}}
                      videoRef={previewVideoRef}
                      asSurface={false}
                      captionMissingLabel="Captions will appear after render completes."
                    />
                  ) : (
                    <p className="text-sm text-[var(--text-secondary)]">{previewError || 'Preview şu anda kullanılamıyor.'}</p>
                  )}
                </SurfaceCard>
              )}

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

                <div className={`relative overflow-hidden rounded-2xl border border-[color:rgba(73,68,84,0.22)] bg-[var(--video-stage-bg)] ${editorFocusMode ? 'min-h-[560px]' : 'min-h-[420px]'}`}>
                  {selectedScene?.thumbnailUrl || coverPreviewUrl ? (
                    <img
                      src={selectedScene?.thumbnailUrl || coverPreviewUrl}
                      alt="Selected scene preview"
                      className="absolute inset-0 h-full w-full object-contain bg-[var(--surface-container-high)]"
                    />
                  ) : (
                    <div className="absolute inset-0 flex items-center justify-center bg-[var(--surface-container-high)] text-[var(--text-secondary)]">
                      <div className="text-center">
                        <p className="text-5xl font-bold text-[var(--accent-primary)]">{selectedPageIndex + 1}</p>
                        <p className="mt-2 text-sm font-semibold">{selectedScene?.label || 'No scene selected'}</p>
                      </div>
                    </div>
                  )}
                  <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(5,8,14,0.06)_0%,rgba(5,8,14,0.42)_100%)]" />

                  <div className="absolute inset-x-4 top-4 flex flex-wrap items-center justify-between gap-2 text-xs text-white/85">
                    <span className="rounded-full bg-black/35 px-3 py-1.5">
                      {selectedScene?.label || 'No scene selected'}
                    </span>
                    <span className={`rounded-full px-3 py-1.5 ${sceneStatusTone(selectedScene?.status || 'draft')}`}>
                      {selectedScene?.status || 'draft'}
                    </span>
                  </div>

                  <div className="absolute bottom-4 left-4 right-4 space-y-2">
                    <div className="h-1 rounded-full bg-white/20">
                      <div
                        className="h-full rounded-full bg-[image:var(--accent-gradient)]"
                        style={{ width: `${sceneItems.length ? ((selectedPageIndex + 1) / sceneItems.length) * 100 : 0}%` }}
                      />
                    </div>
                    <div className="flex items-center justify-between text-xs text-white/75">
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
                      return (
                        <button
                          key={scene.key}
                          type="button"
                          onClick={() => handleSelectScene(scene, index)}
                          className={`focus-ring min-w-[8.6rem] overflow-hidden rounded-xl border text-left transition ${
                            selected
                              ? 'border-[color:rgba(208,188,255,0.55)] bg-[color:rgba(208,188,255,0.12)] text-[var(--accent-primary)]'
                              : 'border-[color:rgba(73,68,84,0.2)] token-surface text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                          }`}
                        >
                          <div className="h-[4.6rem] w-full bg-[var(--surface-container-high)]">
                            {scene.thumbnailUrl ? (
                              <div
                                className="h-full w-full"
                                style={{
                                  backgroundImage: `url(${scene.thumbnailUrl})`,
                                  backgroundSize: 'cover',
                                  backgroundPosition: 'center',
                                }}
                              />
                            ) : (
                              <div className="flex h-full w-full items-center justify-center text-lg font-bold text-[var(--accent-primary)]">
                                {index + 1}
                              </div>
                            )}
                          </div>
                          <div className="px-2.5 py-2">
                            <span className="block text-[0.6rem] font-semibold uppercase tracking-[0.09em]">
                              {scene.label}
                            </span>
                            <span className="mt-1 block truncate text-[0.62rem] normal-case tracking-normal">
                              {scene.timing}
                            </span>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              </SurfaceCard>
            </div>

            <aside>
              <SurfaceCard
                elevated
                className={`flex min-h-0 flex-col gap-4 overflow-hidden ${
                  editorFocusMode ? 'max-h-[calc(100vh-10rem)] min-h-[82vh]' : 'max-h-[82vh] min-h-[72vh]'
                }`}
              >
                <div className="flex shrink-0 flex-wrap items-start justify-between gap-3">
                  <div>
                    <h3 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Editor Workspace</h3>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                      {selectedLesson ? selectedLesson.title || 'Selected lesson' : 'Local draft'}
                    </p>
                  </div>
                  <Button size="sm" variant="secondary" onClick={toggleEditorFocusMode}>
                    {editorFocusMode ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
                    <span>{editorFocusMode ? 'Exit focus' : 'Focus mode'}</span>
                  </Button>
                </div>

                <div className="relative z-10 flex shrink-0 items-center justify-between gap-2 bg-[var(--bg-elevated)] pb-1">
                  <div className="rail-scroll flex gap-2 overflow-x-auto">
                    {EDITOR_PANELS.map((panel) => {
                      const selected = activeEditorPanel === panel;
                      return (
                        <button
                          key={panel}
                          type="button"
                          onClick={() => setActiveEditorPanel(panel)}
                          className={`focus-ring inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium transition ${
                            selected
                              ? 'bg-[var(--surface-container-highest)] text-[var(--accent-primary)]'
                              : 'token-surface text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                          }`}
                        >
                          {editorPanelIcon(panel)}
                          <span>{editorPanelLabel(panel)}</span>
                        </button>
                      );
                    })}
                  </div>
                  {selectedLesson && (
                    <Button size="sm" onClick={handleEditorRender}>
                      <RefreshCcw size={14} />
                      <span>Render</span>
                    </Button>
                  )}
                </div>

                <div className="rail-scroll min-h-0 flex-1 overflow-y-auto pr-1">
                  {activeEditorPanel === 'transcript' && (
                    <>
                      {selectedLesson ? (
                        <TranscriptEditorPanel
                          project={selectedLesson}
                          pages={transcriptPages}
                          loading={loadingTranscript}
                          selectedPageKey={selectedPageKey}
                          selectedPageIndex={selectedPageIndex}
                          focusMode={editorFocusMode}
                          onSelectPage={handleSelectTranscriptPage}
                          onPagesUpdated={handleTranscriptPagesUpdated}
                          onProjectRefresh={refreshProjects}
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
                    </>
                  )}

                  {activeEditorPanel === 'slides' && (
                    <div className="space-y-3">
                      <div>
                        <p className="title-lg text-[var(--text-primary)]">Slides</p>
                        <p className="text-xs text-[var(--text-secondary)]">Select a slide to sync the preview, timeline, and transcript editor.</p>
                      </div>
                      {loadingTranscript ? (
                        <p className="text-sm text-[var(--text-secondary)]">Loading transcript pages...</p>
                      ) : sceneItems.length === 0 ? (
                        <p className="text-sm text-[var(--text-secondary)]">No slide or transcript pages available yet.</p>
                      ) : (
                        <div className="space-y-2">
                          {sceneItems.map((scene, index) => {
                            const selected = scene.key === selectedScene?.key;
                            const expanded = Boolean(expandedSlideKeys[scene.key]);
                            const fullText = textValue(scene.fullText || scene.text);
                            return (
                              <article
                                key={scene.key}
                                className={`rounded-2xl p-3 transition ${
                                  selected
                                    ? 'border border-[color:rgba(208,188,255,0.55)] bg-[color:rgba(208,188,255,0.12)]'
                                    : 'token-surface'
                                }`}
                              >
                                <button
                                  type="button"
                                  onClick={() => handleSelectScene(scene, index)}
                                  className="focus-ring w-full text-left"
                                >
                                  {scene.thumbnailUrl && (
                                    <div
                                      className="mb-3 aspect-video rounded-xl bg-[var(--card-fallback)]"
                                      style={{
                                        backgroundImage: `url(${scene.thumbnailUrl})`,
                                        backgroundSize: 'cover',
                                        backgroundPosition: 'center',
                                      }}
                                    />
                                  )}
                                  <div className="flex flex-wrap items-center justify-between gap-2">
                                    <p className="font-medium text-[var(--text-primary)]">{scene.label}</p>
                                    <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${sceneStatusTone(scene.status)}`}>
                                      {scene.status}
                                    </span>
                                  </div>
                                </button>
                                <p className={`mt-2 whitespace-pre-wrap text-sm leading-relaxed text-[var(--text-secondary)] ${expanded ? '' : 'line-clamp-4'}`}>
                                  {fullText || 'No narration text yet'}
                                </p>
                                {fullText.length > 220 && (
                                  <button
                                    type="button"
                                    onClick={() => toggleSlideExpanded(scene.key)}
                                    className="focus-ring mt-2 text-xs font-semibold text-[var(--accent-primary)]"
                                  >
                                    {expanded ? 'Show less' : 'Show full text'}
                                  </button>
                                )}
                              </article>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}

                  {activeEditorPanel === 'notes' && (
                    <div className="space-y-3">
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
                        <p className="text-xs text-[var(--text-secondary)]">{lessonNotesSavedAt || 'Not saved yet'}</p>
                        <Button size="sm" variant="secondary" onClick={saveLessonNotes}>
                          <Save size={14} />
                          <span>Save Notes</span>
                        </Button>
                      </div>
                    </div>
                  )}

                  {activeEditorPanel === 'tts' && (
                    <TtsSettingsPanel
                      project={selectedLesson}
                      transcriptPages={transcriptPages}
                      selectedPageKey={selectedPageKey}
                      onProjectUpdated={handleProjectUpdated}
                      onRerender={handleRerenderProject}
                    />
                  )}
                </div>
              </SurfaceCard>
            </aside>
          </section>

          <div className="sticky bottom-3 z-20 mt-5">
            <SurfaceCard className="token-surface-elevated flex flex-wrap items-center justify-between gap-3 rounded-2xl p-3 sm:p-4">
              <p className="text-xs text-[var(--text-secondary)]">
                {selectedLesson
                  ? 'Editing an existing project. Use the Transcript panel to save changes or save + rerender this same project.'
                  : editorSavedAtLabel || 'Source import notes remain local until you save a local draft or create a lesson draft.'}
              </p>
              {!selectedLesson && (
                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" onClick={persistEditorDraft}>
                    <Save size={16} />
                    <span>Save Local Draft</span>
                  </Button>
                  <Button onClick={publishFromEditor} disabled={submitting || !sourceFile}>
                    <Upload size={16} />
                    <span>{submitting ? 'Creating...' : 'Create Lesson Draft'}</span>
                  </Button>
                </div>
              )}
            </SurfaceCard>
          </div>
        </>
      )}

        <CreateLessonModal
          open={createModalOpen}
          onClose={() => setCreateModalOpen(false)}
          categories={categories}
          submitting={submitting}
          submitError={submitError}
          submitInfo={submitInfo}
          onSubmit={handleCreateLessonFromModal}
        />
    </div>
  );
}
