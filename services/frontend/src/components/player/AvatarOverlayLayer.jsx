import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Eye, EyeOff, Maximize2, Minimize2, Minus, Move, Plus, RotateCcw } from 'lucide-react';
import { DEFAULT_AVATAR_PLACEMENT, normalizeAvatarPlacement } from '../../utils/avatarPlacement';

const HEIGHT_RATIO = 9 / 16;
const STORAGE_PREFIX = 'visus-avatar-overlay';
const THEATER_SCALE_DEFAULT = 1;
const THEATER_SCALE_MIN = 0.75;
const THEATER_SCALE_MAX = 1.8;
const THEATER_SCALE_STEP = 0.1;
const THEATER_BASE_WIDTH_VW = 42;
const THEATER_BASE_WIDTH_PX = 520;
const theaterForegroundClass = 'h-auto w-full max-h-full max-w-full rounded-lg bg-transparent object-contain shadow-2xl';

export const AVATAR_OVERLAY_Z_INDEX = Object.freeze({
  baseVideo: 0,
  watermark: 20,
  avatar: 25,
  avatarTheater: 30,
  avatarControls: 40,
  videoControls: 50,
  captions: 60,
});

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function storageKey(lessonId, suffix) {
  return `${STORAGE_PREFIX}:${lessonId || 'none'}:${suffix}`;
}

function readStoredJson(key) {
  if (typeof window === 'undefined') return null;
  try {
    const value = window.localStorage.getItem(key);
    return value ? JSON.parse(value) : null;
  } catch {
    return null;
  }
}

function readStoredVisible(lessonId) {
  if (typeof window === 'undefined') return true;
  const value = window.localStorage.getItem(storageKey(lessonId, 'visible'));
  return value === null ? true : value === 'true';
}

function writeStoredVisible(lessonId, visible) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(storageKey(lessonId, 'visible'), visible ? 'true' : 'false');
}

function writeStoredPlacement(lessonId, placement) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(storageKey(lessonId, 'position'), JSON.stringify(placement));
}

function clearStoredPlacement(lessonId) {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(storageKey(lessonId, 'position'));
}

function clampTheaterScale(value) {
  return Number(clamp(
    Number(value || THEATER_SCALE_DEFAULT),
    THEATER_SCALE_MIN,
    THEATER_SCALE_MAX,
  ).toFixed(2));
}

function readStoredTheaterScale(lessonId) {
  if (typeof window === 'undefined') return THEATER_SCALE_DEFAULT;
  const value = Number(window.localStorage.getItem(storageKey(lessonId, 'theater-scale')));
  return Number.isFinite(value) ? clampTheaterScale(value) : THEATER_SCALE_DEFAULT;
}

function writeStoredTheaterScale(lessonId, scale) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(storageKey(lessonId, 'theater-scale'), String(clampTheaterScale(scale)));
}

function clearStoredTheaterScale(lessonId) {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(storageKey(lessonId, 'theater-scale'));
}

function placementFromStorage(lessonId, fallbackPlacement) {
  const stored = readStoredJson(storageKey(lessonId, 'position'));
  return normalizeAvatarPlacement(stored || fallbackPlacement, fallbackPlacement);
}

function containerHeightRatio(width, bounds) {
  const rectWidth = Number(bounds?.width || 0);
  const rectHeight = Number(bounds?.height || 0);
  if (rectWidth > 0 && rectHeight > 0) {
    return width * HEIGHT_RATIO * (rectWidth / rectHeight);
  }
  return width;
}

export function clampAvatarPlacement(placement, bounds = null) {
  const normalized = normalizeAvatarPlacement({ ...placement, position: 'custom' }, placement);
  const width = clamp(Number(normalized.width || DEFAULT_AVATAR_PLACEMENT.width), 0.12, 0.35);
  const height = containerHeightRatio(width, bounds);
  return {
    ...normalized,
    position: 'custom',
    width: Number(width.toFixed(4)),
    x: Number(clamp(Number(normalized.x || 0), 0, Math.max(0, 1 - width)).toFixed(4)),
    y: Number(clamp(Number(normalized.y || 0), 0, Math.max(0, 1 - height)).toFixed(4)),
  };
}

