import { expect, test } from '@playwright/test';

const CAPABILITIES_PAYLOAD = {
  features: {
    avatar: false,
    google_auth: false,
    moderation: false,
    tts_preview: false,
  },
};

function jsonResponse(payload, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  };
}

async function mockPublicApi(page) {
  await page.route('**/api/v1/capabilities/**', (route) => route.fulfill(jsonResponse(CAPABILITIES_PAYLOAD)));
  await page.route('**/api/v1/help/**', (route) => route.fulfill(jsonResponse({
    title: 'Help and Support',
    body: 'Public support guidance for browser smoke coverage.',
    contact_email: 'support@example.test',
    contact_phone: '',
    company_name: 'VISUS VidLab',
    company_address: '',
    support_url: 'https://example.test/support',
    is_default: false,
  })));
  await page.route('**/api/v1/categories/**', (route) => route.fulfill(jsonResponse([
    { id: 1, name: 'Artificial Intelligence', slug: 'artificial-intelligence' },
  ])));
  await page.route('**/api/v1/catalog/feed/**', (route) => route.fulfill(jsonResponse({
    sections: [],
    results: [],
  })));
  await page.route('**/api/v1/catalog/**', (route) => route.fulfill(jsonResponse([])));
}

async function setupPublicSmoke(page) {
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

  await mockPublicApi(page);

  await page.addInitScript(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  return () => {
    expect(consoleErrors, 'browser console errors').toEqual([]);
    expect(pageErrors, 'page errors').toEqual([]);
  };
}

test('renders the public help route without auth', async ({ page }) => {
  const expectNoBrowserErrors = await setupPublicSmoke(page);

  await page.goto('/help');

  await expect(page.getByRole('heading', { name: 'Help and Support' })).toBeVisible();
  await expect(page.getByText('Public support guidance for browser smoke coverage.')).toBeVisible();
  await expect(page.getByText('support@example.test')).toBeVisible();
  await expect(page.getByRole('main').getByText('VISUS VidLab')).toBeVisible();
  expectNoBrowserErrors();
});

test('renders the public browse route with an empty catalog', async ({ page }) => {
  const expectNoBrowserErrors = await setupPublicSmoke(page);

  await page.goto('/browse');

  await expect(page.getByRole('heading', { name: 'Browse The Catalog' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'All' })).toBeVisible();
  await expect(page.getByText('No lessons found')).toBeVisible();
  expectNoBrowserErrors();
});
