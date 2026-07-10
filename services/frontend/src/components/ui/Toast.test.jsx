import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider, toast } from './Toast';

let host;
let root;

async function render(element) {
  await act(async () => {
    root.render(element);
  });
}

function toasts() {
  return Array.from(document.body.querySelectorAll('[data-toast-variant]'));
}

describe('ToastProvider', () => {
  beforeEach(async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers();
    toast.clear();
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    await render(
      <ToastProvider>
        <main>App content</main>
      </ToastProvider>,
    );
  });

  afterEach(async () => {
    await act(async () => {
      toast.clear();
      vi.runOnlyPendingTimers();
      root.unmount();
    });
    host.remove();
    vi.useRealTimers();
  });

  it('renders success, error, warning, and info variants with accessible live regions', async () => {
    await act(async () => {
      toast.success('Lesson published');
      toast.error('Upload failed');
      toast.warning('Unsaved changes');
      toast.info('Rendering started');
    });

    expect(toasts()).toHaveLength(4);
    expect(document.body.querySelector('[data-toast-variant="success"]')).toHaveAttribute('role', 'status');
    expect(document.body.querySelector('[data-toast-variant="success"]')).toHaveAttribute('aria-live', 'polite');
    expect(document.body.querySelector('[data-toast-variant="error"]')).toHaveAttribute('role', 'alert');
    expect(document.body.querySelector('[data-toast-variant="error"]')).toHaveAttribute('aria-live', 'assertive');
    expect(document.body.querySelector('[data-toast-variant="warning"]')).toHaveTextContent('Unsaved changes');
    expect(document.body.querySelector('[data-toast-variant="info"]')).toHaveTextContent('Rendering started');
  });

  it('auto dismisses finite-duration toasts', async () => {
    await act(async () => {
      toast.info('Short toast', { duration: 1000 });
    });

    expect(document.body).toHaveTextContent('Short toast');

    await act(async () => {
      vi.advanceTimersByTime(999);
    });
    expect(document.body).toHaveTextContent('Short toast');

    await act(async () => {
      vi.advanceTimersByTime(302);
    });
    expect(document.body).not.toHaveTextContent('Short toast');
  });

  it('supports manual dismiss from the close button', async () => {
    await act(async () => {
      toast.success('Saved settings', { duration: Infinity });
    });

    await act(async () => {
      document.body.querySelector('button[aria-label="Dismiss success notification"]').click();
      vi.advanceTimersByTime(300);
    });

    expect(document.body).not.toHaveTextContent('Saved settings');
  });

  it('supports keyboard dismiss without moving focus automatically', async () => {
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.textContent = 'Trigger';
    document.body.appendChild(trigger);
    trigger.focus();

    await act(async () => {
      toast.warning('Keyboard dismiss', { duration: Infinity });
    });

    expect(document.activeElement).toBe(trigger);
    const toastItem = document.body.querySelector('[data-toast-variant="warning"]');
    toastItem.focus();
    await act(async () => {
      toastItem.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      vi.advanceTimersByTime(300);
    });

    expect(document.body).not.toHaveTextContent('Keyboard dismiss');
    trigger.remove();
  });

  it('stacks newest notifications and caps the visible count', async () => {
    await render(
      <ToastProvider maxVisible={2}>
        <main>App content</main>
      </ToastProvider>,
    );

    await act(async () => {
      toast.info('First', { duration: Infinity });
      toast.info('Second', { duration: Infinity });
      toast.info('Third', { duration: Infinity });
    });

    expect(toasts()).toHaveLength(2);
    expect(toasts()[0]).toHaveTextContent('Third');
    expect(toasts()[1]).toHaveTextContent('Second');
    expect(document.body).not.toHaveTextContent('First');
  });

  it('merges provider and toast class names and renders progress feedback', async () => {
    await render(
      <ToastProvider className="custom-viewport">
        <main>App content</main>
      </ToastProvider>,
    );

    await act(async () => {
      toast.loading('Uploading media', {
        className: 'custom-toast',
        duration: Infinity,
        progress: 40,
      });
    });

    expect(document.body.querySelector('.visus-toast-viewport').className).toContain('custom-viewport');
    expect(document.body.querySelector('[data-toast-variant="loading"]').className).toContain('custom-toast');
    expect(document.body.querySelector('[data-toast-variant="loading"] [style*="40%"]')).toBeInTheDocument();
  });
});
