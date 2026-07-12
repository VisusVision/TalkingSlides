import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Button from './Button';

let host;
let root;

async function render(element) {
  await act(async () => {
    root.render(element);
  });
  return host;
}

describe('Button', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    host.remove();
  });

  it('keeps native button behavior and class passthrough', async () => {
    const onClick = vi.fn();

    await render(
      <Button onClick={onClick} fullWidth className="custom-button" data-testid="button">
        Save
      </Button>,
    );

    const button = host.querySelector('[data-testid="button"]');
    expect(button).toHaveAttribute('type', 'button');
    expect(button).toHaveTextContent('Save');
    expect(button.className).toContain('w-full');
    expect(button.className).toContain('custom-button');

    await act(async () => {
      button.click();
    });

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('uses shared restrained motion instead of legacy large scale feedback', async () => {
    await render(
      <Button data-testid="button">
        Publish
      </Button>,
    );

    const button = host.querySelector('[data-testid="button"]');
    expect(button.className).toContain('motion-interactive');
    expect(button.className).toContain('enabled:active:scale-[0.98]');
    expect(button.className).not.toContain('hover:scale-105');
    expect(button.className).not.toContain('active:scale-95');
  });

  it('keeps disabled buttons from exposing unscoped interactive transform classes', async () => {
    await render(
      <Button disabled data-testid="button">
        Loading
      </Button>,
    );

    const button = host.querySelector('[data-testid="button"]');
    const classes = button.className.split(/\s+/);
    expect(button).toBeDisabled();
    expect(button.className).toContain('disabled:cursor-not-allowed');
    expect(classes.some((className) => className.startsWith('hover:scale'))).toBe(false);
    expect(classes.some((className) => className.startsWith('active:scale'))).toBe(false);
  });
});
