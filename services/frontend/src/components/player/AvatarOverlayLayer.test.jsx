import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AvatarOverlayLayer from './AvatarOverlayLayer';

const placement = {
  position: 'custom',
  x: 0.7,
  y: 0.1,
  width: 0.24,
};
const SAFE_CONTAINER_BOTTOM = 544;
const SAFE_MAX_WIDTH_PERCENT = 96.71;

function pointerEvent(type, options = {}) {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    clientX: options.clientX ?? 0,
    clientY: options.clientY ?? 0,
    button: options.button ?? 0,
  });
  Object.defineProperty(event, 'pointerId', { value: options.pointerId ?? 1 });
  Object.defineProperty(event, 'pointerType', { value: options.pointerType || 'mouse' });
  return event;
}

function renderOverlay(props = {}) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => {
    root.render(
      <AvatarOverlayLayer
        lessonId="avatar-ux-test"
        src="/avatar-track.mp4"
        enabled
        placement={placement}
        videoRef={{ current: null }}
        {...props}
      />,
    );
  });
  const layer = host.querySelector('[data-testid="avatar-overlay-layer"]') || host.querySelector('[data-testid="avatar-study-panel-layer"]');
  const frame = host.querySelector('[role="group"][aria-label="Avatar overlay"]');
  const resizeGrip = host.querySelector('[data-testid="avatar-resize-grip"]');

  Object.defineProperty(layer, 'getBoundingClientRect', {
    configurable: true,
    value: () => ({
      left: 0,
      top: 0,
      right: 1000,
      bottom: 600,
      width: 1000,
      height: 600,
    }),
  });

  function mockFrameRect(target = host.querySelector('[role="group"][aria-label="Avatar overlay"]'), rect = null) {
    Object.defineProperty(target, 'getBoundingClientRect', {
      configurable: true,
      value: () => {
        if (rect) return rect;
        const width = (Number.parseFloat(target.style.width) || 24) * 10;
        const height = width * 9 / 16;
        const left = (Number.parseFloat(target.style.left) || 0) * 10;
        const top = (Number.parseFloat(target.style.top) || 0) * 6;
        return {
          left,
          top,
          right: left + width,
          bottom: top + height,
          width,
          height,
        };
      },
    });
    target.setPointerCapture = vi.fn();
    target.releasePointerCapture = vi.fn();
    return target;
  }

  mockFrameRect(frame);
  function mockResizeGripRect(
    target = host.querySelector('[data-testid="avatar-resize-grip"]'),
    frameTarget = host.querySelector('[role="group"][aria-label="Avatar overlay"]'),
  ) {
    if (!target || !frameTarget) return target;
    Object.defineProperty(target, 'getBoundingClientRect', {
      configurable: true,
      value: () => {
        const frameRect = frameTarget.getBoundingClientRect();
        return {
          left: frameRect.left,
          top: frameRect.bottom - 36,
          right: frameRect.left + 36,
          bottom: frameRect.bottom,
          width: 36,
          height: 36,
        };
      },
    });
    target.setPointerCapture = vi.fn();
    target.releasePointerCapture = vi.fn();
    return target;
  }

  mockResizeGripRect(resizeGrip, frame);

  return { host, root, layer, frame, resizeGrip, mockFrameRect, mockResizeGripRect };
}

function percentValue(value) {
  return Number.parseFloat(String(value || '0'));
}

function expectFrameAboveSafeBottom(frame) {
  expect(frame.getBoundingClientRect().bottom).toBeLessThanOrEqual(SAFE_CONTAINER_BOTTOM + 0.1);
}

async function dispatch(target, event) {
  await act(async () => {
    target.dispatchEvent(event);
  });
}

