import { expect } from '@playwright/test';

export function jsonResponse(payload, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  };
}

export function collectBrowserErrors(page) {
  const consoleErrors = [];
  const pageErrors = [];

  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text());
    }
  });
  page.on('pageerror', (error) => {
    pageErrors.push(error.message);
  });

  return function expectNoBrowserErrors() {
    expect(consoleErrors, 'browser console errors').toEqual([]);
    expect(pageErrors, 'page errors').toEqual([]);
  };
}

export async function seedAuthenticatedSession(page, { token, user }) {
  await page.addInitScript(({ authToken, authUser }) => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    window.localStorage.setItem('auth_token', authToken);
    window.localStorage.setItem('auth_user', JSON.stringify(authUser));
  }, {
    authToken: token,
    authUser: user,
  });
}

export async function mockCommonAppChromeApi(page, {
  user,
  capabilities,
  categories,
  unreadCount = 0,
}) {
  await page.route('**/api/v1/auth/me/**', (route) => route.fulfill(jsonResponse(user)));
  await page.route('**/api/v1/me/notifications/unread-count/**', (route) => route.fulfill(jsonResponse({
    unread_count: unreadCount,
  })));
  await page.route('**/api/v1/capabilities/**', (route) => route.fulfill(jsonResponse(capabilities)));
  await page.route('**/api/v1/categories/**', (route) => route.fulfill(jsonResponse(categories)));
}
