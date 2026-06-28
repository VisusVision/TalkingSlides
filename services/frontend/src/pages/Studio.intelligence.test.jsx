import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vitest';

import {
  RenderAnalysisPanel,
  lessonIntelligenceDraftLabel,
  lessonIntelligenceEnhancementLabel,
  lessonIntelligenceProviderLabel,
  renderAnalysisActionLabel,
} from './Studio';

async function renderRenderAnalysisPanel(analysis) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);

  await act(async () => {
    root.render(<RenderAnalysisPanel analysis={analysis} />);
  });

  return { host, root };
}

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

    expect(renderAnalysisActionLabel('recompose_visual_only_future')).toBe('Visual-only recompose');

    const { host, root } = await renderRenderAnalysisPanel(analysis);

    expect(host.textContent).toContain('Last render analysis');
    expect(host.textContent).toContain('Diagnostic only');
    expect(host.textContent).toContain('Actual rendering may safely fall back.');
    expect(host.textContent).toContain('Visual-only recompose');
    expect(host.textContent).toContain('Recommended future action: Visual-only recompose');
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
