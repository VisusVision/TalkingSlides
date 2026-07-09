import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LocaleProvider } from '../../i18n/LocaleProvider';
import VideoStage from './VideoStage';

function renderStage(props = {}) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);

  act(() => {
    root.render(
      <LocaleProvider>
        <VideoStage
          lesson={{
            id: 101,
            title: 'Current lesson',
            stream_url: '/media/current.mp4',
          }}
          asSurface={false}
          showLessonDetails={false}
          showSubtitleControls={false}
          {...props}
        />
      </LocaleProvider>,
    );
  });

  return { host, root };
}

describe('VideoStage continue-next prompt', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('renders the prompt inside the fullscreen shell above player overlays', async () => {
    const onContinue = vi.fn();
    const onCancel = vi.fn();
    const { host, root } = renderStage({
      continueNextPrompt: {
        lesson: { id: 102, title: 'Next lesson' },
        secondsRemaining: 3,
      },
      onContinueNext: onContinue,
      onCancelContinueNext: onCancel,
    });

    const shell = host.querySelector('[data-testid="player-fullscreen-shell"]');
    const prompt = host.querySelector('[data-testid="watch-autoplay-next"]');

    expect(shell).toBeTruthy();
    expect(prompt).toBeTruthy();
    expect(shell.contains(prompt)).toBe(true);
    expect(prompt.textContent).toContain('Next: Next lesson');
    expect(prompt.textContent).toContain('Continuing in 3 seconds');
    expect(Number(prompt.style.zIndex)).toBeGreaterThan(60);

    const continueButton = [...prompt.querySelectorAll('button')]
      .find((button) => button.textContent.includes('Continue now'));
    const stayButton = [...prompt.querySelectorAll('button')]
      .find((button) => button.textContent.includes('Stay here'));

    await act(async () => {
      continueButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      stayButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(onContinue).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(1);

    await act(async () => root.unmount());
    host.remove();
  });

  it('starts playback from the explicit overlay and hides it after playing', async () => {
    const onPlaybackStarted = vi.fn();
    const { host, root } = renderStage({ onPlaybackStarted });
    const video = host.querySelector('video');
    const play = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(video, 'play', { configurable: true, value: play });

    const overlay = host.querySelector('[data-testid="playback-start-overlay"]');
    const playButton = overlay.querySelector('button[aria-label="Play lesson"]');
    expect(playButton).toBeVisible();

    await act(async () => {
      playButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });
    expect(play).toHaveBeenCalledTimes(1);

    await act(async () => {
      video.dispatchEvent(new Event('play', { bubbles: true }));
      video.dispatchEvent(new Event('playing', { bubbles: true }));
    });
    expect(onPlaybackStarted).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[data-testid="playback-start-overlay"]')).toBeNull();

    await act(async () => root.unmount());
    host.remove();
  });

  it('shows a retry path when a browser rejects play()', async () => {
    const { host, root } = renderStage();
    const video = host.querySelector('video');
    Object.defineProperty(video, 'play', {
      configurable: true,
      value: vi.fn().mockRejectedValue(new DOMException('Unsupported', 'NotSupportedError')),
    });

    await act(async () => {
      host.querySelector('button[aria-label="Play lesson"]')
        .dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });

    expect(host.querySelector('button[aria-label="Retry playback"]')).toBeVisible();
    expect(host.querySelector('[role="alert"]')).toHaveTextContent('Playback was blocked');

    await act(async () => root.unmount());
    host.remove();
  });

  it('uses a muted fallback when an embedded browser blocks audible playback', async () => {
    const { host, root } = renderStage();
    const video = host.querySelector('video');
    const play = vi.fn()
      .mockRejectedValueOnce(new DOMException('Blocked', 'NotAllowedError'))
      .mockResolvedValueOnce(undefined);
    Object.defineProperty(video, 'play', { configurable: true, value: play });

    await act(async () => {
      host.querySelector('button[aria-label="Play lesson"]')
        .dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(play).toHaveBeenCalledTimes(2);
    expect(video.muted).toBe(true);
    expect(host.querySelector('[data-testid="playback-start-overlay"]')).toBeNull();

    await act(async () => root.unmount());
    host.remove();
  });
});
