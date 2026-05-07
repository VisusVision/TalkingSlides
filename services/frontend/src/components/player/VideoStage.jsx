import { useCallback, useEffect, useRef, useState } from 'react';
import Hls from 'hls.js';
import { AlertCircle, ShieldCheck } from 'lucide-react';
import { formatDuration } from '../../lib/content';
import SurfaceCard from '../ui/SurfaceCard';

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

function hlsManifestUrlForLesson(lesson) {
  return [
    lesson?.streaming?.hls?.manifest_url,
    lesson?.drm?.manifest_url,
  ]
    .map((value) => String(value || '').trim())
    .find(Boolean) || '';
}

function captionTrackSrcForUrl(url) {
  const value = String(url || '').trim();
  if (!value) return '';
  const separator = value.includes('?') ? '&' : '?';
  return `${value}${separator}kind=vtt&track=original`;
}

function textTracksForVideo(video) {
  const tracks = video?.textTracks;
  if (!tracks) return [];
  return Array.from({ length: tracks.length }, (_, index) => tracks[index]).filter(Boolean);
}

function findOriginalCaptionTrack(video) {
  const tracks = textTracksForVideo(video);
  return tracks.find((track) => track.label === 'Original')
    || tracks.find((track) => track.kind === 'subtitles' || track.kind === 'captions')
    || null;
}

