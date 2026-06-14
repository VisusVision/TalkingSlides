import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  adminApproveLesson: vi.fn(),
  adminBlockLesson: vi.fn(),
  adminRequestLessonChanges: vi.fn(),
  approveModerationReviewRequest: vi.fn(),
  fetchAuthenticatedAssetBlobUrl: vi.fn(),
  getModerationReviewRequest: vi.fn(),
  listModerationReviewRequests: vi.fn(),
  rejectModerationReviewRequest: vi.fn(),
  runAdminProjectModerationAction: vi.fn(),
  runModerationReportAction: vi.fn(),
}));

vi.mock('../api', () => ({
  adminApproveLesson: mocks.adminApproveLesson,
  adminBlockLesson: mocks.adminBlockLesson,
  adminRequestLessonChanges: mocks.adminRequestLessonChanges,
  approveModerationReviewRequest: mocks.approveModerationReviewRequest,
  fetchAuthenticatedAssetBlobUrl: mocks.fetchAuthenticatedAssetBlobUrl,
  getModerationReviewRequest: mocks.getModerationReviewRequest,
  listModerationReviewRequests: mocks.listModerationReviewRequests,
  rejectModerationReviewRequest: mocks.rejectModerationReviewRequest,
  runAdminProjectModerationAction: mocks.runAdminProjectModerationAction,
  runModerationReportAction: mocks.runModerationReportAction,
}));

vi.mock('../lib/capabilities', () => ({
  featureEnabled: () => true,
  useCapabilities: () => ({ capabilities: { features: { visual_moderation: { enabled: true } } } }),
}));

import ModerationDashboard from './ModerationDashboard';

function reviewItem(id, overrides = {}) {
  return {
    id,
    source_type: 'review_request',
    source: 'admin_review',
    allowed_actions: ['approve', 'reject_block', 'request_changes', 'rescan', 'view_review'],
    project_id: 1000 + Number(id),
    project_title: `Moderation item ${id}`,
    publisher_username: `publisher${id}`,
    requested_by_username: `publisher${id}`,
    status: 'open',
    moderation_status: 'needs_admin_review',
    publisher_message: 'Please review this lesson.',
    latest_message: 'Please review this lesson.',
    highest_severity: 'medium',
    highest_category: 'violence',
    latest_findings_summary: [
      {
        id: `finding-${id}`,
        category: 'violence',
        severity: 'medium',
        reason_title: 'Violence',
        admin_reason_message: 'Moderation finding requires review.',
        asset_label: 'Project',
      },
    ],
    visual_issues: [],
    finding_badges: [],
    created_at: '2026-01-01T00:00:00Z',
    item_time: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function page(results, overrides = {}) {
  return {
    count: results.length,
    total: results.length,
    limit: 25,
    offset: 0,
    has_more: false,
    next_offset: null,
    results,
    ...overrides,
  };
}

async function renderDashboard(props = {}, { route = '/' } = {}) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[route]}>
        <ModerationDashboard {...props} />
      </MemoryRouter>,
    );
  });
  await act(async () => {});
  return {
    host,
    root,
    rerender: async (nextProps = {}) => {
      await act(async () => {
        root.render(
          <MemoryRouter initialEntries={[route]}>
            <ModerationDashboard {...nextProps} />
          </MemoryRouter>,
        );
      });
      await act(async () => {});
    },
  };
}

