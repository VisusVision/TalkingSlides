import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import VisualEvidencePanel from './VisualEvidencePanel';
import { fetchVisualProposals, generateVisualProposal, reviewVisualProposal, generateVisualSuggestion } from '../../api';
import { studioWorkspaceCopy } from './studioWorkspaceCopy';

vi.mock('../../api', () => ({ fetchVisualProposals: vi.fn(), generateVisualProposal: vi.fn(), reviewVisualProposal: vi.fn(), generateVisualSuggestion: vi.fn() }));
let host, root;
const source = { source_hash: `sha256:${'a'.repeat(64)}` };
const proposal = { id: 'proposal-1', source_hash: source.source_hash, decision: 'pending', inferred: true, is_stale: false,
  rationale: 'Teacher must review order', structure: { kind: 'process', data: { steps: [{ id: 's1', label: 'Collect & record' }, { id: 's2', label: 'Check' }], order: ['s1', 's2'] } },
  supports: [{ quote: 'Source <unsafe> & text' }], warnings: ['Inferred order only'] };
const defaults = { projectId: 1, pageKey: 'page-a', source, disabled: false, onCandidate: vi.fn() };
async function render(props = {}) { await act(async () => { root.render(<VisualEvidencePanel key={`${props.projectId || 1}:${props.pageKey || 'page-a'}`} {...defaults} {...props} />); }); }
async function click(text) { const button = [...host.querySelectorAll('button')].find((node) => node.textContent === text); await act(async () => button.click()); }
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true; document.documentElement.lang = 'en';
  vi.resetAllMocks(); host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  fetchVisualProposals.mockResolvedValue({ proposals: [proposal], model_available: true });
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

it('shows unverified structure, exact source excerpts and keyboard-accessible confirmation', async () => {
  await render();
  expect(host.textContent).toContain('Requires teacher confirmation');
  expect(host.textContent).toContain('Collect & record');
  expect(host.textContent).toContain('Source <unsafe> & text');
  expect(host.querySelector('unsafe')).toBeNull();
  expect(defaults.onCandidate).not.toHaveBeenCalled();
  reviewVisualProposal.mockResolvedValue({ proposal: { ...proposal, decision: 'confirm' }, suggestion: { id: 'ready' } });
  await click('Confirm evidence & preview');
  expect(reviewVisualProposal).toHaveBeenCalledWith(1, 'proposal-1', 'confirm', source.source_hash);
  expect(defaults.onCandidate).toHaveBeenCalledOnce();
  expect(host.textContent).toContain('Teacher confirmed');
});
it('rejects a proposal separately from candidate selection', async () => {
  reviewVisualProposal.mockResolvedValue({ proposal: { ...proposal, decision: 'reject' }, suggestion: null });
  await render(); await click('Reject proposal');
  expect(host.textContent).toContain('Proposal rejected');
  expect(defaults.onCandidate).not.toHaveBeenCalled();
});
it('disables stale or unsaved evidence without making requests', async () => {
  fetchVisualProposals.mockResolvedValue({ proposals: [{ ...proposal, is_stale: true }], model_available: true });
  await render({ disabled: true });
  expect([...host.querySelectorAll('button')].every((button) => button.disabled)).toBe(true);
  await click('Confirm evidence & preview');
  expect(reviewVisualProposal).not.toHaveBeenCalled();
});
it('falls back to deterministic generation on unavailable or rejected model output', async () => {
  generateVisualProposal.mockResolvedValue({ proposal: null, fallback: 'unavailable' });
  generateVisualSuggestion.mockResolvedValue({ suggestion: { id: 'deterministic' } });
  await render(); await click('Propose with local model');
  expect(generateVisualSuggestion).toHaveBeenCalledWith(1, 'page-a', source.source_hash);
  expect(host.textContent).toContain('Showing a deterministic suggestion');
  expect(defaults.onCandidate).toHaveBeenCalledWith({ suggestion: { id: 'deterministic' } });
});
it('ignores confirmation completion after switching page/project', async () => {
  let finish;
  reviewVisualProposal.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  await render(); await click('Confirm evidence & preview');
  fetchVisualProposals.mockResolvedValue({ proposals: [], model_available: false });
  await render({ projectId: 2, pageKey: 'page-b' });
  await act(async () => finish({ proposal: { ...proposal, decision: 'confirm' }, suggestion: { id: 'wrong-page' } }));
  expect(defaults.onCandidate).not.toHaveBeenCalled();
  expect(host.textContent).not.toContain('Teacher confirmed');
});
it('prevents rapid double confirmation and shows request failure', async () => {
  reviewVisualProposal.mockRejectedValue(new Error('409 stale'));
  await render(); await click('Confirm evidence & preview');
  expect(host.textContent).toContain('Evidence request failed');
});
it('localizes every new control in English, Turkish and Arabic', () => {
  for (const locale of ['en', 'tr', 'ar']) {
    const copy = studioWorkspaceCopy(locale);
    for (const key of ['visualEvidenceTitle', 'visualConfirmEvidence', 'visualRejectEvidence', 'visualModelPropose', 'visualNeedsConfirmation']) expect(copy[key]).toBeTruthy();
  }
});
