import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  jsonResponse,
  mockCommonAppChromeApi,
  seedAuthenticatedSession,
} from './support/apiMocks.js';

const AUTH_USER = {
  id: 88,
  username: 'history.learner',
  display_name: 'History Learner',
  first_name: 'History',
  last_name: 'Learner',
  role: 'learner',
  auth_provider: 'password',
  profile: {
    role: 'learner',
    display_name: 'History Learner',
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

const HISTORY_PAYLOAD = [
  {
    id: 4101,
    progress_pct: 73,
    last_watched_at: '2026-05-24T14:30:00Z',
    lesson: {
      id: 810,
      title: 'History Smoke Calculus Review',
      description: 'A mocked watched lesson for the standalone history route.',
      teacher_name: 'History Publisher',
      category_name: 'Mathematics',
      user_progress: 73,
    },
  },
];

async function mockAuthenticatedHistoryApi(page) {
  await mockCommonAppChromeApi(page, {
    user: AUTH_USER,
    capabilities: CAPABILITIES_PAYLOAD,
    categories: [
      { id: 1, name: 'Mathematics', slug: 'mathematics' },
    ],
    unreadCount: 0,
  });

  await page.route('**/api/v1/me/history/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse(HISTORY_PAYLOAD));
  });
}

async function setupAuthenticatedHistorySmoke(page) {
  const expectNoBrowserErrors = collectBrowserErrors(page);

  await mockAuthenticatedHistoryApi(page);
  await seedAuthenticatedSession(page, {
    token: 'history-smoke-token',
    user: AUTH_USER,
  });

  return expectNoBrowserErrors;
}

test('authenticated History renders watched lessons', async ({ page }) => {
  const expectNoBrowserErrors = await setupAuthenticatedHistorySmoke(page);
  const main = page.getByRole('main');

  await page.goto('/history');

  await expect(main.getByRole('heading', { name: 'Continue Watching' })).toBeVisible();
  await expect(main.getByText('Your watched lessons are private to your account.')).toBeVisible();

  const watchedLesson = main.getByRole('link').filter({ hasText: 'History Smoke Calculus Review' });
  await expect(watchedLesson).toHaveCount(1);
  await expect(watchedLesson.first()).toContainText('History Publisher');
  await expect(watchedLesson.first()).toContainText('Mathematics');
  await expect(watchedLesson.first()).toContainText('73% watched');
  await expect(watchedLesson.first()).toContainText('Continue from 73%');

  expectNoBrowserErrors();
});
