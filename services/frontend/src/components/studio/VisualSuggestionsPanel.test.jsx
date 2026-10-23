import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import VisualSuggestionsPanel, { StudioIntelligenceTabs } from './VisualSuggestionsPanel';
import { fetchVisualSuggestions, generateVisualSuggestion, fetchVisualSuggestionPreview, fetchVisualOriginalPreview, fetchVisualDraft, saveVisualDecision } from '../../api';
import { studioWorkspaceCopy } from './studioWorkspaceCopy';

vi.mock('../../api', () => ({ fetchVisualSuggestions: vi.fn(), generateVisualSuggestion: vi.fn(), fetchVisualSuggestionPreview: vi.fn(), fetchVisualOriginalPreview: vi.fn(), fetchVisualDraft: vi.fn(), saveVisualDecision: vi.fn() }));
let host, root;
const page = { id: 7, page_key: 's1-p2', source_slide_index: 0, split_index: 1, original_text: 'Ölçüm & kayıt', narration_text: 'Protected narration' };
const source = { source_hash: 'sha256:' + 'a'.repeat(64), source_mode: 'active', page_key: page.page_key, supported: true };
const suggestion = { id: 'candidate-1', page_key: page.page_key, strategy: 'typography_only', status: 'ready', rationale: 'Authored text only', warnings: ['Use DejaVu Sans'], errors: [], is_stale: false, compatible: true, preview_url: '/private/preview/', applied: false, can_apply: false };
const listing = (items = []) => ({ schema_version: 'visual-suggestion.v1', source, suggestions: items });
async function render(node) { await act(async () => { root.render(node); }); }
async function click(node) { await act(async () => node.click()); }
function button(text) { return [...host.querySelectorAll('button')].find((element) => element.textContent === text); }

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  document.documentElement.lang = 'en';
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  vi.resetAllMocks();
  URL.revokeObjectURL = vi.fn();
  fetchVisualSuggestions.mockResolvedValue(listing());
  fetchVisualDraft.mockResolvedValue({ schema_version: 'visual-draft.v1', page_key: page.page_key, source, selection: { state: 'original', candidate_id: null, version: 0 }, rejected_ids: [], history: [] });
  fetchVisualSuggestionPreview.mockResolvedValue('blob:private-svg');
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); });

