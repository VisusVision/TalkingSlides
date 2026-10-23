import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import VisualDraftControls from './VisualDraftControls';
import { fetchVisualDraft, saveVisualDecision, fetchVisualSuggestionPreview } from '../../api';
vi.mock('../../api', () => ({ fetchVisualDraft: vi.fn(), saveVisualDecision: vi.fn(), fetchVisualSuggestionPreview: vi.fn() }));
const hash = 'sha256:' + 'a'.repeat(64), source = { source_hash: hash, supported: true };
const candidate = { id: 'candidate', source_hash: hash, status: 'ready', compatible: true, is_stale: false, preview_url: '/private/', errors: [] };
const original = { state: 'original', candidate_id: null, version: 0, is_stale: false };
const state = (selection = original, extras = {}) => ({ schema_version: 'visual-draft.v1', page_key: 'p0', source, selection, selected_candidate: selection.state === 'selected' ? candidate : null, rejected_ids: [], history: [], ...extras });
let host, root;
async function render(props = {}) { await act(async () => root.render(<VisualDraftControls projectId={1} pageKey="p0" source={source} candidate={candidate} originalImage="blob:original" originalText="Authored text" {...props} />)); }
const button = (label) => [...host.querySelectorAll('button')].find((b) => b.textContent === label);
async function click(label) { await act(async () => button(label).click()); }
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true; document.documentElement.lang = 'en';
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  vi.resetAllMocks(); URL.revokeObjectURL = vi.fn(); fetchVisualDraft.mockResolvedValue(state()); fetchVisualSuggestionPreview.mockResolvedValue('blob:selected');
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); });

describe('private visual draft controls', () => {
  it('stages an immediate image, saves explicitly, and recovers after remount', async () => {
    await render(); await click('Use this design');
    expect(saveVisualDecision).not.toHaveBeenCalled(); expect(host.textContent).toContain('Unsaved visual choice');
    expect(host.querySelector('[data-testid="visual-working-preview"] img').src).toBe('blob:selected');
    expect(host.querySelector('[data-testid="visual-working-preview"]').textContent).not.toContain('Authored text');
    const selected = state({ state: 'selected', candidate_id: 'candidate', version: 1, is_stale: false });
    saveVisualDecision.mockResolvedValue(selected); await click('Save visual draft');
    expect(saveVisualDecision).toHaveBeenCalledWith(1, { pageKey: 'p0', action: 'select', candidateId: 'candidate', expectedSourceHash: hash, expectedVersion: 0 });
    expect(host.textContent).toContain('Private visual choice saved');
    fetchVisualDraft.mockResolvedValue(selected); await act(async () => { root.unmount(); root = createRoot(host); }); await render();
    expect(host.querySelector('img[alt="Selected design"]').src).toBe('blob:selected'); expect(button('Save visual draft').disabled).toBe(true);
  });
  it('records rejection and stages original restoration before saving', async () => {
    fetchVisualDraft.mockResolvedValue(state({ state: 'selected', candidate_id: 'candidate', version: 1, is_stale: false }));
    saveVisualDecision.mockResolvedValue(state({ ...original, version: 2 }, { rejected_ids: ['candidate'] }));
    await render(); await click('Reject suggestion'); expect(host.textContent).toContain('Suggestion rejected');
    expect(host.querySelector('[data-testid="visual-working-preview"] img').src).toBe('blob:original');
    await click('Use this design'); expect(host.querySelector('img[alt="Selected design"]')).not.toBeNull();
    await click('Restore original'); expect(saveVisualDecision).toHaveBeenCalledTimes(1); expect(host.textContent).toContain('Unsaved visual choice');
    await click('Save visual draft'); expect(saveVisualDecision).toHaveBeenLastCalledWith(1, { pageKey: 'p0', action: 'restore', candidateId: null, expectedSourceHash: hash, expectedVersion: 2 });
  });
  it.each([{ is_stale: true }, { compatible: false }, { status: 'unsupported' }, { source_hash: 'different' }, { errors: ['bad'] }])('disables invalid selection: %j', async (change) => {
    await render({ candidate: { ...candidate, ...change } }); expect(button('Use this design').disabled).toBe(true);
    await click('Use this design'); expect(saveVisualDecision).not.toHaveBeenCalled();
  });
  it('blocks unsaved narration and hides stale selected previews', async () => {
    fetchVisualDraft.mockResolvedValue(state({ state: 'selected', candidate_id: 'candidate', version: 1, is_stale: true }));
    await render({ unsavedChanges: true }); expect(button('Use this design').disabled).toBe(true); expect(button('Reject suggestion').disabled).toBe(true);
    expect(host.querySelector('img[alt="Selected design"]')).toBeNull();
  });
  it('ignores old page/project responses', async () => {
    let finish;
    fetchVisualDraft.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; })).mockResolvedValue(state(original, { page_key: 'p1' }));
    await render(); await render({ projectId: 2, pageKey: 'p1' });
    await act(async () => finish(state({ state: 'selected', candidate_id: 'candidate', version: 9 })));
    expect(host.querySelector('img[alt="Selected design"]')).toBeNull(); expect(host.textContent).toContain('Original design');
  });
  it('ignores late saves and revokes preview URLs after navigation', async () => {
    let finish; await render(); await click('Use this design'); saveVisualDecision.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await click('Save visual draft'); fetchVisualDraft.mockResolvedValue(state(original, { page_key: 'p1' })); await render({ pageKey: 'p1', candidate: null });
    await act(async () => finish(state({ state: 'selected', candidate_id: 'candidate', version: 1 })));
    expect(host.querySelector('img[alt="Selected design"]')).toBeNull(); expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:selected');
  });
  it('keeps conflicts unsaved; rapid saves send only one request', async () => {
    await render(); await click('Use this design'); saveVisualDecision.mockRejectedValue(Object.assign(new Error('Conflict'), { status: 409 }));
    await act(async () => { button('Save visual draft').click(); button('Save visual draft').click(); });
    expect(saveVisualDecision).toHaveBeenCalledTimes(1); expect(host.textContent).toContain('Reload before saving'); expect(host.textContent).toContain('Unsaved visual choice');
    await click('Reload visual draft'); expect(host.textContent).not.toContain('Unsaved visual choice');
  });
});
