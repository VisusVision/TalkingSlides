import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  jsonResponse,
  mockCommonAppChromeApi,
  seedAuthenticatedSession,
} from './support/apiMocks.js';

const USER = {
  id: 42,
  username: 'avatar.teacher',
  first_name: 'Avatar',
  last_name: 'Teacher',
  role: 'teacher',
  profile: { role: 'teacher', display_name: 'Avatar Teacher' },
};

const CAPABILITIES = {
  features: {
    avatar: { enabled: true, status: 'enabled' },
    intelligence: { enabled: true },
    local_tts: { enabled: true },
    visual_moderation: { enabled: false },
  },
};

const AVATAR_PROFILE = {
  profile: {
    avatar_enabled: false,
    avatar_consent_confirmed: false,
  },
  avatar_setup_status: {
    state: 'missing_consent',
    message: 'Confirm avatar consent.',
    checklist: {
      portrait_uploaded: false,
      voice_uploaded: true,
      consent_confirmed: false,
      avatar_generation_enabled: false,
      avatar_prepared: false,
    },
    can_prepare: false,
    can_generate_preview: false,
  },
};

test('avatar navigation opens a working real-person-only setup', async ({ page }) => {
  const expectNoBrowserErrors = collectBrowserErrors(page);
  let uploadBody = '';

  await mockCommonAppChromeApi(page, {
    user: USER,
    capabilities: CAPABILITIES,
    categories: [],
  });
  await page.route('**/api/v1/users/42/avatar/', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill(jsonResponse(AVATAR_PROFILE));
      return;
    }
    if (route.request().method() === 'POST') {
      uploadBody = route.request().postData() || '';
      await route.fulfill(jsonResponse({ status: 'ready', warnings: [] }));
      return;
    }
    throw new Error(`Unexpected avatar request: ${route.request().method()}`);
  });
  await seedAuthenticatedSession(page, { token: 'avatar-page-token', user: USER });

  await page.goto('/avatar');

  await expect(
    page.getByRole('navigation', { name: 'Primary sidebar navigation' }).getByRole('link', { name: 'Avatar' }),
  ).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Create your first avatar' })).toBeVisible();
  await expect(page.getByText('Clone a real person')).toBeVisible();
  await expect(page.getByText('Create a virtual character')).toHaveCount(0);

  await page.getByRole('button', { name: 'New Avatar' }).click();
  const wizard = page.getByTestId('avatar-capture-wizard');
  await expect(wizard).toBeVisible();
  await wizard.getByRole('tab', { name: 'Import media' }).click();
  await wizard.locator('input[type="file"][accept="image/*,video/*"]').setInputFiles({
    name: 'teacher-portrait.png',
    mimeType: 'image/png',
    buffer: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII=',
      'base64',
    ),
  });
  await wizard.getByRole('button', { name: 'Continue' }).click();
  await page.getByLabel('Avatar name').fill('Science Teacher');
  await page.getByRole('button', { name: 'Choose voice' }).click();
  await page.getByLabel(/Use a voice from My voices/).check();
  await page.getByLabel(/I confirm that I have permission to create and use this avatar/).check();
  await page.getByRole('button', { name: 'Create avatar profile' }).click();

  await expect.poll(() => uploadBody).toContain('Science Teacher');
  expect(uploadBody).toContain('avatar_name');
  expect(uploadBody).toContain('avatar_voice_source');
  expect(uploadBody).toContain('existing');
  await expect(wizard).toHaveCount(0);
  await expect(page.getByText('Avatar settings saved.')).toBeVisible();
  expectNoBrowserErrors();
});
