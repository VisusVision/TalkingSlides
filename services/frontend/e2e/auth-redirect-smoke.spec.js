import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  jsonResponse,
} from './support/apiMocks.js';

const CAPABILITIES_PAYLOAD = {
  features: {
    avatar: { enabled: false },
    google_auth: { enabled: false, redirect_flow_enabled: false },
    moderation: { enabled: false },
    tts_preview: { enabled: false },
    visual_moderation: { enabled: false },
  },
};

const AUTH_USER = {
  id: 88,
  username: 'redirect.library.user',
  display_name: 'Redirect Library User',
  first_name: 'Redirect',
  last_name: 'User',
  role: 'learner',
  auth_provider: 'password',
  profile: {
    role: 'learner',
    display_name: 'Redirect Library User',
  },
};

const HISTORY_PAYLOAD = [
  {
    id: 8801,
    progress_pct: 45,
    last_watched_at: '2026-06-04T10:00:00Z',
    lesson: {
      id: 8802,
      title: 'Redirect Login Library Lesson',
      description: 'A mocked library lesson loaded after redirect login.',
      teacher_name: 'Redirect Publisher',
      category_name: 'Frontend QA',
      user_progress: 45,
    },
  },
];

async function mockUnauthenticatedAppShellApi(page) {
  await page.route('**/api/v1/capabilities/**', (route) => route.fulfill(jsonResponse(CAPABILITIES_PAYLOAD)));
  await page.route('**/api/v1/auth/providers/**', (route) => route.fulfill(jsonResponse({
    google: { enabled: false, redirect_flow_enabled: false },
  })));
  await page.route('**/api/v1/catalog/feed/**', (route) => route.fulfill(jsonResponse({
    sections: [],
    results: [],
  })));
  await page.route('**/api/v1/catalog/**', (route) => route.fulfill(jsonResponse([])));
}

async function mockSuccessfulLoginLibraryApi(page) {
  await page.route('**/api/v1/auth/login/**', async (route) => {
    expect(route.request().method()).toBe('POST');
    const payload = route.request().postDataJSON();
    expect(payload).toEqual({
      username: 'redirect.library.user',
      password: 'valid-password',
    });
    await route.fulfill(jsonResponse({
      token: 'redirect-login-token',
      user: AUTH_USER,
    }));
  });
  await page.route('**/api/v1/auth/me/**', (route) => route.fulfill(jsonResponse(AUTH_USER)));
  await page.route('**/api/v1/me/notifications/unread-count/**', (route) => route.fulfill(jsonResponse({
    unread_count: 0,
  })));
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
}

async function setupUnauthenticatedSmoke(page) {
  const expectNoBrowserErrors = collectBrowserErrors(page);

  await mockUnauthenticatedAppShellApi(page);
  await page.addInitScript(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  return expectNoBrowserErrors;
}

async function setupUnauthenticatedNegativeLoginSmoke(page) {
  const expectNoBrowserErrors = collectBrowserErrors(page, {
    allowConsoleError: (text) => (
      text === 'Failed to load resource: the server responded with a status of 401 (Unauthorized)'
    ),
  });

  await mockUnauthenticatedAppShellApi(page);
  await page.addInitScript(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  return expectNoBrowserErrors;
}

function signInModal(page) {
  return page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name: 'Continue Learning', exact: true }) })
    .last();
}

test('unauthenticated Library access redirects to sign in', async ({ page }) => {
  const expectNoBrowserErrors = await setupUnauthenticatedSmoke(page);

  await page.goto('/library');

  await expect(page).toHaveURL((url) => (
    url.pathname === '/'
    && url.searchParams.get('redirect') === '/library'
  ));
  await expect(page.getByRole('main').getByText('No lessons matched your search')).toBeVisible();

  const modal = signInModal(page);

  await expect(modal).toBeVisible();
  await expect(modal.getByText('Sign in to open your teaching studio, sync progress, and publish lessons.', { exact: true })).toBeVisible();
  await expect(modal.getByLabel('Username', { exact: true })).toBeVisible();
  await expect(modal.getByLabel('Password', { exact: true })).toBeVisible();
  await expect(modal.getByRole('button', { name: 'Sign In', exact: true })).toBeVisible();

  expectNoBrowserErrors();
});

test('failed login from auth modal shows an error', async ({ page }) => {
  const expectNoBrowserErrors = await setupUnauthenticatedNegativeLoginSmoke(page);

  await page.route('**/api/v1/auth/login/**', async (route) => {
    expect(route.request().method()).toBe('POST');
    const payload = route.request().postDataJSON();
    expect(payload).toEqual({
      username: 'invalid.library.user',
      password: 'wrong-password',
    });
    await route.fulfill(jsonResponse({ error: 'Invalid username or password.' }, 401));
  });

  await page.goto('/library');

  await expect(page).toHaveURL((url) => (
    url.pathname === '/'
    && url.searchParams.get('redirect') === '/library'
  ));

  const modal = signInModal(page);
  await expect(modal).toBeVisible();

  await modal.getByLabel('Username', { exact: true }).fill('invalid.library.user');
  await modal.getByLabel('Password', { exact: true }).fill('wrong-password');
  await modal.getByRole('button', { name: 'Sign In', exact: true }).click();

  await expect(modal.getByText('Invalid username or password.', { exact: true })).toBeVisible();
  await expect(page).toHaveURL((url) => (
    url.pathname === '/'
    && url.searchParams.get('redirect') === '/library'
  ));
  await expect(modal.getByRole('button', { name: 'Sign In', exact: true })).toBeEnabled();

  expectNoBrowserErrors();
});

test('successful login from auth modal returns to Library', async ({ page }) => {
  const expectNoBrowserErrors = await setupUnauthenticatedSmoke(page);
  await mockSuccessfulLoginLibraryApi(page);

  await page.goto('/library');

  await expect(page).toHaveURL((url) => (
    url.pathname === '/'
    && url.searchParams.get('redirect') === '/library'
  ));

  const modal = signInModal(page);
  await expect(modal).toBeVisible();

  await modal.getByLabel('Username', { exact: true }).fill('redirect.library.user');
  await modal.getByLabel('Password', { exact: true }).fill('valid-password');
  await modal.getByRole('button', { name: 'Sign In', exact: true }).click();

  await expect(page).toHaveURL((url) => url.pathname === '/library');
  await expect(page.getByRole('heading', { name: 'Your Learning Hub', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'History', exact: true })).toBeVisible();
  await expect(page.getByText('Redirect Login Library Lesson', { exact: true })).toBeVisible();
  await expect(page.getByText('Continue from 45%', { exact: true })).toBeVisible();

  expectNoBrowserErrors();
});
