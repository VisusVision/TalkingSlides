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
});
