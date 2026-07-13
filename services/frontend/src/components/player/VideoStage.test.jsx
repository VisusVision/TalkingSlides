import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import VideoStage from './VideoStage';

function renderStage(props = {}) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);

  act(() => {
    root.render(
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
      />,
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
    expect(shell.className).toContain('motion-watch-player');
    expect(prompt).toBeTruthy();
    expect(prompt.className).toContain('motion-watch-status');
    expect(prompt.firstElementChild.className).toContain('motion-watch-complete');
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

  it('surfaces real buffering state without changing media callbacks', async () => {
    const onPlaybackTimeChange = vi.fn();
    const onPlaybackStarted = vi.fn();
    const onPlaybackStopped = vi.fn();
    const { host, root } = renderStage({
      onPlaybackTimeChange,
      onPlaybackStarted,
      onPlaybackStopped,
    });
    const video = host.querySelector('video');

    expect(host.querySelector('[data-testid="player-buffering-status"]')).toBeNull();

    await act(async () => {
      video.dispatchEvent(new Event('waiting', { bubbles: true }));
    });

    const status = host.querySelector('[data-testid="player-buffering-status"]');
    expect(status).toBeTruthy();
    expect(status.getAttribute('role')).toBe('status');
    expect(status.className).toContain('motion-watch-status');
    expect(status.textContent).toContain('Buffering');

    await act(async () => {
      video.dispatchEvent(new Event('playing', { bubbles: true }));
    });

    expect(host.querySelector('[data-testid="player-buffering-status"]')).toBeNull();
    expect(onPlaybackStarted).not.toHaveBeenCalled();
    expect(onPlaybackStopped).not.toHaveBeenCalled();
    expect(onPlaybackTimeChange).not.toHaveBeenCalled();

    await act(async () => root.unmount());
    host.remove();
  });
});
