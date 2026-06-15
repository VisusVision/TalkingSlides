import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  jsonResponse,
  mockCommonAppChromeApi,
  seedAuthenticatedSession,
} from './support/apiMocks.js';

const AUTH_USER = {
  id: 42,
  username: 'release.teacher',
  display_name: 'Release Teacher',
  role: 'teacher',
  auth_provider: 'password',
};

const CREATED_PROJECT_ID = 501;

const CAPABILITIES_PAYLOAD = {
  features: {
    avatar: false,
    google_auth: false,
    moderation: true,
    tts_preview: false,
    visual_moderation: false,
  },
};

function projectPayload(overrides = {}) {
  return {
    id: CREATED_PROJECT_ID,
    title: 'Release Gate Lesson',
    description: 'Mocked lesson used by the authenticated release gate.',
    status: 'done',
    latest_job: {
      id: 9001,
      status: 'done',
    },
    is_published: true,
    owner_id: AUTH_USER.id,
    teacher: AUTH_USER,
    user: AUTH_USER,
    category_name: 'Release QA',
    duration_minutes: 3,
    moderation_status: 'approved',
    manual_moderation_status: '',
    stream_url: '',
    video_url: '',
    vtt_url: '/media/subtitles/release-gate-original.vtt',
    subtitle_vtt_url: '/media/subtitles/release-gate-original.vtt',
    avatar_overlay: null,
    avatar_visible: false,
    protection_mode: 'public',
    ...overrides,
  };
}

const TRANSCRIPT_PAYLOAD = {
  pages: [
    {
      id: 1,
      page_number: 1,
      start_sec: 0,
      end_sec: 8,
      text: 'Welcome to the release gate lesson.',
      narration_text: 'Welcome to the release gate lesson.',
    },
    {
      id: 2,
      page_number: 2,
      start_sec: 8,
      end_sec: 16,
      text: 'This transcript confirms the watch sidebar is visible.',
      narration_text: 'This transcript confirms the watch sidebar is visible.',
    },
  ],
};

const SUBTITLE_BUNDLE = {
  tracks: [
    {
      id: 'original',
      language_code: 'original',
      language_label: 'Original',
      is_original: true,
      status: 'ready',
      vtt_url: '/media/subtitles/release-gate-original.vtt',
    },
  ],
  requestable_languages: [
    { language_code: 'es', language_label: 'Spanish' },
  ],
};

