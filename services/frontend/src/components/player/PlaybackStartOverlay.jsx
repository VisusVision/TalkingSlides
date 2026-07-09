import { useCallback, useEffect, useState } from 'react';
import { LoaderCircle, Play, RotateCcw } from 'lucide-react';
import { AVATAR_OVERLAY_Z_INDEX } from './AvatarOverlayLayer';
import { useLocale } from '../../i18n/LocaleProvider';

export default function PlaybackStartOverlay({
  videoRef,
  sourceKey = '',
  onPlaybackError,
}) {
  const { t } = useLocale();
  const [state, setState] = useState('idle');
  const [message, setMessage] = useState('');
  const [diagnostic, setDiagnostic] = useState('');

  useEffect(() => {
    setState('idle');
    setMessage('');
    setDiagnostic('');
  }, [sourceKey]);

  useEffect(() => {
    const video = videoRef?.current;
    if (!video) return undefined;

    const handlePlaying = () => {
      setState('playing');
      setMessage('');
      setDiagnostic('');
    };
    const handlePause = () => {
      if (!video.ended) setState('paused');
    };
    const handleEnded = () => setState('ended');
    const handleError = () => {
      setState('error');
      setMessage(t('playbackCouldNotStart'));
      setDiagnostic(`MediaError:${video.error?.code || 'unknown'}`);
    };

    video.addEventListener('playing', handlePlaying);
    video.addEventListener('pause', handlePause);
    video.addEventListener('ended', handleEnded);
    video.addEventListener('error', handleError);
    return () => {
      video.removeEventListener('playing', handlePlaying);
      video.removeEventListener('pause', handlePause);
      video.removeEventListener('ended', handleEnded);
      video.removeEventListener('error', handleError);
    };
  }, [sourceKey, t, videoRef]);

  const startPlayback = useCallback(async () => {
    const video = videoRef?.current;
    if (!video) return;
    setState('starting');
    setMessage('');
    setDiagnostic('');
    try {
      if (video.ended) video.currentTime = 0;
      if (video.error) video.load();
      await Promise.resolve(video.play());
      setState('playing');
    } catch (error) {
      if (error?.name === 'NotAllowedError' && !video.muted) {
        try {
          video.muted = true;
          await Promise.resolve(video.play());
          setState('playing');
          setDiagnostic('NotAllowedError:muted-fallback');
          return;
        } catch (fallbackError) {
          error = fallbackError;
        }
      }
      const nextMessage = t('playbackBlocked');
      setState('error');
      setMessage(nextMessage);
      setDiagnostic(`${error?.name || 'Error'}:${error?.message || 'play() rejected'}`);
      onPlaybackError?.({
        reason: 'play_request_rejected',
        message: nextMessage,
        cause: error,
      });
    }
  }, [onPlaybackError, t, videoRef]);

  if (state === 'playing') return null;

  const retry = state === 'error';
  const starting = state === 'starting';
  const label = retry
    ? t('retryPlayback')
    : state === 'ended'
      ? t('playAgain')
      : t('playLesson');
  const Icon = starting ? LoaderCircle : retry ? RotateCcw : Play;

  return (
    <div
      data-testid="playback-start-overlay"
      data-playback-error={diagnostic || undefined}
      className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/10 px-6 text-center"
      style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.captions - 1 }}
    >
      <button
        type="button"
        aria-label={label}
        title={label}
        disabled={starting}
        onClick={startPlayback}
        className="focus-ring pointer-events-auto inline-flex min-h-12 items-center gap-2 rounded-full border border-white/35 bg-black/75 px-5 text-sm font-bold text-white shadow-xl backdrop-blur-sm transition hover:bg-black/90 disabled:cursor-wait disabled:opacity-80"
      >
        <Icon size={20} className={starting ? 'animate-spin' : ''} fill={retry ? 'none' : 'currentColor'} />
        <span>{starting ? t('startingPlayback') : label}</span>
      </button>
      {message && (
        <p role="alert" className="max-w-md rounded-lg bg-black/80 px-3 py-2 text-xs font-medium text-white">
          {message}
        </p>
      )}
    </div>
  );
}
