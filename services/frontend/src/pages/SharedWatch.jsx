import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ShieldCheck } from 'lucide-react';
import { fetchSharedLesson } from '../api';
import VideoStage from '../components/player/VideoStage';
import UnavailableStage from '../components/player/UnavailableStage';
import SurfaceCard from '../components/ui/SurfaceCard';
import { PLAYER_MODES, resolvePlayerMode } from '../components/player/playerMode';

const HlsPlayer = lazy(() => import('../components/player/HlsPlayer'));

function formatExpiresAt(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString([], {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

export default function SharedWatch() {
  const { token } = useParams();
  const videoRef = useRef(null);
  const [lesson, setLesson] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedSubtitleKey, setSelectedSubtitleKey] = useState('original');
  const [playbackTime, setPlaybackTime] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    setLesson(null);

    fetchSharedLesson(token)
      .then((payload) => {
        if (!active) return;
        setLesson(payload);
        setSelectedSubtitleKey(payload?.vtt_url || payload?.subtitle_vtt_url ? 'original' : 'off');
      })
      .catch((err) => {
        if (!active) return;
        const reason = String(err?.reason || '').trim();
        if (reason === 'expired') {
          setError('This share link has expired.');
        } else if (reason === 'revoked') {
          setError('This share link has been revoked.');
        } else {
          setError(err.message || 'This share link is invalid.');
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [token]);

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
  const playerMode = useMemo(() => resolvePlayerMode(lesson, playerCapabilities), [lesson, playerCapabilities]);
  const playbackLesson = useMemo(() => {
    if (!lesson || playerMode.mode !== PLAYER_MODES.PUBLIC_MP4) return lesson;
    return {
      ...lesson,
      stream_url: playerMode.fallbackUrl || lesson.stream_url || lesson.video_url || '',
    };
  }, [lesson, playerMode]);

  const expiresAtLabel = formatExpiresAt(lesson?.share?.expires_at);

  const player = (() => {
    if (!lesson) return null;
    if (playerMode.mode === PLAYER_MODES.PUBLIC_MP4) {
      return (
        <VideoStage
          lesson={playbackLesson}
          subtitleTracks={[]}
          selectedSubtitleKey={selectedSubtitleKey}
          onSubtitleKeyChange={setSelectedSubtitleKey}
          onPlaybackTimeChange={setPlaybackTime}
          videoRef={videoRef}
          showLessonDetails={false}
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
            onPlaybackTimeChange={setPlaybackTime}
            subtitleTracks={[]}
            selectedSubtitleKey={selectedSubtitleKey}
            onSubtitleKeyChange={setSelectedSubtitleKey}
            watermarkLesson={lesson}
          />
        </Suspense>
      );
    }
    return (
      <UnavailableStage
        message={playerMode.message || 'Video source unavailable for this share link.'}
        reason={playerMode.reason}
        mode={playerMode.mode}
      />
    );
  })();

  if (loading) {
    return (
      <div className="mx-auto max-w-5xl space-y-5 px-4 py-8">
        <SurfaceCard elevated className="p-5">
          <p className="body-md">Loading shared lesson...</p>
        </SurfaceCard>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-3xl space-y-5 px-4 py-10">
        <SurfaceCard elevated className="space-y-4 p-5">
          <div>
            <p className="label-sm">Shared Lesson</p>
            <h1 className="mt-1 text-xl font-semibold text-[var(--text-primary)]">Link unavailable</h1>
          </div>
          <p className="text-sm text-[var(--text-secondary)]">{error}</p>
          <Link
            to="/browse"
            className="focus-ring inline-flex h-11 items-center justify-center rounded-full bg-[var(--surface-container-highest)] px-5 text-sm font-medium text-[var(--text-primary)] transition hover:bg-[color:var(--hover-surface-strong)]"
          >
            Browse lessons
          </Link>
        </SurfaceCard>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-5 px-4 py-6">
      <SurfaceCard className="token-glass flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="label-sm">Shared Lesson</p>
          <h1 className="mt-1 text-2xl font-semibold leading-tight text-[var(--text-primary)]">
            {lesson?.title || 'Untitled lesson'}
          </h1>
          {lesson?.description ? (
            <p className="mt-2 max-w-3xl text-sm text-[var(--text-secondary)]">{lesson.description}</p>
          ) : null}
        </div>
        <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-[color:color-mix(in_srgb,var(--accent-secondary),transparent_82%)] px-3 py-1.5 text-xs font-semibold text-[var(--text-primary)]">
          <ShieldCheck size={13} />
          Secure share
        </span>
      </SurfaceCard>

      {player}

      <SurfaceCard className="flex flex-col gap-2 p-4 text-sm text-[var(--text-secondary)] sm:flex-row sm:items-center sm:justify-between">
        <span>{expiresAtLabel ? `Available until ${expiresAtLabel}` : 'This link is time-limited.'}</span>
        <span>{Math.floor(playbackTime) > 0 ? `Playback position ${Math.floor(playbackTime)}s` : 'No sign-in required'}</span>
      </SurfaceCard>
    </div>
  );
}
