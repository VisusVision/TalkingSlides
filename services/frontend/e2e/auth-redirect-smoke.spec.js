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
