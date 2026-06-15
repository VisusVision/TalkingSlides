import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  jsonResponse,
  mockCommonAppChromeApi,
  seedAuthenticatedSession,
} from './support/apiMocks.js';

const AUTH_USER = {
  id: 88,
  username: 'notify.learner',
  display_name: 'Notify Learner',
  first_name: 'Notify',
  last_name: 'Learner',
  role: 'learner',
  auth_provider: 'password',
  profile: {
    role: 'learner',
    display_name: 'Notify Learner',
  },
};

const CAPABILITIES_PAYLOAD = {
  features: {
    avatar: { enabled: false },
    google_auth: { enabled: false },
    moderation: { enabled: false },
    tts_preview: { enabled: false },
    visual_moderation: { enabled: false },
  },
};

function notificationPayload() {
  return {
    count: 1,
    results: [
      {
        id: 7101,
        event_type: 'render_complete',
        title: 'Render complete',
        body: 'Notification smoke lesson is ready to watch.',
        action_url: '/watch?lesson=7101',
        is_read: false,
        created_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
      },
    ],
    limit: 20,
    offset: 0,
    has_more: false,
    next_offset: null,
  };
}

async function mockAuthenticatedNotificationsApi(page) {
  await mockCommonAppChromeApi(page, {
    user: AUTH_USER,
    capabilities: CAPABILITIES_PAYLOAD,
    categories: [
      { id: 1, name: 'Frontend QA', slug: 'frontend-qa' },
    ],
    unreadCount: 1,
  });

  await page.route('**/api/v1/me/notifications/?**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse(notificationPayload()));
  });

  await page.route('**/api/v1/me/notifications/7101/read/**', (route) => {
    expect(route.request().method()).toBe('POST');
    return route.fulfill(jsonResponse({ id: 7101, is_read: true }));
  });

  await page.route('**/api/v1/me/notifications/mark-all-read/**', (route) => {
    expect(route.request().method()).toBe('POST');
    return route.fulfill(jsonResponse({ updated_count: 1 }));
  });
}

async function setupAuthenticatedNotificationsSmoke(page) {
  const expectNoBrowserErrors = collectBrowserErrors(page);

  await mockAuthenticatedNotificationsApi(page);
  await seedAuthenticatedSession(page, {
    token: 'notifications-smoke-token',
    user: AUTH_USER,
  });

  return expectNoBrowserErrors;
}

test('authenticated Notifications renders list and marks an item read', async ({ page }) => {
  const expectNoBrowserErrors = await setupAuthenticatedNotificationsSmoke(page);

  await page.goto('/notifications');

  await expect(page.getByRole('heading', { name: 'Notification center' })).toBeVisible();
  await expect(page.getByText('Review comments, followed publisher updates, and render status changes.')).toBeVisible();
  await expect(page.getByRole('button', { name: /Mark all read/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'All', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Unread/ })).toBeVisible();

  await expect(page.getByRole('heading', { name: 'Render complete' })).toBeVisible();
  await expect(page.getByText('Notification smoke lesson is ready to watch.')).toBeVisible();
  await expect(page.getByText('2h ago')).toBeVisible();
  await expect(page.getByText('Open destination')).toBeVisible();
  await expect(page.getByText('Unread').first()).toBeVisible();

  await page.getByRole('button', { name: 'Mark read' }).click();

  await expect(page.getByRole('heading', { name: 'Notification center' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Render complete' })).toBeVisible();
  await expect(page.getByText('Read').first()).toBeVisible();

  expectNoBrowserErrors();
});
