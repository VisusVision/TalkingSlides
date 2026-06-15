import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  jsonResponse,
  mockCommonAppChromeApi,
  seedAuthenticatedSession,
} from './support/apiMocks.js';

const AUTH_USER = {
  id: 77,
  username: 'library.learner',
  display_name: 'Library Learner',
  first_name: 'Library',
  last_name: 'Learner',
  role: 'learner',
  auth_provider: 'password',
  profile: {
    role: 'learner',
    display_name: 'Library Learner',
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
    id: 3001,
    progress_pct: 64,
    last_watched_at: '2026-05-20T10:00:00Z',
    lesson: {
      id: 501,
      title: 'Library Smoke History Lesson',
      description: 'A mocked history lesson for authenticated library coverage.',
      teacher_name: 'Library Publisher',
      category_name: 'Frontend QA',
      user_progress: 64,
    },
  },
];

const LIKED_LESSONS_PAYLOAD = [
  {
    id: 3002,
    liked_at: '2026-05-21T10:00:00Z',
    lesson: {
      id: 502,
      title: 'Library Smoke Liked Lesson',
      description: 'A mocked liked lesson for authenticated library coverage.',
      teacher_name: 'Favorite Publisher',
      category_name: 'Frontend QA',
    },
  },
];

const FOLLOWING_PAYLOAD = [
  {
    id: 42,
    username: 'followed.publisher',
    display_name: 'Followed Library Publisher',
    bio: 'Publishes lessons used by the library smoke.',
    follower_count: 12,
    lesson_count: 3,
    latest_lessons: [
      {
        id: 503,
        title: 'Latest Followed Lesson',
        teacher_name: 'Followed Library Publisher',
        category_name: 'Frontend QA',
      },
    ],
  },
];

const SAVED_PLAYLISTS_PAYLOAD = [
  {
    id: 601,
    title: 'Library Smoke Saved Playlist',
    description: 'A mocked saved playlist for authenticated library coverage.',
    publisher_name: 'Playlist Publisher',
    publisher_username: 'playlist.publisher',
    item_count: 2,
    save_count: 5,
    items: [
      {
        project: {
          id: 504,
          title: 'Playlist Lesson One',
          teacher_name: 'Playlist Publisher',
          category_name: 'Frontend QA',
        },
      },
    ],
  },
];

async function mockAuthenticatedLibraryApi(page) {
  await mockCommonAppChromeApi(page, {
    user: AUTH_USER,
    capabilities: CAPABILITIES_PAYLOAD,
    categories: [
      { id: 1, name: 'Frontend QA', slug: 'frontend-qa' },
    ],
    unreadCount: 0,
  });

  await page.route('**/api/v1/me/history/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse(HISTORY_PAYLOAD));
  });

  await page.route('**/api/v1/me/liked-lessons/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse(LIKED_LESSONS_PAYLOAD));
  });

  await page.route('**/api/v1/me/following/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse(FOLLOWING_PAYLOAD));
  });

  await page.route('**/api/v1/me/saved-playlists/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse(SAVED_PLAYLISTS_PAYLOAD));
  });
}

async function setupAuthenticatedLibrarySmoke(page) {
  const expectNoBrowserErrors = collectBrowserErrors(page);

  await mockAuthenticatedLibraryApi(page);
  await seedAuthenticatedSession(page, {
    token: 'library-smoke-token',
    user: AUTH_USER,
  });

  return expectNoBrowserErrors;
}

test('authenticated Library renders mocked learning collections', async ({ page }) => {
  const expectNoBrowserErrors = await setupAuthenticatedLibrarySmoke(page);

  await page.goto('/library');

  await expect(page.getByRole('heading', { name: 'Your Learning Hub' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'History' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Liked Lessons' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Following' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Playlists' })).toBeVisible();

  await expect(page.getByText('Library Smoke History Lesson')).toBeVisible();
  await expect(page.getByText('64% watched')).toBeVisible();
  await expect(page.getByText('Continue from 64%')).toBeVisible();

  await page.getByRole('button', { name: 'Liked Lessons' }).click();
  await expect(page.getByText('Library Smoke Liked Lesson')).toBeVisible();

  await page.getByRole('button', { name: 'Following' }).click();
  await expect(page.getByText('Followed Library Publisher')).toBeVisible();

  await page.getByRole('button', { name: 'Playlists' }).click();
  await expect(page.getByText('Library Smoke Saved Playlist')).toBeVisible();

  expectNoBrowserErrors();
});
