import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
const GROUNDED_SVG = readFileSync(new URL('../../../docs/visual_design/phase2c/teacher_confirmed.svg', import.meta.url), 'utf8');
import { collectBrowserErrors, jsonResponse, mockCommonAppChromeApi, seedAuthenticatedSession } from './support/apiMocks.js';

const USER = { id: 42, username: 'draft.teacher', role: 'teacher', is_staff: true, auth_provider: 'password' };
const KEY = 'source-0-split-1', HASH = `sha256:${'a'.repeat(64)}`;
const PROJECT = { id: 501, title: 'Private visual lesson', status: 'done', owner_id: 42, teacher: USER, user: USER,
  latest_job: { id: 9001, status: 'done' }, latest_render_analysis: {}, is_published: true, moderation_status: 'approved', duration_seconds: 8, video_url: '', stream_url: '', avatar_visible: false };
const SOURCE = { page_key: KEY, source_hash: HASH, source_mode: 'active', supported: true };
const CANDIDATE = { id: '11111111-1111-4111-8111-111111111111', page_key: KEY, source_hash: HASH, strategy: 'typography_only', status: 'ready',
  rationale: 'Preserve teacher text', warnings: [], errors: [], is_stale: false, compatible: true, applied: false, can_apply: false,
  contains_text: true, preview_url: '/private/' };
const SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080"><rect width="1920" height="1080" fill="#eef2ff"/><text x="100" y="220" font-size="72">Öğrenme &amp; Evidence</text></svg>';

