import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import shaka from 'shaka-player';
import { AlertCircle, Maximize2, Minimize2 } from 'lucide-react';
import AvatarOverlayLayer, { AVATAR_OVERLAY_Z_INDEX } from './AvatarOverlayLayer';
import WatermarkOverlay from './WatermarkOverlay';
import SurfaceCard from '../ui/SurfaceCard';
import {
  PROTECTED_PLAYBACK_UNAVAILABLE_MESSAGE,
  buildLicenseRequestHeaders,
  buildShakaDrmConfig,
} from './playerMode';

const NATIVE_FULLSCREEN_CONTROL_HIDE_CSS = `
.visus-shell-video::-webkit-media-controls-fullscreen-button {
  display: none;
}
`;

const DRM_INIT_ERROR_MESSAGE = 'Protected playback could not be initialized.';
const DRM_LICENSE_ERROR_MESSAGE = 'Protected playback license request failed.';
const DRM_KEY_SYSTEM_ERROR_MESSAGE = 'This browser does not support the protected playback key system for this lesson.';

function vttUrlForLesson(lesson) {
  return [lesson?.vtt_url, lesson?.subtitle_vtt_url]
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
      <span className="max-w-[92%] whitespace-pre-line rounded-md bg-black/78 px-3 py-1.5 text-sm font-semibold leading-snug text-white shadow-lg sm:text-base">
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

function uniqueContentTypes(system) {
  return Array.from(new Set([
    String(system?.contentType || '').trim(),
    'video/mp4; codecs="avc1.42E01E"',
    'video/mp4',
  ].filter(Boolean)));
}

export async function browserSupportsAnyDrmSystem(drmSystems = []) {
  if (typeof navigator === 'undefined' || typeof navigator.requestMediaKeySystemAccess !== 'function') {
    return false;
  }

  for (const system of drmSystems) {
    const keySystem = String(system?.keySystem || '').trim();
    if (!keySystem) continue;
    const configs = uniqueContentTypes(system).map((contentType) => ({
      initDataTypes: ['cenc'],
      audioCapabilities: [{ contentType: 'audio/mp4; codecs="mp4a.40.2"' }],
      videoCapabilities: [{ contentType }],
      distinctiveIdentifier: 'optional',
      persistentState: 'optional',
      sessionTypes: ['temporary'],
    }));

    try {
      await navigator.requestMediaKeySystemAccess(keySystem, configs);
      return true;
    } catch {
      // Try the next configured key system.
    }
  }

  return false;
}

function shakaErrorReason(error) {
  const code = String(error?.code || error?.detail?.code || '').trim();
  const category = String(error?.category || error?.detail?.category || '').trim();
  const data = Array.isArray(error?.data || error?.detail?.data) ? (error.data || error.detail.data).join(',') : '';
  const haystack = `${code} ${category} ${data}`.toLowerCase();
  if (haystack.includes('license')) return 'drm_license_request_failed';
  if (haystack.includes('key') || haystack.includes('media_key') || haystack.includes('encrypted')) return 'drm_key_system_unsupported';
  return 'drm_shaka_init_failed';
}

function messageForReason(reason) {
  if (reason === 'drm_key_system_unsupported') return DRM_KEY_SYSTEM_ERROR_MESSAGE;
  if (reason === 'drm_license_request_failed') return DRM_LICENSE_ERROR_MESSAGE;
  if (reason === 'drm_browser_unsupported') return PROTECTED_PLAYBACK_UNAVAILABLE_MESSAGE;
  return DRM_INIT_ERROR_MESSAGE;
}

export default function DrmShakaPlayer({
  lesson,
  videoRef,
  manifestUrl,
  drmSystems = [],
  onPlaybackTimeChange,
  onPlaybackStarted,
  onPlaybackStopped,
  onPlaybackEnded,
  onPlaybackError,
  subtitleTracks = [],
  selectedSubtitleKey = 'off',
  preferredSubtitleLanguage = '',
  onSubtitleKeyChange,
  avatarOverlayMode = 'floating',
  watermarkLesson = null,
}) {
  const internalVideoRef = useRef(null);
  const playerShellRef = useRef(null);
  const activeVideoRef = videoRef || internalVideoRef;
  const [playbackError, setPlaybackError] = useState('');
  const [activeCaptionText, setActiveCaptionText] = useState('');
  const [fullscreenActive, setFullscreenActive] = useState(false);

  const sourceUrl = String(manifestUrl || '').trim();
  const shakaConfig = useMemo(() => buildShakaDrmConfig(drmSystems), [drmSystems]);
  const hasLicenseServers = Object.keys(shakaConfig.drm?.servers || {}).length > 0;
  const avatarOverlay = lesson?.avatar_overlay || {};
  const avatarPlacement = avatarOverlay?.placement || avatarOverlay?.defaults || lesson?.avatar_placement || {};
  const avatarStreamUrl = String(avatarOverlay?.stream_url || '').trim();
  const avatarOverlayEnabled = Boolean(avatarOverlayMode !== 'disabled' && avatarOverlay?.enabled && avatarStreamUrl);

  const availableTracks = useMemo(() => {
    const byKey = new Map();
    for (const rawTrack of subtitleTracks || []) {
      const track = normalizeTrack(rawTrack);
      if (track) byKey.set(track.key, track);
    }
    const originalVttUrl = vttUrlForLesson(lesson);
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
  }, [lesson, subtitleTracks]);

  const selectedTrack = availableTracks.find((track) => track.key === selectedSubtitleKey) || null;

  const reportPlaybackError = useCallback((reason, error = null) => {
    const message = messageForReason(reason);
    setPlaybackError(message);
    onPlaybackError?.({ reason, message, error });
  }, [onPlaybackError]);

  const handleTrackReady = useCallback(() => {
    setActiveTextTrack(activeVideoRef.current, selectedTrack);
    setActiveCaptionText(captionTextForVideo(activeVideoRef.current, selectedTrack));
  }, [activeVideoRef, selectedTrack]);

  useEffect(() => {
    const preferredKey = String(preferredSubtitleLanguage || '').trim().toLowerCase();
    if (!preferredKey) return;
    const targetKey = selectionKeyForLanguageCode(preferredKey);
    if (availableTracks.some((track) => track.key === targetKey)) {
      onSubtitleKeyChange?.(targetKey);
    }
  }, [availableTracks, onSubtitleKeyChange, preferredSubtitleLanguage]);

  useEffect(() => {
    if (selectedSubtitleKey === 'off') return;
    if (!availableTracks.some((track) => track.key === selectedSubtitleKey)) {
      onSubtitleKeyChange?.('off');
    }
  }, [availableTracks, onSubtitleKeyChange, selectedSubtitleKey]);

  useEffect(() => {
    handleTrackReady();
  }, [handleTrackReady]);

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

  useEffect(() => {
    setPlaybackError('');
  }, [lesson?.id, sourceUrl, shakaConfig]);

  useEffect(() => {
    const video = activeVideoRef.current;
    if (!video || !sourceUrl) return undefined;
    if (!hasLicenseServers) {
      reportPlaybackError('drm_license_request_failed');
      return undefined;
    }

    let disposed = false;
    let player = null;

    const handleShakaError = (event) => {
      if (disposed) return;
      const reason = shakaErrorReason(event?.detail || event);
      reportPlaybackError(reason, event?.detail || event);
    };

    async function setupProtectedPlayback() {
      try {
        shaka.polyfill?.installAll?.();
        if (shaka.Player?.isBrowserSupported?.() === false) {
          reportPlaybackError('drm_browser_unsupported');
          return;
        }
        const keySystemSupported = await browserSupportsAnyDrmSystem(drmSystems);
        if (!keySystemSupported) {
          reportPlaybackError('drm_key_system_unsupported');
          return;
        }

        player = new shaka.Player(video);
        player.addEventListener?.('error', handleShakaError);
        player.configure(shakaConfig);

        const licenseHeaders = buildLicenseRequestHeaders();
        if (licenseHeaders && Object.keys(licenseHeaders).length > 0) {
          player.getNetworkingEngine?.()?.registerRequestFilter?.((type, request) => {
            if (type === shaka.net.NetworkingEngine.RequestType.LICENSE) {
              request.headers = { ...(request.headers || {}), ...licenseHeaders };
            }
          });
        }

        await player.load(sourceUrl);
      } catch (error) {
        if (!disposed) {
          const reason = shakaErrorReason(error);
          reportPlaybackError(reason, error);
        }
      }
    }

    setupProtectedPlayback();

    return () => {
      disposed = true;
      if (player) {
        player.removeEventListener?.('error', handleShakaError);
        player.destroy?.();
      }
    };
  }, [activeVideoRef, drmSystems, hasLicenseServers, reportPlaybackError, shakaConfig, sourceUrl]);

  const handleVideoError = useCallback(() => {
    reportPlaybackError('drm_shaka_init_failed');
  }, [reportPlaybackError]);

  const handlePlay = useCallback((event) => {
    onPlaybackStarted?.();
    setActiveCaptionText(captionTextForVideo(event.currentTarget, selectedTrack));
  }, [onPlaybackStarted, selectedTrack]);

  const handlePause = useCallback(() => {
    onPlaybackStopped?.();
  }, [onPlaybackStopped]);

  const handleEnded = useCallback(() => {
    onPlaybackStopped?.();
    onPlaybackEnded?.();
  }, [onPlaybackEnded, onPlaybackStopped]);

  const handleTimeUpdate = useCallback((event) => {
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

  return (
    <SurfaceCard elevated className="space-y-4 p-4 sm:p-5">
      <div
        ref={playerShellRef}
        data-testid="player-fullscreen-shell"
        data-fullscreen-active={fullscreenActive ? 'true' : 'false'}
        className={playerShellClassName}
      >
        {sourceUrl ? (
          <>
            <NativeVideoControlStyles />
            <video
              key={`${lesson?.id || 'lesson'}-drm`}
              ref={activeVideoRef}
              data-testid="drm-shaka-video"
              className={videoClassName}
              style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.baseVideo }}
              controls
              controlsList="nodownload nofullscreen noplaybackrate noremoteplayback"
              disablePictureInPicture
              onContextMenu={(event) => event.preventDefault()}
              playsInline
              preload="metadata"
              crossOrigin="anonymous"
              onLoadedMetadata={handleTrackReady}
              onError={handleVideoError}
              onPlay={handlePlay}
              onPause={handlePause}
              onEnded={handleEnded}
              onSeeked={(event) => setActiveCaptionText(captionTextForVideo(event.currentTarget, selectedTrack))}
              onTimeUpdate={handleTimeUpdate}
            >
              {availableTracks.map((track) => (
                <track
                  key={track.key}
                  kind="subtitles"
                  src={captionTrackSrcForUrl(track.vtt_url)}
                  srcLang={track.is_original ? (track.source_language_code || 'und') : track.language_code}
                  label={track.language_label}
                  onLoad={handleTrackReady}
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
          </>
        ) : (
          <div className="flex aspect-video items-center justify-center gap-2 text-sm text-[color:var(--media-text-on-image)] opacity-80">
            <AlertCircle size={16} />
            <span>{PROTECTED_PLAYBACK_UNAVAILABLE_MESSAGE}</span>
          </div>
        )}
      </div>

      {playbackError && (
        <p data-testid="drm-playback-error" className="text-sm font-medium text-[color:var(--feedback-danger-fg)]">{playbackError}</p>
      )}
    </SurfaceCard>
  );
}
