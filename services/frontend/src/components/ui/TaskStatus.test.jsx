import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TaskStatus from './TaskStatus';

describe('TaskStatus', () => {
  let host;
  let root;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
  });

  it('renders determinate progress with accessible source-of-truth values', async () => {
    await act(async () => {
      root.render(
        <TaskStatus
          state="processing"
          title="Rendering lesson"
          description="Generating your video."
          progress={42}
          stage="running"
        />,
      );
    });

    const status = host.querySelector('[data-state="processing"]');
    const progressbar = host.querySelector('[role="progressbar"]');

    expect(status).not.toBeNull();
    expect(status).toHaveAttribute('aria-live', 'polite');
    expect(host.textContent).toContain('Rendering lesson');
    expect(host.textContent).toContain('42%');
    expect(progressbar).toHaveAttribute('aria-valuemin', '0');
    expect(progressbar).toHaveAttribute('aria-valuemax', '100');
    expect(progressbar).toHaveAttribute('aria-valuenow', '42');
    expect(progressbar.querySelector('span').style.transform).toBe('scaleX(0.42)');
  });

  it('does not show fake numeric progress for indeterminate active states', async () => {
    await act(async () => {
      root.render(
        <TaskStatus
          state="uploading"
          title="Creating lesson draft"
          description="Uploading source material."
        />,
      );
    });

    expect(host.querySelector('[role="progressbar"]')).toBeNull();
    expect(host.querySelector('.motion-task-progress')).not.toBeNull();
    expect(host.textContent).not.toContain('%');
  });

  it('caps non-completed progress below 100 until completion is confirmed', async () => {
    await act(async () => {
      root.render(<TaskStatus state="processing" title="Rendering lesson" progress={100} />);
    });

    expect(host.textContent).toContain('99%');
    expect(host.querySelector('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '99');

    await act(async () => {
      root.render(<TaskStatus state="completed" title="Render complete" progress={100} />);
    });

    expect(host.textContent).toContain('100%');
    expect(host.querySelector('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '100');
  });

  it('renders actions without changing callbacks', async () => {
    const onRetry = vi.fn();
    await act(async () => {
      root.render(
        <TaskStatus
          state="failed"
          title="Render failed"
          description="The backend reported a failure."
          action={<button type="button" onClick={onRetry}>Retry</button>}
        />,
      );
    });

    const button = host.querySelector('button');
    await act(async () => button.click());

    expect(host.querySelector('[data-state="failed"]').className).toContain('motion-task-failed');
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
