import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Maximize2, Minimize2, ShieldCheck } from 'lucide-react';
import { formatLessonDuration } from '../../lib/content';
import AvatarOverlayLayer, { AVATAR_OVERLAY_Z_INDEX } from './AvatarOverlayLayer';
import ContinueNextPrompt from './ContinueNextPrompt';
import WatermarkOverlay from './WatermarkOverlay';
import SurfaceCard from '../ui/SurfaceCard';

const NATIVE_FULLSCREEN_CONTROL_HIDE_CSS = `
.visus-shell-video::-webkit-media-controls-fullscreen-button {
  display: none;
}
.visus-shell-video::cue {
  color: #fff;
  background-color: rgba(0, 0, 0, 0.82);
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.95), 0 0 5px rgba(0, 0, 0, 0.9);
  font-weight: 700;
}
`;
const CAPTION_PILL_CLASSNAME = [
  'max-w-[92%] whitespace-pre-line rounded-lg bg-black/80 px-3.5 py-2 text-sm font-semibold leading-snug text-white',
  'shadow-[0_10px_30px_rgba(0,0,0,0.45)] ring-1 ring-white/20 backdrop-blur-[1px] sm:text-base',
].join(' ');
const CAPTION_TEXT_SHADOW = '0 1px 2px rgba(0,0,0,0.95), 0 0 5px rgba(0,0,0,0.9)';

function vttUrlForLesson(lesson) {
  return [lesson?.vtt_url, lesson?.subtitle_vtt_url]
    .map((value) => String(value || '').trim())
    .find(Boolean) || '';
}

function srtUrlForLesson(lesson) {
  return [lesson?.srt_url, lesson?.subtitle_url, lesson?.caption_url]
    .map((value) => String(value || '').trim())
    .find(Boolean) || '';
}

function captionTrackSrcForUrl(url) {
  const value = String(url || '').trim();
  if (!value) return '';
  const separator = value.includes('?') ? '&' : '?';
  return `${value}${separator}kind=vtt`;
}

function textTracksForVideo(video) {
  const tracks = video?.textTracks;
  if (!tracks) return [];
  return Array.from({ length: tracks.length }, (_, index) => tracks[index]).filter(Boolean);
}

function selectionKeyForLanguageCode(value) {
  const code = String(value || '').trim().toLowerCase();
  if (!code || code === 'off') return 'off';
  if (code === 'original') return 'original';
  if (code.startsWith('translated:')) return code;
  return `translated:${code}`;
}

function textTrackMatchesCaptionTrack(textTrack, captionTrack) {
  if (!textTrack || !captionTrack) return false;
  const label = String(textTrack.label || '').trim();
  const language = String(textTrack.language || '').trim().toLowerCase();
  const captionLabel = String(captionTrack.language_label || '').trim();
  const captionLanguage = String(captionTrack.language_code || '').trim().toLowerCase();

  if (captionTrack.is_original) {
    return label === 'Original' || label === captionLabel;
  }

  if (label === captionLabel) return true;
  if (label === 'Original') return false;
  return language === captionLanguage;
}

function setActiveTextTrack(video, captionTrack) {
  const tracks = textTracksForVideo(video);
  for (const textTrack of tracks) {
    textTrack.mode = 'disabled';
  }

  if (!captionTrack) return;

  const selectedTextTrack = tracks.find((textTrack) => textTrackMatchesCaptionTrack(textTrack, captionTrack));
  if (selectedTextTrack) {
    selectedTextTrack.mode = 'hidden';
  }
}

function captionTextForVideo(video, captionTrack) {
  if (!video || !captionTrack) return '';
  const selectedTextTrack = textTracksForVideo(video).find((textTrack) => textTrackMatchesCaptionTrack(textTrack, captionTrack));
  const cues = selectedTextTrack?.activeCues;
  if (!cues) return '';
  return Array.from({ length: cues.length }, (_, index) => String(cues[index]?.text || '').trim())
    .filter(Boolean)
    .join('\n');
}