describe('AvatarOverlayLayer direct manipulation UX', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    vi.restoreAllMocks();
    vi.spyOn(window.HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    vi.spyOn(window.HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('drags the floating avatar from the body and not from controls', async () => {
    const { host, root, frame, resizeGrip } = renderOverlay();

    expect(host.querySelector('[data-testid="avatar-drag-handle"]')).toBeNull();
    expect(host.textContent).not.toContain('Make avatar smaller');
    expect(host.textContent).not.toContain('Make avatar larger');
    expect(host.querySelector('[data-testid="avatar-resize-grip"]')).toBeTruthy();
    const resizeIcon = host.querySelector('[data-avatar-resize-direction="top-right-bottom-left"]');
    expect(resizeIcon).toBeTruthy();
    expect(resizeIcon.querySelector('line[x1="19"][y1="5"][x2="5"][y2="19"]')).toBeTruthy();
    expect(host.querySelector('[data-avatar-controls-layout="row"]')).toBeTruthy();
    expect(frame.getAttribute('data-avatar-player-control-pass-through')).toBe('true');
    const bodySurface = host.querySelector('[data-avatar-body-surface="true"]');
    expect(bodySurface).toBeTruthy();
    expect(frame.getAttribute('data-avatar-video-control-safe-bottom')).toBe('56px');
    expect(bodySurface.style.bottom).toBe('');
    expect(bodySurface.className).toContain('inset-0');
    expect(resizeGrip.getAttribute('data-avatar-resize-bottom-offset')).toBeNull();
    expect(resizeGrip.className).toContain('bottom-0');
    expect(resizeGrip.className).toContain('left-0');

    await dispatch(frame, pointerEvent('pointerdown', { clientX: 750, clientY: 90 }));
    expect(frame.setPointerCapture).toHaveBeenCalledWith(1);
    expect(resizeGrip.setPointerCapture).not.toHaveBeenCalled();
    await dispatch(window, pointerEvent('pointermove', { clientX: 780, clientY: 120 }));
    await dispatch(window, pointerEvent('pointerup', { clientX: 780, clientY: 120 }));

    expect(percentValue(frame.style.left)).toBe(73);
    expect(percentValue(frame.style.top)).toBe(15);
    expectFrameAboveSafeBottom(frame);

    const resetButton = host.querySelector('button[aria-label="Reset avatar position"]');
    await dispatch(resetButton.querySelector('svg') || resetButton, pointerEvent('pointerdown', { clientX: 10, clientY: 10 }));
    await dispatch(window, pointerEvent('pointermove', { clientX: 500, clientY: 300 }));
    await dispatch(window, pointerEvent('pointerup', { clientX: 500, clientY: 300 }));

    expect(percentValue(frame.style.left)).toBe(73);
    expect(percentValue(frame.style.top)).toBe(15);

    await act(async () => resetButton.click());
    expect(percentValue(frame.style.left)).toBe(70);
    expect(percentValue(frame.style.top)).toBe(10);
    expectFrameAboveSafeBottom(frame);

    await act(async () => root.unmount());
    host.remove();
  });

  it('can grow again after being resized to the manual minimum', async () => {
    const { host, root, frame, resizeGrip } = renderOverlay();

    await dispatch(resizeGrip, pointerEvent('pointerdown', { clientX: 700, clientY: 195 }));
    await dispatch(window, pointerEvent('pointermove', { clientX: 900, clientY: 70 }));
    await dispatch(window, pointerEvent('pointerup', { clientX: 900, clientY: 70 }));
    expect(percentValue(frame.style.width)).toBe(20);

    await dispatch(resizeGrip, pointerEvent('pointerdown', { clientX: 820, clientY: 138 }));
    await dispatch(window, pointerEvent('pointermove', { clientX: 700, clientY: 210 }));
    await dispatch(window, pointerEvent('pointerup', { clientX: 700, clientY: 210 }));

    expect(percentValue(frame.style.width)).toBeGreaterThan(20);
    expect(percentValue(frame.style.width)).toBeLessThanOrEqual(98);

    await dispatch(resizeGrip, pointerEvent('pointerdown', { clientX: 700, clientY: 195 }));
    await dispatch(window, pointerEvent('pointermove', { clientX: -120, clientY: 520 }));
    await dispatch(window, pointerEvent('pointerup', { clientX: -120, clientY: 520 }));

    expect(percentValue(frame.style.width)).toBeGreaterThan(90);
    expectFrameAboveSafeBottom(frame);

    await act(async () => root.unmount());
    host.remove();
  });

  it('exits theater into manual floating resize and can shrink below the large preset', async () => {
    const { host, root, layer, mockFrameRect } = renderOverlay();

    const theaterButton = host.querySelector('button[aria-label="Open avatar theater"]');
    await act(async () => theaterButton.click());
    expect(host.querySelector('[data-testid="avatar-theater-overlay"]')).toBeTruthy();

    const theaterFrame = host.querySelector('[data-avatar-theater-frame="true"]');
    mockFrameRect(theaterFrame, {
      left: 100,
      top: 60,
      right: 900,
      bottom: 510,
      width: 800,
      height: 450,
    });
    Object.defineProperty(theaterFrame.querySelector('[data-testid="avatar-resize-grip"]'), 'getBoundingClientRect', {
      configurable: true,
      value: () => ({
        left: 100,
        top: 474,
        right: 136,
        bottom: 510,
        width: 36,
        height: 36,
      }),
    });

    const theaterGrip = theaterFrame.querySelector('[data-testid="avatar-resize-grip"]');
    theaterGrip.setPointerCapture = vi.fn();
    theaterGrip.releasePointerCapture = vi.fn();
    await dispatch(theaterGrip, pointerEvent('pointerdown', { clientX: 118, clientY: 492 }));

    expect(host.querySelector('[data-testid="avatar-theater-overlay"]')).toBeNull();
    expect(theaterGrip.setPointerCapture).toHaveBeenCalledWith(1);
    const capturedFrame = host.querySelector('[role="group"][aria-label="Avatar overlay"]');
    expect(percentValue(capturedFrame.style.width)).toBe(80);
    expect(percentValue(capturedFrame.style.left)).toBe(10);

    await dispatch(window, pointerEvent('pointermove', { clientX: 198, clientY: 530 }));
    await dispatch(window, pointerEvent('pointerup', { clientX: 198, clientY: 530 }));

    const floatingFrame = host.querySelector('[role="group"][aria-label="Avatar overlay"]');
    mockFrameRect(floatingFrame);
    expect(percentValue(floatingFrame.style.width)).toBeGreaterThan(35);
    expect(percentValue(floatingFrame.style.width)).toBeLessThan(80);
    expect(percentValue(floatingFrame.style.left) + percentValue(floatingFrame.style.width)).toBeLessThanOrEqual(100);
    expect(layer.querySelector('[data-testid="avatar-resize-grip"]')).toBeTruthy();
    expect(window.localStorage.getItem('visus-avatar-overlay:avatar-ux-test:theater-scale')).toBeNull();

    await act(async () => root.unmount());
    host.remove();
  });

  it('derives the theater button state from current manual size', async () => {
    const { host, root, frame, resizeGrip, mockFrameRect, mockResizeGripRect } = renderOverlay();

    await dispatch(resizeGrip, pointerEvent('pointerdown', { clientX: 700, clientY: 195 }));
    await dispatch(window, pointerEvent('pointermove', { clientX: -120, clientY: 520 }));
    await dispatch(window, pointerEvent('pointerup', { clientX: -120, clientY: 520 }));
    expect(percentValue(frame.style.width)).toBeCloseTo(SAFE_MAX_WIDTH_PERCENT, 1);

    const shrinkButton = host.querySelector('button[aria-label="Shrink avatar"]');
    expect(shrinkButton).toBeTruthy();
    expect(shrinkButton.getAttribute('aria-pressed')).toBe('true');
    expect(shrinkButton.getAttribute('data-avatar-theater-active')).toBe('true');
    const bodySurface = host.querySelector('[data-avatar-body-surface="true"]');
    expect(bodySurface.getAttribute('data-avatar-player-control-pass-through-bottom')).toBeNull();
    expect(bodySurface.style.bottom).toBe('');
    expectFrameAboveSafeBottom(frame);
    const largeResizeGrip = host.querySelector('[data-testid="avatar-resize-grip"]');
    expect(largeResizeGrip.getAttribute('data-avatar-resize-bottom-offset')).toBeNull();
    expect(largeResizeGrip.className).toContain('bottom-0');
    expect(largeResizeGrip.className).toContain('left-0');
    expect(host.querySelector('[data-testid="avatar-theater-overlay"]')).toBeNull();

    await act(async () => shrinkButton.click());

    const floatingFrame = host.querySelector('[role="group"][aria-label="Avatar overlay"]');
    const floatingGrip = host.querySelector('[data-testid="avatar-resize-grip"]');
    mockFrameRect(floatingFrame);
    mockResizeGripRect(floatingGrip, floatingFrame);
    expect(percentValue(floatingFrame.style.width)).toBe(24);
    expect(percentValue(floatingFrame.style.left)).toBe(70);
    const theaterButton = host.querySelector('button[aria-label="Open avatar theater"]');
    expect(theaterButton).toBeTruthy();
    expect(theaterButton.getAttribute('aria-pressed')).toBe('false');

    await act(async () => theaterButton.click());

    const theaterFrame = host.querySelector('[data-avatar-theater-frame="true"]');
    expect(theaterFrame).toBeTruthy();
    expect(host.querySelector('[data-testid="avatar-overlay-video"][data-avatar-video-mode="theater"]')).toBeTruthy();
    expect(host.querySelector('button[aria-label="Exit avatar theater"]')).toBeTruthy();
    expect(window.localStorage.getItem('visus-avatar-overlay:avatar-ux-test:theater-scale')).toBeNull();

    await act(async () => root.unmount());
    host.remove();
  });

  it('resizes from the bottom-left grip with existing min and max bounds', async () => {
    const { host, root, frame, resizeGrip } = renderOverlay();

    await dispatch(resizeGrip, pointerEvent('pointerdown', { clientX: 700, clientY: 195 }));
    expect(resizeGrip.setPointerCapture).toHaveBeenCalledWith(1);
    expect(frame.setPointerCapture).not.toHaveBeenCalled();
    await dispatch(window, pointerEvent('pointermove', { clientX: -120, clientY: 520 }));
    await dispatch(window, pointerEvent('pointerup', { clientX: -120, clientY: 520 }));

    expect(percentValue(frame.style.width)).toBeCloseTo(SAFE_MAX_WIDTH_PERCENT, 1);
    expect(percentValue(frame.style.left)).toBe(0);
    expect(percentValue(frame.style.top)).toBeGreaterThanOrEqual(0);
    expectFrameAboveSafeBottom(frame);

    const resetButton = host.querySelector('button[aria-label="Reset avatar position"]');
    await act(async () => resetButton.click());
    expect(percentValue(frame.style.width)).toBe(24);
    expect(percentValue(frame.style.left)).toBe(70);
    expectFrameAboveSafeBottom(frame);

    await dispatch(resizeGrip, pointerEvent('pointerdown', { clientX: 700, clientY: 195 }));
    await dispatch(window, pointerEvent('pointermove', { clientX: 900, clientY: 70 }));
    await dispatch(window, pointerEvent('pointerup', { clientX: 900, clientY: 70 }));

    expect(percentValue(frame.style.width)).toBe(20);
    expectFrameAboveSafeBottom(frame);

    await act(async () => root.unmount());
    host.remove();
  });

  it('keeps small and restored avatars above the video control safe strip', async () => {
    const { host, root, frame } = renderOverlay();

    await dispatch(frame, pointerEvent('pointerdown', { clientX: 750, clientY: 90 }));
    await dispatch(window, pointerEvent('pointermove', { clientX: 760, clientY: 690 }));
    await dispatch(window, pointerEvent('pointerup', { clientX: 760, clientY: 690 }));

    expectFrameAboveSafeBottom(frame);
    expect(percentValue(frame.style.top)).toBeCloseTo(68.15, 1);

    await act(async () => root.unmount());
    host.remove();

    window.localStorage.setItem('visus-avatar-overlay:avatar-ux-test:position', JSON.stringify({
      position: 'custom',
      x: 0.7,
      y: 0.92,
      width: 0.24,
    }));
    const restored = renderOverlay();

    await act(async () => {
      window.dispatchEvent(new Event('resize'));
    });

    expectFrameAboveSafeBottom(restored.frame);
    expect(percentValue(restored.frame.style.top)).toBeCloseTo(68.15, 1);

    await act(async () => restored.root.unmount());
    restored.host.remove();
  });

  it('keeps hide, show, theater toggle, and touch pointer paths working', async () => {
    const { host, root, frame, resizeGrip } = renderOverlay();

    await dispatch(frame, pointerEvent('pointerdown', { pointerType: 'touch', clientX: 750, clientY: 90 }));
    await dispatch(window, pointerEvent('pointermove', { pointerType: 'touch', clientX: 760, clientY: 110 }));
    await dispatch(window, pointerEvent('pointerup', { pointerType: 'touch', clientX: 760, clientY: 110 }));
    expect(percentValue(frame.style.left)).toBe(71);

    await dispatch(resizeGrip, pointerEvent('pointerdown', { pointerType: 'touch', clientX: 710, clientY: 195 }));
    await dispatch(window, pointerEvent('pointermove', { pointerType: 'touch', clientX: 650, clientY: 225 }));
    await dispatch(window, pointerEvent('pointerup', { pointerType: 'touch', clientX: 650, clientY: 225 }));
    expect(Number.parseFloat(frame.style.width)).toBeGreaterThan(24);

    const theaterButton = host.querySelector('button[aria-label="Open avatar theater"]');
    const beforeTheaterLeft = percentValue(frame.style.left);
    const beforeTheaterTop = percentValue(frame.style.top);
    await dispatch(theaterButton.querySelector('svg') || theaterButton, pointerEvent('pointerdown', { clientX: 20, clientY: 20 }));
    await dispatch(window, pointerEvent('pointermove', { clientX: 500, clientY: 300 }));
    await dispatch(window, pointerEvent('pointerup', { clientX: 500, clientY: 300 }));
    expect(percentValue(frame.style.left)).toBe(beforeTheaterLeft);
    expect(percentValue(frame.style.top)).toBe(beforeTheaterTop);
    expect(host.querySelector('[data-testid="avatar-theater-overlay"]')).toBeNull();

    await act(async () => theaterButton.click());
    expect(host.querySelector('[data-testid="avatar-theater-overlay"]')).toBeTruthy();
    expect(host.querySelector('[aria-label="Exit avatar theater"]')).toBeTruthy();

    const exitButton = host.querySelector('button[aria-label="Exit avatar theater"]');
    await act(async () => exitButton.click());
    expect(host.querySelector('[data-testid="avatar-theater-overlay"]')).toBeNull();

    const hideButton = host.querySelector('button[aria-label="Hide avatar"]');
    await dispatch(hideButton.querySelector('svg') || hideButton, pointerEvent('pointerdown', { clientX: 20, clientY: 20 }));
    await dispatch(window, pointerEvent('pointermove', { clientX: 500, clientY: 300 }));
    await dispatch(window, pointerEvent('pointerup', { clientX: 500, clientY: 300 }));
    expect(host.querySelector('[aria-label="Show avatar"]')).toBeNull();

    await act(async () => hideButton.click());
    expect(host.querySelector('[aria-label="Show avatar"]')).toBeTruthy();

    const showButton = host.querySelector('button[aria-label="Show avatar"]');
    await act(async () => showButton.click());
    expect(host.querySelector('[aria-label="Hide avatar"]')).toBeTruthy();

    await act(async () => root.unmount());
    host.remove();
  });
});