function clickByText(host, text) {
  const button = [...host.querySelectorAll('button')].find((node) => node.textContent.includes(text));
  expect(button).toBeTruthy();
  act(() => {
    button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  return button;
}

describe('ModerationDashboard pagination and server search', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    vi.useRealTimers();
    vi.clearAllMocks();
    window.sessionStorage.clear();
    mocks.getModerationReviewRequest.mockResolvedValue({});
    mocks.fetchAuthenticatedAssetBlobUrl.mockResolvedValue('blob:preview');
  });

  it('keeps tabs and the active filter button in one action row without a duplicate summary', async () => {
    mocks.listModerationReviewRequests.mockResolvedValue(page([reviewItem(21)]));

    const { host, root } = await renderDashboard(
      {},
      { route: '/moderation?tab=open&filter=manually_blocked' },
    );

    const actionRow = host.querySelector('[data-testid="moderation-filter-row"]');
    expect(actionRow).toBeTruthy();
    const rowButtons = [...actionRow.querySelectorAll('button')].map((button) => button.textContent);

    expect(rowButtons.some((text) => text.includes('Open'))).toBe(true);
    expect(rowButtons.some((text) => text.includes('History'))).toBe(true);
    expect(rowButtons.some((text) => text.includes('Manually blocked'))).toBe(true);
    expect(host.textContent).not.toContain('Filter: Manually blocked');
    expect(actionRow.querySelector('button[aria-label="Filter moderation queue: Manually blocked"]')).toBeTruthy();
    expect(mocks.listModerationReviewRequests).toHaveBeenLastCalledWith(expect.objectContaining({
      tab: 'open',
      filter: 'manually_blocked',
      offset: 0,
    }));

    await act(async () => root.unmount());
    host.remove();
  });

  it('updates backend results when selecting a filter from the action row', async () => {
    mocks.listModerationReviewRequests
      .mockResolvedValueOnce(page([reviewItem(1)]))
      .mockResolvedValueOnce(page([reviewItem(2, { project_title: 'Blocked lesson' })]));

    const { host, root } = await renderDashboard();

    clickByText(host, 'Filters');
    clickByText(host, 'Manually blocked');
    await act(async () => {});

    expect(mocks.listModerationReviewRequests).toHaveBeenLastCalledWith(expect.objectContaining({
      tab: 'open',
      filter: 'manually_blocked',
      offset: 0,
    }));
    expect(host.textContent).toContain('Blocked lesson');
    expect(host.textContent).toContain('Manually blocked');
    expect(host.textContent).not.toContain('Filter: Manually blocked');

    await act(async () => root.unmount());
    host.remove();
  });

  it('keeps Open and History tab actions wired to moderation requests', async () => {
    mocks.listModerationReviewRequests
      .mockResolvedValueOnce(page([reviewItem(1)]))
      .mockResolvedValueOnce(page([reviewItem(2, { status: 'approved' })]))
      .mockResolvedValueOnce(page([reviewItem(3)]));

    const { host, root } = await renderDashboard();

    clickByText(host, 'History');
    await act(async () => {});

    expect(mocks.listModerationReviewRequests).toHaveBeenLastCalledWith(expect.objectContaining({
      tab: 'history',
      filter: 'all',
      offset: 0,
    }));

    clickByText(host, 'Open');
    await act(async () => {});

    expect(mocks.listModerationReviewRequests).toHaveBeenLastCalledWith(expect.objectContaining({
      tab: 'open',
      filter: 'all',
      offset: 0,
    }));

    await act(async () => root.unmount());
    host.remove();
  });

  it('loads more results and appends without duplicate cards', async () => {
    mocks.listModerationReviewRequests
      .mockResolvedValueOnce(page([reviewItem(1), reviewItem(2)], {
        count: 4,
        total: 4,
        limit: 2,
        has_more: true,
        next_offset: 2,
      }))
      .mockResolvedValueOnce(page([reviewItem(2), reviewItem(3)], {
        count: 4,
        total: 4,
        limit: 2,
        offset: 2,
        has_more: false,
      }));

    const { host, root } = await renderDashboard();
    expect(host.textContent).toContain('Moderation item 1');
    expect(host.textContent).toContain('Moderation item 2');

    clickByText(host, 'Load more');
    await act(async () => {});

    expect(host.textContent).toContain('Moderation item 3');
    expect([...host.querySelectorAll('article')]).toHaveLength(3);
    expect(mocks.listModerationReviewRequests).toHaveBeenLastCalledWith(expect.objectContaining({
      limit: 25,
      offset: 2,
      tab: 'open',
      filter: 'all',
    }));

    await act(async () => root.unmount());
    host.remove();
  });

  it('debounces search and sends it to the backend from offset zero', async () => {
    vi.useFakeTimers();
    mocks.listModerationReviewRequests
      .mockResolvedValueOnce(page([reviewItem(1)]))
      .mockResolvedValueOnce(page([reviewItem(9, { project_title: 'Ancient item' })]));

    const { host, root, rerender } = await renderDashboard({ searchQuery: '' });
    expect(mocks.listModerationReviewRequests).toHaveBeenCalledTimes(1);

    await rerender({ searchQuery: 'Ancient' });
    expect(mocks.listModerationReviewRequests).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    await act(async () => {});

    expect(mocks.listModerationReviewRequests).toHaveBeenLastCalledWith(expect.objectContaining({
      q: 'Ancient',
      offset: 0,
      tab: 'open',
      filter: 'all',
    }));
    expect(host.textContent).toContain('Ancient item');

    await act(async () => root.unmount());
    host.remove();
    vi.useRealTimers();
  });

  it('refreshes the current query after approve without clearing search', async () => {
    mocks.listModerationReviewRequests
      .mockResolvedValueOnce(page([reviewItem(5, { project_title: 'Needle lesson' })]))
      .mockResolvedValueOnce(page([reviewItem(5, { project_title: 'Needle lesson', status: 'approved' })]));
    mocks.approveModerationReviewRequest.mockResolvedValue(reviewItem(5, { status: 'approved' }));

    const { host, root } = await renderDashboard({ searchQuery: 'needle' });

    clickByText(host, 'Approve');
    await act(async () => {});

    expect(mocks.approveModerationReviewRequest).toHaveBeenCalledWith(5, '');
    expect(mocks.listModerationReviewRequests).toHaveBeenLastCalledWith(expect.objectContaining({
      q: 'needle',
      offset: 0,
      tab: 'open',
      filter: 'all',
    }));
    expect(host.textContent).toContain('Needle lesson');

    await act(async () => root.unmount());
    host.remove();
  });

  it('keeps detail foldouts and visual previews working', async () => {
    const detailIssue = {
      id: 'visual-1',
      category: 'violence',
      severity: 'high',
      content_type: 'image',
      asset_kind: 'cover',
      asset_label: 'Lesson cover',
      reason_title: 'Graphic content',
      admin_reason_message: 'Review the cover image.',
      preview_url: '/api/v1/projects/1/moderation-preview/1/',
    };
    mocks.listModerationReviewRequests.mockResolvedValue(page([reviewItem(7)]));
    mocks.getModerationReviewRequest.mockResolvedValue({
      ...reviewItem(7),
      visual_issues: [detailIssue],
      findings: [detailIssue],
    });

    const { host, root } = await renderDashboard();

    clickByText(host, 'View details');
    await act(async () => {});
    await act(async () => {});

    expect(mocks.getModerationReviewRequest).toHaveBeenCalledWith(7);
    expect(mocks.fetchAuthenticatedAssetBlobUrl).toHaveBeenCalledWith('/api/v1/projects/1/moderation-preview/1/');
    expect(host.querySelector('[data-preview-fetch-status="200"]')).toBeTruthy();
    expect(host.textContent).toContain('Findings');

    await act(async () => root.unmount());
    host.remove();
  });

  it('links moderation rows to the exact read-only Studio review target', async () => {
    mocks.listModerationReviewRequests.mockResolvedValue(page([reviewItem(8)]));

    const { host, root } = await renderDashboard();
    const reviewLink = [...host.querySelectorAll('a')]
      .find((node) => node.textContent.includes('Open in read-only Studio'));

    expect(reviewLink).toBeTruthy();
    const href = reviewLink.getAttribute('href');
    expect(href).toContain('/studio?');
    expect(href).toContain('mode=review');
    expect(href).toContain('view=editor');
    expect(href).toContain('lesson=1008');
    expect(href).toContain('review=8');
    expect(decodeURIComponent(href)).toContain('returnTo=/');

    await act(async () => root.unmount());
    host.remove();
  });
});
