import fs from 'node:fs/promises';
import path from 'node:path';
import { expect, test } from '@playwright/test';

const USER = { id: 42, username: 'pilot.teacher', display_name: 'Pilot Teacher', role: 'teacher', is_staff: true, auth_provider: 'password' };
const PAGE_KEY = 'source-0-split-1';
const HASH = `sha256:${'a'.repeat(64)}`;
const PROJECT = {
  id: 501, title: 'Öğrenme & Evidence', description: 'A reproducible educational lesson.', status: 'done', owner_id: 42,
  teacher: USER, user: USER, teacher_name: USER.display_name, category_name: 'Science', duration_seconds: 8,
  latest_job: { id: 9001, status: 'done' }, latest_render_analysis: {}, is_published: true,
  moderation_status: 'approved', video_url: '', stream_url: '', avatar_visible: false,
};
const PAGES = [{ id: 7, page_number: 1, page_key: PAGE_KEY, source_slide_index: 0, split_index: 1,
  start_sec: 0, end_sec: 8, text: 'Observe → Measure → Record', original_text: 'Observe → Measure → Record', narration_text: 'Protected teacher narration.' }];
const CANDIDATE = { id: 'pilot', page_key: PAGE_KEY, strategy: 'typography_only', status: 'ready', rationale: 'Preserve the authored educational content.',
  warnings: ['Unstructured prose is not verified numerical evidence.'], errors: [], is_stale: false, compatible: true, applied: false, can_apply: false, preview_url: '/private/preview/' };
const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080"><rect width="1920" height="1080" fill="#f8fafc"/><rect x="90" y="130" width="1740" height="820" rx="32" fill="#e0e7ff"/><text x="150" y="300" font-family="sans-serif" font-size="72" fill="#312e81">Öğrenme &amp; Evidence</text><text x="150" y="460" font-family="sans-serif" font-size="56">Observe → Measure → Record</text></svg>';

async function setup(browser, origin, theme, viewport, anonymous = false) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1, reducedMotion: 'reduce', colorScheme: 'light', locale: 'en-US', timezoneId: 'UTC', isMobile: viewport.width < 600, hasTouch: viewport.width < 600 });
  const page = await context.newPage();
  const errors = [];
  const writes = [];
  let generated = false;
  page.on('pageerror', (error) => errors.push(error.message));
  await page.clock.install({ time: new Date('2026-10-23T12:00:00Z') });
  await page.addInitScript(({ theme, user, anonymous }) => {
    localStorage.clear(); sessionStorage.clear();
    localStorage.setItem('visus-theme-mode', theme);
    if (!anonymous) { localStorage.setItem('auth_token', 'pilot-token'); localStorage.setItem('auth_user', JSON.stringify(user)); }
  }, { theme, user: USER, anonymous });
  // Identical, offline font fallback on both builds; no CDN timing or late swaps.
  await page.route(/https:\/\/fonts\.(googleapis|gstatic)\.com\//, (route) => route.fulfill({ status: 200, body: '', contentType: 'text/css' }));
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) writes.push(pathname);
    if (pathname.endsWith('/visual-suggestions/pilot/preview/')) {
      expect(request.headers().authorization).toBe('Token pilot-token');
      return route.fulfill({ contentType: 'image/svg+xml', body: SVG, headers: { 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store' } });
    }
    if (pathname === '/api/v1/projects/501/visual-suggestions/') {
      if (request.method() === 'POST') { expect(request.postDataJSON()).toEqual({ page_key: PAGE_KEY, expected_source_hash: HASH }); generated = true; return json({ suggestion: CANDIDATE }, 201); }
      return json({ schema_version: 'visual-suggestion.v1', source: { page_key: PAGE_KEY, source_hash: HASH, source_mode: 'active', supported: true }, suggestions: generated ? [CANDIDATE] : [] });
    }
    if (pathname === '/api/v1/capabilities/') return json({ features: { intelligence: { enabled: true }, avatar: { enabled: false }, google_auth: { enabled: false }, visual_moderation: { enabled: false } } });
    if (pathname === '/api/v1/auth/me/') return json(USER);
    if (pathname === '/api/v1/categories/') return json([{ id: 1, name: 'Science', slug: 'science' }]);
    if (pathname === '/api/v1/projects/') return json({ count: 1, results: [PROJECT], has_next: false });
    if (pathname === '/api/v1/projects/501/') return json(PROJECT);
    if (pathname.endsWith('/transcript/')) return json({ pages: PAGES, has_draft: false });
    if (pathname.endsWith('/intelligence/')) return json({ id: 10, status: 'done', provider: 'heuristic', is_stale: false, summary: 'Existing lesson analysis.' });
    if (pathname.endsWith('/moderation/')) return json({ moderation_status: 'approved', can_publish: true, findings: [] });
    if (pathname === '/api/v1/catalog/501/') return json({ ...PROJECT, transcript_pages: PAGES });
    if (pathname === '/api/v1/catalog/' || pathname === '/api/v1/catalog/feed/') return json(pathname.endsWith('/feed/') ? { sections: [], results: [PROJECT] } : [PROJECT]);
    if (pathname === '/api/v1/me/notifications/unread-count/') return json({ unread_count: 1 });
    if (pathname === '/api/v1/me/notifications/') return json({ count: 1, results: [{ id: 1, title: 'Render complete', body: 'Your private lesson preview is ready.', event_type: 'render_complete', action_url: '/watch?lesson=501', is_read: false, created_at: '2026-10-23T10:00:00Z' }], has_more: false });
    if (pathname.endsWith('/playlist-context/')) return json({ mode: 'publisher', items: [] });
    return json({ results: [], count: 0, tracks: [], items: [] });
  });
  return { context, page, origin, errors, writes };
}