function floatingPlacementStyle(placement) {
  const normalized = normalizeAvatarPlacement(placement);
  const base = {
    width: `${(normalized.width * 100).toFixed(2)}%`,
    maxWidth: 'calc(100% - 1rem)',
    zIndex: AVATAR_OVERLAY_Z_INDEX.avatar,
  };

  if (normalized.position === 'custom') {
    return {
      ...base,
      left: `${(normalized.x * 100).toFixed(2)}%`,
      top: `${(normalized.y * 100).toFixed(2)}%`,
    };
  }
  if (normalized.position === 'top-left') return { ...base, left: '4%', top: '8%' };
  if (normalized.position === 'bottom-left') return { ...base, left: '4%', bottom: '8%' };
  if (normalized.position === 'bottom-right') return { ...base, right: '4%', bottom: '8%' };
  return { ...base, right: '4%', top: '8%' };
}

function theaterPlacementStyle(scale) {
  const clampedScale = clampTheaterScale(scale);
  return {
    width: `min(${(THEATER_BASE_WIDTH_VW * clampedScale).toFixed(1)}vw, ${Math.round(THEATER_BASE_WIDTH_PX * clampedScale)}px)`,
    maxWidth: 'calc(100% - 1rem)',
    maxHeight: '100%',
  };
}

function controlButtonClassName(extra = '') {
  return [
    'focus-ring inline-flex h-8 w-8 items-center justify-center rounded-full',
    'border border-white/25 bg-black/70 text-white shadow-sm transition hover:bg-black/85',
    extra,
  ].filter(Boolean).join(' ');
}

function controlsVisibilityClassName(visible) {
  return [
    'transition-opacity duration-200 ease-out',
    visible ? 'pointer-events-auto opacity-100' : 'pointer-events-none opacity-0',
  ].join(' ');
}