async function mockAuthenticatedReleaseGateApi(page) {
  let created = false;

  await mockCommonAppChromeApi(page, {
    user: AUTH_USER,
    capabilities: CAPABILITIES_PAYLOAD,
    categories: [
      { id: 1, name: 'Release QA', slug: 'release-qa' },
    ],
    unreadCount: 0,
  });

  await page.route(/\/api\/v1\/projects\/(?:\?.*)?$/, async (route) => {
    if (route.request().method() === 'POST') {
      created = true;
      return route.fulfill(jsonResponse({
        id: 9001,
        project_id: CREATED_PROJECT_ID,
        status: 'done',
        project: projectPayload(),
      }, 201));
    }

    return route.fulfill(jsonResponse({
      count: created ? 1 : 0,
      results: created ? [projectPayload()] : [],
      limit: 12,
      offset: 0,
      has_next: false,
      next_offset: null,
    }));
  });

  await page.route(`**/api/v1/projects/${CREATED_PROJECT_ID}/`, (route) => {
    route.fulfill(jsonResponse(projectPayload()));
  });
  await page.route(`**/api/v1/projects/${CREATED_PROJECT_ID}/moderation/**`, (route) => {
    route.fulfill(jsonResponse({
      moderation_status: 'approved',
      can_publish: true,
      message: 'Moderation approved.',
      findings: [],
      moderation_summary: {},
    }));
  });
  await page.route(`**/api/v1/projects/${CREATED_PROJECT_ID}/transcript/**`, (route) => {
    route.fulfill(jsonResponse(TRANSCRIPT_PAYLOAD));
  });
  await page.route(`**/api/v1/projects/${CREATED_PROJECT_ID}/studio-preview-token/**`, (route) => {
    route.fulfill(jsonResponse({
      video_url: '',
      vtt_url: '/media/subtitles/release-gate-original.vtt',
      subtitle_vtt_url: '/media/subtitles/release-gate-original.vtt',
    }));
  });
  await page.route(`**/api/v1/projects/${CREATED_PROJECT_ID}/playback-token/**`, (route) => {
    route.fulfill(jsonResponse({
      video_url: '',
      vtt_url: '/media/subtitles/release-gate-original.vtt',
      subtitle_vtt_url: '/media/subtitles/release-gate-original.vtt',
      protection_mode: 'public',
      playback_status: { protection_mode: 'public' },
      avatar_overlay: null,
    }));
  });
  await page.route(`**/api/v1/projects/${CREATED_PROJECT_ID}/subtitle-tracks/**`, (route) => {
    route.fulfill(jsonResponse(SUBTITLE_BUNDLE));
  });
  await page.route(/\/api\/v1\/catalog\/(?:\?.*)?$/, (route) => {
    route.fulfill(jsonResponse([projectPayload()]));
  });
  await page.route(`**/api/v1/catalog/${CREATED_PROJECT_ID}/`, (route) => {
    route.fulfill(jsonResponse(projectPayload()));
  });
  await page.route(`**/api/v1/catalog/${CREATED_PROJECT_ID}/comments/**`, (route) => {
    route.fulfill(jsonResponse([]));
  });
  await page.route(`**/api/v1/catalog/${CREATED_PROJECT_ID}/playlist-context/**`, (route) => {
    route.fulfill(jsonResponse({ mode: 'publisher', items: [] }));
  });
}

async function setupAuthenticatedSession(page) {
  const expectNoBrowserErrors = collectBrowserErrors(page);

  await mockAuthenticatedReleaseGateApi(page);
  await seedAuthenticatedSession(page, {
    token: 'release-gate-token',
    user: AUTH_USER,
  });

  return expectNoBrowserErrors;
}

test('authenticated Studio to Watch release gate surfaces core flow', async ({ page }) => {
  const expectNoBrowserErrors = await setupAuthenticatedSession(page);

  await page.goto('/studio');

  await expect(page.getByRole('heading', { name: 'Teacher Publishing Console' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Studio Editor' })).toBeVisible();

  await page.getByRole('button', { name: 'Studio Editor' }).click();
  await expect(page.getByText('Editor Workspace')).toBeVisible();

  await page.getByLabel('Lesson title').fill('Release Gate Lesson');
  await page.getByLabel('Category').fill('Release QA');
  await page.locator('input[type="file"][accept*=".txt"]').setInputFiles({
    name: 'release-gate.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('Release gate source material.'),
  });
  await page.getByRole('button', { name: 'Create Lesson Draft' }).click();

  await expect(page.getByRole('button', { name: 'Preview In Watch' }).first()).toBeVisible();
  await expect(page.getByText('Ready').first()).toBeVisible();
  await expect(page.getByText('Moderation: Approved').first()).toBeVisible();
  await expect(page.getByText('Release Gate Lesson').first()).toBeVisible();

  await page.goto(`/watch?lesson=${CREATED_PROJECT_ID}`);

  await expect(page.getByRole('heading', { name: 'Study With Focused Context' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Release Gate Lesson' })).toBeVisible();
  await expect(page.getByText('Video source unavailable for this lesson.')).toBeVisible();
  await expect(page.getByText('Secure stream')).toBeVisible();
  await expect(page.getByText('CC', { exact: true })).toBeVisible();
  await expect(page.locator('#watch-subtitle-track')).toBeVisible();
  await expect(page.locator('#watch-subtitle-track')).toContainText('Original');
  await expect(page.getByText('Welcome to the release gate lesson.').first()).toBeVisible();

  expectNoBrowserErrors();
});