describe('Studio Visuals preview tab', () => {
  it('keeps Analysis mounted and uses nested keyboard-accessible tabs', async () => {
    const onAnalysis = vi.fn();
    await render(<StudioIntelligenceTabs analysis={<button onClick={onAnalysis}>Analyze lesson</button>}><VisualSuggestionsPanel projectId={1} page={page} /></StudioIntelligenceTabs>);
    expect(fetchVisualSuggestions).not.toHaveBeenCalled();
    await click(button('Analyze lesson'));
    expect(onAnalysis).toHaveBeenCalledOnce();
    const visualTab = host.querySelector('[data-view="visuals"]');
    await click(visualTab);
    expect(visualTab.getAttribute('aria-selected')).toBe('true');
    expect(fetchVisualSuggestions).toHaveBeenCalledWith(1, 's1-p2');
    expect(button('Analyze lesson').closest('[role="tabpanel"]').hidden).toBe(true);
    await act(async () => visualTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true })));
    expect(button('Analyze lesson').closest('[role="tabpanel"]').hidden).toBe(false);
  });

  it('generates from the selected stable page key and saved source hash only', async () => {
    fetchVisualSuggestions.mockResolvedValueOnce(listing()).mockResolvedValue(listing([suggestion]));
    generateVisualSuggestion.mockResolvedValue({ suggestion });
    await render(<VisualSuggestionsPanel projectId={42} page={page} />);
    expect(host.textContent).toContain('No stored suggestion');
    await click(button('Generate suggestion'));
    expect(generateVisualSuggestion).toHaveBeenCalledWith(42, 's1-p2', source.source_hash);
    expect(fetchVisualSuggestionPreview).toHaveBeenCalledWith(42, 'candidate-1');
    expect(host.querySelector('img[alt="Suggested design"]').getAttribute('src')).toBe('blob:private-svg');
    expect(host.textContent).toContain('Typography only');
    expect(host.textContent).toContain('Use DejaVu Sans');
    expect(host.textContent).toContain('Preview only — no design has been applied.');
    expect(host.textContent).not.toContain('Save & Rerender');
  });

  it('blocks generation for unsaved edits without saving a draft', async () => {
    await render(<VisualSuggestionsPanel projectId={42} page={page} unsavedChanges />);
    expect(button('Generate suggestion').disabled).toBe(true);
    expect(host.textContent).toContain('Save or discard unsaved edits first');
    await click(button('Generate suggestion'));
    expect(generateVisualSuggestion).not.toHaveBeenCalled();
  });

  it('tracks selected page changes and ignores late responses from an old page', async () => {
    let finishOld;
    fetchVisualSuggestions.mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve; })).mockResolvedValue({ ...listing(), source: { ...source, page_key: 'new-page' } });
    await render(<VisualSuggestionsPanel projectId={42} page={page} />);
    await render(<VisualSuggestionsPanel projectId={42} page={{ ...page, id: -12, page_key: 'new-page', split_index: 2 }} />);
    expect(fetchVisualSuggestions).toHaveBeenLastCalledWith(42, 'new-page');
    await act(async () => finishOld(listing([suggestion])));
    expect(host.textContent).not.toContain(suggestion.rationale);
    expect(fetchVisualSuggestionPreview).not.toHaveBeenCalled();
  });

  it('shows saved private draft, stale, unsupported and preservation states', async () => {
    fetchVisualSuggestions.mockResolvedValue({ ...listing([{ ...suggestion, status: 'preserved', preview_url: null, is_stale: true, strategy: 'preserve_original' }]), source: { ...source, source_mode: 'draft' } });
    await render(<VisualSuggestionsPanel projectId={42} page={page} />);
    expect(host.textContent).toContain('Source: saved private draft');
    expect(host.textContent).toContain('Stale — saved source has changed');
    expect(host.textContent).toContain('Original preserved');
    expect(fetchVisualSuggestionPreview).not.toHaveBeenCalled();
    fetchVisualSuggestions.mockResolvedValue(listing([{ ...suggestion, status: 'unsupported', preview_url: null, errors: ['unsafe_text: unsupported source'] }]));
    await click(button('Refresh'));
    expect(host.textContent).toContain('cannot be safely previewed');
    expect(host.textContent).toContain('unsafe_text: unsupported source');
  });

  it('loads original images through saved draft page keys, independently of the active scene mode', async () => {
    fetchVisualOriginalPreview.mockResolvedValue('blob:original-saved-draft');
    fetchVisualSuggestions.mockResolvedValue({ ...listing([{ ...suggestion, status: 'preserved', preview_url: null, strategy: 'preserve_original' }]), source: { ...source, original_available: true, asset_page_ref: 's1-p2', source_mode: 'draft' } });
    await render(<VisualSuggestionsPanel projectId={42} page={{ ...page, id: -17 }} />);
    expect(fetchVisualOriginalPreview).toHaveBeenCalledWith(42, 's1-p2', true);
    expect(host.querySelector('img[alt="Original slide"]').getAttribute('src')).toBe('blob:original-saved-draft');
    expect(host.querySelector('img[alt="Suggested design"]').getAttribute('src')).toBe('blob:original-saved-draft');
    expect(fetchVisualSuggestionPreview).not.toHaveBeenCalled();
  });

  it('handles loading, failed requests, failed preview and retry', async () => {
    let finish;
    fetchVisualSuggestions.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await render(<VisualSuggestionsPanel projectId={42} page={page} />);
    expect(host.textContent).toContain('Loading suggestions...');
    fetchVisualSuggestionPreview.mockRejectedValue(new Error('Forbidden'));
    await act(async () => finish(listing([suggestion])));
    expect(host.querySelector('[role="alert"]').textContent).toContain('Unable to load');
    fetchVisualSuggestions.mockRejectedValue(new Error('Offline'));
    await click(button('Refresh'));
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
  });

  it('never injects SVG or metadata HTML into DOM and revokes private blob URLs', async () => {
    fetchVisualSuggestions.mockResolvedValue(listing([{ ...suggestion, rationale: '<img src=x onerror=alert(1)>', svg: '<script>evil()</script>' }]));
    await render(<VisualSuggestionsPanel projectId={42} page={page} originalImageUrl="blob:original" />);
    expect(host.querySelector('svg')).toBeNull();
    expect(host.querySelector('script')).toBeNull();
    expect(host.querySelector('[onerror]')).toBeNull();
    expect(host.textContent).toContain('<img src=x onerror=alert(1)>');
    expect(host.querySelector('img[alt="Original slide"]').getAttribute('src')).toBe('blob:original');
    await render(<VisualSuggestionsPanel projectId={42} page={{ ...page, page_key: 'different' }} />);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:private-svg');
  });

  it('expands a preview inside the existing image-only dialog without opening executable SVG', async () => {
    fetchVisualSuggestions.mockResolvedValue(listing([suggestion]));
    await render(<VisualSuggestionsPanel projectId={42} page={page} />);
    await click(button('Expand preview'));
    const modal = document.body.querySelector('[role="dialog"]');
    expect(modal.querySelector('img').getAttribute('src')).toBe('blob:private-svg');
    expect(modal.querySelector('[data-testid="visual-expanded-content"]').querySelector('svg, iframe, object, embed, script, a')).toBeNull();
    await act(async () => document.body.querySelector('button[aria-label="Close preview"]').click());
    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
  });

  it('does not fetch private suggestions in read-only review or with a temporary ID alone', async () => {
    await render(<VisualSuggestionsPanel projectId={42} page={page} readOnly />);
    expect(host.textContent).toContain('private to the project owner');
    expect(fetchVisualSuggestions).not.toHaveBeenCalled();
    await render(<VisualSuggestionsPanel projectId={42} page={{ id: -4 }} />);
    expect(host.textContent).toContain('Select a saved transcript page');
    expect(fetchVisualSuggestions).not.toHaveBeenCalled();
  });

  it('uses existing locale fallback and integrates the actual Studio selected page', () => {
    expect(studioWorkspaceCopy('tr').visualTab).toBe('Görseller ✨');
    expect(studioWorkspaceCopy('ar').visualTab).toBe('المرئيات ✨');
    expect(studioWorkspaceCopy('fr').visualTab).toBe('Visuals ✨');
    const studio = readFileSync('src/pages/Studio.jsx', 'utf8');
    expect(studio).toContain('<StudioIntelligenceTabs analysis={');
    expect(studio).toContain('page={selectedScene?.page}');
    expect(studio).toContain('unsavedChanges={Boolean(transcriptDirty || ttsDirty || selectedScene?.isDirty)}');
    expect(studio).toContain('onApplyNarrationSuggestion={handleApplyLessonNarrationSuggestion}');
  });
});
