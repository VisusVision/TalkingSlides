import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  analyzeMyAnalyticsIntelligence: vi.fn(),
  createProject: vi.fn(),
  fetchCategories: vi.fn(),
  fetchMyAnalytics: vi.fn(),
  fetchMyAnalyticsIntelligence: vi.fn(),
}));

vi.mock('../api', () => ({
  analyzeMyAnalyticsIntelligence: apiMocks.analyzeMyAnalyticsIntelligence,
  createProject: apiMocks.createProject,
  fetchCategories: apiMocks.fetchCategories,
  fetchMyAnalytics: apiMocks.fetchMyAnalytics,
  fetchMyAnalyticsIntelligence: apiMocks.fetchMyAnalyticsIntelligence,
}));

vi.mock('../components/ui/PageLoading', () => ({
  usePageLoading: () => {},
}));

vi.mock('../lib/capabilities', async () => {
  const actual = await vi.importActual('../lib/capabilities');
  return {
    ...actual,
    useCapabilities: () => ({
      capabilities: {
        features: {
          avatar: { enabled: false },
          intelligence: { enabled: false },
          local_ollama: { enabled: false },
          visual_moderation: { enabled: true },
          local_tts: { enabled: true },
        },
      },
    }),
  };
});

import Analytics from './Analytics';

const publisherUser = {
  id: 7,
  username: 'publisher',
  profile: { role: 'publisher' },
};

function activityPayload({ categories = [], recentActivities = [] } = {}) {
  return {
    summary: {
      total_lessons: 12,
      published_lessons: 10,
      draft_lessons: 2,
      total_views: 640,
      unique_viewers: 80,
      estimated_watch_time_minutes: 1800,
      completion_rate: 62,
      average_progress: 74,
      engagement_events: 320,
      likes: 45,
      comments: 12,
    },
    charts: {
      engagement_trend: Array.from({ length: 14 }, (_, index) => ({
        date: `2026-06-${String(index + 1).padStart(2, '0')}`,
        total_views: (index + 1) * 4,
        engagement_events: (index + 1) * 2,
      })),
      category_breakdown: categories,
    },
    tables: {
      top_lessons: [],
      recent_lessons: [],
      recent_activity: recentActivities,
    },
    options: {
      categories: categories.map((category) => ({
        slug: category.category_slug,
        name: category.category_name,
      })),
    },
  };
}

function emptyPayload() {
  return {
    summary: {
      total_lessons: 0,
      published_lessons: 0,
      draft_lessons: 0,
      total_views: 0,
      unique_viewers: 0,
      estimated_watch_time_minutes: 0,
      completion_rate: 0,
      average_progress: 0,
      engagement_events: 0,
      likes: 0,
      comments: 0,
    },
    charts: {
      engagement_trend: [{ date: '2026-06-01', total_views: 0, engagement_events: 0 }],
      category_breakdown: [],
    },
    tables: {
      top_lessons: [],
      recent_lessons: [],
      recent_activity: [],
    },
    options: { categories: [] },
  };
}

async function renderAnalytics(payload) {
  apiMocks.fetchMyAnalytics.mockResolvedValue(payload);
  apiMocks.fetchCategories.mockResolvedValue([]);

  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/analytics']}>
        <Analytics user={publisherUser} />
      </MemoryRouter>,
    );
  });
  await act(async () => {});
  await act(async () => {});

  return { host, root };
}

describe('Analytics adaptive layout states', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    vi.clearAllMocks();
    window.sessionStorage.clear();
  });

  it('keeps the chart and many category rows visible together', async () => {
    const categories = Array.from({ length: 8 }, (_, index) => ({
      category_slug: `category-${index + 1}`,
      category_name: `Category ${index + 1}`,
      views: 200 - index * 10,
      engagement_events: 90 - index * 5,
      lesson_count: index + 1,
      completion_rate: 60 - index,
    }));

    const { host, root } = await renderAnalytics(activityPayload({ categories }));

    expect(host.textContent).toContain('Views over time');
    expect(host.querySelector('[data-testid="analytics-chart-body"]')).toBeTruthy();
    const categoryList = host.querySelector('[data-testid="analytics-category-list"]');
    expect(categoryList).toBeTruthy();
    expect(categoryList.className).toContain('min-h-0');
    expect(categoryList.className).toContain('flex-1');
    expect(categoryList.className).toContain('overflow-y-auto');
    expect(host.querySelectorAll('[data-testid="analytics-category-row"]')).toHaveLength(8);
    expect(host.textContent).toContain('Category 8');
    expect(apiMocks.fetchMyAnalyticsIntelligence).not.toHaveBeenCalled();

    await act(async () => root.unmount());
    host.remove();
  });

  it('keeps expanded recent activity inside a scrollable card body', async () => {
    const recentActivities = Array.from({ length: 12 }, (_, index) => ({
      type: index % 3 === 0 ? 'like' : 'progress',
      lesson_id: index + 1,
      lesson_title: `Recent activity lesson ${index + 1}`,
      value: 45 + index,
      timestamp: `2026-06-${String(index + 1).padStart(2, '0')}T10:00:00Z`,
    }));

    const { host, root } = await renderAnalytics(activityPayload({ recentActivities }));

    const card = host.querySelector('[data-testid="analytics-recent-activity-card"]');
    const list = host.querySelector('[data-testid="analytics-recent-activity-list"]');
    expect(card).toBeTruthy();
    expect(card.className).toContain('max-h-[34rem]');
    expect(card.className).toContain('overflow-hidden');
    expect(list).toBeTruthy();
    expect(list.className).toContain('min-h-0');
    expect(list.className).toContain('flex-1');
    expect(list.className).toContain('overflow-y-auto');
    expect(host.querySelectorAll('[data-testid="analytics-recent-activity-row"]')).toHaveLength(3);

    const expandButton = [...host.querySelectorAll('button')].find((button) => button.textContent.includes('Show 9 more'));
    expect(expandButton).toBeTruthy();
    await act(async () => {
      expandButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(host.querySelectorAll('[data-testid="analytics-recent-activity-row"]')).toHaveLength(12);
    expect(host.textContent).toContain('Recent activity lesson 12');
    expect(host.querySelector('[data-testid="analytics-recent-activity-list"]').className).toContain('overflow-y-auto');

    await act(async () => root.unmount());
    host.remove();
  });

  it('renders balanced no-data chart and category empty states', async () => {
    const { host, root } = await renderAnalytics(emptyPayload());

    expect(host.textContent).toContain('No analytics yet');
    expect(host.textContent).toContain('No recorded activity in this range.');
    expect(host.textContent).toContain('Category breakdown will appear once lessons collect activity.');
    expect(host.textContent).toContain('Activity will appear here after viewers like, comment, or make progress on your lessons.');
    expect(host.querySelector('[data-testid="analytics-chart-body"]')).toBeNull();

    await act(async () => root.unmount());
    host.remove();
  });
});
