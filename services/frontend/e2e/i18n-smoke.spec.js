import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  jsonResponse,
} from './support/apiMocks.js';

const LANGUAGES = ['en', 'tr', 'es', 'fr', 'de', 'it', 'pt', 'ru', 'ja', 'ko', 'zh-CN', 'ar'];

const AUTH_USER = {
  id: 42,
  username: 'i18n.teacher',
  display_name: 'I18n Teacher',
  role: 'teacher',
  auth_provider: 'password',
  profile: {
    display_name: 'I18n Teacher',
    is_public_profile: true,
  },
};

const CAPABILITIES_PAYLOAD = {
  features: {
    avatar: false,
    google_auth: false,
    intelligence: false,
    moderation: true,
    tts_preview: false,
    visual_moderation: false,
  },
};

const LESSON_ID = 501;

const LESSON = {
  id: LESSON_ID,
  title: 'Source Language Lesson',
  description: 'Original lesson copy should remain untranslated.',
  status: 'done',
  latest_job: { id: 9001, status: 'done' },
  is_published: true,
  owner_id: AUTH_USER.id,
  teacher: AUTH_USER,
  user: AUTH_USER,
  teacherName: 'I18n Teacher',
  category_name: 'International QA',
  categoryName: 'International QA',
  duration_seconds: 161,
  duration_minutes: 3,
  durationMinutes: 3,
  views: 1250,
  view_count: 1250,
  moderation_status: 'approved',
  manual_moderation_status: '',
  stream_url: '',
  video_url: '',
  vtt_url: '/media/subtitles/i18n-original.vtt',
  subtitle_vtt_url: '/media/subtitles/i18n-original.vtt',
  avatar_overlay: null,
  avatar_visible: false,
  protection_mode: 'public',
  transcript_pages: [
    {
      id: 1,
      page_number: 1,
      start_sec: 0,
      end_sec: 8,
      text: 'Generated transcript stays in the lesson language.',
      narration_text: 'Generated transcript stays in the lesson language.',
    },
  ],
};

const EMPTY_PAGE = {
  count: 0,
  results: [],
  limit: 12,
  offset: 0,
  has_next: false,
  next_offset: null,
};

function routePayload(url, method) {
  const path = url.pathname;

  if (path.endsWith('/auth/me/')) return AUTH_USER;
  if (path.endsWith('/me/notifications/unread-count/')) return { unread_count: 0 };
  if (path.endsWith('/capabilities/')) return CAPABILITIES_PAYLOAD;
  if (path.endsWith('/categories/')) return [{ id: 1, name: 'International QA', slug: 'international-qa' }];
  if (path.endsWith('/me/profile/')) return AUTH_USER.profile;
  if (path.endsWith(`/users/${AUTH_USER.id}/avatar/`)) return { avatar_enabled: false, avatar_consent_confirmed: false };

  if (path.endsWith('/catalog/feed/')) {
    return {
      sections: [
        { id: 'featured', key: 'featured', title: 'Featured', items: [LESSON] },
      ],
      results: [LESSON],
    };
  }
  if (path.endsWith('/catalog/')) return [LESSON];
  if (path.endsWith(`/catalog/${LESSON_ID}/`)) return LESSON;
  if (path.endsWith(`/catalog/${LESSON_ID}/comments/`)) return [
    { id: 1, display_name: 'Learner One', text: 'User comment stays untranslated.', created_at: '2026-06-22T12:00:00Z' },
  ];
  if (path.endsWith(`/catalog/${LESSON_ID}/playlist-context/`)) return { mode: 'publisher', items: [] };

  if (path.endsWith('/projects/')) {
    if (method === 'POST') return { id: 9001, project_id: LESSON_ID, status: 'done', project: LESSON };
    return { ...EMPTY_PAGE, count: 1, results: [LESSON] };
  }
  if (path.endsWith(`/projects/${LESSON_ID}/`)) return LESSON;
  if (path.endsWith(`/projects/${LESSON_ID}/moderation/`)) {
    return {
      moderation_status: 'approved',
      can_publish: true,
      message: 'Moderation approved.',
      findings: [],
      moderation_summary: {},
    };
  }
  if (path.endsWith(`/projects/${LESSON_ID}/transcript/`)) return { pages: LESSON.transcript_pages };
  if (path.endsWith(`/projects/${LESSON_ID}/intelligence/`)) return { status: 'disabled' };
  if (path.endsWith(`/projects/${LESSON_ID}/studio-preview-token/`)) return LESSON;
  if (path.endsWith(`/projects/${LESSON_ID}/playback-token/`)) {
    return {
      video_url: '',
      vtt_url: LESSON.vtt_url,
      subtitle_vtt_url: LESSON.subtitle_vtt_url,
      protection_mode: 'public',
      playback_status: { protection_mode: 'public' },
      avatar_overlay: null,
    };
  }
  if (path.endsWith(`/projects/${LESSON_ID}/subtitle-tracks/`)) {
    return {
      tracks: [
        {
          id: 'original',
          language_code: 'original',
          language_label: 'Original',
          is_original: true,
          status: 'ready',
          vtt_url: LESSON.vtt_url,
        },
      ],
      requestable_languages: [],
    };
  }

  if (path.endsWith('/me/analytics/')) return { summary: {}, top_lessons: [], recent_activity: [], recent_lessons: [] };
  if (path.endsWith('/me/analytics/intelligence/')) return { status: 'disabled' };

  return {};
}

async function mockI18nSmokeApi(page) {
  await page.route('**/api/v1/**', (route) => {
    const url = new URL(route.request().url());
    route.fulfill(jsonResponse(routePayload(url, route.request().method())));
  });
}

async function seedI18nSmokeSession(page) {
  await page.addInitScript(({ authToken, authUser }) => {
    window.localStorage.setItem('auth_token', authToken);
    window.localStorage.setItem('auth_user', JSON.stringify(authUser));
  }, {
    authToken: 'i18n-smoke-token',
    authUser: AUTH_USER,
  });
}

test('smoke renders core routes for every supported UI language', async ({ page }) => {
  test.setTimeout(120_000);

  const expectNoBrowserErrors = collectBrowserErrors(page);

  await mockI18nSmokeApi(page);
  await seedI18nSmokeSession(page);

  for (const language of LANGUAGES) {
    await page.goto('/');
    await page.locator('select[aria-label]').first().selectOption(language);

    await expect(page.locator('html')).toHaveAttribute('lang', language);
    await expect(page.locator('html')).toHaveAttribute('dir', language === 'ar' ? 'rtl' : 'ltr');
    await expect(page.locator('select[aria-label]').first()).toHaveValue(language);
    await expect(page.getByRole('main')).toBeVisible();
    await expect(page.getByText('Source Language Lesson').first()).toBeVisible();

    await page.goto(`/watch?lesson=${LESSON_ID}`);
    await expect(page.getByRole('main')).toBeVisible();
    await expect(page.getByText('Generated transcript stays in the lesson language.').first()).toBeVisible();
    await expect(page.getByText('User comment stays untranslated.').first()).toBeVisible();

    await page.goto('/studio');
    await expect(page.getByRole('main')).toBeVisible();
    await expect(page.getByText('Source Language Lesson').first()).toBeVisible();

    await page.goto('/settings');
    await expect(page.getByRole('main')).toBeVisible();
    await expect(page.locator('select[aria-label]').first()).toHaveValue(language);
  }

  expectNoBrowserErrors();
});
