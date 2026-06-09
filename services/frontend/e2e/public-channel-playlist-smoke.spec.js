import { expect, test } from '@playwright/test';
import { collectBrowserErrors, jsonResponse } from './support/apiMocks.js';

const CAPABILITIES_PAYLOAD = {
  features: {
    avatar: { enabled: false },
    google_auth: { enabled: false, redirect_flow_enabled: false },
    moderation: { enabled: false },
    tts_preview: { enabled: false },
    visual_moderation: { enabled: false },
  },
};

const CHANNEL_PROFILE = {
  id: 42,
  username: 'channel.academy',
  display_name: 'Channel Academy',
  bio: 'Public lessons for algebra, geometry, and exam prep.',
  website_url: 'https://example.test/channel-academy',
  contact_email: 'hello@example.test',
  social_links: {
    youtube: 'https://youtube.com/@channelacademy',
  },
  is_public_profile: true,
  profile_private: false,
  follower_count: 128,
  lesson_count: 2,
  stats: {
    total_likes: 34,
  },
};

const CHANNEL_LESSONS = {
  results: [
    {
      id: 701,
      title: 'Quadratic Functions Field Guide',
      description: 'A concise walkthrough of graph shape, roots, and vertex form.',
      teacher_name: 'Channel Academy',
      teacher_id: 42,
      teacher_username: 'channel.academy',
      category_name: 'Algebra',
      duration_minutes: 12,
      view_count: 2400,
      created_at: '2026-05-21T10:00:00Z',
    },
    {
      id: 702,
      title: 'Geometry Proof Warmup',
      description: 'Practice the structure of a two-column proof.',
      teacher_name: 'Channel Academy',
      teacher_id: 42,
      teacher_username: 'channel.academy',
      category_name: 'Geometry',
      duration_minutes: 9,
      view_count: 120,
      created_at: '2026-05-18T10:00:00Z',
    },
  ],
};

const CHANNEL_PLAYLISTS = {
  results: [
    {
      id: 901,
      title: 'Exam Prep Sequence',
      description: 'A public sequence for final review.',
      item_count: 2,
      items: [
        {
          project: {
            id: 701,
            title: 'Quadratic Functions Field Guide',
            teacher_name: 'Channel Academy',
            category_name: 'Algebra',
          },
        },
      ],
    },
  ],
};

const PLAYLIST_DETAIL = {
  id: 901,
  title: 'Exam Prep Sequence',
  description: 'A public sequence for final review.',
  is_public: true,
  publisher_id: 42,
  publisher_name: 'Channel Academy',
  publisher_username: 'channel.academy',
  item_count: 2,
  save_count: 7,
  is_saved: false,
  items: [
    {
      project: {
        id: 701,
        title: 'Quadratic Functions Field Guide',
        description: 'A concise walkthrough of graph shape, roots, and vertex form.',
        teacher_name: 'Channel Academy',
        teacher_id: 42,
        teacher_username: 'channel.academy',
        category_name: 'Algebra',
        duration_minutes: 12,
        created_at: '2026-05-21T10:00:00Z',
      },
    },
    {
      project: {
        id: 702,
        title: 'Geometry Proof Warmup',
        description: 'Practice the structure of a two-column proof.',
        teacher_name: 'Channel Academy',
        teacher_id: 42,
        teacher_username: 'channel.academy',
        category_name: 'Geometry',
        duration_minutes: 9,
        created_at: '2026-05-18T10:00:00Z',
      },
    },
  ],
};

async function mockPublicDiscoveryApi(page) {
  await page.route('**/api/v1/capabilities/**', (route) => route.fulfill(jsonResponse(CAPABILITIES_PAYLOAD)));
  await page.route('**/api/v1/categories/**', (route) => route.fulfill(jsonResponse([
    { id: 1, name: 'Algebra', slug: 'algebra' },
    { id: 2, name: 'Geometry', slug: 'geometry' },
  ])));

  await page.route('**/api/v1/users/42/profile/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse(CHANNEL_PROFILE));
  });
  await page.route('**/api/v1/users/42/lessons/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse(CHANNEL_LESSONS));
  });
  await page.route('**/api/v1/users/42/playlists/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse(CHANNEL_PLAYLISTS));
  });
  await page.route('**/api/v1/playlists/901/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse(PLAYLIST_DETAIL));
  });
}

async function setupPublicDiscoverySmoke(page) {
  const expectNoBrowserErrors = collectBrowserErrors(page);

  await mockPublicDiscoveryApi(page);
  await page.addInitScript(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  return expectNoBrowserErrors;
}

test('renders a public channel with lessons and playlists', async ({ page }) => {
  const expectNoBrowserErrors = await setupPublicDiscoverySmoke(page);
  const main = page.getByRole('main');

  await page.goto('/channel/42');

  await expect(main.getByRole('heading', { name: 'Channel Academy' })).toBeVisible();
  await expect(main.getByText('@channel.academy')).toBeVisible();
  await expect(main.getByText('Public lessons for algebra, geometry, and exam prep.')).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Quadratic Functions Field Guide' })).toBeVisible();

  await main.getByRole('button', { name: 'Playlists' }).click();
  await expect(main.getByText('Exam Prep Sequence')).toBeVisible();
  await expect(main.getByText('A public sequence for final review.')).toBeVisible();

  expectNoBrowserErrors();
});

test('renders a public playlist with owner and lesson items', async ({ page }) => {
  const expectNoBrowserErrors = await setupPublicDiscoverySmoke(page);
  const main = page.getByRole('main');

  await page.goto('/playlist/901');

  await expect(main.getByRole('heading', { name: 'Exam Prep Sequence' })).toBeVisible();
  await expect(main.getByText('A public sequence for final review.')).toBeVisible();
  await expect(main.getByRole('link', { name: 'Channel Academy' })).toBeVisible();
  await expect(main.getByText('@channel.academy')).toBeVisible();

  const lessonItems = main.getByRole('link').filter({ hasText: 'Quadratic Functions Field Guide' });
  await expect(lessonItems).toHaveCount(1);
  await expect(lessonItems.first()).toContainText('Algebra');
  await expect(lessonItems.first()).toContainText('12m');

  expectNoBrowserErrors();
});
