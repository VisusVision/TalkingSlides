import { useEffect, useMemo, useRef, useState } from 'react';
import { heartbeatPlaybackSession } from '../api';

export const PLAYBACK_SESSION_DENIED_MESSAGE = 'Playback session expired or is active elsewhere. Please refresh.';

const DEFAULT_HEARTBEAT_INTERVAL_MS = 25000;
const MIN_HEARTBEAT_INTERVAL_MS = 5000;
const MAX_HEARTBEAT_BACKOFF_MS = 120000;

function envEnabled(value) {
  const normalized = String(value ?? 'true').trim().toLowerCase();
  return !['false', '0', 'off', 'no'].includes(normalized);
}

function heartbeatIntervalMs() {
  const parsed = Number(import.meta.env.VITE_PLAYER_HEARTBEAT_INTERVAL_MS || DEFAULT_HEARTBEAT_INTERVAL_MS);
  if (!Number.isFinite(parsed) || parsed <= 0) return DEFAULT_HEARTBEAT_INTERVAL_MS;
  return Math.max(MIN_HEARTBEAT_INTERVAL_MS, parsed);
}

function currentVisibility() {
  if (typeof document === 'undefined') return 'visible';
  return document.visibilityState === 'hidden' ? 'hidden' : 'visible';
}

function isSessionDenial(error) {
  const status = Number(error?.status || 0);
  if ([401, 403, 409].includes(status)) return true;

  const payload = error?.payload || {};
  if (payload.revoked === true || payload.active === false) return true;

  const reason = String(error?.reason || payload.reason || '').trim().toLowerCase();
  return [
    'missing_grant',
    'revoked',
    'superseded',
    'inactive',
    'hidden_too_long',
    'risk_blocked',
    'session_expired',
    'concurrency_active_elsewhere',
  ].includes(reason);
}

function pauseVideo(videoRef) {
  const video = videoRef?.current;
  if (!video || typeof video.pause !== 'function') return;
  try {
    video.pause();
  } catch {
    // Best-effort only; denial state still replaces the player UI.
  }
}

export default function usePlaybackHeartbeat({
  lessonId,
  active,
  videoRef,
  sourceKey = '',
  visibilityLock = false,
  onDenied,
} = {}) {
  const [error, setError] = useState('');
  const [visibility, setVisibility] = useState(currentVisibility);
  const timerRef = useRef(null);
  const inFlightRef = useRef(false);
  const deniedRef = useRef(false);
  const failureCountRef = useRef(0);

  const heartbeatEnabled = envEnabled(import.meta.env.VITE_PLAYER_HEARTBEAT_ENABLED);
  const lockUiEnabled = visibilityLock && envEnabled(import.meta.env.VITE_PLAYER_VISIBILITY_LOCK_ENABLED);
  const intervalMs = useMemo(() => heartbeatIntervalMs(), []);

  useEffect(() => {
    deniedRef.current = false;
    failureCountRef.current = 0;
    setError('');
    setVisibility(currentVisibility());
  }, [lessonId, sourceKey]);

  useEffect(() => {
    const clearTimer = () => {
      if (timerRef.current) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };

    if (!heartbeatEnabled || !lessonId || !active || deniedRef.current) {
      clearTimer();
      return clearTimer;
    }

    let disposed = false;

    const scheduleNext = () => {
      clearTimer();
      if (disposed || deniedRef.current) return;
      const failures = failureCountRef.current;
      const delay = failures
        ? Math.min(intervalMs * (2 ** failures), MAX_HEARTBEAT_BACKOFF_MS)
        : intervalMs;
      timerRef.current = window.setTimeout(sendHeartbeat, delay);
    };

    const handleDenial = (err) => {
      deniedRef.current = true;
      clearTimer();
      pauseVideo(videoRef);
      setError(PLAYBACK_SESSION_DENIED_MESSAGE);
      if (typeof onDenied === 'function') {
        onDenied(PLAYBACK_SESSION_DENIED_MESSAGE, err);
      }
    };

    const sendHeartbeat = async () => {
      if (disposed || deniedRef.current || inFlightRef.current) return;
      inFlightRef.current = true;
      const nextVisibility = currentVisibility();
      setVisibility(nextVisibility);

      try {
        await heartbeatPlaybackSession(lessonId, nextVisibility);
        if (disposed) return;
        failureCountRef.current = 0;
        setError('');
      } catch (err) {
        if (disposed) return;
        if (isSessionDenial(err)) {
          handleDenial(err);
          return;
        }
        failureCountRef.current = Math.min(failureCountRef.current + 1, 4);
      } finally {
        inFlightRef.current = false;
      }

      scheduleNext();
    };

    const handleVisibilityChange = () => {
      setVisibility(currentVisibility());
      if (!lockUiEnabled) return;
      clearTimer();
      sendHeartbeat();
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    sendHeartbeat();

    return () => {
      disposed = true;
      clearTimer();
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [active, heartbeatEnabled, intervalMs, lessonId, lockUiEnabled, onDenied, sourceKey, videoRef]);

  return {
    denied: Boolean(error),
    enabled: Boolean(heartbeatEnabled),
    error,
    visibility,
  };
}