async function setup(page, grounded = false) {
  let generated = false, selection = { state: 'original', candidate_id: null, version: 0, is_stale: false, contains_text: false };
  const candidate = grounded ? { ...CANDIDATE, strategy: 'flowchart', rationale: 'Teacher-confirmed source list order',
    evidence: { origin: 'teacher_confirmed', supports: [{ page_key: KEY, locator: 'display:0:58', quote: 'Process ideas\n- Collect sample\n- Record value\n- Check result' }] } } : CANDIDATE;
  let proposal = { id: '22222222-2222-4222-8222-222222222222', source_hash: HASH, decision: 'pending', inferred: true, is_stale: false,
    rationale: 'Propose order of explicit source items', structure: { kind: 'process', data: { steps: [{ id: 's1', label: 'Collect sample' }, { id: 's2', label: 'Record value' }, { id: 's3', label: 'Check result' }], order: ['s1', 's2', 's3'] } },
    supports: [{ quote: 'Process ideas\n- Collect sample\n- Record value\n- Check result' }], warnings: ['Verify inferred sequence; no causal claim.'] };
  let proposed = false;
  const history = [], rejected = new Set(), writes = [];
  const errors = collectBrowserErrors(page);
  function draft() { return { schema_version: 'visual-draft.v1', page_key: KEY, source: SOURCE, selection, selected_candidate: selection.candidate_id ? candidate : null, rejected_ids: [...rejected], history, applied: false }; }
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    const json = (value, status = 200) => route.fulfill(jsonResponse(value, status));
    if (request.method() === 'POST') writes.push(path);
    if (path.endsWith('/visual-proposals/')) {
      if (request.method() === 'POST') { proposed = true; return json({ proposal }); }
      return json({ model_available: grounded, proposals: proposed ? [proposal] : [] });
    }
    if (path.endsWith('/decision/')) {
      const data = request.postDataJSON();
      expect(request.headers().authorization).toBe('Token visual-draft-test-token');
      expect(data.expected_source_hash).toBe(HASH);
      proposal = { ...proposal, decision: data.action };
      generated = data.action === 'confirm';
      return json({ proposal, suggestion: generated ? candidate : null });
    }
    if (path.endsWith('/visual-draft/')) {
      if (request.method() === 'POST') {
        expect(request.headers().authorization).toBe('Token visual-draft-test-token');
        const data = request.postDataJSON();
        expect(data.page_key).toBe(KEY); expect(data.expected_source_hash).toBe(HASH);
        if (data.expected_version !== selection.version) return json({ detail: 'Conflict' }, 409);
        if (data.action === 'select') { selection = { ...selection, state: 'selected', candidate_id: data.candidate_id, contains_text: true }; rejected.delete(data.candidate_id); }
        if (data.action === 'reject') { rejected.add(data.candidate_id); if (selection.candidate_id === data.candidate_id) selection = { ...selection, state: 'original', candidate_id: null, contains_text: false }; }
        if (data.action === 'restore') selection = { ...selection, state: 'original', candidate_id: null, contains_text: false };
        selection = { ...selection, version: selection.version + 1 };
        history.unshift({ action: data.action, candidate_id: data.candidate_id || null, version: selection.version, created_at: '2026-10-23T09:00:00Z' });
      }
      return json(draft());
    }
    if (path.endsWith('/visual-suggestions/')) {
      const reused = generated;
      if (request.method() === 'POST') { generated = true; return json({ suggestion: candidate, reused }, reused ? 200 : 201); }
      return json({ schema_version: 'visual-suggestion.v1', source: SOURCE, suggestions: generated ? [candidate] : [] });
    }
    if (path.endsWith('/preview/')) {
      expect(request.headers().authorization).toBe('Token visual-draft-test-token');
      return route.fulfill({ contentType: 'image/svg+xml', body: grounded ? GROUNDED_SVG : SVG, headers: { 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store' } });
    }
    if (path === '/api/v1/projects/') return json({ count: 1, results: [PROJECT], has_next: false });
    if (path === '/api/v1/projects/501/') return json(PROJECT);
    if (path.endsWith('/transcript/')) return json({ pages: [{ id: 7, page_number: 1, page_key: KEY, source_slide_index: 0, split_index: 1,
      start_sec: 0, end_sec: 8, text: grounded ? 'Process ideas\n- Collect sample\n- Record value\n- Check result' : 'Authored original', original_text: grounded ? 'Process ideas\n- Collect sample\n- Record value\n- Check result' : 'Authored original', narration_text: 'Protected narration' }], has_draft: false });
    if (path.endsWith('/intelligence/')) return json({ id: 10, status: 'done', provider: 'heuristic', is_stale: false, summary: 'Lesson analysis' });
    if (path.endsWith('/moderation/')) return json({ moderation_status: 'approved', can_publish: true, findings: [] });
    return json({ results: [], count: 0, tracks: [], items: [] });
  });
  await mockCommonAppChromeApi(page, { user: USER, categories: [], capabilities: { features: { intelligence: { enabled: true }, avatar: { enabled: false }, visual_moderation: { enabled: false }, google_auth: { enabled: false } } } });
  await seedAuthenticatedSession(page, { user: USER, token: 'visual-draft-test-token' });
  return { errors, writes };
}
async function openVisuals(page) {
  await page.goto('/studio?view=editor&lesson=501');
  await expect(page.getByTestId('studio-creator-header')).toBeVisible();
  const inspector = page.locator('button[aria-controls="studio-compact-inspector-workspace"]');
  if (page.viewportSize().width < 1280) {
    await expect(inspector).toBeVisible();
    await inspector.click();
  }
  await page.locator('#studio-inspector-tab-intelligence').click();
  await page.getByRole('tab', { name: 'Visuals ✨', exact: true }).click();
}
for (const [device, viewport] of Object.entries({ desktop: { width: 1600, height: 1000 }, mobile: { width: 390, height: 844 } })) {
  test(`private visual selection survives reload and restores original on ${device}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const state = await setup(page); await openVisuals(page);
    await page.getByRole('button', { name: 'Generate suggestion', exact: true }).click();
    const use = page.getByRole('button', { name: 'Use this design', exact: true });
    await expect(use).toBeEnabled(); await use.focus(); await page.keyboard.press('Enter');
    await expect(page.getByText('Unsaved visual choice — save before leaving this page.')).toBeVisible();
    const preview = page.getByTestId('visual-working-preview');
    await expect(preview.getByRole('img', { name: 'Selected design', exact: true })).toBeVisible();
    await expect.poll(() => preview.locator('img').evaluate((img) => img.complete && img.naturalWidth > 0)).toBe(true);
    await expect(preview).not.toContainText('Authored original');
    expect(state.writes).toEqual(['/api/v1/projects/501/visual-suggestions/']);
    await page.getByRole('button', { name: 'Save visual draft', exact: true }).click();
    await expect(page.getByText('Private visual choice saved. Video unchanged.')).toBeVisible();
    await preview.scrollIntoViewIfNeeded();
    expect((await preview.boundingBox()).width).toBeLessThanOrEqual(viewport.width);
    await test.info().attach(`saved-private-visual-${device}`, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
    await openVisuals(page);
    await expect(preview.getByRole('img', { name: 'Selected design', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save visual draft', exact: true })).toBeDisabled();
    await page.getByRole('button', { name: 'Regenerate', exact: true }).click();
    await expect(page.getByText('The deterministic generator returned the same design.')).toBeVisible();
    await page.getByRole('button', { name: 'Reject suggestion', exact: true }).click();
    await expect(page.getByText('Suggestion rejected', { exact: true }).first()).toBeVisible();
    await expect(preview).toContainText('Authored original');
    await use.click(); await page.getByRole('button', { name: 'Save visual draft', exact: true }).click();
    await expect(page.getByText('Private visual choice saved. Video unchanged.')).toBeVisible();
    await page.getByRole('button', { name: 'Restore original', exact: true }).click();
    await expect(preview).toContainText('Authored original');
    await page.getByRole('button', { name: 'Save visual draft', exact: true }).click();
    await openVisuals(page); await expect(preview).toContainText('Authored original');
    await page.getByRole('tab', { name: 'Analysis', exact: true }).click();
    await expect(page.getByRole('tab', { name: 'Analysis', exact: true })).toHaveAttribute('aria-selected', 'true');
    expect(state.writes.every((path) => path.endsWith('/visual-draft/') || path.endsWith('/visual-suggestions/'))).toBe(true);
    state.errors();
  });
}

for (const [device, viewport] of Object.entries({ desktop: { width: 1600, height: 1000 }, mobile: { width: 390, height: 844 } })) {
  test(`grounded evidence review precedes selection and save on ${device}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const state = await setup(page, true); await openVisuals(page);
    await page.getByRole('button', { name: 'Propose with local model', exact: true }).click();
    await expect(page.getByText('Requires teacher confirmation', { exact: true })).toBeVisible();
    await expect(page.getByText('Collect sample', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Use this design', exact: true })).toBeDisabled();
    const confirm = page.getByRole('button', { name: 'Confirm evidence & preview', exact: true });
    await confirm.focus(); await page.keyboard.press('Enter');
    await expect(page.getByText('Teacher confirmed', { exact: true })).toBeVisible();
    await expect(page.getByText('Process flowchart', { exact: true })).toBeVisible();
    await expect(page.getByRole('img', { name: 'Suggested design', exact: true })).toBeVisible();
    expect(state.writes).toEqual(['/api/v1/projects/501/visual-proposals/', '/api/v1/projects/501/visual-proposals/22222222-2222-4222-8222-222222222222/decision/']);
    await page.getByRole('button', { name: 'Use this design', exact: true }).click();
    await page.getByRole('button', { name: 'Save visual draft', exact: true }).click();
    await expect(page.getByText('Private visual choice saved. Video unchanged.', { exact: true })).toBeVisible();
    await openVisuals(page);
    await expect(page.getByTestId('visual-working-preview').getByRole('img')).toBeVisible();
    await expect(page.getByText('Teacher confirmed', { exact: true })).toBeVisible();
    await page.getByTestId('visual-working-preview').scrollIntoViewIfNeeded();
    await test.info().attach(`grounded-evidence-${device}`, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
    await page.getByRole('tab', { name: 'Analysis', exact: true }).click();
    await expect(page.getByRole('tab', { name: 'Analysis', exact: true })).toHaveAttribute('aria-selected', 'true');
    expect(state.writes.every((path) => /visual-(?:proposals|draft)/.test(path))).toBe(true);
    state.errors();
  });
}