function CaptionLayer({ text }) {
  if (!text) return null;
  return (
    <div
      data-testid="player-caption-layer"
      data-caption-layer="subtitles"
      className="pointer-events-none absolute inset-x-3 bottom-14 flex justify-center px-2 text-center sm:bottom-16"
      style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.captions }}
    >
      <span className={CAPTION_PILL_CLASSNAME} style={{ textShadow: CAPTION_TEXT_SHADOW }}>
        {text}
      </span>
    </div>
  );
}

function PlayerShellFullscreenButton({ active, onClick }) {
  return (
    <button
      type="button"
      data-testid="player-shell-fullscreen"
      aria-label={active ? 'Exit player fullscreen' : 'Enter player fullscreen'}
      title={active ? 'Exit player fullscreen' : 'Enter player fullscreen'}
      onClick={onClick}
      className="focus-ring absolute left-3 top-3 inline-flex h-9 w-9 items-center justify-center rounded-full border border-white/25 bg-black/70 text-white shadow-sm transition hover:bg-black/85"
      style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.videoControls }}
    >
      {active ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
    </button>
  );
}

function NativeVideoControlStyles() {
  return <style>{NATIVE_FULLSCREEN_CONTROL_HIDE_CSS}</style>;
}

function normalizeTrack(track) {
  if (!track || typeof track !== 'object') return null;
  const rawCode = String(track.language_code || '').trim().toLowerCase();
  const isOriginal = track.is_original === true
    || track.type === 'original'
    || track.id === 'original'
    || rawCode === 'original';
  const languageCode = isOriginal ? 'original' : rawCode;
  const vttUrl = String(track.vtt_url || track.subtitle_vtt_url || '').trim();
  const status = String(track.status || 'ready').trim().toLowerCase();

  if (!languageCode || !vttUrl || status !== 'ready') return null;

  return {
    ...track,
    key: selectionKeyForLanguageCode(languageCode),
    language_code: languageCode,
    language_label: isOriginal
      ? 'Original'
      : String(track.language_label || track.label || languageCode.toUpperCase()).trim(),
    source_language_code: String(track.source_language_code || '').trim().toLowerCase(),
    is_original: isOriginal,
    vtt_url: vttUrl,
  };
}

