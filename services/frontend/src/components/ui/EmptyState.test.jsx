import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { SearchX } from 'lucide-react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import EmptyState from './EmptyState';

let host;
let root;

async function render(element) {
  await act(async () => {
    root.render(element);
  });
  return host;
}

describe('EmptyState', () => {
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

  it('renders title, description, and decorative icon', async () => {
    await render(
      <EmptyState
        icon={SearchX}
        title="No lessons found"
        description="Try another keyword or category."
      />,
    );

    const state = host.querySelector('[data-empty-state]');
    const iconWrap = state.querySelector('[aria-hidden="true"]');
    expect(host.querySelector('h2')).toHaveTextContent('No lessons found');
    expect(host).toHaveTextContent('Try another keyword or category.');
    expect(iconWrap).toBeTruthy();
    expect(iconWrap.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
  });

  it('renders primary and secondary actions without changing button behavior', async () => {
    const onPrimary = vi.fn();
    const onSecondary = vi.fn();

    await render(
      <EmptyState
        title="No saved playlists yet"
        action={<button type="button" onClick={onPrimary}>Browse lessons</button>}
        secondaryAction={<button type="button" onClick={onSecondary}>Open settings</button>}
      />,
    );

    const [primary, secondary] = host.querySelectorAll('button');
    await act(async () => primary.click());
    await act(async () => secondary.click());

    expect(onPrimary).toHaveBeenCalledTimes(1);
    expect(onSecondary).toHaveBeenCalledTimes(1);
  });

  it('supports compact and contained rendering with className merging', async () => {
    await render(
      <EmptyState
        as="article"
        compact
        contained
        className="custom-empty"
        data-testid="empty"
        title="No unread notifications"
      />,
    );

    const state = host.querySelector('[data-testid="empty"]');
    expect(state.tagName).toBe('ARTICLE');
    expect(state.className).toContain('token-surface');
    expect(state.className).toContain('min-h-32');
    expect(state.className).toContain('custom-empty');
  });

  it('supports heading override, children, and native props passthrough', async () => {
    await render(
      <EmptyState
        as="div"
        titleAs="h3"
        title="No recorded activity in this range"
        id="analytics-empty"
        aria-label="Analytics empty state"
      >
        <span className="extra-content">0 events</span>
      </EmptyState>,
    );

    const state = host.querySelector('#analytics-empty');
    expect(state).toHaveAttribute('aria-label', 'Analytics empty state');
    expect(host.querySelector('h3')).toHaveTextContent('No recorded activity in this range');
    expect(host.querySelector('.extra-content')).toHaveTextContent('0 events');
  });
});
