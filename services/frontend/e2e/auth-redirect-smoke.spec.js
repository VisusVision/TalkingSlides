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

test('unauthenticated Library access redirects to sign in', async ({ page }) => {
  const expectNoBrowserErrors = await setupUnauthenticatedSmoke(page);

  await page.goto('/library');

  await expect(page).toHaveURL((url) => (
    url.pathname === '/'
    && url.searchParams.get('redirect') === '/library'
  ));
  await expect(page.getByRole('main').getByText('No lessons matched your search')).toBeVisible();

  const signInModal = page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name: 'Continue Learning', exact: true }) })
    .last();

  await expect(signInModal).toBeVisible();
  await expect(signInModal.getByText('Sign in to open your teaching studio, sync progress, and publish lessons.', { exact: true })).toBeVisible();
  await expect(signInModal.getByLabel('Username', { exact: true })).toBeVisible();
  await expect(signInModal.getByLabel('Password', { exact: true })).toBeVisible();
  await expect(signInModal.getByRole('button', { name: 'Sign In', exact: true })).toBeVisible();

  expectNoBrowserErrors();
});
