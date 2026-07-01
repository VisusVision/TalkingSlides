import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { readFileSync } from 'node:fs';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { previewPartialRenderImpact } from '../api';
import {
  PredictedRerenderImpactPanel,
  PreviewRerenderImpactButton,
  RenderAnalysisPanel,
  lessonIntelligenceDraftLabel,
  lessonIntelligenceEnhancementLabel,
  lessonIntelligenceProviderLabel,
  partialRenderPreviewSourceLabel,
  renderAnalysisActionLabel,
} from './Studio';

async function renderNode(node) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);

  await act(async () => {
    root.render(node);
  });

  return { host, root };
}

async function renderRenderAnalysisPanel(analysis) {
  return renderNode(<RenderAnalysisPanel analysis={analysis} />);
}

beforeEach(() => {
  global.fetch = vi.fn();
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

describe('lesson intelligence draft labels', () => {
  it('distinguishes heuristic suggestions from Ollama AI drafts', () => {
    expect(lessonIntelligenceDraftLabel({ generated_by: 'heuristic', ai_generated: false })).toBe('Heuristic suggestion');
    expect(lessonIntelligenceDraftLabel({ generated_by: 'ollama', ai_generated: true })).toBe('AI draft');
  });

  it('treats missing provider metadata as an AI draft only when not marked heuristic', () => {
    expect(lessonIntelligenceDraftLabel({ ai_generated: false })).toBe('Heuristic suggestion');
    expect(lessonIntelligenceDraftLabel({ draft_narration: 'Explain the objective.' })).toBe('AI draft');
  });
});

describe('lesson intelligence status labels', () => {
  it('labels a full Ollama report as completed', () => {
    const report = {
      id: 1,
      status: 'done',
      provider: 'ollama',
      fallback_used: false,
      enhancement_provider: 'ollama',
      enhancement_status: 'done',
      metadata: { progressive_enhancement: { failed_chunks: 0 } },
    };

    expect(lessonIntelligenceProviderLabel(report)).toBe('Ollama insight completed');
    expect(lessonIntelligenceEnhancementLabel(report)).toBe('Ollama insight completed');
  });

  it('labels partial Ollama reports as usable fallback reports', () => {
    const report = {
      id: 2,
      status: 'done',
      provider: 'ollama',
      fallback_used: true,
      enhancement_provider: 'ollama',
      enhancement_status: 'partial',
      metadata: { progressive_enhancement: { completed_chunks: 3, failed_chunks: 1 } },
    };

    expect(lessonIntelligenceProviderLabel(report)).toBe('Partial Ollama insight with heuristic fallback');
    expect(lessonIntelligenceEnhancementLabel(report)).toBe('Partial Ollama insight with heuristic fallback');
  });

  it('labels degraded heuristic reports as fallback without generic failed copy', () => {
    const report = {
      id: 3,
      status: 'done',
      provider: 'heuristic',
      fallback_used: true,
      enhancement_provider: 'ollama',
      enhancement_status: 'degraded',
      metadata: { progressive_enhancement: { degraded_reason: 'chunk_timeout' } },
    };

    expect(lessonIntelligenceProviderLabel(report)).toBe('Heuristic fallback shown');
    expect(lessonIntelligenceEnhancementLabel(report)).toBe('Heuristic fallback shown');
    expect(lessonIntelligenceEnhancementLabel(report).toLowerCase()).not.toContain('failed');
  });

  it('does not render done degraded Ollama reports as generic failures', () => {
    const report = {
      id: 4,
      status: 'done',
      provider: 'ollama',
      fallback_used: true,
      enhancement_provider: 'ollama',
      enhancement_status: 'degraded',
      metadata: {
        progressive_enhancement: {
          completed_chunks: 4,
          failed_chunks: 1,
          degraded_reason: 'ollama_no_progress_timeout',
        },
      },
    };

    expect(lessonIntelligenceProviderLabel(report)).toBe('Partial Ollama insight with heuristic fallback');
    expect(lessonIntelligenceEnhancementLabel(report)).toBe('Partial Ollama insight with heuristic fallback');
    expect(lessonIntelligenceEnhancementLabel(report).toLowerCase()).not.toContain('failed');
  });
});

describe('render analysis diagnostics', () => {
  it('labels recommended actions with publisher-safe wording', async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const analysis = {
      mode: 'report_only',
      classifier: {
        available: true,
        pages: {
          'slide-1': { page_key: 'slide-1', index: 0, classification: 'display_text_changed' },
        },
      },
      plan: {
        mode: 'report_only',
        summary: {
          recompose_visual_only_future: 1,
          full_rerender_required_future: 0,
          unknown_requires_full: 0,
        },
        pages: {
          'slide-1': {
            page_key: 'slide-1',
            classification: 'display_text_changed',
            recommended_action: 'recompose_visual_only_future',
            future_only: true,
            actual_behavior_changed: false,
            reasons: ['display_text_changed'],
          },
        },
      },
    };

    expect(renderAnalysisActionLabel('recompose_visual_only_future')).toBe('Visual-only recomposition');
    expect(renderAnalysisActionLabel('reuse_all')).toBe('Reuse existing assets');
    expect(renderAnalysisActionLabel('rerun_tts_avatar_future')).toBe('Rerun narration/avatar');
    expect(renderAnalysisActionLabel('unknown_requires_full')).toBe('Unknown, safest full rerender');

    const { host, root } = await renderRenderAnalysisPanel(analysis);

    expect(host.textContent).toContain('Last render analysis');
    expect(host.textContent).toContain('Diagnostic only');
    expect(host.textContent).toContain('Actual rendering may safely fall back.');
    expect(host.textContent).toContain('Visual-only recomposition');
    expect(host.textContent).toContain('Recommended future action: Visual-only recomposition');
    expect(host.querySelectorAll('button')).toHaveLength(0);

    await act(async () => root.unmount());
    host.remove();
  });

  it('renders nothing when analysis is missing', async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const { host, root } = await renderRenderAnalysisPanel(null);

    expect(host.textContent).toBe('');
    expect(host.querySelector('[data-testid="render-analysis-panel"]')).toBeNull();

    await act(async () => root.unmount());
    host.remove();
  });
});

