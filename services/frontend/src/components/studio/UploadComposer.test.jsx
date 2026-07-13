import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import UploadComposer from './UploadComposer';

describe('UploadComposer', () => {
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

  it('renders indeterminate upload feedback without fake percentages', async () => {
    await act(async () => {
      root.render(
        <UploadComposer
          categories={[]}
          submitting
          submitError=""
          onSubmit={vi.fn()}
        />,
      );
    });

    const status = host.querySelector('[data-testid="upload-task-status"]');
    expect(status).not.toBeNull();
    expect(status).toHaveAttribute('data-state', 'uploading');
    expect(host.querySelector('[role="progressbar"]')).toBeNull();
    expect(host.querySelector('.motion-task-progress')).not.toBeNull();
    expect(status.textContent).not.toContain('%');
  });

  it('keeps the existing submit payload contract', async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    await act(async () => {
      root.render(
        <UploadComposer
          categories={[{ name: 'Design' }]}
          submitting={false}
          submitError=""
          onSubmit={onSubmit}
        />,
      );
    });

    const sourceFile = new File(['lesson'], 'lesson.txt', { type: 'text/plain' });
    const input = host.querySelector('input[type="file"][accept=".pptx,.pdf,.docx,.txt"]');
    Object.defineProperty(input, 'files', {
      configurable: true,
      value: [sourceFile],
    });

    await act(async () => {
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => {
      host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      file: sourceFile,
      coverFile: null,
      title: '',
      category: '',
      pauseSec: '0.2',
      whiteboardModeAll: false,
      avatarEnabled: false,
    }));
  });
});
