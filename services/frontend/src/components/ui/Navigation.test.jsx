import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import MobileBottomNav from './MobileBottomNav';
import SideRail from './SideRail';

let host;
let root;

const publisherUser = {
  id: 7,
  username: 'publisher',
  profile: { role: 'publisher' },
};

async function render(element, route = '/library') {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[route]}>
        {element}
      </MemoryRouter>,
    );
  });
  return host;
}

describe('navigation active states', () => {
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

  it('marks the active desktop rail item as the current page with a non-color indicator', async () => {
    await render(
      <SideRail
        user={publisherUser}
        collapsed={false}
        expanded
        onToggleCollapse={vi.fn()}
      />,
    );

    const libraryLink = host.querySelector('a[href="/library"]');
    expect(libraryLink).toHaveAttribute('aria-current', 'page');
    expect(libraryLink.className).toContain('shadow-[inset_0_0_0_1px_var(--accent-primary)]');
    expect(libraryLink.querySelector('span[aria-hidden="true"]')).toBeTruthy();
  });

  it('marks the active mobile nav item as the current page with a visible selected marker', async () => {
    await render(<MobileBottomNav user={publisherUser} />, '/analytics');

    const analyticsLink = host.querySelector('a[href="/analytics"]');
    expect(analyticsLink).toHaveAttribute('aria-current', 'page');
    expect(analyticsLink.className).toContain('shadow-[inset_0_0_0_1px_var(--accent-primary)]');
    expect(analyticsLink.querySelector('span[aria-hidden="true"]')).toBeTruthy();
  });
});