describe('predicted rerender impact preview', () => {
  it('renders prediction-only impact details with friendly labels', async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const prediction = {
      mode: 'prediction_only',
      source: 'request_payload',
      available: true,
      summary: {
        reuse_all: 1,
        recompose_visual_only_future: 1,
        rerun_tts_avatar_future: 0,
        rerun_avatar_future: 0,
        metadata_only_future: 0,
        rerender_page_future: 0,
        full_rerender_required_future: 0,
        unknown_requires_full: 0,
      },
      pages: [
        {
          page_key: 'slide-1',
          index: 0,
          classification: 'display_text_changed',
          reasons: ['display_text_changed'],
          requires_full: false,
          recommended_action: 'recompose_visual_only_future',
          future_only: true,
          actual_behavior_changed: false,
        },
      ],
    };

    expect(partialRenderPreviewSourceLabel('request_payload')).toBe('Current editor payload');

    const { host, root } = await renderNode(<PredictedRerenderImpactPanel prediction={prediction} />);

    expect(host.textContent).toContain('Predicted rerender impact');
    expect(host.textContent).toContain('Prediction only');
    expect(host.textContent).toContain('Actual rendering may safely fall back.');
    expect(host.textContent).toContain('Source: Current editor payload');
    expect(host.textContent).toContain('Visual-only recomposition');
    expect(host.textContent).toContain('Reuse existing assets');
    expect(host.textContent).toContain('slide-1');

    await act(async () => root.unmount());
    host.remove();
  });

  it('shows quiet diagnostic errors without action buttons', async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const { host, root } = await renderNode(
      <PredictedRerenderImpactPanel error="Rerender impact preview is unavailable." />,
    );

    expect(host.textContent).toContain('Predicted rerender impact');
    expect(host.textContent).toContain('Rerender impact preview is unavailable.');
    expect(host.querySelectorAll('button')).toHaveLength(0);

    await act(async () => root.unmount());
    host.remove();
  });

  it('renders the preview button and calls its handler', async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const onClick = vi.fn();
    const { host, root } = await renderNode(<PreviewRerenderImpactButton onClick={onClick} />);

    const button = host.querySelector('[data-testid="partial-render-preview-button"]');
    expect(button).not.toBeNull();
    expect(button.textContent).toContain('Preview rerender impact');

    await act(async () => {
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(onClick).toHaveBeenCalledTimes(1);

    await act(async () => root.unmount());
    host.remove();
  });

  it('calls the prediction endpoint without using the save endpoint', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ mode: 'prediction_only', source: 'request_payload' }),
    });
    const payload = { pages: [{ id: 1, page_key: 'slide-1', narration_text: 'Hello' }] };

    const result = await previewPartialRenderImpact(12, payload);

    expect(result.mode).toBe('prediction_only');
    expect(global.fetch).toHaveBeenCalledTimes(1);
    const [url, options] = global.fetch.mock.calls[0];
    expect(url).toContain('/projects/12/partial-render-preview/');
    expect(url).not.toContain('/transcript/');
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual(payload);
  });

  it('keeps Save & Rerender wired to the existing save flow', () => {
    const source = readFileSync('src/pages/Studio.jsx', 'utf8');
    expect(source).toContain('PreviewRerenderImpactButton');
    expect(source).toContain('previewPartialRenderImpact');
    expect(source).toContain('onClick={() => handleGlobalEditorSave({ triggerRerender: true })}');
    expect(source).toContain('Save & Rerender');
  });
});
