import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  jsonResponse,
  mockCommonAppChromeApi,
} from './support/apiMocks.js';
import { translateAppMessage } from '../src/i18n/messages.js';

const USER = {
  id: 7,
  username: 'teacher',
  first_name: 'Teacher',
  profile: { role: 'teacher' },
};

const CAPABILITIES = {
  features: {
    avatar: { enabled: true },
    google_auth: { enabled: false },
    intelligence: { enabled: true },
    moderation: { enabled: true },
    visual_moderation: { enabled: true },
  },
};

const LOCALES = [
  ['en', 'ltr'],
  ['tr', 'ltr'],
  ['es', 'ltr'],
  ['fr', 'ltr'],
  ['de', 'ltr'],
  ['it', 'ltr'],
  ['pt', 'ltr'],
  ['ru', 'ltr'],
  ['ja', 'ltr'],
  ['ko', 'ltr'],
  ['zh-CN', 'ltr'],
  ['ar', 'rtl'],
];

const ROUTES = [
  '/settings',
  '/',
  '/studio',
  '/browse',
  '/watch',
  '/share/invalid-token',
  '/notifications',
  '/analytics',
];

async function mockI18nApi(page) {
  await mockCommonAppChromeApi(page, {
    user: USER,
    capabilities: CAPABILITIES,
    categories: [{ id: 1, name: 'Science', slug: 'science' }],
  });
  await page.route('**/api/v1/me/profile/**', (route) => route.fulfill(jsonResponse({})));
  await page.route('**/api/v1/catalog/feed/**', (route) => route.fulfill(jsonResponse({ sections: [], results: [] })));
  await page.route('**/api/v1/catalog/**', (route) => route.fulfill(jsonResponse([])));
  await page.route('**/api/v1/share/**', (route) => route.fulfill(jsonResponse({ code: 'share_expired' }, 410)));
  await page.route('**/api/v1/admin/stats/**', (route) => route.fulfill(jsonResponse({ metrics: {}, series: [] })));
  await page.route('**/api/v1/projects/**', (route) => route.fulfill(jsonResponse({ results: [] })));
  await page.route('**/api/v1/me/notifications/**', (route) => route.fulfill(jsonResponse({ results: [] })));
  await page.route('**/api/v1/**', (route) => route.fulfill(jsonResponse({ results: [] })));
}

test('all supported locales update app shell and document direction', async ({ page }) => {
  test.setTimeout(180_000);
  const expectNoBrowserErrors = collectBrowserErrors(page);
  await mockI18nApi(page);
  await page.addInitScript(({ authToken, authUser }) => {
    window.localStorage.setItem('auth_token', authToken);
    window.localStorage.setItem('auth_user', JSON.stringify(authUser));
  }, { authToken: 'token-i18n', authUser: USER });
  await page.goto('/settings');

  for (const [locale, direction] of LOCALES) {
    await page.goto('/settings');
    await page.getByTestId('settings-language-selector').selectOption(locale);
    await expect(page.locator('html')).toHaveAttribute('lang', locale);
    await expect(page.locator('html')).toHaveAttribute('dir', direction);
    await expect(page.locator('body')).toContainText(translateAppMessage(locale, 'settingsLabel'));
    await expect(page.getByTestId('settings-language-selector')).toHaveValue(locale);
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('lang', locale);
    await expect(page.locator('html')).toHaveAttribute('dir', direction);

    for (const route of ROUTES) {
      await page.goto(route);
      await expect(page.locator('html')).toHaveAttribute('lang', locale);
      await expect(page.locator('html')).toHaveAttribute('dir', direction);
    }
  }

  await page.goto('/settings');
  await page.getByTestId('settings-language-selector').selectOption('en');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
  expectNoBrowserErrors();
});
