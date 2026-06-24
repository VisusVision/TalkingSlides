import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, Focus, Heart, MessageSquare, Send, ShieldCheck, Sparkles, UserPlus } from 'lucide-react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  addComment,
  fetchCatalog,
  fetchComments,
  fetchLesson,
  fetchPlaybackToken,
  generateSubtitleTrack,
  getPlaylistContext,
  saveProgress,
  fetchSubtitleTrackBundle,
  fetchSubtitleTracks,
  toggleLike,
  toggleFollowPublisher,
} from '../api';
import VideoStage from '../components/player/VideoStage';
import AvatarOverlayLayer from '../components/player/AvatarOverlayLayer';
import UnavailableStage from '../components/player/UnavailableStage';
import { PLAYER_MODES, resolvePlayerMode } from '../components/player/playerMode';
import ChapterList from '../components/player/ChapterList';
import TranscriptPanel from '../components/player/TranscriptPanel';
import NotesPanel from '../components/player/NotesPanel';
import RelatedLessonsRow from '../components/player/RelatedLessonsRow';
import LessonActionButton from '../components/moderation/LessonActionButton';
import Button from '../components/ui/Button';
import SurfaceCard from '../components/ui/SurfaceCard';
import { formatDuration, normalizeLesson } from '../lib/content';
import { buildChapters, buildTranscriptLines, resolveTranscriptSeekTarget } from '../lib/watch';
import { featureEnabled, useCapabilities } from '../lib/capabilities';
import usePlaybackHeartbeat from '../hooks/usePlaybackHeartbeat';

const COMMENT_PREVIEW_LIMIT = 5;
const AVATAR_ENHANCEMENT_POLL_INTERVAL_MS = 15000;
const PLAYLIST_COLLAPSED_KEY = 'visus-watch-playlist-collapsed';
const AUTOPLAY_NEXT_KEY = 'visus-watch-autoplay-next';
const AUTOPLAY_COUNTDOWN_SECONDS = 5;
const HlsPlayer = lazy(() => import('../components/player/HlsPlayer'));

function normalizeCatalogList(payload) {
  const list = Array.isArray(payload) ? payload : payload.results || [];
  return list.map((item) => normalizeLesson(item));
}

function lessonSearchMatch(lesson, query) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return true;

  return [lesson.title, lesson.description, lesson.teacherName, lesson.categoryName]
    .join(' ')
    .toLowerCase()
    .includes(q);
}

function savedNoteKey(lessonId) {
  return `visus-notes-${lessonId || 'none'}`;
}

function draftNoteKey(lessonId) {
  return `visus-notes-draft-${lessonId || 'none'}`;
}

function focusModeKey(lessonId) {
  return `visus-focus-mode-${lessonId || 'none'}`;
}

function subtitleTrackCode(track) {
  const raw = String(track?.language_code || '').trim().toLowerCase();
  if (!raw || raw === 'original' || track?.is_original === true) return '';
  return raw;
}

function subtitleSelectionKeyForCode(value) {
  const code = String(value || '').trim().toLowerCase();
  if (!code || code === 'off') return 'off';
  if (code === 'original') return 'original';
  if (code.startsWith('translated:')) return code;
  return `translated:${code}`;
}

function lessonOriginalSubtitleUrl(lesson) {
  return [lesson?.vtt_url, lesson?.subtitle_vtt_url]
    .map((value) => String(value || '').trim())
    .find(Boolean) || '';
}

function isReadySubtitleTrack(track) {
  return String(track?.status || '').trim().toLowerCase() === 'ready' && Boolean(track?.vtt_url);
}

function subtitleProviderMessage(track) {
  const providerUsed = String(track?.metadata?.provider_used || track?.provider || '').trim().toLowerCase();
  return providerUsed === 'mock' ? ' Mock provider used; this is not a real translation.' : '';
}

function normalizeSubtitleOptions(lesson, subtitleTracks) {
  const byKey = new Map();
  const originalUrl = lessonOriginalSubtitleUrl(lesson);
  if (originalUrl) {
    byKey.set('original', { key: 'original', label: 'Original' });
  }
  for (const track of subtitleTracks || []) {
    if (!isReadySubtitleTrack(track)) continue;
    const isOriginal = track?.is_original === true
      || String(track?.language_code || '').trim().toLowerCase() === 'original'
      || String(track?.type || '').trim().toLowerCase() === 'original';
    const code = isOriginal ? 'original' : String(track?.language_code || '').trim().toLowerCase();
    if (!code) continue;
    const key = subtitleSelectionKeyForCode(code);
    byKey.set(key, {
      key,
      label: isOriginal
        ? 'Original'
        : String(track?.language_label || track?.label || code.toUpperCase()).trim(),
    });
  }
  const options = Array.from(byKey.values());
  return options.sort((a, b) => {
    if (a.key === 'original') return -1;
    if (b.key === 'original') return 1;
    return a.label.localeCompare(b.label);
  });
}

