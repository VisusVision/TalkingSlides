import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  jsonResponse,
  mockCommonAppChromeApi,
  seedAuthenticatedSession,
} from './support/apiMocks.js';

const AUTH_USER = {
  id: 99,
  username: 'analytics.teacher',
  display_name: 'Analytics Teacher',
  first_name: 'Analytics',
  last_name: 'Teacher',
  role: 'teacher',
  auth_provider: 'password',
  profile: {
    role: 'teacher',
    display_name: 'Analytics Teacher',
  },
};

const CAPABILITIES_PAYLOAD = {
  features: {
    avatar: { enabled: false },
    google_auth: { enabled: false },
    intelligence: { enabled: false },
    moderation: { enabled: false },
    tts_preview: { enabled: false },
    visual_moderation: { enabled: false },
  },
};

const CATEGORIES_PAYLOAD = [
  { id: 1, name: 'Frontend QA', slug: 'frontend-qa' },
  { id: 2, name: 'Product Training', slug: 'product-training' },
];

const ANALYTICS_PAYLOAD = {
  summary: {
    total_lessons: 4,
    published_lessons: 3,
    draft_lessons: 1,
    total_views: 1280,
    unique_viewers: 240,
    estimated_watch_time_minutes: 3600,
    completion_rate: 72,
    average_progress: 81,
    engagement_events: 96,
    likes: 32,
    comments: 12,
    trends: {
      total_views_pct: 12.5,
      watch_time_pct: 8,
      completion_rate_pct: 3,
      engagement_events_pct: 5,
    },
  },
  charts: {
    views_over_time: [
      { date: '2026-06-01', views: 120 },
      { date: '2026-06-02', views: 180 },
      { date: '2026-06-03', views: 260 },
    ],
    category_breakdown: [
      {
        category_name: 'Frontend QA',
        category_slug: 'frontend-qa',
        views: 760,
        engagement_events: 58,
        lesson_count: 2,
        completion_rate: 78,
      },
    ],
  },
  tables: {
    top_lessons: [
      {
        id: 7001,
        title: 'Analytics Smoke Lesson',
        views: 640,
        engagement_events: 48,
        likes: 18,
        comments: 7,
        completion_rate: 74,
        average_progress: 82,
      },
    ],
    recent_lessons: [
      {
        id: 7002,
        title: 'Recent Analytics Lesson',
        views: 320,
        engagement_events: 24,
        likes: 9,
        comments: 3,
        completion_rate: 69,
        average_progress: 77,
        updated_at: '2026-06-02T12:00:00Z',
      },
    ],
    recent_activity: [
      {
        type: 'progress',
        lesson_id: 7001,
        lesson_title: 'Analytics Smoke Lesson',
        value: 82,
        timestamp: '2026-06-03T14:00:00Z',
      },
    ],
  },
  options: {
    categories: CATEGORIES_PAYLOAD,
  },
  meta: {
    estimated_metrics: true,
  },
};

async function mockAuthenticatedAnalyticsApi(page) {
  await mockCommonAppChromeApi(page, {
    user: AUTH_USER,
    capabilities: CAPABILITIES_PAYLOAD,
    categories: CATEGORIES_PAYLOAD,
    unreadCount: 0,
  });

  await page.route('**/api/v1/me/analytics/?**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse(ANALYTICS_PAYLOAD));
  });
}

async function setupAuthenticatedAnalyticsSmoke(page) {
  const expectNoBrowserErrors = collectBrowserErrors(page);

  await mockAuthenticatedAnalyticsApi(page);
  await seedAuthenticatedSession(page, {
    token: 'analytics-smoke-token',
    user: AUTH_USER,
  });

  return expectNoBrowserErrors;
}

test('authenticated Analytics renders mocked dashboard metrics', async ({ page }) => {
  const expectNoBrowserErrors = await setupAuthenticatedAnalyticsSmoke(page);

  await page.goto('/analytics');

  const main = page.getByRole('main');
  const sectionByHeading = (name) => main
    .locator('section')
    .filter({ has: page.getByRole('heading', { name, exact: true }) })
    .last();
  const metricCard = (label, value) => main
    .locator('section')
    .filter({ has: page.getByText(label, { exact: true }) })
    .filter({ has: page.getByText(value, { exact: true }) })
    .last();

  await expect(main.getByRole('heading', { name: 'Performance Overview', exact: true })).toBeVisible();
  await expect(main.getByText('Real engagement signals from lesson progress, likes, and comments.', { exact: true })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Last 7 days', exact: true })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Refresh', exact: true })).toBeVisible();

  await expect(metricCard('Total Views', '1.3K')).toBeVisible();
  await expect(metricCard('Watch Time', '60 hrs')).toBeVisible();
  await expect(metricCard('Completion Rate', '72%')).toBeVisible();
  await expect(metricCard('Engagement Events', '96')).toBeVisible();

  const viewsOverTimeSection = sectionByHeading('Views over time');
  await expect(viewsOverTimeSection).toBeVisible();
  await expect(viewsOverTimeSection.getByText('260 views', { exact: true })).toBeVisible();

  await expect(sectionByHeading('Category Breakdown')).toBeVisible();

  const topLessonsSection = sectionByHeading('Top Lessons');
  await expect(topLessonsSection).toBeVisible();
  await expect(topLessonsSection.getByText('Analytics Smoke Lesson', { exact: true })).toBeVisible();

  const recentActivitySection = sectionByHeading('Recent Activity');
  await expect(recentActivitySection).toBeVisible();
  await expect(recentActivitySection.getByText('A learner reached 82% progress', { exact: true })).toBeVisible();

  const recentLessonsSection = sectionByHeading('Recent Lessons');
  await expect(recentLessonsSection).toBeVisible();
  await expect(recentLessonsSection.getByText('Recent Analytics Lesson', { exact: true })).toBeVisible();

  expectNoBrowserErrors();
});
