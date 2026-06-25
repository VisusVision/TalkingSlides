import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Eye, EyeOff, Maximize2, Minimize2, MoveDiagonal, RotateCcw } from 'lucide-react';
import { DEFAULT_AVATAR_PLACEMENT, normalizeAvatarPlacement } from '../../utils/avatarPlacement';

const HEIGHT_RATIO = 9 / 16;
const MANUAL_WIDTH_MIN = 0.20;
const MANUAL_WIDTH_MAX = 0.98;
const THEATER_ACTIVE_WIDTH_RATIO = 0.70;
const VIDEO_CONTROL_SAFE_BOTTOM = 56;
const STORAGE_PREFIX = 'visus-avatar-overlay';
const THEATER_SCALE_DEFAULT = 1;
const THEATER_SCALE_MIN = 0.32;
const THEATER_SCALE_MAX = 1.35;
const THEATER_BASE_WIDTH_VW = 98;
const THEATER_BASE_WIDTH_PX = 1800;
const THEATER_MAX_HEIGHT_VH = 98;
const THEATER_OBJECT_POSITION = '50% 45%';
const theaterFrameClass = [
  'pointer-events-none relative flex aspect-video items-center justify-center overflow-hidden',
  'rounded-xl border border-white/30 bg-white/5 shadow-2xl ring-1 ring-black/30',
  'transition-all duration-200 ease-out',
].join(' ');
const theaterForegroundFrameClass = 'flex h-full w-full items-center justify-center overflow-hidden rounded-xl';
const theaterForegroundClass = 'h-full w-full bg-transparent object-cover';

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

function clearStoredTheaterScale(lessonId) {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(storageKey(lessonId, 'theater-scale'));
}

function manualSizeFromWidth(width) {
  if (width <= 0.205) return 'small';
  if (width >= 0.27) return 'large';
  return 'medium';
}