export default function VideoStage({
  lesson,
  onPlaybackTimeChange,
  videoRef,
  asSurface = true,
  captionMissingLabel = 'No captions yet',
}) {
  const internalVideoRef = useRef(null);
  const activeVideoRef = videoRef || internalVideoRef;
  const avatarOverlayRef = useRef(null);
  const stageRef = useRef(null);
  const trackRef = useRef(null);
  const [captionsAvailable, setCaptionsAvailable] = useState(false);
  const [captionsEnabled, setCaptionsEnabled] = useState(false);
  const [captionsStatus, setCaptionsStatus] = useState(captionMissingLabel);
  const [fullscreenActive, setFullscreenActive] = useState(false);
  const vttUrl = vttUrlForLesson(lesson);
  const hlsManifestUrl = hlsManifestUrlForLesson(lesson);
  const captionTrackSrc = captionTrackSrcForUrl(vttUrl);
  const srtUrl = srtUrlForLesson(lesson);
  const hasVideo = Boolean(lesson?.stream_url || hlsManifestUrl);
  const mediaSrc = String(lesson?.stream_url || '').trim();
  const avatarOverlaySrc = String(lesson?.avatar_overlay?.stream_url || '').trim();
  const avatarOverlayEnabled = Boolean(lesson?.avatar_overlay?.enabled && avatarOverlaySrc);
  const avatarOverlaySize = String(lesson?.avatar_overlay?.size || lesson?.avatar_overlay?.defaults?.size || 'medium').trim().toLowerCase();
  const avatarOverlaySizeClass = avatarOverlaySize === 'small'
    ? 'w-24 sm:w-28 md:w-32'
    : avatarOverlaySize === 'large'
      ? 'w-36 sm:w-44 md:w-52'
      : 'w-28 sm:w-36 md:w-44';
  const fallbackCaptionStatus = srtUrl
    ? 'Captions generated but WebVTT track is unavailable. Rerender to create WebVTT.'
    : captionMissingLabel;

  const setCaptionMode = useCallback((enabled) => {
    const video = activeVideoRef.current;
    const selectedTrack = findOriginalCaptionTrack(video);

    if (!selectedTrack) {
      setCaptionsAvailable(false);
      setCaptionsEnabled(false);
      setCaptionsStatus(vttUrl ? 'Captions generated but WebVTT track is unavailable. Rerender to create WebVTT.' : fallbackCaptionStatus);
      return false;
    }

    textTracksForVideo(video).forEach((track) => {
      track.mode = track === selectedTrack && enabled ? 'showing' : 'disabled';
    });

    setCaptionsAvailable(true);
    setCaptionsEnabled(Boolean(enabled));
    const enabledStatus = import.meta.env.DEV ? 'Caption track loaded from secure stream' : 'CC enabled';
    setCaptionsStatus(enabled ? enabledStatus : 'CC off');
    return true;
  }, [activeVideoRef, fallbackCaptionStatus, vttUrl]);

  const handleCaptionTrackReady = useCallback(() => {
    if (!vttUrl) return;
    setCaptionMode(true);
  }, [setCaptionMode, vttUrl]);

  const handleCaptionTrackError = useCallback(() => {
    setCaptionsAvailable(false);
    setCaptionsEnabled(false);
    setCaptionsStatus('Captions could not be loaded.');
  }, []);

  const handleCaptionToggle = useCallback(() => {
    if (!vttUrl || !captionsAvailable) return;
    setCaptionMode(!captionsEnabled);
  }, [captionsAvailable, captionsEnabled, setCaptionMode, vttUrl]);

  const handleFullscreenToggle = useCallback(() => {
    const stage = stageRef.current;
    if (!stage) return;
    if (document.fullscreenElement) {
      document.exitFullscreen?.().catch(() => {});
      return;
    }
    stage.requestFullscreen?.().catch(() => {});
  }, []);

  useEffect(() => {
    const video = activeVideoRef.current;
    if (!video || !hasVideo) return undefined;

    const canUseNativeHls = video.canPlayType('application/vnd.apple.mpegurl') !== '';
    if (!mediaSrc && hlsManifestUrl && canUseNativeHls) {
      video.src = hlsManifestUrl;
      return undefined;
    }

    if (!mediaSrc && hlsManifestUrl && Hls.isSupported()) {
      const hls = new Hls();
      hls.loadSource(hlsManifestUrl);
      hls.attachMedia(video);
      return () => hls.destroy();
    }

    return undefined;
  }, [activeVideoRef, hasVideo, hlsManifestUrl, mediaSrc, lesson?.id]);

  useEffect(() => {
    setCaptionsAvailable(false);
    setCaptionsEnabled(false);

    if (!hasVideo) {
      setCaptionsStatus('');
      return undefined;
    }

    if (!vttUrl) {
      setCaptionsStatus(fallbackCaptionStatus);
      return undefined;
    }

    setCaptionsStatus('Loading captions...');
    const readyTimer = window.setTimeout(() => {
      if (!setCaptionMode(true)) {
        setCaptionsStatus('Loading captions...');
      }
    }, 0);
    const diagnosticsTimer = window.setTimeout(() => {
      const video = activeVideoRef.current;
      if (!findOriginalCaptionTrack(video)) {
        setCaptionsStatus('Captions generated but WebVTT track is unavailable. Rerender to create WebVTT.');
      }
    }, 2500);

    return () => {
      window.clearTimeout(readyTimer);
      window.clearTimeout(diagnosticsTimer);
    };
  }, [activeVideoRef, fallbackCaptionStatus, hasVideo, lesson?.id, setCaptionMode, vttUrl]);

  useEffect(() => {
    const onFullscreenChange = () => {
      setFullscreenActive(Boolean(document.fullscreenElement && stageRef.current && document.fullscreenElement === stageRef.current));
    };
    document.addEventListener('fullscreenchange', onFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', onFullscreenChange);
  }, []);

  useEffect(() => {
    const mainVideo = activeVideoRef.current;
    const avatarVideo = avatarOverlayRef.current;
    if (!mainVideo || !avatarVideo || !avatarOverlayEnabled) return undefined;

    avatarVideo.muted = true;
    avatarVideo.defaultMuted = true;
    avatarVideo.playbackRate = mainVideo.playbackRate || 1;

    const syncTime = () => {
      if (!Number.isFinite(mainVideo.currentTime)) return;
      if (Math.abs((avatarVideo.currentTime || 0) - mainVideo.currentTime) > 0.15) {
        avatarVideo.currentTime = mainVideo.currentTime;
      }
    };
    const syncPlay = () => {
      syncTime();
      avatarVideo.play().catch(() => {});
    };
    const syncPause = () => avatarVideo.pause();
    const syncRate = () => {
      avatarVideo.playbackRate = mainVideo.playbackRate || 1;
    };

    mainVideo.addEventListener('play', syncPlay);
    mainVideo.addEventListener('playing', syncPlay);
    mainVideo.addEventListener('pause', syncPause);
    mainVideo.addEventListener('seeking', syncTime);
    mainVideo.addEventListener('seeked', syncTime);
    mainVideo.addEventListener('timeupdate', syncTime);
    mainVideo.addEventListener('ratechange', syncRate);

    if (!mainVideo.paused) {
      syncPlay();
    } else {
      syncPause();
      syncTime();
    }

    return () => {
      mainVideo.removeEventListener('play', syncPlay);
      mainVideo.removeEventListener('playing', syncPlay);
      mainVideo.removeEventListener('pause', syncPause);
      mainVideo.removeEventListener('seeking', syncTime);
      mainVideo.removeEventListener('seeked', syncTime);
      mainVideo.removeEventListener('timeupdate', syncTime);
      mainVideo.removeEventListener('ratechange', syncRate);
    };
  }, [activeVideoRef, avatarOverlayEnabled, avatarOverlaySrc, lesson?.id]);

  const content = (
    <>
      <div ref={stageRef} className="relative overflow-hidden rounded-2xl bg-[color:var(--video-stage-bg)]">
        {hasVideo ? (
          <>
            <video
              key={lesson.id}
              ref={activeVideoRef}
              src={mediaSrc || undefined}
              className="aspect-video w-full"
              controls
              controlsList="nofullscreen"
              playsInline
              preload="metadata"
              crossOrigin="anonymous"
              onLoadedMetadata={handleCaptionTrackReady}
              onTimeUpdate={(event) => onPlaybackTimeChange?.(Number(event.currentTarget.currentTime || 0))}
            >
              {vttUrl && (
                <track
                  key={captionTrackSrc}
                  ref={trackRef}
                  kind="subtitles"
                  src={captionTrackSrc}
                  srcLang="tr"
                  label="Original"
                  default
                  onLoad={handleCaptionTrackReady}
                  onError={handleCaptionTrackError}
                />
              )}
            </video>
            {avatarOverlayEnabled && (
              <div className="pointer-events-none absolute right-3 top-3 z-20">
                <video
                  key={`${lesson?.id || 'lesson'}-${avatarOverlaySrc}`}
                  ref={avatarOverlayRef}
                  src={avatarOverlaySrc}
                  className={`${avatarOverlaySizeClass} block overflow-hidden rounded-xl border-2 border-emerald-300 bg-black shadow-2xl`}
                  muted
                  playsInline
                  autoPlay
                  preload="metadata"
                  crossOrigin="anonymous"
                  onLoadedMetadata={(event) => {
                    event.currentTarget.play().catch(() => {});
                  }}
                />
                <span className="absolute left-2 top-1 rounded bg-black/70 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-200">
                  Avatar
                </span>
              </div>
            )}
            {vttUrl && (
              <button
                type="button"
                aria-pressed={captionsEnabled}
                disabled={!captionsAvailable}
                onClick={handleCaptionToggle}
                className="focus-ring absolute left-3 top-3 z-30 inline-flex h-9 items-center justify-center rounded-full border border-white/25 bg-black/60 px-3 text-xs font-semibold text-white shadow-sm backdrop-blur transition hover:bg-black/75 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {captionsEnabled ? 'CC On' : 'CC Off'}
              </button>
            )}
            <button
              type="button"
              onClick={handleFullscreenToggle}
              className="focus-ring absolute left-24 top-3 z-30 inline-flex h-9 items-center justify-center rounded-full border border-white/25 bg-black/60 px-3 text-xs font-semibold text-white shadow-sm backdrop-blur transition hover:bg-black/75"
            >
              {fullscreenActive ? 'Exit Fullscreen' : 'Fullscreen'}
            </button>
          </>
        ) : (
          <div className="flex aspect-video items-center justify-center gap-2 text-sm text-[color:var(--media-text-on-image)] opacity-80">
            <AlertCircle size={16} />
            <span>Video source unavailable for this lesson.</span>
          </div>
        )}
      </div>

      <div className="space-y-2">
        <h1 className="headline-md text-[var(--text-primary)]">{lesson?.title || 'Select a lesson to start'}</h1>
        <p className="body-md max-w-3xl">{lesson?.description || 'Choose a lesson from related content to begin playback.'}</p>
        <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--text-secondary)]">
          <span className="rounded-full bg-[color:var(--surface-muted)] px-2.5 py-1">{lesson?.category_name || 'General'}</span>
          <span className="rounded-full bg-[color:var(--surface-muted)] px-2.5 py-1">{formatDuration(lesson?.duration_minutes || 8)}</span>
          <span className="rounded-full bg-[color:var(--surface-muted)] px-2.5 py-1">{lesson?.teacher_name || 'VISUS Instructor'}</span>
          <span className="inline-flex items-center gap-1 rounded-full bg-[color:color-mix(in_srgb,var(--accent-secondary),transparent_82%)] px-2.5 py-1 text-[var(--text-primary)]">
            <ShieldCheck size={12} />
            Secure stream
          </span>
          {hasVideo && (
            <span className="rounded-full bg-[color:var(--surface-muted)] px-2.5 py-1">
              {vttUrl ? captionsStatus : fallbackCaptionStatus}
            </span>
          )}
        </div>
      </div>
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