async function open(state, scenario) {
  const { page, origin } = state;
  const route = { dashboard: '/', studio: '/studio?view=editor&lesson=501', analysis: '/studio?view=editor&lesson=501', visuals: '/studio?view=editor&lesson=501', dialog: '/studio?view=editor&lesson=501', login: '/library', watch: '/watch?lesson=501', notifications: '/notifications' }[scenario];
  await page.goto(origin + route);
  if (['studio', 'analysis', 'visuals', 'dialog'].includes(scenario)) {
    await expect(page.getByTestId('studio-creator-header')).toBeVisible();
    if (scenario !== 'studio') {
      await page.locator('#studio-inspector-tab-intelligence').click();
      await expect(page.getByRole('tab', { name: 'Analysis', exact: true })).toHaveAttribute('aria-selected', 'true');
    }
    if (['visuals', 'dialog'].includes(scenario)) {
      await page.getByRole('tab', { name: 'Visuals ✨', exact: true }).click();
      await page.getByRole('button', { name: 'Generate suggestion', exact: true }).click();
      const image = page.getByRole('img', { name: 'Suggested design', exact: true });
      await expect(image).toBeVisible();
      await expect.poll(() => image.evaluate((image) => image.complete && image.naturalWidth > 0)).toBe(true);
      await expect(page.getByText('Preview only — no design has been applied.')).toBeVisible();
      if (scenario === 'dialog') {
        await page.getByRole('button', { name: 'Expand preview', exact: true }).last().click();
        await expect(page.getByRole('dialog')).toBeVisible();
        await page.keyboard.press('Tab');
        expect(await page.getByRole('dialog').evaluate((dialog) => dialog.contains(document.activeElement))).toBe(true);
      } else {
        await page.getByRole('tab', { name: 'Analysis', exact: true }).click();
        await page.getByRole('tab', { name: 'Visuals ✨', exact: true }).click();
        await expect(image).toBeVisible();
      }
    }
  } else if (scenario === 'login') await expect(page.getByLabel('Username', { exact: true })).toBeVisible();
  else if (scenario === 'notifications') await expect(page.getByRole('heading', { name: 'Render complete' })).toBeVisible();
  else if (scenario === 'watch') await expect(page.getByRole('heading', { name: PROJECT.title }).first()).toBeVisible();
  else await expect(page.getByRole('main')).toBeVisible();
  await page.waitForLoadState('networkidle');
  await expect(page.locator('html')).toHaveAttribute('data-theme', state.theme);
  await page.evaluate(() => document.fonts.ready);
  await page.mouse.move(0, 0);
  await page.evaluate(() => window.scrollTo(0, 0));
  expect(state.errors).toEqual([]);
  expect(state.writes).toEqual(['visuals', 'dialog'].includes(scenario) ? ['/api/v1/projects/501/visual-suggestions/'] : []);
}

async function layout(page) {
  return page.evaluate(() => [...document.querySelectorAll('h1,h2,h3,button,input,textarea,main,nav,aside,section,figure,[role="dialog"]')].filter((el) => el.getBoundingClientRect().width && el.getBoundingClientRect().height).map((el) => {
    const style = getComputedStyle(el), rect = el.getBoundingClientRect();
    return { tag: el.tagName, label: (el.getAttribute('aria-label') || el.textContent).trim().slice(0, 80),
      rect: ['x', 'y', 'width', 'height'].map((key) => Math.round(rect[key] * 10) / 10),
      font: [style.fontFamily, style.fontSize, style.fontWeight, style.lineHeight, style.letterSpacing],
      color: style.color, background: style.backgroundColor, radius: style.borderRadius,
    };
  }));
}

for (const theme of ['light', 'dark']) for (const [device, viewport] of Object.entries({ desktop: { width: 1600, height: 1000 }, mobile: { width: 390, height: 844 } })) {
  for (const scenario of ['dashboard', 'studio', 'analysis', 'visuals', 'dialog', 'login', 'watch', 'notifications']) {
    test(`${scenario} ${theme} ${device}`, async ({ browser }, info) => {
      const baseline = await setup(browser, 'http://127.0.0.1:4174', theme, viewport, scenario === 'login');
      const candidate = await setup(browser, 'http://127.0.0.1:4173', theme, viewport, scenario === 'login');
      baseline.theme = theme; candidate.theme = theme;
      try {
        await open(baseline, scenario); await open(candidate, scenario);
        const baselineImage = await baseline.page.screenshot({ fullPage: true, animations: 'disabled' });
        const candidateImage = await candidate.page.screenshot({ fullPage: true, animations: 'disabled' });
        await info.attach('tailwind3', { body: baselineImage, contentType: 'image/png' });
        await info.attach('tailwind4', { body: candidateImage, contentType: 'image/png' });
        const before = await layout(baseline.page), after = await layout(candidate.page);
        await info.attach('computed-layout', { body: JSON.stringify({ before, after }, null, 2), contentType: 'application/json' });
        const golden = info.snapshotPath('same-run-baseline.png');
        await fs.mkdir(path.dirname(golden), { recursive: true }); await fs.writeFile(golden, baselineImage);
        expect.soft(candidateImage).toMatchSnapshot('same-run-baseline.png', { threshold: 0.1, maxDiffPixelRatio: 0.001 });
        expect(after).toEqual(before);
      } finally { await baseline.context.close(); await candidate.context.close(); }
    });
  }
}
