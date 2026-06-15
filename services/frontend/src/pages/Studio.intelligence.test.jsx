import { describe, expect, it } from 'vitest';

import {
  lessonIntelligenceDraftLabel,
  lessonIntelligenceEnhancementLabel,
  lessonIntelligenceProviderLabel,
} from './Studio';

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
