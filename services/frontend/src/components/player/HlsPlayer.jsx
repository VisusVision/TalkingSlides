import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Hls from 'hls.js';
import { AlertCircle } from 'lucide-react';
import SurfaceCard from '../ui/SurfaceCard';

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
    selectedTextTrack.mode = 'showing';
  }
}

function avatarSizeClass(size) {
  const normalized = String(size || 'medium').trim().toLowerCase();
  if (normalized === 'small') return 'w-[18%] min-w-[110px] max-w-[180px]';
  if (normalized === 'large') return 'w-[30%] min-w-[170px] max-w-[320px]';
  return 'w-[24%] min-w-[140px] max-w-[240px]';
}

function avatarPositionClass(position) {
  const normalized = String(position || 'top-right').trim().toLowerCase();
  if (normalized === 'top-left') return 'left-4 top-4';
  if (normalized === 'bottom-left') return 'bottom-4 left-4';
  if (normalized === 'bottom-right') return 'bottom-4 right-4';
  return 'right-4 top-4';
}

export default function HlsPlayer({
  lesson,
  videoRef,
  manifestUrl,
  fallbackUrl,
  fallbackAllowed,
  onPlaybackTimeChange,
  onPlaybackStarted,
  onPlaybackStopped,
  onPlaybackError,
  subtitleTracks = [],
  selectedSubtitleKey = 'off',
  preferredSubtitleLanguage = '',
  onSubtitleKeyChange,
}) {
  const internalVideoRef = useRef(null);
  const avatarVideoRef = useRef(null);
  const activeVideoRef = videoRef || internalVideoRef;
  const [playbackError, setPlaybackError] = useState('');
  const [usingFallback, setUsingFallback] = useState(false);

  const sourceUrl = usingFallback ? String(fallbackUrl || '').trim() : String(manifestUrl || '').trim();
  const canUseFallback = Boolean(fallbackAllowed && fallbackUrl);
  const avatarOverlay = lesson?.avatar_overlay || {};
  const avatarDefaults = avatarOverlay?.defaults || {};
  const avatarStreamUrl = String(avatarOverlay?.stream_url || '').trim();
  const avatarOverlayEnabled = Boolean(avatarOverlay?.enabled && avatarStreamUrl);

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

  const activateFallback = useCallback((reason) => {
    if (!canUseFallback) {
      const message = 'Secure stream is not available for this lesson.';
      setPlaybackError(message);
      onPlaybackError?.({ reason, message });
      return;
    }
    setPlaybackError('');
    setUsingFallback(true);
  }, [canUseFallback, onPlaybackError]);

  const syncAvatarPlayback = useCallback((video) => {
    const avatarVideo = avatarVideoRef.current;
    if (!video || !avatarVideo || !avatarOverlayEnabled) return;
    if (Number.isFinite(video.currentTime) && Math.abs((avatarVideo.currentTime || 0) - video.currentTime) > 0.25) {
      try {
        avatarVideo.currentTime = video.currentTime;
      } catch {
        // Browsers can reject early avatar seeks before metadata is available.
      }
    }
    avatarVideo.playbackRate = video.playbackRate || 1;
    if (video.paused || video.ended) {
      avatarVideo.pause();
      return;
    }
    avatarVideo.play().catch(() => {});
  }, [avatarOverlayEnabled]);

  const handleTrackReady = useCallback(() => {
    setActiveTextTrack(activeVideoRef.current, selectedTrack);
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
    setPlaybackError('');
    setUsingFallback(false);
  }, [lesson?.id, manifestUrl, fallbackUrl]);

  useEffect(() => {
    const video = activeVideoRef.current;
    if (!video || !sourceUrl) return undefined;

    if (usingFallback) {
      video.src = sourceUrl;
      video.load();
      return undefined;
    }

    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = sourceUrl;
      video.load();
      return undefined;
    }

    if (!Hls.isSupported()) {
      activateFallback('hls_not_supported');
      return undefined;
    }

    const hls = new Hls({ enableWorker: true });
    hls.on(Hls.Events.ERROR, (_event, data) => {
      if (data?.fatal) {
        hls.destroy();
        activateFallback(data?.type || data?.details || 'hls_fatal_error');
      }
    });
    hls.loadSource(sourceUrl);
    hls.attachMedia(video);

    return () => {
      hls.destroy();
    };
  }, [activateFallback, activeVideoRef, sourceUrl, usingFallback]);

  const handleVideoError = useCallback(() => {
    if (!usingFallback) {
      activateFallback('video_source_error');
      return;
    }
    const message = 'Secure stream is not available for this lesson.';
    setPlaybackError(message);
    onPlaybackError?.({ reason: 'fallback_video_error', message });
  }, [activateFallback, onPlaybackError, usingFallback]);

  const handlePlay = useCallback((event) => {
    onPlaybackStarted?.();
    syncAvatarPlayback(event.currentTarget);
  }, [onPlaybackStarted, syncAvatarPlayback]);

  const handlePause = useCallback(() => {
    avatarVideoRef.current?.pause();
    onPlaybackStopped?.();
  }, [onPlaybackStopped]);

  const handleTimeUpdate = useCallback((event) => {
    onPlaybackTimeChange?.(Number(event.currentTarget.currentTime || 0));
    syncAvatarPlayback(event.currentTarget);
  }, [onPlaybackTimeChange, syncAvatarPlayback]);

  return (
    <SurfaceCard elevated className="space-y-4 p-4 sm:p-5">
      <div className="relative overflow-hidden rounded-xl bg-[color:var(--video-stage-bg)]">
        {sourceUrl ? (
          <>
            <video
              key={`${lesson?.id || 'lesson'}-${usingFallback ? 'mp4' : 'hls'}`}
              ref={activeVideoRef}
              className="aspect-video w-full bg-black"
              controls
              controlsList="nodownload noplaybackrate noremoteplayback"
              disablePictureInPicture
              onContextMenu={(event) => event.preventDefault()}
              playsInline
              preload="metadata"
              crossOrigin="anonymous"
              onLoadedMetadata={handleTrackReady}
              onError={handleVideoError}
              onPlay={handlePlay}
              onPause={handlePause}
              onEnded={handlePause}
              onSeeked={(event) => syncAvatarPlayback(event.currentTarget)}
              onRateChange={(event) => {
                if (avatarVideoRef.current) avatarVideoRef.current.playbackRate = event.currentTarget.playbackRate || 1;
              }}
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
            {avatarOverlayEnabled && (
              <video
                ref={avatarVideoRef}
                src={avatarStreamUrl}
                className={`pointer-events-none absolute aspect-video rounded-lg border border-black/30 bg-black object-cover shadow-xl ${avatarSizeClass(avatarDefaults.size)} ${avatarPositionClass(avatarDefaults.position)}`}
                muted
                playsInline
                preload="metadata"
                crossOrigin="anonymous"
                onLoadedMetadata={() => syncAvatarPlayback(activeVideoRef.current)}
              />
            )}
          </>
        ) : (
          <div className="flex aspect-video items-center justify-center gap-2 text-sm text-[color:var(--media-text-on-image)] opacity-80">
            <AlertCircle size={16} />
            <span>Secure stream is not available for this lesson.</span>
          </div>
        )}
      </div>

      {usingFallback && (
        <p className="text-xs text-[var(--text-secondary)]">Playing MP4 fallback allowed by this lesson.</p>
      )}

      {playbackError && (
        <p className="text-sm font-medium text-[color:var(--feedback-danger-fg)]">{playbackError}</p>
      )}
    </SurfaceCard>
  );
}