function AvatarControls({
  visible,
  theater,
  onHide,
  onShow,
  onReset,
  onTheaterToggle,
  onDragPointerDown,
  onTheaterSizeDecrease,
  onTheaterSizeIncrease,
  onTheaterSizeReset,
  compact = false,
}) {
  if (!visible) {
    return (
      <button
        type="button"
        title="Show avatar"
        aria-label="Show avatar"
        onClick={onShow}
        className="focus-ring pointer-events-auto inline-flex items-center gap-2 rounded-full border border-white/25 bg-black/75 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:bg-black/90"
      >
        <Eye size={14} />
        <span>Show avatar</span>
      </button>
    );
  }

  return (
    <div className={`pointer-events-auto flex items-center gap-1 ${compact ? 'justify-end' : ''}`}>
      {typeof onDragPointerDown === 'function' && (
        <button
          type="button"
          title="Drag avatar"
          aria-label="Drag avatar"
          data-testid="avatar-drag-handle"
          onPointerDown={onDragPointerDown}
          className={`${controlButtonClassName()} cursor-grab active:cursor-grabbing`}
        >
          <Move size={15} />
        </button>
      )}
      {theater && typeof onTheaterSizeDecrease === 'function' && (
        <button
          type="button"
          title="Make avatar smaller"
          aria-label="Make avatar smaller"
          onClick={onTheaterSizeDecrease}
          className={controlButtonClassName()}
        >
          <Minus size={15} />
        </button>
      )}
      {theater && typeof onTheaterSizeIncrease === 'function' && (
        <button
          type="button"
          title="Make avatar larger"
          aria-label="Make avatar larger"
          onClick={onTheaterSizeIncrease}
          className={controlButtonClassName()}
        >
          <Plus size={15} />
        </button>
      )}
      {theater && typeof onTheaterSizeReset === 'function' && (
        <button
          type="button"
          title="Reset avatar theater size"
          aria-label="Reset avatar theater size"
          onClick={onTheaterSizeReset}
          className={controlButtonClassName()}
        >
          <RotateCcw size={15} />
        </button>
      )}
      <button
        type="button"
        title="Reset avatar position"
        aria-label="Reset avatar position"
        onClick={onReset}
        className={controlButtonClassName()}
      >
        <RotateCcw size={15} />
      </button>
      <button
        type="button"
        title={theater ? 'Exit avatar theater' : 'Open avatar theater'}
        aria-label={theater ? 'Exit avatar theater' : 'Open avatar theater'}
        onClick={onTheaterToggle}
        className={controlButtonClassName()}
      >
        {theater ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
      </button>
      <button
        type="button"
        title="Hide avatar"
        aria-label="Hide avatar"
        onClick={onHide}
        className={controlButtonClassName()}
      >
        <EyeOff size={15} />
      </button>
    </div>
  );
}

export default function AvatarOverlayLayer({
  lessonId,
  src,
  enabled = true,
  placement,
  videoRef,
  mode = 'floating',
  className = '',
}) {
  const containerRef = useRef(null);
  const frameRef = useRef(null);
  const avatarVideoRef = useRef(null);
  const dragStateRef = useRef(null);
  const autoHideTimerRef = useRef(null);
  const hoverWithinRef = useRef(false);
  const focusWithinRef = useRef(false);
  const draggingRef = useRef(false);
  const theaterOpenRef = useRef(false);
  const [avatarVisible, setAvatarVisible] = useState(() => readStoredVisible(lessonId));
  const defaultPlacement = useMemo(() => normalizeAvatarPlacement(placement || DEFAULT_AVATAR_PLACEMENT), [placement]);
  const [currentPlacement, setCurrentPlacement] = useState(() => placementFromStorage(lessonId, defaultPlacement));
  const [dragging, setDragging] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(false);
  const [focusWithin, setFocusWithin] = useState(false);
  const [theaterOpen, setTheaterOpen] = useState(false);
  const [theaterScale, setTheaterScale] = useState(() => readStoredTheaterScale(lessonId));
  const isStudyPanel = mode === 'study-panel';

  useEffect(() => {
    setAvatarVisible(readStoredVisible(lessonId));
    setCurrentPlacement(placementFromStorage(lessonId, defaultPlacement));
    setTheaterScale(readStoredTheaterScale(lessonId));
    setTheaterOpen(false);
    setControlsVisible(false);
    setFocusWithin(false);
    hoverWithinRef.current = false;
    focusWithinRef.current = false;
    draggingRef.current = false;
    theaterOpenRef.current = false;
  }, [defaultPlacement, lessonId, src]);

  useEffect(() => {
    writeStoredVisible(lessonId, avatarVisible);
  }, [avatarVisible, lessonId]);

  useEffect(() => {
    focusWithinRef.current = focusWithin;
  }, [focusWithin]);

  useEffect(() => {
    draggingRef.current = dragging;
  }, [dragging]);

  useEffect(() => {
    theaterOpenRef.current = theaterOpen;
  }, [theaterOpen]);

  useEffect(() => () => {
    if (autoHideTimerRef.current) {
      window.clearTimeout(autoHideTimerRef.current);
    }
  }, []);

  const clearAutoHideTimer = useCallback(() => {
    if (autoHideTimerRef.current) {
      window.clearTimeout(autoHideTimerRef.current);
      autoHideTimerRef.current = null;
    }
  }, []);

  const showControls = useCallback(({ autoHide = false } = {}) => {
    clearAutoHideTimer();
    setControlsVisible(true);
    if (!autoHide) return;
    autoHideTimerRef.current = window.setTimeout(() => {
      if (
        !hoverWithinRef.current
        && !focusWithinRef.current
        && !draggingRef.current
        && !theaterOpenRef.current
        && !dragStateRef.current
      ) {
        setControlsVisible(false);
      }
      autoHideTimerRef.current = null;
    }, 2600);
  }, [clearAutoHideTimer]);

  const handleFramePointerEnter = useCallback((event) => {
    if (event.pointerType === 'mouse') {
      hoverWithinRef.current = true;
      showControls();
    }
  }, [showControls]);

  const handleFramePointerLeave = useCallback((event) => {
    if (event.pointerType === 'mouse') {
      hoverWithinRef.current = false;
      if (!focusWithinRef.current && !draggingRef.current && !theaterOpenRef.current) {
        setControlsVisible(false);
      }
    }
  }, []);

  const handleFramePointerDown = useCallback((event) => {
    if (event.target?.closest?.('[data-avatar-controls="true"]')) return;
    showControls({ autoHide: event.pointerType !== 'mouse' });
  }, [showControls]);

  const handleFrameFocus = useCallback(() => {
    focusWithinRef.current = true;
    setFocusWithin(true);
    showControls();
  }, [showControls]);

  const handleFrameBlur = useCallback((event) => {
    if (event.currentTarget.contains(event.relatedTarget)) return;
    focusWithinRef.current = false;
    setFocusWithin(false);
    if (!dragging && !theaterOpen) {
      setControlsVisible(false);
    }
  }, [dragging, theaterOpen]);

  const syncAvatarPlayback = useCallback(() => {
    const mainVideo = videoRef?.current;
    const avatarVideo = avatarVideoRef.current;
    if (!mainVideo || !avatarVideo || !enabled || !src || !avatarVisible) return;

    if (
      Number.isFinite(mainVideo.currentTime)
      && Math.abs((avatarVideo.currentTime || 0) - mainVideo.currentTime) > 0.25
    ) {
      try {
        avatarVideo.currentTime = mainVideo.currentTime;
      } catch {
        // Browsers can reject seeks until the overlay video has metadata.
      }
    }

    avatarVideo.playbackRate = mainVideo.playbackRate || 1;
    if (mainVideo.paused || mainVideo.ended) {
      avatarVideo.pause();
      return;
    }
    avatarVideo.play().catch(() => {});
  }, [avatarVisible, enabled, src, videoRef]);

  useEffect(() => {
    const mainVideo = videoRef?.current;
    if (!mainVideo || !enabled || !src || !avatarVisible) return undefined;
    const events = ['play', 'playing', 'pause', 'ended', 'seeked', 'ratechange', 'timeupdate', 'loadedmetadata'];
    events.forEach((eventName) => mainVideo.addEventListener(eventName, syncAvatarPlayback));
    syncAvatarPlayback();
    return () => {
      events.forEach((eventName) => mainVideo.removeEventListener(eventName, syncAvatarPlayback));
    };
  }, [avatarVisible, enabled, src, syncAvatarPlayback, videoRef]);

  const persistPlacement = useCallback((nextPlacement) => {
    const bounds = containerRef.current?.getBoundingClientRect();
    const clamped = clampAvatarPlacement(nextPlacement, bounds);
    setCurrentPlacement(clamped);
    writeStoredPlacement(lessonId, clamped);
  }, [lessonId]);

  const handleDragPointerDown = useCallback((event) => {
    const bounds = containerRef.current?.getBoundingClientRect();
    const frame = frameRef.current?.getBoundingClientRect();
    if (!bounds || !frame) return;
    event.preventDefault();
    event.stopPropagation();
    dragStateRef.current = {
      bounds,
      offsetX: event.clientX - frame.left,
      offsetY: event.clientY - frame.top,
      pointerType: event.pointerType || 'mouse',
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    clearAutoHideTimer();
    setControlsVisible(true);
    draggingRef.current = true;
    setDragging(true);
  }, [clearAutoHideTimer]);

  useEffect(() => {
    if (!dragging) return undefined;

    const handlePointerMove = (event) => {
      const dragState = dragStateRef.current;
      if (!dragState) return;
      const { bounds, offsetX, offsetY } = dragState;
      if (!bounds.width || !bounds.height) return;
      persistPlacement({
        ...currentPlacement,
        position: 'custom',
        x: (event.clientX - bounds.left - offsetX) / bounds.width,
        y: (event.clientY - bounds.top - offsetY) / bounds.height,
      });
    };

    const handlePointerUp = () => {
      const pointerType = dragStateRef.current?.pointerType || 'mouse';
      dragStateRef.current = null;
      draggingRef.current = false;
      setDragging(false);
      showControls({ autoHide: pointerType !== 'mouse' });
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp, { once: true });
    window.addEventListener('pointercancel', handlePointerUp, { once: true });
    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
      window.removeEventListener('pointercancel', handlePointerUp);
    };
  }, [currentPlacement, dragging, persistPlacement, showControls]);

  const handleReset = useCallback(() => {
    setCurrentPlacement(defaultPlacement);
    clearStoredPlacement(lessonId);
  }, [defaultPlacement, lessonId]);

  const updateTheaterScale = useCallback((delta) => {
    clearAutoHideTimer();
    setControlsVisible(true);
    setTheaterScale((previous) => {
      const next = clampTheaterScale(previous + delta);
      writeStoredTheaterScale(lessonId, next);
      return next;
    });
  }, [clearAutoHideTimer, lessonId]);

  const handleTheaterSizeDecrease = useCallback(() => {
    updateTheaterScale(-THEATER_SCALE_STEP);
  }, [updateTheaterScale]);

  const handleTheaterSizeIncrease = useCallback(() => {
    updateTheaterScale(THEATER_SCALE_STEP);
  }, [updateTheaterScale]);

  const handleTheaterSizeReset = useCallback(() => {
    clearAutoHideTimer();
    clearStoredTheaterScale(lessonId);
    setControlsVisible(true);
    setTheaterScale(THEATER_SCALE_DEFAULT);
  }, [clearAutoHideTimer, lessonId]);

  const handleShow = useCallback(() => setAvatarVisible(true), []);
  const handleHide = useCallback(() => {
    setAvatarVisible(false);
    setControlsVisible(false);
    setTheaterOpen(false);
  }, []);
  const handleTheaterToggle = useCallback(() => {
    clearAutoHideTimer();
    setControlsVisible(true);
    setTheaterOpen((previous) => {
      theaterOpenRef.current = !previous;
      return !previous;
    });
  }, [clearAutoHideTimer]);

  if (!enabled || !src) return null;

  const renderAvatarVideo = ({ theater = false } = {}) => (
    <video
      ref={avatarVideoRef}
      src={src}
      data-testid="avatar-overlay-video"
      data-avatar-video-mode={theater ? 'theater' : 'pip'}
      className={[
        'pointer-events-none rounded-lg',
        theater ? theaterForegroundClass : 'h-full w-full bg-black object-cover',
      ].join(' ')}
      muted
      playsInline
      preload="metadata"
      crossOrigin="anonymous"
      onLoadedMetadata={syncAvatarPlayback}
    />
  );

  if (isStudyPanel) {
    return (
      <div
        ref={containerRef}
        data-testid="avatar-study-panel-layer"
        data-avatar-layer="viewer"
        data-avatar-mode="study-panel"
        className={`space-y-2 ${className}`}
      >
        {!avatarVisible ? (
          <div className="flex min-h-[7rem] items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-black/80">
            <AvatarControls
              visible={false}
              onShow={handleShow}
            />
          </div>
        ) : (
          <>
            <div
              data-avatar-theater-frame={theaterOpen ? 'true' : undefined}
              className={[
                'relative aspect-video overflow-hidden rounded-lg border bg-black shadow-sm transition-shadow duration-200',
                theaterOpen
                  ? 'border-white/30 shadow-xl ring-2 ring-white/25'
                  : 'border-[var(--border-subtle)]',
              ].join(' ')}
            >
              {renderAvatarVideo({ theater: theaterOpen })}
              <div
                tabIndex={0}
                role="group"
                aria-label="Avatar overlay"
                className="absolute inset-0 pointer-events-auto"
                onPointerEnter={handleFramePointerEnter}
                onPointerLeave={handleFramePointerLeave}
                onPointerDown={handleFramePointerDown}
                onFocus={handleFrameFocus}
                onBlur={handleFrameBlur}
              />
              <div
                data-testid="avatar-overlay-controls"
                data-avatar-controls="true"
                data-controls-visible={theaterOpen || controlsVisible ? 'true' : 'false'}
                className={`absolute right-2 top-2 ${controlsVisibilityClassName(theaterOpen || controlsVisible)}`}
                style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.avatarControls }}
              >
                <AvatarControls
                  visible
                  compact
                  theater={theaterOpen}
                  onHide={handleHide}
                  onReset={handleReset}
                  onTheaterToggle={handleTheaterToggle}
                />
              </div>
            </div>
          </>
        )}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      data-testid="avatar-overlay-layer"
      data-avatar-layer="viewer"
      data-avatar-mode="floating"
      className={`pointer-events-none absolute inset-0 ${className}`}
      style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.avatar }}
    >
      {!avatarVisible ? (
        <div className="absolute right-3 top-3" style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.avatarControls }}>
          <AvatarControls visible={false} onShow={handleShow} />
        </div>
      ) : theaterOpen ? (
        <div
          data-testid="avatar-theater-overlay"
          className="pointer-events-none absolute inset-x-3 top-3 bottom-16 flex items-center justify-center sm:inset-x-5 sm:top-5 sm:bottom-20"
          style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.avatarTheater }}
        >
          <div
            data-avatar-theater-frame="true"
            className="pointer-events-auto relative flex items-center justify-center"
            style={theaterPlacementStyle(theaterScale)}
          >
            <div
              data-avatar-theater-foreground-frame="true"
              className="flex h-auto w-full items-center justify-center"
            >
              {renderAvatarVideo({ theater: true })}
            </div>
            <div
              data-avatar-controls="true"
              className="absolute right-3 top-3"
              style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.avatarControls }}
            >
              <AvatarControls
                visible
                compact
                theater
                onHide={handleHide}
                onReset={handleReset}
                onTheaterToggle={handleTheaterToggle}
                onTheaterSizeDecrease={handleTheaterSizeDecrease}
                onTheaterSizeIncrease={handleTheaterSizeIncrease}
                onTheaterSizeReset={handleTheaterSizeReset}
              />
            </div>
          </div>
        </div>
      ) : (
        <div
          ref={frameRef}
          tabIndex={0}
          role="group"
          aria-label="Avatar overlay"
          className="pointer-events-auto absolute aspect-video rounded-lg border border-black/30 bg-black shadow-xl"
          style={floatingPlacementStyle(currentPlacement)}
          onPointerEnter={handleFramePointerEnter}
          onPointerLeave={handleFramePointerLeave}
          onPointerDown={handleFramePointerDown}
          onFocus={handleFrameFocus}
          onBlur={handleFrameBlur}
        >
          {renderAvatarVideo()}
          <div
            data-testid="avatar-overlay-controls"
            data-avatar-controls="true"
            data-controls-visible={controlsVisible || dragging ? 'true' : 'false'}
            className={`absolute right-1 top-1 ${controlsVisibilityClassName(controlsVisible || dragging)}`}
            style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.avatarControls }}
          >
            <AvatarControls
              visible
              theater={false}
              onHide={handleHide}
              onReset={handleReset}
              onTheaterToggle={handleTheaterToggle}
              onDragPointerDown={handleDragPointerDown}
            />
          </div>
        </div>
      )}
    </div>
  );
}
