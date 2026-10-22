import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchVisualSuggestions, generateVisualSuggestion, fetchVisualSuggestionPreview, fetchVisualOriginalPreview, setToken } from './api';

beforeEach(() => { global.fetch = vi.fn(); localStorage.clear(); setToken('private-token'); URL.createObjectURL = vi.fn(() => 'blob:private'); });
afterEach(() => vi.restoreAllMocks());
function json(data, ok = true) { return { ok, status: ok ? 200 : 409, json: async () => data }; }

describe('private Visual Designer API', () => {
  it('lists using a stable page key with authorization and no cache', async () => {
    fetch.mockResolvedValue(json({ suggestions: [] }));
    await fetchVisualSuggestions(9, 's1-p2');
    const [url, options] = fetch.mock.calls[0];
    expect(url).toContain('/projects/9/visual-suggestions/?page_key=s1-p2');
    expect(options.headers.Authorization).toBe('Token private-token');
    expect(options.cache).toBe('no-store');
  });
  it('posts only saved source identity, never narration or editor state', async () => {
    fetch.mockResolvedValue(json({ suggestion: {} }));
    await generateVisualSuggestion(9, 's1-p2', 'source-hash');
    const [url, options] = fetch.mock.calls[0];
    expect(url).toContain('/projects/9/visual-suggestions/');
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual({ page_key: 's1-p2', expected_source_hash: 'source-hash' });
  });
  it('fetches the private SVG as an image blob through its own route', async () => {
    const blob = new Blob(['<svg/>'], { type: 'image/svg+xml' });
    fetch.mockResolvedValue({ ok: true, headers: new Headers({ 'Content-Type': 'image/svg+xml; charset=utf-8' }), blob: async () => blob });
    expect(await fetchVisualSuggestionPreview(9, 'candidate')).toBe('blob:private');
    expect(fetch.mock.calls[0][0]).toContain('/projects/9/visual-suggestions/candidate/preview/');
    expect(URL.createObjectURL).toHaveBeenCalledWith(blob);
  });
  it('reuses private original delivery with a draft key rather than a temporary page ID', async () => {
    fetch.mockResolvedValue({ ok: true, headers: new Headers({ 'Content-Type': 'image/png' }), blob: async () => new Blob(['png']) });
    await fetchVisualOriginalPreview(9, 's1-p2', true);
    expect(fetch.mock.calls[0][0]).toContain('/projects/9/transcript-pages/s1-p2/background/original/?draft=1');
    expect(fetch.mock.calls[0][1].headers.Authorization).toBe('Token private-token');
  });
  it('rejects non-image content and saved-source conflicts', async () => {
    fetch.mockResolvedValue({ ok: true, headers: new Headers({ 'Content-Type': 'text/html' }) });
    await expect(fetchVisualSuggestionPreview(9, 'candidate')).rejects.toThrow('Unsupported');
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    fetch.mockResolvedValue(json({ error: 'Saved source changed' }, false));
    await expect(generateVisualSuggestion(9, 'page', 'old')).rejects.toThrow();
  });
});