function formatCommentDate(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function compactCount(value, noun) {
  const count = Math.max(0, Number(value || 0));
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

function avatarOverlayDataForLesson(lesson) {
  const avatarOverlay = lesson?.avatar_overlay || {};
  const avatarStreamUrl = String(avatarOverlay?.stream_url || '').trim();
  return {
    enabled: Boolean(avatarOverlay?.enabled && avatarStreamUrl),
    src: avatarStreamUrl,
    quality: String(avatarOverlay?.quality || '').trim(),
    enhancedAvailable: Boolean(avatarOverlay?.enhanced_available),
    enhancedPending: Boolean(avatarOverlay?.enhanced_pending),
    placement: avatarOverlay?.placement || avatarOverlay?.defaults || lesson?.avatar_placement || {},
    processing: ['queued', 'processing'].includes(String(lesson?.avatar_processing_status || '').trim().toLowerCase()),
    message: String(lesson?.avatar_processing_message || '').trim(),
  };
}

function mergePlaybackIntoLesson(previousLesson, playbackData) {
  if (!previousLesson || !playbackData) return previousLesson;
  return {
    ...previousLesson,
    stream_url: playbackData.video_url || previousLesson.stream_url,
    srt_url: playbackData.srt_url || previousLesson.srt_url,
    vtt_url: playbackData.vtt_url || previousLesson.vtt_url,
    subtitle_vtt_url: playbackData.subtitle_vtt_url || previousLesson.subtitle_vtt_url,
    avatar_overlay: playbackData.avatar_overlay || previousLesson.avatar_overlay,
    avatar_processing_status: playbackData.avatar_processing_status || previousLesson.avatar_processing_status,
    avatar_processing_message: playbackData.avatar_processing_message || previousLesson.avatar_processing_message,
    avatar_visible: playbackData.avatar_visible ?? previousLesson.avatar_visible,
    avatar_available: playbackData.avatar_available ?? previousLesson.avatar_available,
    avatar_updated_at: playbackData.avatar_updated_at || previousLesson.avatar_updated_at,
    avatar_enhancement: playbackData.avatar_enhancement || previousLesson.avatar_enhancement,
    final_avatar_engine_chain: playbackData.final_avatar_engine_chain || previousLesson.final_avatar_engine_chain,
    protection_mode: playbackData.protection_mode || previousLesson.protection_mode,
    allow_mp4_fallback: playbackData.allow_mp4_fallback ?? previousLesson.allow_mp4_fallback,
    playback_status: playbackData.playback_status || previousLesson.playback_status,
    protection: playbackData.protection || previousLesson.protection,
    streaming: playbackData.streaming || previousLesson.streaming,
    drm: playbackData.drm || previousLesson.drm,
    watermark: playbackData.watermark || previousLesson.watermark,
  };
}

function contextRowsFromPayload(context, currentLessonId) {
    const rawItems = Array.isArray(context?.items) ? context.items : [];
    return rawItems
      .map((item, index) => {
        const project = item?.project || item;
        const contextLesson = normalizeLesson(project);
        return {
          key: `${contextLesson.id || index}-${index}`,
          lesson: contextLesson,
          isCurrent: Boolean(item?.is_current) || Number(contextLesson.id) === Number(currentLessonId),
        };
      })
      .filter((row) => row.lesson.id);
  }

  function nextLessonFromContext(context, currentLessonId) {
    const rows = contextRowsFromPayload(context, currentLessonId);
    const currentIndex = rows.findIndex((row) => row.isCurrent);
    const nextRow = currentIndex >= 0
      ? rows.slice(currentIndex + 1).find((row) => !row.isCurrent)
      : rows.find((row) => !row.isCurrent);
    return nextRow?.lesson || rows.find((row) => !row.isCurrent)?.lesson || null;
  }

  function isAutoplayNextEnabled() {
    return window.localStorage.getItem(AUTOPLAY_NEXT_KEY) !== '0';
  }

  function WatchContextPanel({ context, currentLessonId, onOpenLesson }) {
    const [playlistCollapsed, setPlaylistCollapsed] = useState(
      () => window.localStorage.getItem(PLAYLIST_COLLAPSED_KEY) === 'true',
    );
    const rows = contextRowsFromPayload(context, currentLessonId);

    const isPlaylistMode = context?.mode === 'playlist';
    useEffect(() => {
      if (isPlaylistMode) {
        window.localStorage.setItem(PLAYLIST_COLLAPSED_KEY, playlistCollapsed ? 'true' : 'false');
      }
    }, [isPlaylistMode, playlistCollapsed]);

    if (!rows.length) return null;

    const title = isPlaylistMode ? 'More from this playlist' : 'More from this publisher';
    const subtitle = isPlaylistMode ? context?.playlist?.title || '' : rows[0]?.lesson?.teacherName || '';
    const nextLesson = nextLessonFromContext(context, currentLessonId);

    if (isPlaylistMode && playlistCollapsed) {
      return (
        <SurfaceCard className="p-4">
          <button
            type="button"
            onClick={() => setPlaylistCollapsed(false)}
            className="focus-ring flex w-full items-center justify-between gap-3 rounded-xl text-left"
            aria-expanded="false"
          >
            <span className="min-w-0">
              <span className="line-clamp-1 text-sm font-semibold text-[var(--text-primary)]">
                Next: {nextLesson?.title || 'End of playlist'}
              </span>
              {subtitle ? (
                <span className="mt-1 block truncate text-xs text-[var(--text-secondary)]">{subtitle}</span>
              ) : null}
            </span>
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-[var(--surface-container-highest)] px-3 py-1.5 text-xs font-semibold text-[var(--text-secondary)]">
              Expand
              <ChevronDown size={14} />
            </span>
          </button>
        </SurfaceCard>
      );
    }

    return (
      <SurfaceCard className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-[var(--text-primary)]">{title}</p>
            {subtitle && (
              <p className="mt-1 truncate text-xs text-[var(--text-secondary)]">{subtitle}</p>
            )}
          </div>
          {isPlaylistMode ? (
            <button
              type="button"
              onClick={() => setPlaylistCollapsed(true)}
              className="focus-ring inline-flex shrink-0 items-center gap-1 rounded-full bg-[var(--surface-container-highest)] px-3 py-1.5 text-xs font-semibold text-[var(--text-secondary)] transition hover:bg-[color:var(--hover-surface-strong)]"
              aria-expanded="true"
            >
              Hide
              <ChevronDown size={14} className="rotate-180" />
            </button>
          ) : null}
        </div>
        <div className="rail-scroll max-h-[17.5rem] space-y-2 overflow-y-auto pr-1 lg:max-h-[22rem]">
          {rows.map((row) => {
            const { lesson: contextLesson, isCurrent } = row;
            return (
              <button
                key={row.key}
                type="button"
                disabled={isCurrent}
                onClick={() => onOpenLesson(contextLesson.id)}
                className={[
                  'focus-ring flex w-full items-center gap-3 rounded-xl border p-2 text-left transition',
                  isCurrent
                    ? 'border-[color:var(--accent-primary)] bg-[color:color-mix(in_srgb,var(--accent-primary),transparent_86%)]'
                    : 'border-[var(--border-subtle)] bg-[var(--surface-container-high)] hover:bg-[color:var(--hover-surface-strong)]',
                ].join(' ')}
              >
                {contextLesson.imageUrl ? (
                  <img
                    src={contextLesson.imageUrl}
                    alt=""
                    className="h-16 w-24 shrink-0 rounded-lg object-cover"
                  />
                ) : (
                  <span className="flex h-16 w-24 shrink-0 items-center justify-center rounded-lg bg-[var(--surface-container-highest)] text-xs font-semibold text-[var(--accent-primary)]">
                    {String(contextLesson.title || 'L').charAt(0).toUpperCase()}
                  </span>
                )}
                <span className="min-w-0 flex-1">
                  <span className="line-clamp-2 text-sm font-semibold leading-snug text-[var(--text-primary)]">
                    {contextLesson.title}
                  </span>
                  <span className="mt-1 block truncate text-xs text-[var(--text-secondary)]">
                    {isCurrent ? 'Now playing' : `${contextLesson.categoryName || 'Lesson'} - ${formatDuration(contextLesson.durationMinutes || 8)}`}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </SurfaceCard>
    );
  }

function WatchStudyPanel({
  lesson,
  videoRef,
  avatarFeatureEnabled,
  notes,
  onNotesChange,
  onSave,
  savedAtLabel,
  unsaved,
  saveActionLabel,
  saveHint,
}) {
  const avatar = avatarOverlayDataForLesson(lesson);

  return (
    <SurfaceCard data-testid="study-mode-panel" className="space-y-3 p-3 xl:sticky xl:top-4">
      {avatarFeatureEnabled && (
      <div className="space-y-2">
        {avatar.enabled ? (
          <AvatarOverlayLayer
            lessonId={lesson?.id}
            src={avatar.src}
            enabled={avatar.enabled}
            placement={avatar.placement}
            videoRef={videoRef}
            mode="study-panel"
          />
        ) : (
          <div className="flex min-h-[8rem] items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-container-high)] px-3 text-center text-xs text-[var(--text-secondary)]">
            {avatar.processing ? (avatar.message || 'Avatar is being prepared.') : 'Avatar is not available for this lesson.'}
          </div>
        )}
      </div>
      )}

      <label className="block text-xs font-medium text-[var(--text-secondary)]">
        Notes
        <textarea
          data-testid="study-mode-notes"
          value={notes}
          onChange={(event) => onNotesChange(event.target.value)}
          placeholder="Capture ideas, definitions, and questions while watching..."
          className="focus-ring mt-1 min-h-[260px] w-full resize-y rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 text-sm leading-relaxed text-[var(--text-primary)]"
        />
      </label>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-[var(--text-secondary)]">
          {savedAtLabel || 'Auto-saved locally'}{unsaved ? ' - unsaved changes' : ''}
        </p>
        <Button size="sm" onClick={onSave}>
          {saveActionLabel}
        </Button>
      </div>

      {saveHint && (
        <p className="rounded-lg bg-[color:color-mix(in_srgb,var(--surface-muted),transparent_6%)] px-3 py-2 text-xs text-[var(--text-secondary)]">
          {saveHint}
        </p>
      )}
    </SurfaceCard>
  );
}

function PublisherIdentity({ publisherId, publisherName, publisherAvatarUrl, publisherInitials, followerCount }) {
  const initial = String(publisherInitials || publisherName || 'V').trim().slice(0, 2).toUpperCase() || 'V';
  const avatar = publisherAvatarUrl ? (
    <img
      src={publisherAvatarUrl}
      alt=""
      className="h-9 w-9 shrink-0 rounded-full border border-[var(--border-subtle)] object-cover"
    />
  ) : (
    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[var(--border-subtle)] bg-[var(--surface-container-highest)] text-xs font-bold text-[var(--accent-primary)]">
      {initial}
    </span>
  );

  const content = (
    <>
      {avatar}
      <span className="min-w-0">
        <span className="block truncate font-semibold text-[var(--text-primary)]">{publisherName}</span>
        {followerCount > 0 ? (
          <span className="block text-xs text-[var(--text-secondary)]">{compactCount(followerCount, 'follower')}</span>
        ) : null}
      </span>
    </>
  );

  if (!publisherId) {
    return <span className="inline-flex min-w-0 items-center gap-2 text-sm">{content}</span>;
  }

  return (
    <Link
      to={`/channel/${publisherId}`}
      className="focus-ring inline-flex min-w-0 max-w-full items-center gap-2 rounded-full bg-[var(--surface-container-high)] py-1 pl-1 pr-3 text-sm transition hover:bg-[color:var(--hover-surface-strong)]"
    >
      {content}
    </Link>
  );
}



export default function Watch({ searchQuery, user, onLoginRequest }) {
  const navigate = useNavigate();
  const { capabilities } = useCapabilities();
  const avatarFeatureEnabled = featureEnabled(capabilities, 'avatar');
  const videoRef = useRef(null);
  const [searchParams, setSearchParams] = useSearchParams();

  const [catalogLessons, setCatalogLessons] = useState([]);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [loadingLesson, setLoadingLesson] = useState(true);
  const [lessonError, setLessonError] = useState('');

  const [lesson, setLesson] = useState(null);
  const [transcriptPayload, setTranscriptPayload] = useState(null);
  const [subtitleTracks, setSubtitleTracks] = useState([]);
  const [requestableSubtitleLanguages, setRequestableSubtitleLanguages] = useState([]);
  const [requestLanguageCode, setRequestLanguageCode] = useState('en');
  const [subtitleRequestMessage, setSubtitleRequestMessage] = useState('');
  const [requestingSubtitleLanguage, setRequestingSubtitleLanguage] = useState(false);
  const [preferredSubtitleLanguage, setPreferredSubtitleLanguage] = useState('');
  const [selectedSubtitleKey, setSelectedSubtitleKey] = useState('off');
  const [pendingSubtitleRequest, setPendingSubtitleRequest] = useState(null);
  const [playbackTime, setPlaybackTime] = useState(0);
  const [playbackActive, setPlaybackActive] = useState(false);
  const [focusMode, setFocusMode] = useState(false);
  const [notesCollapsed, setNotesCollapsed] = useState(false);
  const [transcriptCollapsed, setTranscriptCollapsed] = useState(false);
  const [chaptersCollapsed, setChaptersCollapsed] = useState(false);
  const [notes, setNotes] = useState('');
  const [savedNotes, setSavedNotes] = useState('');
  const [savedAtLabel, setSavedAtLabel] = useState('Auto-saved locally');
  const [saveHint, setSaveHint] = useState('');
  const [comments, setComments] = useState([]);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [commentText, setCommentText] = useState('');
  const [commentError, setCommentError] = useState('');
  const [commentSubmitting, setCommentSubmitting] = useState(false);
  const [commentsExpanded, setCommentsExpanded] = useState(false);
  const [likeBusy, setLikeBusy] = useState(false);
  const [likeError, setLikeError] = useState('');
  const [followBusy, setFollowBusy] = useState(false);
  const [followError, setFollowError] = useState('');
  const [playlistContext, setPlaylistContext] = useState(null);
  const [autoplayPrompt, setAutoplayPrompt] = useState(null);
  const progressSavedAtRef = useRef(0);
  const resumeAppliedKeyRef = useRef('');
  const commentsSectionRef = useRef(null);
  const manualSubtitleSelectionLessonRef = useRef('');

  const activeLessonId = Number(searchParams.get('lesson') || 0) || null;
  const resumeRequested = searchParams.get('resume') === '1';

  useEffect(() => {
    if (!activeLessonId) {
      setFocusMode(false);
      return;
    }
    setFocusMode(window.localStorage.getItem(focusModeKey(activeLessonId)) === 'true');
  }, [activeLessonId]);

  const handleFocusModeToggle = useCallback(() => {
    setFocusMode((previous) => {
      const next = !previous;
      if (activeLessonId) {
        window.localStorage.setItem(focusModeKey(activeLessonId), next ? 'true' : 'false');
      }
      return next;
    });
  }, [activeLessonId]);

  useEffect(() => {
    let active = true;

    async function loadCatalogLessons() {
      setLoadingCatalog(true);
      try {
        const payload = await fetchCatalog();
        if (!active) return;
        const list = normalizeCatalogList(payload);
        setCatalogLessons(list);

        if (!activeLessonId && list[0]?.id) {
          setSearchParams({ lesson: String(list[0].id) }, { replace: true });
        }
      } catch {
        if (!active) return;
        setCatalogLessons([]);
      } finally {
        if (active) {
          setLoadingCatalog(false);
        }
      }
    }

    loadCatalogLessons();

    return () => {
      active = false;
    };
  }, [activeLessonId, setSearchParams]);

  useEffect(() => {
    if (!activeLessonId) return;

    let active = true;

    async function loadLessonData() {
      setLoadingLesson(true);
      setLessonError('');
      setPlaylistContext(null);

      try {
        const [lessonData, tracksData] = await Promise.all([
          fetchLesson(activeLessonId),
          fetchSubtitleTrackBundle(activeLessonId).catch(() => ({ tracks: [], requestableLanguages: [] })),
        ]);

        if (!active) return;

        setLesson(lessonData);
        setTranscriptPayload({
          pages: Array.isArray(lessonData?.transcript_pages) ? lessonData.transcript_pages : [],
        });
        setSubtitleTracks(tracksData?.tracks || []);
        setRequestableSubtitleLanguages(tracksData?.requestableLanguages || []);
        const hasOriginalSubtitles = normalizeSubtitleOptions(lessonData, tracksData?.tracks || [])
          .some((option) => option.key === 'original');

        setSubtitleRequestMessage('');
        setRequestingSubtitleLanguage(false);
        setPreferredSubtitleLanguage(hasOriginalSubtitles ? 'original' : '');
        setSelectedSubtitleKey(hasOriginalSubtitles ? 'original' : 'off');
        setPendingSubtitleRequest(null);
        setPlaybackTime(0);
        setPlaybackActive(false);
        setAutoplayPrompt(null);
        progressSavedAtRef.current = 0;
        manualSubtitleSelectionLessonRef.current = '';
        setLikeError('');
        setFollowError('');
      } catch (err) {
        if (!active) return;
        setLessonError(err.message || 'Failed to load lesson.');
      } finally {
        if (active) {
          setLoadingLesson(false);
        }
      }
    }

    loadLessonData();

    return () => {
      active = false;
    };
  }, [activeLessonId]);

  useEffect(() => {
    if (!activeLessonId || loadingLesson || !lesson || Number(lesson.id) !== Number(activeLessonId)) {
      setPlaylistContext(null);
      return undefined;
    }

    let active = true;
    setPlaylistContext(null);

    getPlaylistContext(activeLessonId)
      .then((payload) => {
        if (active) setPlaylistContext(payload);
      })
      .catch(() => {
        if (active) setPlaylistContext(null);
      });

    return () => {
      active = false;
    };
  }, [activeLessonId, lesson?.id, loadingLesson]);

  useEffect(() => {
    if (!activeLessonId) {
      setComments([]);
      setCommentText('');
      setCommentError('');
      setCommentsExpanded(false);
      return undefined;
    }

    let active = true;

    async function loadComments() {
      setCommentsLoading(true);
      setCommentError('');
      try {
        const payload = await fetchComments(activeLessonId);
        if (!active) return;
        setComments(Array.isArray(payload) ? payload : payload?.results || []);
        setCommentsExpanded(false);
      } catch (err) {
        if (!active) return;
        setCommentError(err.message || 'Could not load comments.');
        setComments([]);
      } finally {
        if (active) setCommentsLoading(false);
      }
    }

    setCommentText('');
    loadComments();
    return () => {
      active = false;
    };
  }, [activeLessonId]);

  useEffect(() => {
    const persisted = window.localStorage.getItem(savedNoteKey(activeLessonId)) || '';
    const draft = window.localStorage.getItem(draftNoteKey(activeLessonId));
    const hydrated = draft !== null ? draft : persisted;

    setNotes(hydrated || '');
    setSavedNotes(persisted || '');

    if (draft !== null && draft !== persisted) {
      setSavedAtLabel('Loaded local draft');
      setSaveHint('Unsaved note draft restored from this browser cache.');
      return;
    }

    setSavedAtLabel(persisted ? 'Loaded saved note' : 'Drafting locally');
    setSaveHint('');
  }, [activeLessonId]);

  useEffect(() => {
    const draftKey = draftNoteKey(activeLessonId);
    if (notes !== savedNotes) {
      window.localStorage.setItem(draftKey, notes);
      return;
    }

    window.localStorage.removeItem(draftKey);
  }, [activeLessonId, notes, savedNotes]);

  const hasUnsavedNotes = notes !== savedNotes;

  useEffect(() => {
    if (!hasUnsavedNotes) return undefined;

    const handleBeforeUnload = (event) => {
      event.preventDefault();
      event.returnValue = '';
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [hasUnsavedNotes]);

  const saveNotes = () => {
    if (!user) {
      window.localStorage.setItem(draftNoteKey(activeLessonId), notes);
      setSavedAtLabel('Draft kept locally');
      setSaveHint('Sign in to save this note to your account session.');

      if (typeof onLoginRequest === 'function') {
        onLoginRequest(activeLessonId ? `/watch?lesson=${activeLessonId}` : '/watch');
      }
      return;
    }

    window.localStorage.setItem(savedNoteKey(activeLessonId), notes);
    window.localStorage.removeItem(draftNoteKey(activeLessonId));
    setSavedNotes(notes);
    setSaveHint('');
    setSavedAtLabel(`Saved at ${new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`);
  };

  const visibleLessons = useMemo(
    () => catalogLessons.filter((item) => lessonSearchMatch(item, searchQuery)),
    [catalogLessons, searchQuery],
  );

  const relatedLessons = useMemo(() => {
    const source = visibleLessons.length ? visibleLessons : catalogLessons;
    return source.filter((item) => item.id !== activeLessonId).slice(0, 12);
  }, [visibleLessons, catalogLessons, activeLessonId]);

  const autoplayNextLesson = useMemo(
    () => nextLessonFromContext(playlistContext, activeLessonId) || relatedLessons[0] || null,
    [activeLessonId, playlistContext, relatedLessons],
  );

  const openLessonById = useCallback((id) => {
    const lessonId = Number(id || 0);
    if (!lessonId) return;
    setAutoplayPrompt(null);
    setSearchParams({ lesson: String(lessonId) });
  }, [setSearchParams]);

  const chapters = useMemo(() => buildChapters(transcriptPayload, lesson), [transcriptPayload, lesson]);
  const transcriptLines = useMemo(
    () => buildTranscriptLines(transcriptPayload, lesson),
    [transcriptPayload, lesson],
  );
  const readySubtitleCodes = useMemo(() => {
    const codes = new Set();
    for (const track of subtitleTracks || []) {
      const code = subtitleTrackCode(track);
      if (code && isReadySubtitleTrack(track)) codes.add(code);
    }
    return codes;
  }, [subtitleTracks]);
  const activeSubtitleCodes = useMemo(() => {
    const codes = new Set();
    for (const track of subtitleTracks || []) {
      const code = subtitleTrackCode(track);
      const status = String(track?.status || '').trim().toLowerCase();
      if (code && ['pending', 'processing', 'ready'].includes(status)) codes.add(code);
    }
    return codes;
  }, [subtitleTracks]);
  const missingSubtitleLanguages = useMemo(
    () => requestableSubtitleLanguages.filter((language) => !activeSubtitleCodes.has(language.code)),
    [activeSubtitleCodes, requestableSubtitleLanguages],
  );
  const selectedRequestLanguage = useMemo(
    () => (
      pendingSubtitleRequest
      || missingSubtitleLanguages.find((language) => language.code === requestLanguageCode)
      || missingSubtitleLanguages[0]
      || requestableSubtitleLanguages.find((language) => language.code === requestLanguageCode)
      || requestableSubtitleLanguages[0]
    ),
    [missingSubtitleLanguages, pendingSubtitleRequest, requestLanguageCode, requestableSubtitleLanguages],
  );
  const subtitleOptions = useMemo(
    () => normalizeSubtitleOptions(lesson, subtitleTracks),
    [lesson, subtitleTracks],
  );
  const selectedSubtitleOption = subtitleOptions.find((option) => option.key === selectedSubtitleKey) || null;

  useEffect(() => {
    const lessonKey = String(activeLessonId || lesson?.id || '');
    if (!lessonKey || manualSubtitleSelectionLessonRef.current === lessonKey) return;
    if (selectedSubtitleKey !== 'off') return;
    if (subtitleOptions.some((option) => option.key === 'original')) {
      setSelectedSubtitleKey('original');
    }
  }, [activeLessonId, lesson?.id, selectedSubtitleKey, subtitleOptions]);

  const playerCapabilities = useMemo(() => {
    if (typeof document === 'undefined') {
      return {
        nativeHlsSupported: false,
        hlsJsSupported: false,
        emeSupported: false,
        hlsEnabled: import.meta.env.VITE_PLAYER_ENABLE_HLS !== 'false',
        drmShakaEnabled: import.meta.env.VITE_PLAYER_ENABLE_DRM_SHAKA === 'true',
      };
    }

    const probe = document.createElement('video');
    const mediaSource = typeof window !== 'undefined' ? (window.MediaSource || window.WebKitMediaSource) : null;
    return {
      nativeHlsSupported: Boolean(probe.canPlayType('application/vnd.apple.mpegurl')),
      hlsJsSupported: Boolean(
        mediaSource
        && typeof mediaSource.isTypeSupported === 'function'
        && mediaSource.isTypeSupported('video/mp4; codecs="avc1.42E01E,mp4a.40.2"'),
      ),
      emeSupported: typeof navigator !== 'undefined' && typeof navigator.requestMediaKeySystemAccess === 'function',
      hlsEnabled: import.meta.env.VITE_PLAYER_ENABLE_HLS !== 'false',
      drmShakaEnabled: import.meta.env.VITE_PLAYER_ENABLE_DRM_SHAKA === 'true',
    };
  }, []);
  const playerMode = useMemo(
    () => resolvePlayerMode(lesson, playerCapabilities),
    [lesson, playerCapabilities],
  );
  const playbackLesson = useMemo(() => {
    if (!lesson) return lesson;
    if (playerMode.mode !== PLAYER_MODES.PUBLIC_MP4) return lesson;
    return {
      ...lesson,
      stream_url: playerMode.fallbackUrl || lesson.stream_url || lesson.video_url || '',
    };
  }, [lesson, playerMode]);
  const playableMode = playerMode.mode === PLAYER_MODES.PUBLIC_MP4 || playerMode.mode === PLAYER_MODES.SECURE_HLS;
  const playbackSourceKey = useMemo(
    () => [
      activeLessonId || '',
      playerMode.mode || '',
      playbackLesson?.stream_url || '',
      playerMode.manifestUrl || '',
      playerMode.fallbackUrl || '',
    ].join('|'),
    [activeLessonId, playbackLesson?.stream_url, playerMode.fallbackUrl, playerMode.manifestUrl, playerMode.mode],
  );
  const handlePlaybackStarted = useCallback(() => {
    setPlaybackActive(true);
    setAutoplayPrompt(null);
  }, []);
  const handlePlaybackStopped = useCallback(() => {
    setPlaybackActive(false);
  }, []);
  const handlePlaybackEnded = useCallback(() => {
    setPlaybackActive(false);
    if (!autoplayNextLesson?.id || !isAutoplayNextEnabled()) {
      return;
    }
    setAutoplayPrompt({
      lesson: autoplayNextLesson,
      secondsRemaining: AUTOPLAY_COUNTDOWN_SECONDS,
    });
  }, [autoplayNextLesson]);
  const handlePlaybackDenied = useCallback(() => {
    setPlaybackActive(false);
  }, []);
  const playbackHeartbeat = usePlaybackHeartbeat({
    lessonId: activeLessonId,
    active: Boolean(playbackActive && playableMode && !loadingLesson),
    videoRef,
    sourceKey: playbackSourceKey,
    visibilityLock: Boolean(lesson?.protection?.visibility_lock),
    onDenied: handlePlaybackDenied,
  });

  useEffect(() => {
    if (!autoplayPrompt) return undefined;

    const nextLessonId = Number(autoplayPrompt.lesson?.id || 0);
    if (!nextLessonId) {
      setAutoplayPrompt(null);
      return undefined;
    }

    if (autoplayPrompt.secondsRemaining <= 0) {
      openLessonById(nextLessonId);
      return undefined;
    }

    const timerId = window.setTimeout(() => {
      setAutoplayPrompt((current) => {
        if (!current || Number(current.lesson?.id || 0) !== nextLessonId) return current;
        return {
          ...current,
          secondsRemaining: Math.max(0, Number(current.secondsRemaining || 0) - 1),
        };
      });
    }, 1000);

    return () => window.clearTimeout(timerId);
  }, [autoplayPrompt, openLessonById]);

  useEffect(() => {
    const enhancedPending = Boolean(lesson?.avatar_overlay?.enhanced_pending);
    if (!avatarFeatureEnabled || !activeLessonId || !playbackActive || !enhancedPending || loadingLesson) return undefined;

    let cancelled = false;
    const pollForEnhancedAvatar = async () => {
      try {
        const playbackData = await fetchPlaybackToken(activeLessonId);
        if (cancelled || !playbackData?.avatar_overlay) return;
        const nextOverlay = playbackData.avatar_overlay;
        const nextUrl = String(nextOverlay.stream_url || '').trim();
        const currentUrl = String(lesson?.avatar_overlay?.stream_url || '').trim();
        if (nextOverlay.enhanced_available && nextUrl && nextUrl !== currentUrl) {
          setLesson((previous) => mergePlaybackIntoLesson(previous, playbackData));
        } else if (!nextOverlay.enhanced_pending && nextUrl) {
          setLesson((previous) => mergePlaybackIntoLesson(previous, playbackData));
        }
      } catch {
        // Keep base playback undisturbed if enhancement polling fails.
      }
    };

    const intervalId = window.setInterval(pollForEnhancedAvatar, AVATAR_ENHANCEMENT_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [
    activeLessonId,
    avatarFeatureEnabled,
    loadingLesson,
    playbackActive,
    lesson?.avatar_overlay?.enhanced_pending,
    lesson?.avatar_overlay?.stream_url,
  ]);

  const userProgressPct = Math.max(0, Math.min(100, Number(lesson?.user_progress || 0)));
  const likedByMe = Boolean(lesson?.user_liked || lesson?.liked_by_me);
  const likeCount = Math.max(0, Number(lesson?.like_count || 0));
  const commentCount = Math.max(0, Number(lesson?.comment_count || 0), comments.length);
  const publisherId = Number(lesson?.publisher_id || lesson?.teacher_id || lesson?.teacherId || 0) || null;
  const publisherName = lesson?.publisher_display_name || lesson?.teacher_name || lesson?.teacherName || 'VISUS Instructor';
  const publisherInitials = lesson?.publisher_initials || '';
  const publisherAvatarUrl = (
    lesson?.publisher_logo_url
    || lesson?.publisher?.logo_url
    || lesson?.publisher_avatar_url
    || lesson?.publisher?.avatar_url
    || lesson?.teacher_avatar_url
    || lesson?.avatar_url
    || ''
  );
  const publisherFollowerCount = Math.max(0, Number(lesson?.publisher_follower_count ?? lesson?.follower_count ?? 0));
  const isFollowingPublisher = Boolean(lesson?.publisher_is_following ?? lesson?.is_following_publisher);
  const isOwnPublisher = Boolean(user?.id && publisherId && Number(user.id) === Number(publisherId));
  const visibleComments = commentsExpanded ? comments : comments.slice(0, COMMENT_PREVIEW_LIMIT);
  const hiddenCommentCount = Math.max(0, comments.length - COMMENT_PREVIEW_LIMIT);
  const progressLabel = userProgressPct > 0 ? `${Math.round(userProgressPct)}%` : '';

  useEffect(() => {
    if (!preferredSubtitleLanguage) return;
    const targetKey = subtitleSelectionKeyForCode(preferredSubtitleLanguage);
    if (subtitleOptions.some((option) => option.key === targetKey)) {
      setSelectedSubtitleKey(targetKey);
    }
  }, [preferredSubtitleLanguage, subtitleOptions]);

  useEffect(() => {
    setPlaybackActive(false);
  }, [playbackSourceKey]);

  useEffect(() => {
    const canResumePlayback = playerMode.mode === PLAYER_MODES.PUBLIC_MP4 || playerMode.mode === PLAYER_MODES.SECURE_HLS;
    if (!resumeRequested || !user || !activeLessonId || loadingLesson || !canResumePlayback) return undefined;
    if (userProgressPct <= 0 || userProgressPct >= 95) return undefined;

    const resumeKey = `${activeLessonId}:${userProgressPct}`;
    if (resumeAppliedKeyRef.current === resumeKey) return undefined;

    const video = videoRef.current;
    if (!video) return undefined;

    const applyResume = () => {
      const duration = Number(video.duration || 0);
      if (!Number.isFinite(duration) || duration <= 0 || resumeAppliedKeyRef.current === resumeKey) return;
      const nextTime = duration * (userProgressPct / 100);
      video.currentTime = Math.max(0, Math.min(duration - 1, nextTime));
      setPlaybackTime(video.currentTime);
      progressSavedAtRef.current = userProgressPct;
      resumeAppliedKeyRef.current = resumeKey;
    };

    if (Number.isFinite(Number(video.duration)) && Number(video.duration) > 0) {
      applyResume();
      return undefined;
    }

    video.addEventListener('loadedmetadata', applyResume, { once: true });
    return () => {
      video.removeEventListener('loadedmetadata', applyResume);
    };
  }, [activeLessonId, loadingLesson, playerMode.mode, resumeRequested, user, userProgressPct]);

  useEffect(() => {
    if (pendingSubtitleRequest) return;
    if (missingSubtitleLanguages.length && !missingSubtitleLanguages.some((language) => language.code === requestLanguageCode)) {
      setRequestLanguageCode(missingSubtitleLanguages[0].code);
    }
  }, [missingSubtitleLanguages, pendingSubtitleRequest, requestLanguageCode]);

  useEffect(() => {
    if (!activeLessonId || !pendingSubtitleRequest?.code) return undefined;

    let active = true;
    let timeoutId;

    const pollSubtitleTracks = async () => {
      try {
        const tracks = await fetchSubtitleTracks(activeLessonId);
        if (!active) return;
        setSubtitleTracks(tracks || []);
        const track = (tracks || []).find((item) => subtitleTrackCode(item) === pendingSubtitleRequest.code);
        const status = String(track?.status || '').trim().toLowerCase();
        if (track && isReadySubtitleTrack(track)) {
          setPreferredSubtitleLanguage(pendingSubtitleRequest.code);
          setSubtitleRequestMessage(`${pendingSubtitleRequest.label} subtitles are ready. Select them from the subtitle menu.${subtitleProviderMessage(track)}`);
          setPendingSubtitleRequest(null);
          setRequestingSubtitleLanguage(false);
          return;
        }
        if (status === 'failed') {
          setSubtitleRequestMessage(track?.error_message || `Could not generate ${pendingSubtitleRequest.label} subtitles.`);
          setPendingSubtitleRequest(null);
          setRequestingSubtitleLanguage(false);
        }
      } catch (err) {
        if (!active) return;
        setSubtitleRequestMessage(err.message || `Could not refresh ${pendingSubtitleRequest.label} subtitle status.`);
      }
    };

    pollSubtitleTracks();
    timeoutId = window.setInterval(pollSubtitleTracks, 3000);

    return () => {
      active = false;
      if (timeoutId) window.clearInterval(timeoutId);
    };
  }, [activeLessonId, pendingSubtitleRequest]);

  const activeChapterId = useMemo(() => {
    const activeChapter = chapters.find(
      (chapter) => playbackTime >= chapter.startSeconds && playbackTime < chapter.endSeconds,
    );
    return activeChapter?.id || chapters[0]?.id || null;
  }, [chapters, playbackTime]);

  const jumpToTime = (seconds) => {
    const video = videoRef.current;
    if (!video) return;
    const target = resolveTranscriptSeekTarget(seconds, video.duration);
    if (target === null) return;
    video.currentTime = target;
    video.play().catch(() => {});
    setPlaybackTime(target);
  };

  const handlePlaybackTimeChange = (seconds) => {
    const currentTime = Number(seconds || 0);
    setPlaybackTime(currentTime);

    if (!user || !activeLessonId || !videoRef.current?.duration) {
      return;
    }

    const percent = Math.round((currentTime / Number(videoRef.current.duration || 1)) * 100);
    if (Number.isNaN(percent)) {
      return;
    }

    if (Math.abs(percent - progressSavedAtRef.current) >= 5) {
      progressSavedAtRef.current = percent;
      saveProgress(activeLessonId, Math.max(0, Math.min(100, percent))).catch(() => {});
    }
  };

  const handleRequestSubtitleLanguage = async () => {
    const language = selectedRequestLanguage;
    if (!activeLessonId || !language || requestingSubtitleLanguage) return;

    if (readySubtitleCodes.has(language.code)) {
      setPreferredSubtitleLanguage(language.code);
      setSubtitleRequestMessage(`${language.label} subtitles are already available in the player menu.`);
      return;
    }

    setRequestingSubtitleLanguage(true);
    setSubtitleRequestMessage(`Generating ${language.label} subtitles...`);

    try {
      const track = await generateSubtitleTrack(activeLessonId, {
        language_code: language.code,
        language_label: language.label,
        provider: 'auto',
      });
      if (isReadySubtitleTrack(track)) {
        const refreshedTracks = await fetchSubtitleTracks(activeLessonId);
        setSubtitleTracks(refreshedTracks || []);
        setPreferredSubtitleLanguage(language.code);
        setPendingSubtitleRequest(null);
        setSubtitleRequestMessage(`${language.label} subtitles are ready. Select them from the subtitle menu.${subtitleProviderMessage(track)}`);
        setRequestingSubtitleLanguage(false);
        return;
      }
      if (String(track?.status || '').trim().toLowerCase() === 'failed') {
        setPendingSubtitleRequest(null);
        setSubtitleRequestMessage(track?.error_message || `Could not generate ${language.label} subtitles.`);
        setRequestingSubtitleLanguage(false);
        return;
      }
      setPendingSubtitleRequest(language);
      setSubtitleRequestMessage(`Generating ${language.label} subtitles...`);
    } catch (err) {
      setSubtitleRequestMessage(err.message || `Could not generate ${language.label} subtitles.`);
      setRequestingSubtitleLanguage(false);
    }
  };

  const loginRedirectPath = activeLessonId ? `/watch?lesson=${activeLessonId}` : '/watch';

  const handleToggleLike = async () => {
    if (!activeLessonId || likeBusy) return;
    if (!user) {
      setLikeError('Sign in to like this lesson.');
      if (typeof onLoginRequest === 'function') {
        onLoginRequest(loginRedirectPath);
      }
      return;
    }
    setLikeBusy(true);
    setLikeError('');
    try {
      const payload = await toggleLike(activeLessonId);
      setLesson((current) => current ? {
        ...current,
        user_liked: Boolean(payload?.liked),
        liked_by_me: Boolean(payload?.liked),
        like_count: Number(payload?.like_count ?? current.like_count ?? 0),
      } : current);
    } catch (err) {
      setLikeError(err.message || 'Could not update like.');
    } finally {
      setLikeBusy(false);
    }
  };

  const handleToggleFollow = async () => {
    if (!publisherId || followBusy || isOwnPublisher) return;
    if (!user) {
      setFollowError('Sign in to follow this publisher.');
      if (typeof onLoginRequest === 'function') {
        onLoginRequest(loginRedirectPath);
      }
      return;
    }
    setFollowBusy(true);
    setFollowError('');
    try {
      const payload = await toggleFollowPublisher(publisherId);
      setLesson((current) => current ? {
        ...current,
        publisher_is_following: Boolean(payload?.is_following),
        is_following_publisher: Boolean(payload?.is_following),
        publisher_follower_count: Number(payload?.follower_count ?? current.publisher_follower_count ?? 0),
        follower_count: Number(payload?.follower_count ?? current.follower_count ?? 0),
      } : current);
    } catch (err) {
      setFollowError(err.message || 'Could not update follow.');
    } finally {
      setFollowBusy(false);
    }
  };

  const handleLessonModerationCompleted = (payload) => {
    if (!payload || Number(payload.project_id) !== Number(activeLessonId)) return;
    setLesson((current) => current ? {
      ...current,
      moderation_status: payload.moderation_status ?? current.moderation_status,
      is_published: payload.is_published ?? current.is_published,
    } : current);
  };

  const handleSubmitComment = async (event) => {
    event.preventDefault();
    if (!activeLessonId || commentSubmitting) return;
    if (!user) {
      setCommentError('Sign in to post a comment.');
      if (typeof onLoginRequest === 'function') {
        onLoginRequest(loginRedirectPath);
      }
      return;
    }
    const trimmed = commentText.trim();
    if (!trimmed) {
      setCommentError('Comment text is required.');
      return;
    }
    setCommentSubmitting(true);
    setCommentError('');
    try {
      const created = await addComment(activeLessonId, trimmed);
      setComments((current) => [created, ...current]);
      setLesson((current) => current ? {
        ...current,
        comment_count: Math.max(Number(current.comment_count || 0) + 1, comments.length + 1),
      } : current);
      setCommentText('');
    } catch (err) {
      setCommentError(err.message || 'Could not post comment.');
    } finally {
      setCommentSubmitting(false);
    }
  };

  const handlePlayAutoplayNow = useCallback(() => {
    const nextLessonId = Number(autoplayPrompt?.lesson?.id || 0);
    if (nextLessonId) {
      openLessonById(nextLessonId);
    }
  }, [autoplayPrompt, openLessonById]);

  const handleCancelAutoplay = useCallback(() => {
    setAutoplayPrompt(null);
  }, []);

  const renderPlayerStage = () => {
    if (playbackHeartbeat.error) {
      return (
        <UnavailableStage
          message={playbackHeartbeat.error}
          reason="playback_session_denied"
          mode={PLAYER_MODES.UNAVAILABLE}
        />
      );
    }

    if (playerMode.mode === PLAYER_MODES.PUBLIC_MP4) {
      return (
        <VideoStage
          lesson={playbackLesson}
          subtitleTracks={subtitleTracks}
          preferredSubtitleLanguage={preferredSubtitleLanguage}
          selectedSubtitleKey={selectedSubtitleKey}
          onSubtitleKeyChange={setSelectedSubtitleKey}
          onPlaybackTimeChange={handlePlaybackTimeChange}
          onPlaybackStarted={handlePlaybackStarted}
          onPlaybackStopped={handlePlaybackStopped}
          onPlaybackEnded={handlePlaybackEnded}
          videoRef={videoRef}
          showSubtitleControls={false}
          showLessonDetails={false}
          avatarOverlayMode={!avatarFeatureEnabled || focusMode ? 'disabled' : 'floating'}
          watermarkLesson={lesson}
        />
      );
    }

    if (playerMode.mode === PLAYER_MODES.SECURE_HLS) {
      return (
        <Suspense
          fallback={(
            <SurfaceCard elevated className="p-4 sm:p-5">
              <p className="body-md">Loading secure player...</p>
            </SurfaceCard>
          )}
        >
          <HlsPlayer
            lesson={lesson}
            videoRef={videoRef}
            manifestUrl={playerMode.manifestUrl}
            fallbackUrl={playerMode.fallbackUrl}
            fallbackAllowed={playerMode.fallbackAllowed}
            onPlaybackTimeChange={handlePlaybackTimeChange}
            onPlaybackStarted={handlePlaybackStarted}
            onPlaybackStopped={handlePlaybackStopped}
            onPlaybackEnded={handlePlaybackEnded}
            subtitleTracks={subtitleTracks}
            preferredSubtitleLanguage={preferredSubtitleLanguage}
            selectedSubtitleKey={selectedSubtitleKey}
            onSubtitleKeyChange={setSelectedSubtitleKey}
            avatarOverlayMode={!avatarFeatureEnabled || focusMode ? 'disabled' : 'floating'}
            watermarkLesson={lesson}
          />
        </Suspense>
      );
    }

    return (
      <UnavailableStage
        message={playerMode.message}
        reason={playerMode.reason}
        mode={playerMode.mode}
      />
    );
  };

  return (
    <div className="space-y-5">
      <SurfaceCard className="token-glass flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="label-sm">Watch</p>
          <h1 className="headline-md mt-1 text-[var(--text-primary)]">Study With Focused Context</h1>
        </div>

        <Button variant={focusMode ? 'primary' : 'secondary'} onClick={handleFocusModeToggle} disabled={!activeLessonId}>
          <Focus size={15} />
          <span>{focusMode ? 'Exit Focus' : 'Focus Mode'}</span>
        </Button>
      </SurfaceCard>

      {loadingCatalog && (
        <SurfaceCard elevated>
          <p className="body-md">Loading lesson catalog...</p>
        </SurfaceCard>
      )}

      {lessonError && (
        <SurfaceCard elevated>
          <p className="text-sm text-[color:var(--feedback-danger-fg)]">{lessonError}</p>
        </SurfaceCard>
      )}

      {!loadingCatalog && !activeLessonId && (
        <SurfaceCard elevated className="space-y-3">
          <p className="title-lg text-[var(--text-primary)]">No lesson selected</p>
          <Button onClick={() => navigate('/')}>
            <span>Go To Dashboard</span>
          </Button>
        </SurfaceCard>
      )}

      {activeLessonId && !lessonError && (
        <section className={focusMode ? 'grid gap-5 xl:grid-cols-[minmax(0,4fr)_minmax(16rem,1fr)]' : 'layout-grid-12'}>
          <div className={focusMode ? 'space-y-5' : 'lg:col-span-8 space-y-5'}>
            {loadingLesson ? (
              <SurfaceCard elevated>
                <p className="body-md">Loading lesson player...</p>
              </SurfaceCard>
            ) : (
              <>
                <div className="relative">
                  {renderPlayerStage()}
                  {autoplayPrompt?.lesson ? (
                    <div
                      data-testid="watch-autoplay-next"
                      className="absolute inset-0 z-[60] flex items-end justify-center rounded-[1.5rem] bg-[linear-gradient(180deg,rgba(0,0,0,0.18),rgba(0,0,0,0.72))] p-4 sm:items-center"
                    >
                      <div className="w-full max-w-lg rounded-2xl border border-white/15 bg-[color:rgba(8,12,20,0.88)] p-4 text-white shadow-2xl backdrop-blur">
                        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-white/65">Up next</p>
                        <h2 className="mt-2 line-clamp-2 text-lg font-semibold leading-tight">
                          Next: {autoplayPrompt.lesson.title}
                        </h2>
                        <p className="mt-1 text-sm text-white/75">
                          Playing in {Math.max(0, Number(autoplayPrompt.secondsRemaining || 0))}...
                        </p>
                        <div className="mt-4 flex flex-wrap gap-2">
                          <Button size="sm" onClick={handlePlayAutoplayNow}>
                            Play now
                          </Button>
                          <Button size="sm" variant="secondary" onClick={handleCancelAutoplay}>
                            Stay here
                          </Button>
                        </div>
                      </div>
                    </div>
                  ) : null}
                </div>

                <SurfaceCard className="space-y-3 p-4">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                    <div className="min-w-0 space-y-2">
                      <div>
                        <h1 className="text-xl font-semibold leading-tight text-[var(--text-primary)]">
                          {lesson?.title || 'Untitled lesson'}
                        </h1>
                        <div className="mt-3">
                          <PublisherIdentity
                            publisherId={publisherId}
                            publisherName={publisherName}
                            publisherAvatarUrl={publisherAvatarUrl}
                            publisherInitials={publisherInitials}
                            followerCount={publisherFollowerCount}
                          />
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--text-secondary)]">
                        <span className="rounded-full bg-[color:var(--surface-muted)] px-2.5 py-1">{lesson?.category_name || lesson?.categoryName || 'General'}</span>
                        <span className="rounded-full bg-[color:var(--surface-muted)] px-2.5 py-1">{formatDuration(lesson?.duration_minutes || lesson?.durationMinutes || 8)}</span>
                        <span className="inline-flex items-center gap-1 rounded-full bg-[color:color-mix(in_srgb,var(--accent-secondary),transparent_82%)] px-2.5 py-1 text-[var(--text-primary)]">
                          <ShieldCheck size={12} />
                          Secure stream
                        </span>
                        {progressLabel && userProgressPct < 95 && (
                          <span className="rounded-full bg-[var(--surface-container-highest)] px-2.5 py-1 font-semibold text-[var(--accent-primary)]">
                            Continue from {progressLabel}
                          </span>
                        )}
                      </div>
                      {lesson?.description && (
                        <p className="line-clamp-2 max-w-3xl text-sm text-[var(--text-secondary)]">{lesson.description}</p>
                      )}
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-2">
                      <Button
                        size="sm"
                        variant={likedByMe ? 'primary' : 'secondary'}
                        onClick={handleToggleLike}
                        disabled={likeBusy}
                      >
                        <Heart size={14} className={likedByMe ? 'fill-current' : ''} />
                        <span>{likeBusy ? 'Saving...' : likedByMe ? 'Liked' : 'Like'}</span>
                        <span className="text-xs opacity-80">{likeCount}</span>
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        type="button"
                        onClick={() => commentsSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
                      >
                        <MessageSquare size={14} />
                        <span>{commentCount}</span>
                        <span>Comments</span>
                      </Button>
                      {publisherId && !isOwnPublisher && (
                        <Button
                          size="sm"
                          variant={isFollowingPublisher ? 'primary' : 'secondary'}
                          type="button"
                          onClick={handleToggleFollow}
                          disabled={followBusy}
                        >
                          {isFollowingPublisher ? <Check size={14} /> : <UserPlus size={14} />}
                          <span>{followBusy ? 'Saving...' : isFollowingPublisher ? 'Following' : 'Follow'}</span>
                        </Button>
                      )}
                      <LessonActionButton
                        lesson={lesson}
                        user={user}
                        onLoginRequest={onLoginRequest}
                        onCompleted={handleLessonModerationCompleted}
                      />
                    </div>
                  </div>
                  {likeError && (
                    <p className="text-xs font-medium text-[color:var(--feedback-danger-fg)]">{likeError}</p>
                  )}
                  {followError && (
                    <p className="text-xs font-medium text-[color:var(--feedback-danger-fg)]">{followError}</p>
                  )}
                </SurfaceCard>

                <SurfaceCard className="space-y-3 p-4">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-[var(--text-primary)]">CC</p>
                      <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
                        <label className="sr-only" htmlFor="watch-subtitle-track">Subtitle track</label>
                        <select
                          id="watch-subtitle-track"
                          value={selectedSubtitleKey}
                          onChange={(event) => {
                            manualSubtitleSelectionLessonRef.current = String(activeLessonId || lesson?.id || '');
                            setPreferredSubtitleLanguage('');
                            setSelectedSubtitleKey(event.target.value);
                          }}
                          disabled={subtitleOptions.length === 0}
                          className="focus-ring h-10 min-w-[12rem] rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-55"
                        >
                          <option value="off">Off</option>
                          {subtitleOptions.map((option) => (
                            <option key={option.key} value={option.key}>{option.label}</option>
                          ))}
                        </select>
                        {selectedSubtitleOption && (
                          <span className="text-xs text-[var(--text-secondary)]">
                            Showing {selectedSubtitleOption.label}
                          </span>
                        )}
                        {!selectedSubtitleOption && subtitleOptions.length === 0 && (
                          <span className="text-xs text-[var(--text-secondary)]">No subtitle tracks available yet.</span>
                        )}
                      </div>
                    </div>
                    <div className="flex flex-col gap-2 lg:min-w-[22rem]">
                      <span className="text-xs font-medium text-[var(--text-secondary)]">Need another subtitle?</span>
                      <div className="flex flex-col gap-2 sm:flex-row">
                        <label className="sr-only" htmlFor="watch-subtitle-language">Language</label>
                        <select
                          id="watch-subtitle-language"
                          value={selectedRequestLanguage?.code || ''}
                          onChange={(event) => setRequestLanguageCode(event.target.value)}
                          disabled={requestingSubtitleLanguage || Boolean(pendingSubtitleRequest) || missingSubtitleLanguages.length === 0}
                          className="focus-ring h-10 min-w-0 flex-1 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-55"
                        >
                          {missingSubtitleLanguages.length > 0 ? (
                            missingSubtitleLanguages.map((language) => (
                              <option key={language.code} value={language.code}>
                                {language.label}
                              </option>
                            ))
                          ) : (
                            <option value="">All listed languages available</option>
                          )}
                        </select>
                        <Button
                          size="sm"
                          onClick={handleRequestSubtitleLanguage}
                          disabled={requestingSubtitleLanguage || Boolean(pendingSubtitleRequest) || missingSubtitleLanguages.length === 0}
                          className="shrink-0"
                        >
                          <Sparkles size={14} />
                          <span>{requestingSubtitleLanguage ? 'Generating...' : 'Generate CC with AI'}</span>
                        </Button>
                      </div>
                    </div>
                  </div>
                  {subtitleRequestMessage && (
                    <p className="text-xs text-[var(--text-secondary)]">{subtitleRequestMessage}</p>
                  )}
                </SurfaceCard>

                <div ref={commentsSectionRef}>
                  <SurfaceCard className="space-y-3 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <p className="text-sm font-semibold text-[var(--text-primary)]">Comments</p>
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">Share a note about this lesson.</p>
                      </div>
                      <span className="rounded-full bg-[var(--surface-container-highest)] px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
                        {commentCount} comment{commentCount === 1 ? '' : 's'}
                      </span>
                    </div>

                    <form className="space-y-2" onSubmit={handleSubmitComment}>
                    <label className="block text-xs font-medium text-[var(--text-secondary)]">
                      Add comment
                      <textarea
                        value={commentText}
                        onChange={(event) => setCommentText(event.target.value)}
                        maxLength={2000}
                        placeholder={user ? 'Write a comment...' : 'Sign in to post a comment.'}
                        disabled={!user || commentSubmitting}
                        className="focus-ring mt-1 min-h-[72px] w-full resize-y rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 text-sm text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-65"
                      />
                    </label>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-xs text-[var(--text-secondary)]">
                        {user ? `${commentText.length}/2000` : 'You can read comments without signing in.'}
                      </p>
                      {user ? (
                        <Button size="sm" type="submit" disabled={commentSubmitting || !commentText.trim()}>
                          <Send size={14} />
                          <span>{commentSubmitting ? 'Posting...' : 'Post comment'}</span>
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          variant="secondary"
                          type="button"
                          onClick={() => {
                            if (typeof onLoginRequest === 'function') onLoginRequest(loginRedirectPath);
                          }}
                        >
                          Sign in to comment
                        </Button>
                      )}
                    </div>
                    </form>

                    {commentError && (
                      <p className="rounded-xl bg-[color:var(--feedback-danger-bg)] px-3 py-2 text-xs font-medium text-[color:var(--feedback-danger-fg)]">
                        {commentError}
                      </p>
                    )}

                  <div className="space-y-2">
                    {commentsLoading ? (
                      <p className="text-sm text-[var(--text-secondary)]">Loading comments...</p>
                    ) : comments.length === 0 ? (
                      <p className="text-sm text-[var(--text-secondary)]">No comments yet.</p>
                    ) : (
                      visibleComments.map((comment) => (
                        <article key={comment.id} className="rounded-xl bg-[var(--surface-container-high)] p-3">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <p className="text-sm font-semibold text-[var(--text-primary)]">{comment.display_name || comment.username || 'Viewer'}</p>
                            {comment.created_at && (
                              <span className="text-xs text-[var(--text-secondary)]">{formatCommentDate(comment.created_at)}</span>
                            )}
                          </div>
                          <p className="mt-2 whitespace-pre-wrap text-sm text-[var(--text-secondary)]">{comment.text}</p>
                        </article>
                      ))
                    )}
                  </div>

                    {hiddenCommentCount > 0 && (
                      <div>
                        <Button
                          size="sm"
                          variant="secondary"
                          type="button"
                          onClick={() => setCommentsExpanded((previous) => !previous)}
                        >
                          {commentsExpanded ? 'Show fewer comments' : `Show ${hiddenCommentCount} more comment${hiddenCommentCount === 1 ? '' : 's'}`}
                        </Button>
                      </div>
                    )}
                  </SurfaceCard>
                </div>
              </>
            )}

            {!focusMode && (
              <RelatedLessonsRow
                lessons={relatedLessons}
                onOpenLesson={openLessonById}
              />
            )}
          </div>

          <aside className={focusMode ? 'space-y-5' : 'lg:col-span-4 space-y-5'}>
            {focusMode ? (
              <WatchStudyPanel
                lesson={lesson}
                videoRef={videoRef}
                avatarFeatureEnabled={avatarFeatureEnabled}
                notes={notes}
                onNotesChange={setNotes}
                onSave={saveNotes}
                savedAtLabel={savedAtLabel}
                unsaved={hasUnsavedNotes}
                saveActionLabel={user ? 'Save Note' : 'Sign In To Save'}
                saveHint={saveHint || (!user ? 'Drafts remain cached locally while you sign in.' : '')}
              />
            ) : (
              <>
                <WatchContextPanel
                  context={playlistContext}
                  currentLessonId={activeLessonId}
                  onOpenLesson={openLessonById}
                />
                <NotesPanel
                  notes={notes}
                  onNotesChange={setNotes}
                  onSave={saveNotes}
                  savedAtLabel={savedAtLabel}
                  unsaved={hasUnsavedNotes}
                  saveActionLabel={user ? 'Save Note' : 'Sign In To Save'}
                  saveHint={saveHint || (!user ? 'Drafts remain cached locally while you sign in.' : '')}
                  collapsed={notesCollapsed}
                  onToggle={() => setNotesCollapsed((prev) => !prev)}
                />
                <TranscriptPanel
                  lines={transcriptLines}
                  playbackTime={playbackTime}
                  onJump={jumpToTime}
                  collapsed={transcriptCollapsed}
                  onToggle={() => setTranscriptCollapsed((prev) => !prev)}
                />
                <ChapterList
                  chapters={chapters}
                  activeChapterId={activeChapterId}
                  onJump={jumpToTime}
                  collapsed={chaptersCollapsed}
                  onToggle={() => setChaptersCollapsed((prev) => !prev)}
                />
              </>
            )}
          </aside>
        </section>
      )}

    </div>
  );
}
