import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  fetchCatalog: vi.fn(),
  fetchCategories: vi.fn(),
  fetchLikedLessons: vi.fn(),
  fetchNotificationUnreadCount: vi.fn(),
  fetchNotifications: vi.fn(),
  fetchUserHistory: vi.fn(),
  getFollowingPublishers: vi.fn(),
  getSavedPlaylists: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  markNotificationRead: vi.fn(),
}));

vi.mock('../api', () => ({
  fetchCatalog: apiMocks.fetchCatalog,
  fetchCategories: apiMocks.fetchCategories,
  fetchLikedLessons: apiMocks.fetchLikedLessons,
  fetchNotificationUnreadCount: apiMocks.fetchNotificationUnreadCount,
  fetchNotifications: apiMocks.fetchNotifications,
  fetchUserHistory: apiMocks.fetchUserHistory,
  getFollowingPublishers: apiMocks.getFollowingPublishers,
  getSavedPlaylists: apiMocks.getSavedPlaylists,
  markAllNotificationsRead: apiMocks.markAllNotificationsRead,
  markNotificationRead: apiMocks.markNotificationRead,
}));

vi.mock('../components/ui/PageLoading', () => ({
  usePageLoading: () => {},
}));

import Browse from './Browse';
import Library from './Library';
import Notifications from './Notifications';

let host;
let root;

async function renderPage(element, route = '/') {
  await act(async () => {
    root.render(<MemoryRouter initialEntries={[route]}>{element}</MemoryRouter>);
  });
}

async function flushAsync() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function waitForText(text) {
  for (let index = 0; index < 8; index += 1) {
    await flushAsync();
    if (host.textContent.includes(text)) return;
  }
  throw new Error('Missing text: ' + text + '\n' + host.textContent);
}

function buttonsNamed(text) {
  return [...host.querySelectorAll('button')].filter((button) => button.textContent.includes(text));
}

describe('cross-app ProductGuidance adoption', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    document.documentElement.lang = 'en';
    document.documentElement.dir = '';
    window.localStorage.clear();
    window.sessionStorage.clear();
    window.scrollTo = vi.fn();
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

  it('shows Browse no-results guidance from real category filter state without recommendation wording', async () => {
    apiMocks.fetchCategories.mockResolvedValue([{ id: 1, name: 'Frontend QA', slug: 'frontend-qa' }]);
    apiMocks.fetchCatalog.mockResolvedValue([]);

    await renderPage(<Browse searchQuery="" user={null} onLoginRequest={vi.fn()} />, '/browse?category=frontend-qa');
    await waitForText('No lessons match this view.');

    expect(host.querySelector('[data-testid="product-guidance"]')).toHaveAttribute('data-status', 'needs-attention');
    expect(host.textContent).toContain('Frontend QA');
    expect(host.textContent).toContain('Clear category');
    expect(host.textContent).not.toMatch(/AI recommended|recommended for you|score/i);
  });

  it('shows Library continuation only when history progress exists', async () => {
    apiMocks.fetchUserHistory.mockResolvedValue([{
      id: 3001,
      progress_pct: 64,
      last_watched_at: '2026-05-20T10:00:00Z',
      lesson: {
        id: 501,
        title: 'Library Smoke History Lesson',
        teacher_name: 'Library Publisher',
        category_name: 'Frontend QA',
        user_progress: 64,
      },
    }]);
    apiMocks.fetchLikedLessons.mockResolvedValue([]);
    apiMocks.getFollowingPublishers.mockResolvedValue([]);
    apiMocks.getSavedPlaylists.mockResolvedValue([]);

    await renderPage(<Library searchQuery="" />, '/library');
    await waitForText('Continue where you left off.');

    expect(host.querySelector('[data-testid="product-guidance"]')).toHaveAttribute('data-status', 'ready');
    expect(host.textContent).toContain('Library Smoke History Lesson');
    expect(host.textContent).toContain('64% watched');
    expect(host.textContent).not.toMatch(/quality score|AI recommends|estimated completion/i);
  });

  it('shows Notifications failure guidance from unread failure events and preserves read callbacks', async () => {
    apiMocks.fetchNotificationUnreadCount.mockResolvedValue({ unread_count: 1 });
    apiMocks.fetchNotifications.mockResolvedValue({
      count: 1,
      results: [{
        id: 7101,
        event_type: 'publisher_lesson_render_failed',
        title: 'Render failed',
        body: 'The render failed because the uploaded source was removed.',
        action_url: '/studio?project=7101',
        is_read: false,
        created_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
      }],
      limit: 20,
      offset: 0,
      has_more: false,
      next_offset: null,
    });
    apiMocks.markNotificationRead.mockResolvedValue({ id: 7101, is_read: true });
    apiMocks.markAllNotificationsRead.mockResolvedValue({ unread_count: 0 });

    await renderPage(<Notifications user={{ id: 88 }} />, '/notifications');
    await waitForText('A render update needs attention.');

    expect(host.querySelector('[data-testid="product-guidance"]')).toHaveAttribute('data-status', 'failed');
    expect(host.textContent).toContain('The render failed because the uploaded source was removed.');
    expect(buttonsNamed('Mark all read')).toHaveLength(1);

    const openButton = buttonsNamed('Open notification')[0];
    await act(async () => {
      openButton.click();
    });
    expect(apiMocks.markNotificationRead).toHaveBeenCalledWith(7101);
  });

  it('uses Turkish and Arabic page guidance copy without normal-path English leakage', async () => {
    document.documentElement.lang = 'tr-TR';
    apiMocks.fetchUserHistory.mockResolvedValue([]);
    apiMocks.fetchLikedLessons.mockResolvedValue([]);
    apiMocks.getFollowingPublishers.mockResolvedValue([]);
    apiMocks.getSavedPlaylists.mockResolvedValue([]);

    await renderPage(<Library searchQuery="" />, '/library');
    await waitForText('Kutuphane olusturmaya basla.');
    expect(host.textContent).not.toContain('Start building your library.');

    document.documentElement.lang = 'ar';
    document.documentElement.dir = 'rtl';
    apiMocks.fetchNotificationUnreadCount.mockResolvedValue({ unread_count: 0 });
    apiMocks.fetchNotifications.mockResolvedValue({ count: 0, results: [], has_more: false, next_offset: null });

    await renderPage(<Notifications user={{ id: 88 }} />, '/notifications');
    await waitForText('لا توجد إشعارات بعد.');
    expect(host.textContent).not.toContain('No notifications yet.');
  });
});
