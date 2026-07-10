import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Dialog from './Dialog';

let host;
let root;

async function flushFocus() {
  await act(async () => {
    await new Promise((resolve) => {
      window.setTimeout(resolve, 0);
    });
  });
}

async function render(element) {
  await act(async () => {
    root.render(element);
  });
  await flushFocus();
}

function dialog() {
  return document.body.querySelector('[role="dialog"]');
}

function overlay() {
  return document.body.querySelector('[data-dialog-overlay]');
}

describe('Dialog', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    document.body.style.overflow = '';
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    host.remove();
    document.body.style.overflow = '';
  });

  it('renders nothing while closed', async () => {
    await render(
      <Dialog open={false} onClose={vi.fn()} titleId="closed-title">
        <Dialog.Title id="closed-title">Closed</Dialog.Title>
      </Dialog>,
    );

    expect(dialog()).toBeNull();
  });

  it('renders a portal dialog with ARIA title and description associations', async () => {
    await render(
      <Dialog open onClose={vi.fn()} titleId="dialog-title" descriptionId="dialog-description">
        <Dialog.Header>
          <Dialog.Title>Dialog title</Dialog.Title>
          <Dialog.Close onClose={vi.fn()} />
        </Dialog.Header>
        <Dialog.Body>
          <Dialog.Description>Helpful description</Dialog.Description>
          <button type="button">Focusable action</button>
        </Dialog.Body>
      </Dialog>,
    );

    const panel = dialog();
    expect(panel).toBeInTheDocument();
    expect(panel).not.toBe(host.firstChild);
    expect(panel).toHaveAttribute('aria-modal', 'true');
    expect(panel).toHaveAttribute('aria-labelledby', 'dialog-title');
    expect(panel).toHaveAttribute('aria-describedby', 'dialog-description');
    expect(document.getElementById('dialog-title')).toHaveTextContent('Dialog title');
    expect(document.getElementById('dialog-description')).toHaveTextContent('Helpful description');
  });

  it('supports close button, Escape, backdrop, and inside click behavior', async () => {
    const onClose = vi.fn();

    await render(
      <Dialog open onClose={onClose} titleId="close-title">
        <Dialog.Header>
          <Dialog.Title>Close behavior</Dialog.Title>
          <Dialog.Close onClose={onClose} closeLabel="Close test dialog" />
        </Dialog.Header>
        <Dialog.Body>
          <button type="button">Inside</button>
        </Dialog.Body>
      </Dialog>,
    );

    await act(async () => {
      dialog().dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    });
    expect(onClose).not.toHaveBeenCalled();

    await act(async () => {
      overlay().dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(onClose).toHaveBeenCalledTimes(2);

    await act(async () => {
      document.body.querySelector('button[aria-label="Close test dialog"]').click();
    });
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it('respects disabled Escape and backdrop closing', async () => {
    const onClose = vi.fn();

    await render(
      <Dialog
        open
        onClose={onClose}
        titleId="disabled-close-title"
        closeOnBackdrop={false}
        closeOnEscape={false}
      >
        <Dialog.Title>Disabled close behavior</Dialog.Title>
        <Dialog.Body>Body</Dialog.Body>
      </Dialog>,
    );

    await act(async () => {
      overlay().dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });

    expect(onClose).not.toHaveBeenCalled();
  });

  it('merges classes, applies size variants, and keeps body/footer structure', async () => {
    await render(
      <Dialog open onClose={vi.fn()} titleId="class-title" size="xl" className="custom-panel">
        <Dialog.Header>
          <Dialog.Title>Class behavior</Dialog.Title>
        </Dialog.Header>
        <Dialog.Body className="custom-body">
          Long body content
        </Dialog.Body>
        <Dialog.Footer className="custom-footer">
          Footer content
        </Dialog.Footer>
      </Dialog>,
    );

    const panel = dialog();
    const body = document.body.querySelector('.custom-body');
    expect(panel.className).toContain('max-w-4xl');
    expect(panel.className).toContain('custom-panel');
    expect(panel.className).toContain('rounded-dialog');
    expect(panel.className).toContain('shadow-dialog');
    expect(panel.className).toContain('text-start');
    expect(body.className).toContain('overflow-y-auto');
    expect(body).toHaveTextContent('Long body content');
    expect(document.body.querySelector('.custom-footer')).toHaveTextContent('Footer content');
  });

  it('moves focus inside the dialog, traps Tab, restores focus, and restores body scroll', async () => {
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.textContent = 'Open dialog';
    document.body.appendChild(trigger);
    trigger.focus();
    document.body.style.overflow = 'auto';

    await render(
      <Dialog open onClose={vi.fn()} titleId="focus-title">
        <Dialog.Title>Focus behavior</Dialog.Title>
        <Dialog.Body>
          <button type="button">First</button>
          <button type="button">Last</button>
        </Dialog.Body>
      </Dialog>,
    );

    const buttons = Array.from(dialog().querySelectorAll('button'));
    expect(document.activeElement).toBe(buttons[0]);
    expect(document.body.style.overflow).toBe('hidden');

    buttons[1].focus();
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }));
    });
    expect(document.activeElement).toBe(buttons[0]);

    await render(
      <Dialog open={false} onClose={vi.fn()} titleId="focus-title">
        <Dialog.Title>Focus behavior</Dialog.Title>
      </Dialog>,
    );

    expect(document.activeElement).toBe(trigger);
    expect(document.body.style.overflow).toBe('auto');
    trigger.remove();
  });
});
