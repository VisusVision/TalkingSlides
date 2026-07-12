import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ProfileMenu from './ProfileMenu';

let host;
let root;

const user = {
  id: 42,
  username: 'motion-user',
  profile: {
    display_name: 'Motion User',
    role: 'student',
  },
};

async function render(element) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        {element}
      </MemoryRouter>,
    );
  });
  return host;
}

async function click(element) {
  await act(async () => {
    element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

describe('ProfileMenu motion behavior', () => {
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

  it('renders the account popover with motion classes and preserves outside-click close behavior', async () => {
    await render(
      <ProfileMenu
        user={user}
        authLoading={false}
        onLoginRequest={vi.fn()}
        onLogout={vi.fn()}
      />,
    );

    await click(host.querySelector('button[aria-label="Open account menu"]'));

    const menu = host.querySelector('[role="menu"]');
    expect(menu).toBeTruthy();
    expect(menu.className).toContain('motion-popover-in');
    expect(host.querySelector('[role="menuitem"]').className).toContain('motion-interactive');

    await act(async () => {
      document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    });

    expect(host.querySelector('[role="menu"]')).toBeNull();
  });
});
