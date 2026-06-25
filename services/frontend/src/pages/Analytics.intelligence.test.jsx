import { describe, expect, it } from 'vitest';

import {
  analyticsDisplayReportAfterRefresh,
  analyticsEnhancementLabelText,
  analyticsProviderStatusLabel,
} from './Analytics';

describe('analytics intelligence status labels', () => {
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

    expect(analyticsProviderStatusLabel(report)).toBe('Ollama insight completed');
    expect(analyticsEnhancementLabelText(report)).toBe('Ollama insight completed');
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

    expect(analyticsProviderStatusLabel(report)).toBe('Partial Ollama insight with heuristic fallback');
    expect(analyticsEnhancementLabelText(report)).toBe('Partial Ollama insight with heuristic fallback');
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

    expect(analyticsProviderStatusLabel(report)).toBe('Heuristic fallback shown');
    expect(analyticsEnhancementLabelText(report)).toBe('Heuristic fallback shown');
    expect(analyticsEnhancementLabelText(report).toLowerCase()).not.toContain('failed');
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

    expect(analyticsProviderStatusLabel(report)).toBe('Partial Ollama insight with heuristic fallback');
    expect(analyticsEnhancementLabelText(report)).toBe('Partial Ollama insight with heuristic fallback');
    expect(analyticsEnhancementLabelText(report).toLowerCase()).not.toContain('failed');
  });

  it('labels an existing report with a pending refresh as updating', () => {
    const report = {
      id: 5,
      status: 'done',
      provider: 'heuristic',
      fallback_used: false,
      pending_report_id: 6,
      refresh_status: 'running',
    };

    expect(analyticsEnhancementLabelText(report)).toBe('Updating insight...');
  });

  it('keeps the current insight visible while a newer refresh is pending', () => {
    const current = {
      id: 10,
      status: 'done',
      provider: 'ollama',
      source_hash: 'old-source',
      current_source_hash: 'old-source',
      summary: 'Current completed insight',
    };
    const pending = {
      id: 11,
      status: 'done',
      provider: 'heuristic',
      fallback_used: true,
      source_hash: 'new-source',
      current_source_hash: 'new-source',
      summary: 'Pending refresh insight',
      enhancement_pending: true,
      enhancement_status: 'pending',
    };

    const display = analyticsDisplayReportAfterRefresh(current, pending);

    expect(display.id).toBe(current.id);
    expect(display.pending_report_id).toBe(pending.id);
    expect(display.latest_refresh_report_id).toBe(pending.id);
    expect(display.current_source_hash).toBe('new-source');
    expect(display.summary).toBe('Current completed insight');
    expect(display.insight_stale).toBe(true);
    expect(display.enhancement_pending).toBe(true);
  });

  it('replaces the current insight after a newer report is complete', () => {
    const current = {
      id: 12,
      status: 'done',
      provider: 'heuristic',
      summary: 'Old insight',
    };
    const next = {
      id: 13,
      status: 'done',
      provider: 'ollama',
      summary: 'New insight',
      enhancement_status: 'done',
    };

    expect(analyticsDisplayReportAfterRefresh(current, next)).toBe(next);
  });
});