export default function VideoStage({
  lesson,
  subtitleTracks = [],
  preferredSubtitleLanguage = '',
  selectedSubtitleKey,
  onSubtitleKeyChange,
  onPlaybackTimeChange,
  onPlaybackStarted,
  onPlaybackStopped,
  onPlaybackEnded,
  videoRef,
  asSurface = true,
  captionMissingLabel = 'No captions yet',
  showSubtitleControls = true,
  showLessonDetails = true,
  avatarOverlayMode = 'floating',
  watermarkLesson = null,
  continueNextPrompt = null,
  onContinueNext,
  onCancelContinueNext,
}) {
  const internalVideoRef = useRef(null);
  const playerShellRef = useRef(null);
  const activeVideoRef = videoRef || internalVideoRef;
  const [internalSelectedTrackKey, setInternalSelectedTrackKey] = useState('off');
  const [captionLoadFailed, setCaptionLoadFailed] = useState(false);
  const [activeCaptionText, setActiveCaptionText] = useState('');
  const [fullscreenActive, setFullscreenActive] = useState(false);
  const selectionControlled = selectedSubtitleKey !== undefined;
  const selectedTrackKey = selectionControlled ? selectedSubtitleKey : internalSelectedTrackKey;
  const setSelectedTrackKeyValue = useCallback((nextKey) => {
    if (!selectionControlled) {
      setInternalSelectedTrackKey(nextKey);
    }
    onSubtitleKeyChange?.(nextKey);
  }, [onSubtitleKeyChange, selectionControlled]);

  const originalVttUrl = vttUrlForLesson(lesson);
  const srtUrl = srtUrlForLesson(lesson);
  const hasVideo = Boolean(lesson?.stream_url);
  const avatarOverlay = lesson?.avatar_overlay || {};
  const avatarPlacement = avatarOverlay?.placement || avatarOverlay?.defaults || lesson?.avatar_placement || {};
  const avatarStreamUrl = String(avatarOverlay?.stream_url || '').trim();
  const avatarOverlayEnabled = Boolean(avatarOverlayMode !== 'disabled' && avatarOverlay?.enabled && avatarStreamUrl);
  const avatarStatus = String(lesson?.avatar_processing_status || 'none').trim().toLowerCase();
  const avatarProcessing = !avatarOverlayEnabled && ['queued', 'processing'].includes(avatarStatus);

  const availableTracks = useMemo(() => {
    const byKey = new Map();
    for (const rawTrack of subtitleTracks || []) {
      const track = normalizeTrack(rawTrack);
      if (track) byKey.set(track.key, track);
    }
    if (!byKey.has('original') && originalVttUrl) {
      byKey.set('original', {
        key: 'original',
        language_code: 'original',
        language_label: 'Original',
        source_language_code: '',
        status: 'ready',
        is_original: true,
        vtt_url: originalVttUrl,
      });
    }
    const original = byKey.get('original');
    const translated = Array.from(byKey.values())
      .filter((track) => !track.is_original)
      .sort((a, b) => a.language_label.localeCompare(b.language_label));
    return original ? [original, ...translated] : translated;
  }, [originalVttUrl, subtitleTracks]);

  const selectedTrack = availableTracks.find((track) => track.key === selectedTrackKey) || null;
  const fallbackCaptionStatus = srtUrl
    ? 'Captions generated but WebVTT track is unavailable. Rerender to create WebVTT.'
    : captionMissingLabel;
  const loadedTrackLabel = availableTracks.map((track) => track.language_label).join(', ');
  const onlyOriginalTrack = availableTracks.length === 1 && availableTracks[0]?.is_original;
  const trackAvailabilityLabel = onlyOriginalTrack
    ? 'Only original captions are available.'
    : `Caption tracks loaded: ${loadedTrackLabel}.`;
  const captionStatus = useMemo(() => {
    if (!hasVideo) return '';
    if (!availableTracks.length) return fallbackCaptionStatus;
    if (captionLoadFailed && selectedTrackKey !== 'off') return 'Captions could not be loaded.';
    if (selectedTrackKey === 'off') return 'CC off';
    return selectedTrack ? `CC enabled: ${selectedTrack.language_label}` : 'CC off';
  }, [availableTracks.length, captionLoadFailed, fallbackCaptionStatus, hasVideo, selectedTrack, selectedTrackKey]);

  useEffect(() => {
    if (selectedTrackKey === 'off') return;
    if (!availableTracks.some((track) => track.key === selectedTrackKey)) {
      setSelectedTrackKeyValue('off');
    }
  }, [availableTracks, selectedTrackKey, setSelectedTrackKeyValue]);

  useEffect(() => {
    const preferredKey = String(preferredSubtitleLanguage || '').trim().toLowerCase();
    if (!preferredKey) return;
    const targetKey = selectionKeyForLanguageCode(preferredKey);
    if (availableTracks.some((track) => track.key === targetKey)) {
      setSelectedTrackKeyValue(targetKey);
    }
  }, [availableTracks, preferredSubtitleLanguage, setSelectedTrackKeyValue]);

  useEffect(() => {
    setCaptionLoadFailed(false);
  }, [lesson?.id, selectedTrackKey]);

  useEffect(() => {
    const video = activeVideoRef.current;
    if (!video) return;

    setActiveTextTrack(video, selectedTrack);
  }, [activeVideoRef, selectedTrack]);

  useEffect(() => {
    const video = activeVideoRef.current;
    if (!video || !selectedTrack) {
      setActiveCaptionText('');
      return undefined;
    }

    const updateCaptionText = () => {
      setActiveCaptionText(captionTextForVideo(video, selectedTrack));
    };
    const selectedTextTrack = textTracksForVideo(video).find((textTrack) => textTrackMatchesCaptionTrack(textTrack, selectedTrack));
    selectedTextTrack?.addEventListener?.('cuechange', updateCaptionText);
    video.addEventListener('timeupdate', updateCaptionText);
    video.addEventListener('seeked', updateCaptionText);
    video.addEventListener('loadedmetadata', updateCaptionText);
    updateCaptionText();

    return () => {
      selectedTextTrack?.removeEventListener?.('cuechange', updateCaptionText);
      video.removeEventListener('timeupdate', updateCaptionText);
      video.removeEventListener('seeked', updateCaptionText);
      video.removeEventListener('loadedmetadata', updateCaptionText);
    };
  }, [activeVideoRef, selectedTrack]);

  const handleCaptionTrackReady = useCallback(() => {
    setCaptionLoadFailed(false);
    const video = activeVideoRef.current;
    if (!video) return;
    setActiveTextTrack(video, selectedTrack);
  }, [activeVideoRef, selectedTrack]);

  const handleCaptionTrackError = useCallback(() => {
    if (selectedTrackKey !== 'off') {
      setCaptionLoadFailed(true);
    }
  }, [selectedTrackKey]);

  const handleSubtitleSelectionChange = useCallback((event) => {
    const nextKey = event.target.value;
    const nextTrack = availableTracks.find((track) => track.key === nextKey) || null;
    setActiveTextTrack(activeVideoRef.current, nextTrack);
    setSelectedTrackKeyValue(nextKey);
  }, [activeVideoRef, availableTracks, setSelectedTrackKeyValue]);

  const handleVideoPlay = useCallback((event) => {
    onPlaybackStarted?.();
    setActiveCaptionText(captionTextForVideo(event.currentTarget, selectedTrack));
  }, [onPlaybackStarted, selectedTrack]);

  const handleVideoPause = useCallback(() => {
    onPlaybackStopped?.();
  }, [onPlaybackStopped]);

  const handleVideoEnded = useCallback(() => {
    onPlaybackStopped?.();
    onPlaybackEnded?.();
  }, [onPlaybackEnded, onPlaybackStopped]);

  const handleVideoSeeked = useCallback((event) => {
    setActiveCaptionText(captionTextForVideo(event.currentTarget, selectedTrack));
  }, [selectedTrack]);

  const handleVideoTimeUpdate = useCallback((event) => {
    onPlaybackTimeChange?.(Number(event.currentTarget.currentTime || 0));
    setActiveCaptionText(captionTextForVideo(event.currentTarget, selectedTrack));
  }, [onPlaybackTimeChange, selectedTrack]);

  useEffect(() => {
    const handleFullscreenChange = () => {
      setFullscreenActive(document.fullscreenElement === playerShellRef.current);
    };
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    handleFullscreenChange();
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, []);

  const handlePlayerShellFullscreenToggle = useCallback(() => {
    const target = playerShellRef.current;
    if (!target || typeof document === 'undefined') return;
    try {
      if (document.fullscreenElement) {
        document.exitFullscreen?.().catch?.(() => {});
        return;
      }
      target.requestFullscreen?.().catch?.(() => {});
    } catch {
      // Fullscreen can be blocked by embedded browser surfaces.
    }
  }, []);

  const playerShellClassName = [
    'relative overflow-hidden bg-[color:var(--video-stage-bg)]',
    fullscreenActive ? 'flex h-screen items-center justify-center rounded-none' : 'rounded-xl',
  ].join(' ');
  const videoClassName = fullscreenActive
    ? 'visus-shell-video h-full w-full bg-black object-contain'
    : 'visus-shell-video aspect-video w-full bg-black';
  const mediaCrossOrigin = lesson?.session_binding_active || lesson?.protection?.session_binding_active
    ? 'use-credentials'
    : 'anonymous';

  const content = (
    <>
      <div
        ref={playerShellRef}
        data-testid="player-fullscreen-shell"
        data-fullscreen-active={fullscreenActive ? 'true' : 'false'}
        className={playerShellClassName}
      >
        {hasVideo ? (
          <>
            <NativeVideoControlStyles />
            <video
              key={lesson.id}
              ref={activeVideoRef}
              src={lesson.stream_url}
              className={videoClassName}
              style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.baseVideo }}
              controls
              controlsList="nodownload nofullscreen noplaybackrate noremoteplayback"
              disablePictureInPicture
              onContextMenu={(event) => event.preventDefault()}
              playsInline
              preload="metadata"
              crossOrigin={mediaCrossOrigin}
              onLoadedMetadata={handleCaptionTrackReady}
              onPlay={handleVideoPlay}
              onPause={handleVideoPause}
              onEnded={handleVideoEnded}
              onSeeked={handleVideoSeeked}
              onTimeUpdate={handleVideoTimeUpdate}
            >
              {availableTracks.map((track) => (
                <track
                  key={track.key}
                  kind="subtitles"
                  src={captionTrackSrcForUrl(track.vtt_url)}
                  srcLang={track.is_original ? (track.source_language_code || 'und') : track.language_code}
                  label={track.language_label}
                  onLoad={handleCaptionTrackReady}
                  onError={handleCaptionTrackError}
                />
              ))}
            </video>
            <WatermarkOverlay lesson={watermarkLesson} />
            {avatarOverlayEnabled && (
              <AvatarOverlayLayer
                lessonId={lesson?.id}
                src={avatarStreamUrl}
                enabled={avatarOverlayEnabled}
                placement={avatarPlacement}
                videoRef={activeVideoRef}
              />
            )}
            <CaptionLayer text={activeCaptionText} />
            <PlayerShellFullscreenButton
              active={fullscreenActive}
              onClick={handlePlayerShellFullscreenToggle}
            />
            <ContinueNextPrompt
              prompt={continueNextPrompt}
              onContinue={onContinueNext}
              onCancel={onCancelContinueNext}
            />
          </>
        ) : (
          <div className="flex aspect-video items-center justify-center gap-2 text-sm text-[color:var(--media-text-on-image)] opacity-80">
            <AlertCircle size={16} />
            <span>Video source unavailable for this lesson.</span>
          </div>
        )}
      </div>

      {hasVideo && avatarProcessing && (
        <p className="text-xs text-[var(--text-secondary)]">Avatar is being prepared.</p>
      )}

      {hasVideo && showSubtitleControls && (
        <div className="flex flex-col gap-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <label className="flex min-w-0 items-center gap-2 text-sm text-[var(--text-secondary)]">
              <span className="shrink-0 font-medium text-[var(--text-primary)]">Subtitles</span>
              <select
                value={selectedTrackKey}
                onChange={handleSubtitleSelectionChange}
                disabled={availableTracks.length === 0}
                className="focus-ring h-9 min-w-[9rem] rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-2.5 text-sm text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-55"
              >
                <option value="off">Off</option>
                {availableTracks.map((track) => (
                  <option key={track.key} value={track.key}>
                    {track.language_label}
                  </option>
                ))}
              </select>
            </label>
            <p className="mt-1 text-xs text-[var(--text-secondary)]">Use this menu to choose subtitle language.</p>
          </div>
          <div className="min-w-0 text-xs text-[var(--text-secondary)]">
            {availableTracks.length > 0 ? (
              <>
                <span>{captionStatus}</span>
                <span className="mx-2 text-[var(--border-strong)]">|</span>
                <span>{trackAvailabilityLabel}</span>
              </>
            ) : (
              <span>{captionStatus}</span>
            )}
          </div>
        </div>
      )}

      {showLessonDetails && (
        <div className="space-y-2">
          <h1 className="headline-md text-[var(--text-primary)]">{lesson?.title || 'Select a lesson to start'}</h1>
          <p className="body-md max-w-3xl">{lesson?.description || 'Choose a lesson from related content to begin playback.'}</p>
          <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--text-secondary)]">
            <span className="rounded-full bg-[color:var(--surface-muted)] px-2.5 py-1">{lesson?.category_name || 'General'}</span>
            <span className="rounded-full bg-[color:var(--surface-muted)] px-2.5 py-1">{formatLessonDuration(lesson)}</span>
            <span className="rounded-full bg-[color:var(--surface-muted)] px-2.5 py-1">{lesson?.teacher_name || 'VISUS Instructor'}</span>
            <span className="inline-flex items-center gap-1 rounded-full bg-[color:color-mix(in_srgb,var(--accent-secondary),transparent_82%)] px-2.5 py-1 text-[var(--text-primary)]">
              <ShieldCheck size={12} />
              Secure stream
            </span>
          </div>
        </div>
      )}
    </>
  );

  if (!asSurface) {
    return <div className="space-y-4">{content}</div>;
  }

  return (
    <SurfaceCard elevated className="space-y-4 p-4 sm:p-5">
      {content}
    </SurfaceCard>
  );
}
