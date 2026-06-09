import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  jsonResponse,
} from './support/apiMocks.js';

const AUTH_USER = {
  id: 91,
  username: 'logout.library.user',
  display_name: 'Logout Library User',
  first_name: 'Logout',
  last_name: 'User',
  role: 'learner',
  auth_provider: 'password',
  profile: {
    role: 'learner',
    display_name: 'Logout Library User',
  },
};

const CAPABILITIES_PAYLOAD = {
  features: {
    avatar: { enabled: false },
    google_auth: { enabled: false, redirect_flow_enabled: false },
    moderation: { enabled: false },
    tts_preview: { enabled: false },
    visual_moderation: { enabled: false },
  },
};

const HISTORY_PAYLOAD = [
  {
    id: 9101,
    progress_pct: 58,
    last_watched_at: '2026-06-05T10:00:00Z',
    lesson: {
      id: 9102,
      title: 'Logout Smoke Library Lesson',
      description: 'A mocked library lesson for logout coverage.',
      teacher_name: 'Logout Publisher',
      category_name: 'Frontend QA',
      user_progress: 58,
    },
  },
];

async function mockLogoutSmokeApi(page) {
  let authRevoked = false;

  await page.route('**/api/v1/auth/me/**', (route) => {
    if (authRevoked) {
      return route.fulfill(jsonResponse({ detail: 'Authentication credentials were not provided.' }, 401));
    }

    return route.fulfill(jsonResponse(AUTH_USER));
  });
  await page.route('**/api/v1/me/notifications/unread-count/**', (route) => route.fulfill(jsonResponse({
    unread_count: 0,
  })));
  await page.route('**/api/v1/capabilities/**', (route) => route.fulfill(jsonResponse(CAPABILITIES_PAYLOAD)));
  await page.route('**/api/v1/categories/**', (route) => route.fulfill(jsonResponse([
    { id: 1, name: 'Frontend QA', slug: 'frontend-qa' },
  ])));

  await page.route('**/api/v1/auth/providers/**', (route) => route.fulfill(jsonResponse({
    google: { enabled: false, redirect_flow_enabled: false },
  })));
  await page.route('**/api/v1/auth/logout/**', (route) => {
    expect(route.request().method()).toBe('POST');
    authRevoked = true;
    return route.fulfill(jsonResponse({ ok: true }));
  });
  await page.route('**/api/v1/me/history/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse(HISTORY_PAYLOAD));
  });
  await page.route('**/api/v1/me/liked-lessons/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse([]));
  });
  await page.route('**/api/v1/me/following/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse([]));
  });
  await page.route('**/api/v1/me/saved-playlists/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse([]));
  });
  await page.route('**/api/v1/catalog/feed/**', (route) => route.fulfill(jsonResponse({
    sections: [],
    results: [],
  })));
  await page.route('**/api/v1/catalog/**', (route) => route.fulfill(jsonResponse([])));
}

async function setupLogoutSmoke(page) {
  const expectNoBrowserErrors = collectBrowserErrors(page);

  await mockLogoutSmokeApi(page);
  await page.goto('/');
  await page.evaluate(({ token, user }) => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    window.localStorage.setItem('auth_token', token);
    window.localStorage.setItem('auth_user', JSON.stringify(user));
  }, {
    token: 'logout-smoke-token',
    user: AUTH_USER,
  });

  return expectNoBrowserErrors;
}

function signInModal(page) {
  return page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name: 'Continue Learning', exact: true }) })
    .last();
}

test('logout revokes Library access', async ({ page }) => {
  const expectNoBrowserErrors = await setupLogoutSmoke(page);

  await page.goto('/library');

  await expect(page.getByRole('heading', { name: 'Your Learning Hub', exact: true })).toBeVisible();
  await expect(page.getByText('Logout Smoke Library Lesson', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Open account menu', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Sign Out', exact: true }).click();

  await expect(page).toHaveURL((url) => url.pathname === '/');
  await expect.poll(async () => page.evaluate(() => localStorage.getItem('auth_token'))).toBeNull();
  await expect.poll(async () => page.evaluate(() => localStorage.getItem('auth_user'))).toBeNull();

  await page.goto('/library');

  await expect(page).toHaveURL((url) => (
    url.pathname === '/'
    && url.searchParams.get('redirect') === '/library'
  ));
  await expect(signInModal(page)).toBeVisible();

  expectNoBrowserErrors();
});
