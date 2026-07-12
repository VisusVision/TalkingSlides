import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Header from './Header';

vi.mock('../../api', () => ({
  fetchAuthenticatedMediaBlobUrl: vi.fn(),
  fetchNotificationUnreadCount: vi.fn(() => Promise.resolve({ unread_count: 1 })),
  fetchNotifications: vi.fn(() => Promise.resolve({
    results: [
      {
        id: 1,
        title: 'Render complete',
        body: 'Your video is ready.',
        event_type: 'render_completed',
        is_read: false,
        created_at: new Date().toISOString(),
        action_url: '/library',
      },
    ],
  })),
  markAllNotificationsRead: vi.fn(() => Promise.resolve()),
  markNotificationRead: vi.fn(() => Promise.resolve()),
}));

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

describe('Header notification menu motion behavior', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    vi.clearAllMocks();
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

  it('renders the notification dropdown with motion classes and preserves outside-click close behavior', async () => {
    await render(
      <Header
        searchQuery=""
        onSearchQueryChange={vi.fn()}
        user={user}
        authLoading={false}
        onLoginRequest={vi.fn()}
        onLogout={vi.fn()}
      />,
    );

    await click(host.querySelector('button[aria-label="Notifications"]'));
    await act(async () => {});

    const dropdown = host.querySelector('.motion-popover-in');
    expect(dropdown).toBeTruthy();
    expect(dropdown).toHaveTextContent('Notifications');
    expect(dropdown.querySelector('button')).toHaveClass('motion-interactive');

    await act(async () => {
      document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    });

    expect(host.querySelector('.motion-popover-in')).toBeNull();
  });
});
