import { expect, test } from '@playwright/test';
import { collectBrowserErrors, jsonResponse, mockCommonAppChromeApi, seedAuthenticatedSession } from './support/apiMocks.js';

const USER = { id: 42, username: 'navigation.teacher', role: 'teacher', is_staff: true, auth_provider: 'password' };
const PAGE_KEY = 'source-0-split-1';
const SOURCE_HASH = `sha256:${'a'.repeat(64)}`;
const PROJECT = {
  id: 501, title: 'Navigation Security Lesson', status: 'done', owner_id: 42,
  teacher: USER, user: USER, latest_job: { id: 9001, status: 'done' },
  latest_render_analysis: {}, is_published: true, moderation_status: 'approved',
  duration_seconds: 8, video_url: '', stream_url: '', avatar_visible: false,
};
const CANDIDATE = {
  id: 'navigation-preview', page_key: PAGE_KEY, strategy: 'typography_only', status: 'ready',
  rationale: 'Preserve authored educational content.', warnings: [], errors: [],
  is_stale: false, compatible: true, applied: false, can_apply: false,
  preview_url: '/private/preview/',
};

async function setupStudio(page, context, baseURL) {
  const expectNoErrors = collectBrowserErrors(page);
  const externalNavigations = [];
  const writes = [];
  const origin = new URL(baseURL).origin;
  // Abort a regression before contacting any external site, including popups.
  await context.route('**/*', (route) => {
    const request = route.request();
    if (request.isNavigationRequest() && new URL(request.url()).origin !== origin) {
      externalNavigations.push(request.url());
      return route.abort();
    }
    return route.continue();
  });
  let generated = false;
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      writes.push({ path, method: request.method(), body: request.postDataJSON() });
    }
    if (path === '/api/v1/projects/501/visual-suggestions/navigation-preview/preview/') {
      return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080"><rect width="1920" height="1080" fill="#f8fafc"/><text x="120" y="200" font-size="60">Öğrenme &amp; kayıt</text></svg>' });
    }
    if (path === '/api/v1/projects/501/visual-suggestions/') {
      expect(new URL(request.url()).searchParams.get('page_key') || request.postDataJSON()?.page_key).toBe(PAGE_KEY);
      if (request.method() === 'POST') {
        expect(request.postDataJSON()).toEqual({ page_key: PAGE_KEY, expected_source_hash: SOURCE_HASH });
        generated = true;
        return route.fulfill(jsonResponse({ suggestion: CANDIDATE }, 201));
      }
      return route.fulfill(jsonResponse({ schema_version: 'visual-suggestion.v1', source: { page_key: PAGE_KEY, source_hash: SOURCE_HASH, source_mode: 'active', supported: true }, suggestions: generated ? [CANDIDATE] : [] }));
    }
    if (path === '/api/v1/projects/') return route.fulfill(jsonResponse({ count: 1, results: [PROJECT], has_next: false }));
    if (path === '/api/v1/projects/501/') return route.fulfill(jsonResponse(PROJECT));
    if (path === '/api/v1/projects/501/transcript/') return route.fulfill(jsonResponse({ pages: [{ id: 7, page_number: 1, page_key: PAGE_KEY, source_slide_index: 0, split_index: 1, start_sec: 0, end_sec: 8, text: 'Öğrenme & kayıt', original_text: 'Öğrenme & kayıt', narration_text: 'Protected teacher narration' }], has_draft: false }));
    if (path === '/api/v1/projects/501/intelligence/') return route.fulfill(jsonResponse({ id: 10, status: 'done', provider: 'heuristic', is_stale: false, summary: 'Existing lesson analysis.' }));
    if (path === '/api/v1/projects/501/moderation/') return route.fulfill(jsonResponse({ moderation_status: 'approved', can_publish: true, findings: [] }));
    if (path === '/api/v1/admin/moderation/review-requests/9/') return route.fulfill(jsonResponse({ id: 9, project_id: 501, project_title: PROJECT.title, status: 'open' }));
    return route.fulfill(jsonResponse({ results: [], count: 0, tracks: [], items: [] }));
  });
  await mockCommonAppChromeApi(page, { user: USER, categories: [], capabilities: { features: { intelligence: { enabled: true }, avatar: { enabled: false }, visual_moderation: { enabled: false }, google_auth: { enabled: false } } } });
  await seedAuthenticatedSession(page, { user: USER, token: 'navigation-test-token' });
  return { expectNoErrors, externalNavigations, writes, origin };
}

for (const [name, target] of [
  ['protocol-relative', '//redirect.invalid'], ['backslash', '/\\redirect.invalid'],
  ['control-character', '/\t/redirect.invalid'], ['encoded-separator', '/%2fredirect.invalid'],
  ['nested-encoding', '/%255credirect.invalid'], ['normalized-path', '/a/%2e%2e//redirect.invalid'],
  ['external-scheme', 'https://redirect.invalid'], ['malformed-encoding', '/bad%ZZ'],
]) {
  test(`Studio review Back rejects ${name} returnTo`, async ({ page, context, baseURL }) => {
    const state = await setupStudio(page, context, baseURL);
    const params = new URLSearchParams({ lesson: '501', review: '9', returnTo: target });
    await page.goto(`/studio?${params}`);
    await expect(page.getByText('Admin review mode - read only', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Back to moderation', exact: true }).click();
    await expect(page).toHaveURL((url) => url.origin === state.origin && url.pathname === '/moderation' && url.search === '');
    expect(state.externalNavigations).toEqual([]);
    expect(state.writes).toEqual([]);
    state.expectNoErrors();
  });
}

test('Studio review Back preserves a valid internal query and hash', async ({ page, context, baseURL }) => {
  const state = await setupStudio(page, context, baseURL);
  const target = '/moderation?status=open&q=hello%20world#report-42';
  await page.goto(`/studio?${new URLSearchParams({ lesson: '501', review: '9', returnTo: target })}`);
  await page.getByRole('button', { name: 'Back to moderation', exact: true }).click();
  await expect(page).toHaveURL(state.origin + target);
  expect(state.externalNavigations).toEqual([]);
  expect(state.writes).toEqual([]);
  state.expectNoErrors();
});

test('Studio Visuals preview and Analysis remain functional across navigation', async ({ page, context, baseURL }) => {
  const state = await setupStudio(page, context, baseURL);
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto('/studio?view=editor&lesson=501');
  await page.locator('#studio-inspector-tab-intelligence').click();
  await expect(page.getByRole('tab', { name: 'Analysis', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('tab', { name: 'Visuals ✨', exact: true }).click();
  await expect(page.getByText('No stored suggestion for this page. Generate a preview to begin.')).toBeVisible();
  await page.getByRole('button', { name: 'Generate suggestion', exact: true }).click();
  await expect(page.getByRole('img', { name: 'Suggested design', exact: true })).toBeVisible();
  await expect.poll(() => page.getByRole('img', { name: 'Suggested design', exact: true }).evaluate((img) => img.complete && img.naturalWidth > 0)).toBe(true);
  await expect(page.getByText('Preview only — no design has been applied.')).toBeVisible();
  await page.getByRole('tab', { name: 'Analysis', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Analysis', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('tab', { name: 'Visuals ✨', exact: true }).click();
  await expect(page.getByRole('img', { name: 'Suggested design', exact: true })).toBeVisible();
  expect(state.writes).toEqual([{ path: '/api/v1/projects/501/visual-suggestions/', method: 'POST', body: { page_key: PAGE_KEY, expected_source_hash: SOURCE_HASH } }]);
  expect(state.externalNavigations).toEqual([]);
  state.expectNoErrors();
});