function numeric(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function manualMaxWidthForBounds(bounds) {
  const rectWidth = Number(bounds?.width || 0);
  const safeRect = getSafeContainerRect(bounds);
  if (rectWidth > 0 && safeRect?.height > 0) {
    return Math.max(
      MANUAL_WIDTH_MIN,
      Math.min(MANUAL_WIDTH_MAX, safeRect.height / (rectWidth * HEIGHT_RATIO)),
    );
  }
  return MANUAL_WIDTH_MAX;
}

function isAvatarEffectivelyLarge(placement, theaterOpen = false) {
  if (theaterOpen) return true;
  return numeric(placement?.width, 0) >= THEATER_ACTIVE_WIDTH_RATIO;
}

function placementFromStorage(lessonId, fallbackPlacement) {
  const stored = readStoredJson(storageKey(lessonId, 'position'));
  if (stored?.position === 'custom') {
    return clampAvatarPlacement(stored);
  }
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

function videoControlSafeBottomCss() {
  return `${VIDEO_CONTROL_SAFE_BOTTOM}px`;
}

export function getSafeContainerRect(containerRect) {
  const width = Number(containerRect?.width || 0);
  const height = Number(containerRect?.height || 0);
  if (width <= 0 || height <= 0) return null;
  const safeBottomInset = Math.min(VIDEO_CONTROL_SAFE_BOTTOM, Math.max(0, height));
  const safeHeight = Math.max(0, height - safeBottomInset);
  const left = Number(containerRect.left || 0);
  const top = Number(containerRect.top || 0);
  return {
    left,
    top,
    right: left + width,
    bottom: top + safeHeight,
    width,
    height: safeHeight,
  };
}

export function clampAvatarRectToSafeArea(rect, safeRect) {
  if (!rect || !safeRect) return rect;
  const width = Number(rect.width || 0);
  const height = Number(rect.height || 0);
  const left = clamp(
    Number(rect.left || 0),
    safeRect.left,
    Math.max(safeRect.left, safeRect.right - width),
  );
  const top = clamp(
    Number(rect.top || 0),
    safeRect.top,
    Math.max(safeRect.top, safeRect.bottom - height),
  );
  return {
    left,
    top,
    right: left + width,
    bottom: top + height,
    width,
    height,
  };
}

export function clampAvatarPlacementToSafeArea(placement, bounds = null) {
  const safeRect = getSafeContainerRect(bounds);
  if (!safeRect || !bounds?.width || !bounds?.height) return placement;
  const widthPx = placement.width * bounds.width;
  const heightPx = widthPx * HEIGHT_RATIO;
  const rect = {
    left: bounds.left + (placement.x * bounds.width),
    top: bounds.top + (placement.y * bounds.height),
    width: widthPx,
    height: heightPx,
  };
  const clampedRect = clampAvatarRectToSafeArea(rect, safeRect);
  return {
    ...placement,
    x: Number(((clampedRect.left - bounds.left) / bounds.width).toFixed(4)),
    y: Number(((clampedRect.top - bounds.top) / bounds.height).toFixed(4)),
  };
}

export function clampAvatarPlacement(placement, bounds = null) {
  const fallback = normalizeAvatarPlacement(placement || DEFAULT_AVATAR_PLACEMENT);
  const source = placement || fallback;
  const maxWidth = manualMaxWidthForBounds(bounds);
  const width = clamp(numeric(source.width, fallback.width), MANUAL_WIDTH_MIN, maxWidth);
  const height = containerHeightRatio(width, bounds);
  const x = numeric(source.x, fallback.x);
  const y = numeric(source.y, fallback.y);
  const clamped = {
    ...fallback,
    position: 'custom',
    size: manualSizeFromWidth(width),
    width: Number(width.toFixed(4)),
    x: Number(clamp(x, 0, Math.max(0, 1 - width)).toFixed(4)),
    y: Number(clamp(y, 0, Math.max(0, 1 - height)).toFixed(4)),
  };
  return clampAvatarPlacementToSafeArea(clamped, bounds);
}

function placementsEqual(left, right) {
  return left?.position === right?.position
    && left?.size === right?.size
    && Number(left?.width) === Number(right?.width)
    && Number(left?.x) === Number(right?.x)
    && Number(left?.y) === Number(right?.y);
}

function floatingPlacementStyle(placement, bounds = null) {
  const normalized = clampAvatarPlacement(placement, bounds);
  const base = {
    width: `${(normalized.width * 100).toFixed(2)}%`,
    maxWidth: normalized.position === 'custom' ? '100%' : 'calc(100% - 1rem)',
    zIndex: AVATAR_OVERLAY_Z_INDEX.avatar,
  };

  if (normalized.position === 'custom') {
    return {
      ...base,
      left: `${(normalized.x * 100).toFixed(2)}%`,
      top: `${(normalized.y * 100).toFixed(2)}%`,
    };
  }
  return {
    ...base,
    left: `${(normalized.x * 100).toFixed(2)}%`,
    top: `${(normalized.y * 100).toFixed(2)}%`,
  };
}

function studyPanelPlacementStyle(placement) {
  const normalized = clampAvatarPlacement(placement);
  return {
    width: `${(normalized.width * 100).toFixed(2)}%`,
    maxWidth: '100%',
  };
}

function theaterPlacementStyle(scale) {
  const clampedScale = clampTheaterScale(scale);
  return {
    aspectRatio: '16 / 9',
    height: `min(${(THEATER_BASE_WIDTH_VW * clampedScale).toFixed(1)}%, ${Math.round(THEATER_BASE_WIDTH_PX * HEIGHT_RATIO * clampedScale)}px, ${THEATER_MAX_HEIGHT_VH}vh)`,
    maxHeight: '100%',
    maxWidth: `min(${(THEATER_BASE_WIDTH_VW * clampedScale).toFixed(1)}%, ${Math.round(THEATER_BASE_WIDTH_PX * clampedScale)}px)`,
    width: 'auto',
  };
}

function controlButtonClassName(extra = '', interactive = true) {
  return [
    'focus-ring inline-flex h-8 w-8 items-center justify-center rounded-full',
    interactive ? 'pointer-events-auto' : 'pointer-events-none',
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

const INTERACTIVE_AVATAR_TARGET_SELECTOR = [
  'button',
  'a',
  'input',
  'select',
  'textarea',
  '[role="button"]',
  '[data-avatar-control="true"]',
  '[data-avatar-controls="true"]',
  '[data-avatar-no-drag="true"]',
  '[data-avatar-resize-grip="true"]',
].join(', ');

function isInteractiveAvatarTarget(target) {
  return Boolean(target?.closest?.(INTERACTIVE_AVATAR_TARGET_SELECTOR));
}

function handleAvatarControlPointerDown(event) {
  event.stopPropagation();
}

function AvatarResizeGrip({ theater = false, onPointerDown }) {
  return (
    <button
      type="button"
      data-testid="avatar-resize-grip"
      data-avatar-resize-grip="true"
      data-avatar-no-drag="true"
      title={theater ? 'Resize avatar theater' : 'Resize avatar overlay'}
      aria-label={theater ? 'Resize avatar theater' : 'Resize avatar overlay'}
      onPointerDown={onPointerDown}
      onClick={(event) => event.preventDefault()}
      className={[
        'focus-ring pointer-events-auto absolute bottom-0 left-0 z-10 inline-flex h-9 w-9',
        'touch-none cursor-sw-resize items-center justify-center rounded-bl-lg rounded-tr-xl',
        'border border-white/25 bg-black/65 text-white/85 shadow-sm transition hover:bg-black/85 hover:text-white',
      ].join(' ')}
      style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.avatarControls + 1 }}
    >
      <MoveDiagonal
        size={16}
        data-avatar-resize-icon="sw-ne"
        data-avatar-resize-direction="top-right-bottom-left"
        aria-hidden="true"
      />
    </button>
  );
}

function AvatarControls({
  visible,
  theater,
  inTheater = theater,
  onHide,
  onShow,
  onReset,
  onTheaterToggle,
  onTheaterReset,
  compact = false,
  interactive = true,
  stacked = false,
}) {
  if (!visible) {
    return (
      <button
        type="button"
        data-avatar-control="true"
        data-avatar-no-drag="true"
        title="Show avatar"
        aria-label="Show avatar"
        onPointerDown={handleAvatarControlPointerDown}
        onClick={onShow}
        className="focus-ring pointer-events-auto inline-flex items-center gap-2 rounded-full border border-white/25 bg-black/75 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:bg-black/90"
      >
        <Eye size={14} />
        <span>Show avatar</span>
      </button>
    );
  }

  return (
    <div
      aria-hidden={!interactive}
      data-avatar-controls-layout={stacked ? 'stacked' : 'row'}
      className={[
        'pointer-events-none flex gap-1',
        stacked ? 'flex-col items-end' : `items-center ${compact ? 'justify-end' : ''}`,
      ].join(' ')}
    >
      {(() => {
        const resetHandler = theater && typeof onTheaterReset === 'function' ? onTheaterReset : onReset;
        const resetLabel = theater && typeof onTheaterReset === 'function'
          ? 'Reset avatar theater'
          : 'Reset avatar position';
        if (typeof resetHandler !== 'function') return null;
        return (
          <button
            type="button"
            data-avatar-control="true"
            data-avatar-no-drag="true"
            title={resetLabel}
            aria-label={resetLabel}
            onPointerDown={handleAvatarControlPointerDown}
            onClick={resetHandler}
            tabIndex={interactive ? undefined : -1}
            className={controlButtonClassName('', interactive)}
          >
            <RotateCcw size={15} />
          </button>
        );
      })()}
      {(() => {
        const theaterLabel = theater
          ? (inTheater ? 'Exit avatar theater' : 'Shrink avatar')
          : 'Open avatar theater';
        return (
          <button
            type="button"
            data-avatar-control="true"
            data-avatar-no-drag="true"
            data-avatar-theater-active={theater ? 'true' : 'false'}
            title={theaterLabel}
            aria-label={theaterLabel}
            aria-pressed={theater ? 'true' : 'false'}
            onPointerDown={handleAvatarControlPointerDown}
            onClick={onTheaterToggle}
            tabIndex={interactive ? undefined : -1}
            className={controlButtonClassName(theater ? 'ring-2 ring-white/55' : '', interactive)}
          >
            {theater ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
          </button>
        );
      })()}
      <button
        type="button"
        data-avatar-control="true"
        data-avatar-no-drag="true"
        title="Hide avatar"
        aria-label="Hide avatar"
        onPointerDown={handleAvatarControlPointerDown}
        onClick={onHide}
        tabIndex={interactive ? undefined : -1}
        className={controlButtonClassName('', interactive)}
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
  const resizeStateRef = useRef(null);
  const autoHideTimerRef = useRef(null);
  const hoverWithinRef = useRef(false);
  const focusWithinRef = useRef(false);
  const draggingRef = useRef(false);
  const resizingRef = useRef(false);
  const [avatarVisible, setAvatarVisible] = useState(() => readStoredVisible(lessonId));
  const defaultPlacement = useMemo(() => normalizeAvatarPlacement(placement || DEFAULT_AVATAR_PLACEMENT), [placement]);
  const [currentPlacement, setCurrentPlacement] = useState(() => placementFromStorage(lessonId, defaultPlacement));
  const [dragging, setDragging] = useState(false);
  const [resizing, setResizing] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(false);
  const [focusWithin, setFocusWithin] = useState(false);
  const [theaterOpen, setTheaterOpen] = useState(false);
  const [theaterScale, setTheaterScale] = useState(() => readStoredTheaterScale(lessonId));
  const [studyPanelCustomSize, setStudyPanelCustomSize] = useState(false);
  const isStudyPanel = mode === 'study-panel';
  const getPlacementBounds = useCallback(() => {
    return containerRef.current?.getBoundingClientRect() || null;
  }, []);

  useEffect(() => {
    setAvatarVisible(readStoredVisible(lessonId));
    setCurrentPlacement(placementFromStorage(lessonId, defaultPlacement));
    setTheaterScale(readStoredTheaterScale(lessonId));
    setTheaterOpen(false);
    setControlsVisible(false);
    setFocusWithin(false);
    setStudyPanelCustomSize(false);
    hoverWithinRef.current = false;
    focusWithinRef.current = false;
    draggingRef.current = false;
    resizingRef.current = false;
    dragStateRef.current = null;
    resizeStateRef.current = null;
  }, [defaultPlacement, lessonId, src]);

  useLayoutEffect(() => {
    if (!avatarVisible) return;
    const bounds = getPlacementBounds();
    if (!bounds) return;
    setCurrentPlacement((previousPlacement) => {
      const nextPlacement = clampAvatarPlacement(previousPlacement, bounds);
      if (placementsEqual(previousPlacement, nextPlacement)) return previousPlacement;
      if (readStoredJson(storageKey(lessonId, 'position'))) {
        writeStoredPlacement(lessonId, nextPlacement);
      }
      return nextPlacement;
    });
  }, [avatarVisible, getPlacementBounds, lessonId, src]);

  useEffect(() => {
    if (!avatarVisible) return undefined;
    const handleResize = () => {
      const bounds = getPlacementBounds();
      if (!bounds) return;
      setCurrentPlacement((previousPlacement) => {
        const nextPlacement = clampAvatarPlacement(previousPlacement, bounds);
        if (placementsEqual(previousPlacement, nextPlacement)) return previousPlacement;
        writeStoredPlacement(lessonId, nextPlacement);
        return nextPlacement;
      });
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [avatarVisible, getPlacementBounds, lessonId]);

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
    resizingRef.current = resizing;
  }, [resizing]);

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
        && !resizingRef.current
        && !dragStateRef.current
        && !resizeStateRef.current
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
      if (event.relatedTarget?.closest?.('[data-avatar-controls="true"]')) return;
      hoverWithinRef.current = false;
      if (!focusWithinRef.current && !draggingRef.current && !resizingRef.current) {
        setControlsVisible(false);
      }
    }
  }, []);

  const handleFrameFocus = useCallback(() => {
    focusWithinRef.current = true;
    setFocusWithin(true);
    showControls();
  }, [showControls]);

  const handleFrameBlur = useCallback((event) => {
    if (event.currentTarget.contains(event.relatedTarget)) return;
    focusWithinRef.current = false;
    setFocusWithin(false);
    if (!dragging && !resizing) {
      setControlsVisible(false);
    }
  }, [dragging, resizing]);

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
    const bounds = getPlacementBounds();
    const clamped = clampAvatarPlacement(nextPlacement, bounds);
    setCurrentPlacement(clamped);
    writeStoredPlacement(lessonId, clamped);
  }, [getPlacementBounds, lessonId]);

  const handleDragPointerDown = useCallback((event) => {
    if (event.button !== undefined && event.button !== 0) return;
    const bounds = getPlacementBounds();
    const frame = frameRef.current?.getBoundingClientRect();
    if (!bounds || !frame) return;
    event.preventDefault();
    event.stopPropagation();
    dragStateRef.current = {
      bounds,
      offsetX: event.clientX - frame.left,
      offsetY: event.clientY - frame.top,
      placement: currentPlacement,
      pointerType: event.pointerType || 'mouse',
      target: event.currentTarget,
      pointerId: event.pointerId,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    clearAutoHideTimer();
    setControlsVisible(true);
    draggingRef.current = true;
    setDragging(true);
  }, [clearAutoHideTimer, currentPlacement, getPlacementBounds]);

  const handleFramePointerDown = useCallback((event) => {
    if (isInteractiveAvatarTarget(event.target)) return;
    showControls({ autoHide: event.pointerType !== 'mouse' });
    if (isStudyPanel || theaterOpen) return;
    handleDragPointerDown(event);
  }, [handleDragPointerDown, isStudyPanel, showControls, theaterOpen]);

  useEffect(() => {
    if (!dragging) return undefined;

    const handlePointerMove = (event) => {
      const dragState = dragStateRef.current;
      if (!dragState) return;
      const { bounds, offsetX, offsetY, placement } = dragState;
      if (!bounds.width || !bounds.height) return;
      persistPlacement({
        ...placement,
        position: 'custom',
        x: (event.clientX - bounds.left - offsetX) / bounds.width,
        y: (event.clientY - bounds.top - offsetY) / bounds.height,
      });
    };

    const handlePointerUp = () => {
      const dragState = dragStateRef.current;
      const pointerType = dragState?.pointerType || 'mouse';
      dragState?.target?.releasePointerCapture?.(dragState.pointerId);
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
  }, [dragging, persistPlacement, showControls]);

  const handleResizePointerDown = useCallback((event) => {
    if (event.button !== undefined && event.button !== 0) return;
    const bounds = getPlacementBounds();
    const frame = frameRef.current?.getBoundingClientRect();
    if (!bounds || !frame) return;
    event.preventDefault();
    event.stopPropagation();
    const startsInTheater = theaterOpen;
    let floatingRightPx = frame.right - bounds.left;
    let floatingTopPx = frame.top - bounds.top;
    let frameWidthPx = frame.width;
    let placementForResize = currentPlacement;
    let pointerOffsetX = 0;
    let pointerOffsetY = 0;

    if (startsInTheater) {
      const minWidthPx = MANUAL_WIDTH_MIN * bounds.width;
      const maxWidthPx = Math.max(minWidthPx, manualMaxWidthForBounds(bounds) * bounds.width);
      frameWidthPx = clamp(frame.width, minWidthPx, maxWidthPx);
      placementForResize = clampAvatarPlacement({
        ...currentPlacement,
        position: 'custom',
        width: frameWidthPx / bounds.width,
        x: (floatingRightPx - frameWidthPx) / bounds.width,
        y: floatingTopPx / bounds.height,
      }, bounds);
      floatingRightPx = (placementForResize.x + placementForResize.width) * bounds.width;
      floatingTopPx = placementForResize.y * bounds.height;
      frameWidthPx = placementForResize.width * bounds.width;
      pointerOffsetX = (event.clientX - bounds.left) - (floatingRightPx - frameWidthPx);
      pointerOffsetY = (event.clientY - bounds.top) - (floatingTopPx + (frameWidthPx * HEIGHT_RATIO));
      // Capture the rendered theater rectangle first; resize gestures then use only manual bounds.
      setTheaterOpen(false);
      if (isStudyPanel) {
        setStudyPanelCustomSize(true);
      }
      setCurrentPlacement(placementForResize);
      writeStoredPlacement(lessonId, placementForResize);
    }

    resizeStateRef.current = {
      bounds,
      floatingRightPx,
      floatingTopPx,
      frameWidthPx,
      placement: placementForResize,
      pointerOffsetX,
      pointerOffsetY,
      pointerType: event.pointerType || 'mouse',
      target: event.currentTarget,
      pointerId: event.pointerId,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    clearAutoHideTimer();
    setControlsVisible(true);
    resizingRef.current = true;
    setResizing(true);
  }, [clearAutoHideTimer, currentPlacement, getPlacementBounds, isStudyPanel, lessonId, theaterOpen]);

  useEffect(() => {
    if (!resizing) return undefined;

    const handlePointerMove = (event) => {
      const resizeState = resizeStateRef.current;
      if (!resizeState) return;

      const {
        bounds,
        floatingRightPx,
        floatingTopPx,
        frameWidthPx,
        placement,
      } = resizeState;
      if (!bounds.width || !bounds.height) return;

      const pointerX = event.clientX - bounds.left;
      const pointerY = event.clientY - bounds.top;
      const adjustedPointerX = pointerX - (resizeState.pointerOffsetX || 0);
      const adjustedPointerY = pointerY - (resizeState.pointerOffsetY || 0);
      const widthFromLeftPx = floatingRightPx - adjustedPointerX;
      const widthFromHeightPx = (adjustedPointerY - floatingTopPx) / HEIGHT_RATIO;
      const preferredWidthPx = Math.abs(widthFromHeightPx - frameWidthPx) > Math.abs(widthFromLeftPx - frameWidthPx)
        ? widthFromHeightPx
        : widthFromLeftPx;
      const minWidthPx = MANUAL_WIDTH_MIN * bounds.width;
      const maxWidthPx = Math.max(minWidthPx, manualMaxWidthForBounds(bounds) * bounds.width);
      const nextWidthPx = clamp(preferredWidthPx, minWidthPx, maxWidthPx);
      persistPlacement({
        ...placement,
        position: 'custom',
        width: nextWidthPx / bounds.width,
        x: (floatingRightPx - nextWidthPx) / bounds.width,
        y: floatingTopPx / bounds.height,
      });
    };

    const handlePointerUp = () => {
      const resizeState = resizeStateRef.current;
      const pointerType = resizeState?.pointerType || 'mouse';
      resizeState?.target?.releasePointerCapture?.(resizeState.pointerId);
      resizeStateRef.current = null;
      resizingRef.current = false;
      setResizing(false);
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
  }, [lessonId, persistPlacement, resizing, showControls]);

  const handleReset = useCallback(() => {
    const bounds = getPlacementBounds();
    setCurrentPlacement(clampAvatarPlacement(defaultPlacement, bounds));
    setStudyPanelCustomSize(false);
    clearStoredPlacement(lessonId);
  }, [defaultPlacement, getPlacementBounds, lessonId]);

  const applyFloatingPreset = useCallback(() => {
    const bounds = getPlacementBounds();
    const nextPlacement = clampAvatarPlacement(defaultPlacement, bounds);
    setCurrentPlacement(nextPlacement);
    setStudyPanelCustomSize(false);
    clearStoredPlacement(lessonId);
  }, [defaultPlacement, getPlacementBounds, lessonId]);

  const handleTheaterReset = useCallback(() => {
    handleReset();
    clearStoredTheaterScale(lessonId);
    showControls({ autoHide: true });
    setTheaterScale(THEATER_SCALE_DEFAULT);
  }, [handleReset, lessonId, showControls]);

  const handleShow = useCallback(() => {
    const bounds = getPlacementBounds();
    setCurrentPlacement((previousPlacement) => clampAvatarPlacement(previousPlacement, bounds));
    setAvatarVisible(true);
  }, [getPlacementBounds]);
  const handleHide = useCallback(() => {
    setAvatarVisible(false);
    setControlsVisible(false);
    setTheaterOpen(false);
    hoverWithinRef.current = false;
    focusWithinRef.current = false;
    setFocusWithin(false);
  }, []);
  const handleTheaterToggle = useCallback(() => {
    hoverWithinRef.current = false;
    focusWithinRef.current = false;
    setFocusWithin(false);
    if (isAvatarEffectivelyLarge(currentPlacement, theaterOpen)) {
      setTheaterOpen(false);
      applyFloatingPreset();
    } else {
      clearStoredTheaterScale(lessonId);
      setTheaterScale(THEATER_SCALE_DEFAULT);
      setTheaterOpen(true);
    }
    showControls({ autoHide: true });
  }, [applyFloatingPreset, currentPlacement, lessonId, showControls, theaterOpen]);

  if (!enabled || !src) return null;

  const theaterButtonActive = isAvatarEffectivelyLarge(currentPlacement, theaterOpen);
  const studyPanelFrameStyle = theaterOpen
    ? theaterPlacementStyle(theaterScale)
    : (studyPanelCustomSize ? studyPanelPlacementStyle(currentPlacement) : undefined);

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
      style={theater ? { objectPosition: THEATER_OBJECT_POSITION } : undefined}
      muted
      playsInline
      preload="metadata"
      crossOrigin="use-credentials"
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
              ref={frameRef}
              data-avatar-theater-frame={theaterOpen ? 'true' : undefined}
              className={[
                'relative mx-auto aspect-video overflow-hidden rounded-lg border bg-black shadow-sm transition-shadow duration-200',
                theaterOpen
                  ? 'border-white/30 shadow-xl ring-2 ring-white/25'
                  : 'border-[var(--border-subtle)]',
              ].join(' ')}
              style={studyPanelFrameStyle}
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
              {theaterOpen && <AvatarResizeGrip theater onPointerDown={handleResizePointerDown} />}
              <div
                data-testid="avatar-overlay-controls"
                data-avatar-controls="true"
                data-controls-visible={controlsVisible || dragging || resizing ? 'true' : 'false'}
                className={`absolute right-2 top-2 ${controlsVisibilityClassName(controlsVisible || dragging || resizing)}`}
                style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.avatarControls }}
                onPointerDown={(event) => event.stopPropagation()}
              >
                <AvatarControls
                  visible
                  compact
                  theater={theaterButtonActive}
                  inTheater={theaterOpen}
                  interactive={controlsVisible || dragging || resizing}
                  onHide={handleHide}
                  onReset={handleReset}
                  onTheaterToggle={handleTheaterToggle}
                  onTheaterReset={handleTheaterReset}
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
      data-avatar-video-control-safe-bottom={videoControlSafeBottomCss()}
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
          data-avatar-video-control-safe-bottom={videoControlSafeBottomCss()}
          className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-center"
          style={{ bottom: videoControlSafeBottomCss(), zIndex: AVATAR_OVERLAY_Z_INDEX.avatarTheater }}
        >
          <div
            ref={frameRef}
            data-avatar-theater-frame="true"
            tabIndex={0}
            role="group"
            aria-label="Avatar overlay"
            className={theaterFrameClass}
            style={theaterPlacementStyle(theaterScale)}
            onPointerEnter={handleFramePointerEnter}
            onPointerLeave={handleFramePointerLeave}
            onPointerDown={handleFramePointerDown}
            onFocus={handleFrameFocus}
            onBlur={handleFrameBlur}
          >
            <div
              data-avatar-theater-foreground-frame="true"
              className={theaterForegroundFrameClass}
            >
              {renderAvatarVideo({ theater: true })}
            </div>
            <div
              aria-hidden="true"
              data-avatar-body-surface="true"
              className="absolute inset-0 pointer-events-auto"
              onPointerEnter={handleFramePointerEnter}
              onPointerLeave={handleFramePointerLeave}
            />
            <AvatarResizeGrip theater onPointerDown={handleResizePointerDown} />
            <div
              data-testid="avatar-overlay-controls"
              data-avatar-controls="true"
              data-controls-visible={controlsVisible || dragging || resizing ? 'true' : 'false'}
              className={`absolute right-3 top-3 ${controlsVisibilityClassName(controlsVisible || dragging || resizing)}`}
              style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.avatarControls }}
              onPointerDown={(event) => event.stopPropagation()}
            >
              <AvatarControls
                visible
                compact
                theater
                inTheater
                interactive={controlsVisible || dragging || resizing}
                onHide={handleHide}
                onReset={handleReset}
                onTheaterToggle={handleTheaterToggle}
                onTheaterReset={handleTheaterReset}
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
          className={[
            'pointer-events-none absolute aspect-video touch-none rounded-lg border border-black/30 bg-black shadow-xl',
            dragging ? 'cursor-grabbing' : 'cursor-grab',
          ].join(' ')}
          data-avatar-player-control-pass-through="true"
          data-avatar-video-control-safe-bottom={videoControlSafeBottomCss()}
          style={floatingPlacementStyle(currentPlacement)}
          onPointerEnter={handleFramePointerEnter}
          onPointerLeave={handleFramePointerLeave}
          onPointerDown={handleFramePointerDown}
          onFocus={handleFrameFocus}
          onBlur={handleFrameBlur}
        >
          {renderAvatarVideo()}
          <div
            aria-hidden="true"
            data-avatar-body-surface="true"
            className="absolute inset-0 pointer-events-auto"
            onPointerEnter={handleFramePointerEnter}
            onPointerLeave={handleFramePointerLeave}
          />
          <AvatarResizeGrip onPointerDown={handleResizePointerDown} />
          <div
            data-testid="avatar-overlay-controls"
            data-avatar-controls="true"
            data-controls-visible={controlsVisible || dragging || resizing ? 'true' : 'false'}
            className={`absolute right-1 top-1 ${controlsVisibilityClassName(controlsVisible || dragging || resizing)}`}
            style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.avatarControls }}
            onPointerDown={(event) => event.stopPropagation()}
          >
            <AvatarControls
              visible
              theater={theaterButtonActive}
              inTheater={false}
              interactive={controlsVisible || dragging || resizing}
              onHide={handleHide}
              onReset={handleReset}
              onTheaterToggle={handleTheaterToggle}
            />
          </div>
        </div>
      )}
    </div>
  );
}
