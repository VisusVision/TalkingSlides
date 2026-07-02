import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Copy,
  Eye,
  Filter,
  Gauge,
  Lightbulb,
  MessageSquare,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Users,
} from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';
import {
  analyzeMyAnalyticsIntelligence,
  createProject,
  fetchCategories,
  fetchMyAnalytics,
  fetchMyAnalyticsIntelligence,
} from '../api';
import CreateLessonModal from '../components/studio/CreateLessonModal';
import SurfaceCard from '../components/ui/SurfaceCard';
import { usePageLoading } from '../components/ui/PageLoading';
import { useI18n } from '../i18n/I18nProvider';
import { canAccessStudio } from '../lib/auth';
import { featureEnabled, useCapabilities } from '../lib/capabilities';
import { copyTextToClipboard } from '../utils/clipboard';
import {
  clearRouteSessionState,
  onRouteReset,
  readRouteSessionState,
  writeRouteSessionState,
} from '../utils/routeSession';

const RANGE_OPTIONS = [
  { key: '7', labelKey: 'analytics.ranges.last7' },
  { key: '30', labelKey: 'analytics.ranges.last30' },
  { key: '90', labelKey: 'analytics.ranges.last90' },
];

const DONUT_COLORS = [
  'var(--accent-primary)',
  '#38bdf8',
  '#34d399',
  '#f59e0b',
  '#fb7185',
  '#a78bfa',
];
const ANALYTICS_INTELLIGENCE_ENHANCEMENT_POLL_INTERVAL_MS = 6000;

function toNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function percent(value) {
  return `${Math.max(0, Math.min(100, Math.round(toNumber(value))))}%`;
}

function formatPercentMetric(value) {
  const normalized = typeof value === 'string' ? value.replace('%', '').trim() : value;
  return percent(normalized);
}

function humanizeAnalyticsSignal(signal) {
  const text = String(signal || '').trim();
  if (!text) return '';
  const completionMatch = text.match(/\bcompletion(?:_rate)?\s*=\s*([0-9]+(?:\.[0-9]+)?%?)/i);
  const progressMatch = text.match(/\b(?:average_)?progress\s*=\s*([0-9]+(?:\.[0-9]+)?%?)/i);
  const compactProgressMatch = text.match(/\b([0-9]+(?:\.[0-9]+)?)%\s+completion,\s+([0-9]+(?:\.[0-9]+)?)%\s+average progress\b/i);
  if (compactProgressMatch) {
    const completion = formatPercentMetric(compactProgressMatch[1]);
    const progress = formatPercentMetric(compactProgressMatch[2]);
    const sentence = `About ${completion} of learners completed these lessons, while the average viewer reached ${progress} of the lesson.`;
    return text.replace(compactProgressMatch[0], sentence);
  }
  if (completionMatch && progressMatch) {
    const completion = formatPercentMetric(completionMatch[1]);
    const progress = formatPercentMetric(progressMatch[1]);
    return `About ${completion} of learners completed these lessons, while the average viewer reached ${progress} of the lesson.`;
  }
  if (completionMatch) {
    return `About ${formatPercentMetric(completionMatch[1])} of learners completed these lessons.`;
  }
  if (progressMatch) {
    return `The average viewer reached ${formatPercentMetric(progressMatch[1])} of the lesson.`;
  }
  return text
    .replace(/\bcompletion_rate\b/gi, 'completion')
    .replace(/\baverage_progress\b/gi, 'average progress')
    .replace(/\bengagement_score\b/gi, 'engagement score')
    .replace(/_/g, ' ');
}

function rangeDates(rangeKey) {
  const days = Math.max(1, Number(rangeKey || 7));
  const to = new Date();
  const from = new Date();
  from.setDate(to.getDate() - (days - 1));

  const fmt = (date) => date.toISOString().slice(0, 10);
  return {
    from: fmt(from),
    to: fmt(to),
  };
}

function isStaffUser(user) {
  return Boolean(user?.is_staff || user?.is_superuser);
}

function emptyAnalyticsStats() {
  return {
    metrics: {
      totalLessons: 0,
      publishedLessons: 0,
      draftLessons: 0,
      totalViews: 0,
      uniqueViewers: 0,
      watchHours: 0,
      completionRate: 0,
      averageProgress: 0,
      engagementEvents: 0,
      likes: 0,
      comments: 0,
      trendViewsPct: null,
      trendWatchPct: null,
      trendCompletionPct: null,
      trendEngagementPct: null,
    },
    series: [],
    topLessons: [],
    recentLessons: [],
    recentActivity: [],
    categoryBreakdown: [],
    categoryOptions: [],
    insight: 'No activity yet. Share a published lesson to collect insights.',
    isEmpty: true,
    meta: {},
  };
}

function normalizeCategories(source) {
  if (!Array.isArray(source)) return [];
  return source
    .map((category) => ({
      id: category?.id ?? category?.slug ?? category?.name,
      slug: String(category?.slug || '').trim(),
      name: String(category?.name || category?.label || category?.slug || '').trim(),
    }))
    .filter((category) => category.slug && category.name);
}

function normalizeSeries(source) {
  if (!Array.isArray(source)) return [];
  return source
    .map((point, index) => {
      if (typeof point === 'number') {
        return {
          label: `D${index + 1}`,
          value: point,
          engagement: point,
        };
      }

      const rawDate = String(point?.label || point?.day || point?.date || `D${index + 1}`);
      return {
        label: rawDate.slice(5, 10) || rawDate.slice(0, 10),
        value: toNumber(point?.total_views ?? point?.views ?? point?.video_plays ?? point?.value, 0),
        engagement: toNumber(point?.engagement ?? point?.engagement_events ?? point?.value, 0),
      };
    })
    .filter((point) => point.value >= 0)
    .slice(-14);
}

function normalizeTopLessons(source) {
  if (!Array.isArray(source)) return [];
  return source
    .slice(0, 8)
    .map((item, index) => {
      const completionPct = toNumber(item?.completion_pct ?? item?.completion_rate ?? item?.completionRate, 0);
      const progressPct = toNumber(
        item?.progress_pct ?? item?.average_progress_pct ?? item?.average_progress ?? item?.retention_pct,
        completionPct,
      );
      return {
        id: item?.lesson_id || item?.id || `top-${index}`,
        title: String(item?.title || item?.name || `Lesson ${index + 1}`),
        progressPct,
        completionPct,
        retentionPct: progressPct,
        views: toNumber(item?.views ?? item?.total_views ?? item?.video_plays, 0),
        engagementEvents: toNumber(item?.engagement_events, 0),
        likes: toNumber(item?.likes, 0),
        comments: toNumber(item?.comments, 0),
      };
    })
    .filter((item) => (
      item.views > 0
      || item.engagementEvents > 0
      || item.likes > 0
      || item.comments > 0
      || item.progressPct > 0
      || item.completionPct > 0
    ));
}

function normalizeRecentLessons(source) {
  if (!Array.isArray(source)) return [];
  return source.slice(0, 8).map((item, index) => ({
    id: item?.lesson_id || item?.id || `recent-${index}`,
    title: String(item?.title || item?.name || `Lesson ${index + 1}`),
    publishedAt: String(item?.latest_activity_at || item?.published_at || item?.updated_at || item?.created_at || ''),
    views: toNumber(item?.views ?? item?.total_views ?? item?.video_plays, 0),
    completionPct: toNumber(item?.completion_pct ?? item?.completion_rate, 0),
    progressPct: toNumber(item?.progress_pct ?? item?.average_progress_pct ?? item?.average_progress, 0),
    engagementEvents: toNumber(item?.engagement_events, 0),
    likes: toNumber(item?.likes, 0),
    comments: toNumber(item?.comments, 0),
  }));
}

function normalizeRecentActivity(source) {
  if (!Array.isArray(source)) return [];
  return source.slice(0, 30).map((item, index) => ({
    id: `${item?.type || 'activity'}-${item?.lesson_id || index}-${item?.timestamp || index}`,
    type: String(item?.type || 'activity').toLowerCase(),
    timestamp: String(item?.timestamp || ''),
    title: String(item?.lesson_title || item?.title || 'Lesson activity'),
    value: item?.value,
  })).map((item) => ({
    ...item,
    progress: Math.max(0, Math.min(100, Math.round(toNumber(item.value, 0)))),
  }));
}

function normalizeCategoryBreakdown(source) {
  if (!Array.isArray(source)) return [];
  return source
    .slice(0, 8)
    .map((item, index) => {
      const views = toNumber(item?.views ?? item?.total_views ?? item?.video_plays, 0);
      const engagement = toNumber(item?.engagement_events ?? item?.engagement, 0);
      const lessonCount = toNumber(item?.lesson_count ?? item?.lessons ?? item?.count, 0);
      const value = Math.max(views, engagement, lessonCount);
      return {
        id: item?.category_slug || item?.slug || item?.category_id || `category-${index}`,
        name: String(item?.category_name || item?.name || item?.category || item?.label || 'Uncategorized'),
        views,
        engagement,
        lessonCount,
        completionRate: toNumber(item?.completion_rate ?? item?.average_progress, 0),
        value,
      };
    })
    .filter((item) => item.value > 0 || item.lessonCount > 0);
}

function trendValue(rawValue, hasActivity) {
  if (!hasActivity) return null;
  const numeric = toNumber(rawValue, NaN);
  if (!Number.isFinite(numeric) || Math.abs(numeric) < 0.05) return null;
  return numeric;
}

function buildInsight(metrics, categoryBreakdown, t) {
  if (metrics.totalLessons <= 0 || (metrics.totalViews <= 0 && metrics.engagementEvents <= 0)) {
    return t('analytics.noActivityInsight');
  }
  if (metrics.completionRate > 0 && metrics.completionRate < 50) {
    return t('analytics.lowCompletionInsight');
  }
  const topCategory = categoryBreakdown[0];
  if (topCategory?.name && topCategory.engagement > 0) {
    return t('analytics.topCategoryInsight', { category: topCategory.name });
  }
  if (metrics.uniqueViewers > 0 && metrics.engagementEvents > metrics.uniqueViewers) {
    return t('analytics.repeatEngagementInsight');
  }
  return t('analytics.defaultInsight');
}

function normalizeAnalyticsStats(payload, t) {
  if (!payload || typeof payload !== 'object') {
    return emptyAnalyticsStats();
  }

  const summary = payload.summary || payload.metrics || payload.kpis || {};
  const charts = payload.charts || {};
  const tables = payload.tables || {};
  const trends = summary.trends || payload.trends || {};
  const watchMinutes = toNumber(summary.estimated_watch_time_minutes ?? summary.watch_time_minutes, 0);
  const totalViews = toNumber(summary.total_views ?? summary.video_plays ?? summary.views, 0);
  const engagementEvents = toNumber(summary.engagement_events, 0);
  const likes = toNumber(summary.likes, 0);
  const comments = toNumber(summary.comments, 0);
  const hasActivity = totalViews > 0 || engagementEvents > 0 || likes > 0 || comments > 0;

  const categoryBreakdown = normalizeCategoryBreakdown(
    charts.category_popularity || charts.category_breakdown || tables.top_categories || payload.category_popularity,
  );

  const metrics = {
    totalLessons: toNumber(summary.total_lessons ?? summary.lessons_published, 0),
    publishedLessons: toNumber(summary.published_lessons ?? summary.lessons_published, 0),
    draftLessons: toNumber(summary.draft_lessons, 0),
    totalViews,
    uniqueViewers: toNumber(summary.unique_viewers, 0),
    watchHours: watchMinutes / 60,
    completionRate: toNumber(summary.completion_rate ?? summary.average_progress, 0),
    averageProgress: toNumber(summary.average_progress, 0),
    engagementEvents,
    likes,
    comments,
    trendViewsPct: trendValue(trends.video_plays_pct ?? trends.total_views_pct ?? payload.trend_views_pct, hasActivity),
    trendWatchPct: trendValue(trends.watch_time_pct ?? payload.trend_watch_pct, hasActivity),
    trendCompletionPct: trendValue(trends.completion_rate_pct ?? payload.trend_completion_pct, hasActivity),
    trendEngagementPct: trendValue(trends.engagement_events_pct ?? payload.trend_engagement_pct, hasActivity),
  };

  const topLessons = normalizeTopLessons(tables.top_lessons || payload.top_lessons || payload.topLessons);
  const recentLessons = normalizeRecentLessons(
    tables.recent_lessons || payload.recent_lessons || payload.recentLessons,
  );
  const recentActivity = normalizeRecentActivity(payload.recent_activity || tables.recent_activity);
  const series = normalizeSeries(
    charts.engagement_trend || charts.views_over_time || payload.views_series || payload.daily_views,
  );
  const categoryOptions = normalizeCategories(payload.options?.categories);

  return {
    metrics,
    series,
    topLessons,
    recentLessons,
    recentActivity,
    categoryBreakdown,
    categoryOptions,
    insight: buildInsight(metrics, categoryBreakdown, t),
    isEmpty: !hasActivity,
    meta: payload.meta || {},
  };
}

function TrendBadge({ value }) {
  if (value === null || value === undefined) return null;
  const numeric = toNumber(value, NaN);
  if (!Number.isFinite(numeric)) return null;
  const positive = numeric >= 0;
  return (
    <span className={`rounded-full px-2 py-1 text-[0.68rem] font-semibold ${
      positive
        ? 'bg-emerald-400/15 text-emerald-400'
        : 'bg-rose-400/15 text-rose-300'
    }`}>
      {positive ? '+' : ''}{numeric.toFixed(1)}%
    </span>
  );
}

function EmptyPanel({ message, className = '' }) {
  return (
    <div className={`flex min-h-36 items-center justify-center rounded-2xl border border-dashed border-[color:var(--border-subtle)] bg-[color:var(--surface-muted)]/30 p-5 text-center text-sm text-[var(--text-secondary)] ${className}`}>
      {message}
    </div>
  );
}

function CompletionRing({ value }) {
  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  const boundedValue = Math.max(0, Math.min(100, toNumber(value)));
  const dashOffset = circumference - (boundedValue / 100) * circumference;

  return (
    <div className="relative h-28 w-28 shrink-0">
      <svg viewBox="0 0 112 112" className="h-full w-full -rotate-90">
        <circle
          cx="56"
          cy="56"
          r={radius}
          fill="none"
          stroke="var(--surface-muted)"
          strokeWidth="10"
        />
        <circle
          cx="56"
          cy="56"
          r={radius}
          fill="none"
          stroke="var(--accent-primary)"
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="font-['Manrope'] text-xl font-extrabold tracking-[-0.03em] text-[var(--text-primary)]">
          {percent(value)}
        </span>
      </div>
    </div>
  );
}

export function analyticsProviderStatusLabel(report) {
  if (!report || report.status === 'empty') return 'No report yet';
  if (report.enabled === false || report.status === 'disabled') return 'AI provider disabled';
  const provider = String(report.provider || '').toLowerCase();
  const enhancementStatus = analyticsEnhancementStatus(report);
  const fallbackUsed = Boolean(report.fallback_used);
  if (provider === 'ollama' && fallbackUsed) return 'Partial Ollama insight with heuristic fallback';
  if (provider === 'ollama') return 'Ollama insight completed';
  if (provider === 'heuristic' && fallbackUsed) return 'Heuristic fallback shown';
  if (provider === 'heuristic') return 'Heuristic insight';
  if (fallbackUsed) return 'Heuristic fallback shown';
  if (enhancementStatus === 'partial') return 'Partial Ollama insight with heuristic fallback';
  if (provider) return `${provider.charAt(0).toUpperCase()}${provider.slice(1)} analysis`;
  return 'Analysis';
}

function analyticsProviderStatusTone(report) {
  const label = analyticsProviderStatusLabel(report);
  if (label === 'No report yet') return 'bg-[color:var(--surface-muted)]/45 text-[var(--text-secondary)]';
  if (label === 'AI provider disabled') return 'bg-amber-400/15 text-amber-300';
  if (label === 'Partial Ollama insight with heuristic fallback' || label === 'Heuristic fallback shown') {
    return 'bg-amber-400/15 text-amber-300';
  }
  return 'bg-emerald-400/15 text-emerald-300';
}

function analyticsReportHasUsableResult(report) {
  return Boolean(report?.id && String(report?.status || '').toLowerCase() === 'done');
}

function ProviderLabel({ report }) {
  const label = analyticsProviderStatusLabel(report);
  const className = analyticsProviderStatusTone(report);
  return (
    <span className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold ${className}`}>
      {label}
    </span>
  );
}

function analyticsEnhancementStatus(report) {
  return String(report?.enhancement_status || '').trim().toLowerCase();
}

function analyticsRefreshStatus(report) {
  return String(report?.refresh_status || '').trim().toLowerCase();
}

const ANALYTICS_ACTIVE_ENHANCEMENT_STATUSES = new Set([
  'pending',
  'running',
  'analyzing_chunks',
  'chunk_processing',
  'synthesizing',
  'final_synthesis',
  'final_aggregation',
]);
const ANALYTICS_FAILED_ENHANCEMENT_STATUSES = new Set([
  'failed',
  'unavailable',
  'disabled',
  'stale',
  'superseded',
  'degraded',
]);

function analyticsEnhancementPending(report) {
  return Boolean(
    report?.enhancement_pending
    || ANALYTICS_ACTIVE_ENHANCEMENT_STATUSES.has(analyticsEnhancementStatus(report))
    || ANALYTICS_ACTIVE_ENHANCEMENT_STATUSES.has(analyticsRefreshStatus(report)),
  );
}

function analyticsOllamaFallbackFailed(report) {
  return Boolean(
    report?.fallback_used
    && String(report?.provider || '').toLowerCase() === 'heuristic'
    && String(report?.enhancement_provider || '').toLowerCase() === 'ollama'
    && ANALYTICS_FAILED_ENHANCEMENT_STATUSES.has(analyticsEnhancementStatus(report)),
  );
}

function analyticsRetryOnCooldown(report) {
  if (!analyticsOllamaFallbackFailed(report)) return false;
  const availableAt = Date.parse(report?.retry_available_at || report?.metadata?.progressive_enhancement?.retry_available_at || '');
  return Number.isFinite(availableAt) && Date.now() < availableAt;
}

function analyticsUpToDate(report) {
  return Boolean(
    report?.id
    && !analyticsIntelligenceIsStale(report)
    && !analyticsEnhancementPending(report)
    && !analyticsOllamaFallbackFailed(report)
    && String(report?.provider || '').toLowerCase() === 'ollama',
  );
}

function analyticsEnhancementMeta(report) {
  return report?.metadata?.progressive_enhancement || {};
}

export function analyticsEnhancementLabelText(report) {
  const refreshStatus = analyticsRefreshStatus(report);
  if (report?.pending_report_id && ANALYTICS_ACTIVE_ENHANCEMENT_STATUSES.has(refreshStatus)) {
    return 'Updating insight...';
  }
  if (report?.latest_refresh_failed) {
    return 'Latest refresh failed';
  }
  const status = analyticsEnhancementStatus(report);
  const provider = String(report?.enhancement_provider || '').toLowerCase();
  if (provider !== 'ollama') return '';
  const failed = ANALYTICS_FAILED_ENHANCEMENT_STATUSES.has(status);
  const pending = ANALYTICS_ACTIVE_ENHANCEMENT_STATUSES.has(status);
  const meta = analyticsEnhancementMeta(report);
  const phase = String(meta.phase || '').toLowerCase();
  const chunkCount = Number(meta.chunk_count || report?.metadata?.chunk_count || 0);
  const completedChunks = Number(meta.completed_chunks || report?.metadata?.completed_chunks || 0);
  const failedChunks = Number(meta.failed_chunks || report?.metadata?.failed_chunks || 0);
  const degradedReason = String(meta.degraded_reason || '').toLowerCase();
  const chunkAnalysisTimedOut = failed
    && chunkCount > 0
    && completedChunks <= 0
    && degradedReason === 'chunk_timeout';
  const partialProgressTimedOut = failed
    && completedChunks > 0
    && degradedReason === 'ollama_no_progress_timeout';
  const finalAggregationTimedOut = failed
    && chunkCount > 0
    && completedChunks >= chunkCount
    && degradedReason === 'final_aggregation_timeout';
  const processedChunks = Math.min(chunkCount, completedChunks + failedChunks);
  const currentChunk = Number(meta.current_chunk_index || meta.current_chunk?.index || 0);
  const visibleProgress = Math.max(processedChunks, currentChunk);
  const usableReport = analyticsReportHasUsableResult(report);
  const reportProvider = String(report?.provider || '').toLowerCase();
  const partialWithFallback = Boolean(
    usableReport
    && reportProvider === 'ollama'
    && report?.fallback_used
    && (status === 'partial' || status === 'degraded' || failedChunks > 0 || completedChunks > 0),
  );
  const heuristicFallback = Boolean(
    usableReport
    && reportProvider === 'heuristic'
    && report?.fallback_used
    && failed,
  );
  if (pending) {
    if (phase === 'synthesizing' || phase === 'final_synthesis' || phase === 'final_aggregation') return 'Synthesizing final insight';
    if (chunkCount > 1) return `Ollama analyzing ${visibleProgress}/${chunkCount} chunks`;
    return 'Ollama enhancement running';
  }
  if (status === 'done') {
    return failedChunks > 0 || report?.fallback_used
      ? 'Partial Ollama insight with heuristic fallback'
      : 'Ollama insight completed';
  }
  if (status === 'partial' || partialWithFallback) return 'Partial Ollama insight with heuristic fallback';
  if (heuristicFallback) return 'Heuristic fallback shown';
  if (failed) {
    if (usableReport) return 'Heuristic fallback shown';
    if (finalAggregationTimedOut) return 'Ollama enhancement failed during final summary';
    if (chunkAnalysisTimedOut) return 'Ollama enhancement failed during chunk analysis';
    if (partialProgressTimedOut) return 'Ollama enhancement timed out after partial progress';
    if (analyticsOllamaFallbackFailed(report)) return 'Heuristic fallback shown';
    return 'Ollama enhancement failed';
  }
  return '';
}

function AnalyticsEnhancementLabel({ report }) {
  const status = analyticsEnhancementStatus(report);
  const refreshStatus = analyticsRefreshStatus(report);
  const pending = ANALYTICS_ACTIVE_ENHANCEMENT_STATUSES.has(status)
    || ANALYTICS_ACTIVE_ENHANCEMENT_STATUSES.has(refreshStatus);
  const failed = ANALYTICS_FAILED_ENHANCEMENT_STATUSES.has(status) || Boolean(report?.latest_refresh_failed);
  const label = analyticsEnhancementLabelText(report);
  if (!label) return null;
  const className = failed
    ? 'bg-amber-400/15 text-amber-300'
    : pending
      ? 'bg-sky-400/15 text-sky-300'
      : 'bg-emerald-400/15 text-emerald-300';
  return (
    <span className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold ${className}`}>
      {label}
    </span>
  );
}

function IntelligenceLanguageLabel({ report }) {
  if (!report || report.status === 'empty') return null;
  const language = String(report.output_language || report.metadata?.output_language || '').toLowerCase();
  const detected = String(report.detected_language || report.metadata?.detected_language || '').toLowerCase();
  const label = language === 'tr'
    ? 'Turkish analysis'
    : language === 'en'
      ? 'English analysis'
      : detected === 'unknown'
        ? 'Language uncertain'
        : 'Language auto';
  return (
    <span className="inline-flex items-center rounded-full bg-[color:var(--surface-muted)]/45 px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
      {label}
    </span>
  );
}

function analyticsInputWasCompacted(report) {
  return Boolean(report?.metadata?.input_truncated || report?.metadata?.compaction?.input_truncated);
}

function analyticsIntelligenceIsStale(report) {
  return Boolean(report?.is_stale || report?.insight_stale);
}

export function analyticsDisplayReportAfterRefresh(currentReport, nextReport) {
  if (!nextReport) return currentReport || null;
  if (!currentReport || !analyticsReportHasUsableResult(currentReport)) return nextReport;
  if (nextReport.active_report_id && nextReport.active_report_id === currentReport.id) return nextReport;

  const nextRefreshStatus = analyticsRefreshStatus(nextReport) || analyticsEnhancementStatus(nextReport);
  const nextPending = analyticsEnhancementPending(nextReport);
  const nextFailedWithoutResult = (
    !analyticsReportHasUsableResult(nextReport)
    && (nextReport.latest_refresh_failed || ANALYTICS_FAILED_ENHANCEMENT_STATUSES.has(nextRefreshStatus))
  );

  if (!nextPending && !nextFailedWithoutResult) return nextReport;

  return {
    ...currentReport,
    current_source_hash: nextReport.current_source_hash || nextReport.source_hash || currentReport.current_source_hash || '',
    current_run_key: nextReport.current_run_key || nextReport.run_key || currentReport.current_run_key || '',
    insight_stale: true,
    is_stale: true,
    refresh_status: nextRefreshStatus || (nextPending ? 'pending' : 'failed'),
    enhancement_pending: nextPending,
    latest_refresh_failed: Boolean(nextFailedWithoutResult),
    pending_report_id: nextPending ? (nextReport.pending_report_id || nextReport.id || null) : null,
    latest_refresh_report_id: nextReport.latest_refresh_report_id || nextReport.id || null,
  };
}

function RiskBadge({ level, outputLanguage = 'en' }) {
  const normalized = String(level || '').toLowerCase();
  const normalizedLanguage = String(outputLanguage || '').toLowerCase();
  const className = normalized === 'high'
    ? 'bg-rose-400/15 text-rose-300'
    : normalized === 'low'
      ? 'bg-emerald-400/15 text-emerald-300'
      : 'bg-amber-400/15 text-amber-300';
  const label = normalizedLanguage === 'tr'
    ? { high: 'Yüksek risk', medium: 'Orta risk', low: 'Düşük risk' }[normalized] || 'Risk bekleniyor'
    : normalized ? `${normalized.charAt(0).toUpperCase()}${normalized.slice(1)} risk` : 'Risk pending';
  return (
    <span className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold ${className}`}>
      {label}
    </span>
  );
}

function HealthScoreRing({ value }) {
  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  const boundedValue = Math.max(0, Math.min(100, toNumber(value)));
  const dashOffset = circumference - (boundedValue / 100) * circumference;

  return (
    <div className="relative h-28 w-28 shrink-0">
      <svg viewBox="0 0 112 112" className="h-full w-full -rotate-90">
        <circle
          cx="56"
          cy="56"
          r={radius}
          fill="none"
          stroke="var(--surface-muted)"
          strokeWidth="10"
        />
        <circle
          cx="56"
          cy="56"
          r={radius}
          fill="none"
          stroke="var(--accent-primary)"
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-['Manrope'] text-2xl font-extrabold tracking-[-0.03em] text-[var(--text-primary)]">
          {Math.round(boundedValue)}
        </span>
        <span className="text-[0.62rem] font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">score</span>
      </div>
    </div>
  );
}

function intelligenceItemText(item) {
  if (typeof item === 'string') return humanizeAnalyticsSignal(item);
  if (!item || typeof item !== 'object') return '';
  return humanizeAnalyticsSignal(item.message || item.recommendation || item.title || item.type || '');
}

function intelligenceItemDetail(item) {
  if (!item || typeof item !== 'object') return '';
  return humanizeAnalyticsSignal(item.evidence || item.action_label || '');
}

function CollapsibleAnalyticsSection({ title, count = 0, icon: Icon = Lightbulb, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="rounded-2xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-muted)]/20">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="focus-ring flex w-full items-center justify-between gap-3 rounded-2xl px-4 py-3 text-left"
      >
        <span className="flex min-w-0 items-center gap-2">
          <Icon size={16} className="shrink-0 text-[var(--accent-primary)]" />
          <span className="min-w-0">
            <span className="block text-sm font-bold text-[var(--text-primary)]">{title}</span>
            <span className="mt-0.5 block text-xs text-[var(--text-secondary)]">
              {count} item{count === 1 ? '' : 's'}
            </span>
          </span>
        </span>
        <ChevronDown
          size={16}
          className={`shrink-0 text-[var(--text-secondary)] transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>
      {open && <div className="space-y-2 px-4 pb-4">{children}</div>}
    </section>
  );
}

function IntelligenceList({ title, items, emptyText, icon: Icon = Lightbulb }) {
  const visibleItems = Array.isArray(items) ? items.filter(Boolean).slice(0, 6) : [];
  return (
    <CollapsibleAnalyticsSection title={title} count={visibleItems.length} icon={Icon}>
      {visibleItems.length > 0 ? (
        <div className="space-y-2">
          {visibleItems.map((item, index) => {
            const text = intelligenceItemText(item);
            const detail = intelligenceItemDetail(item);
            return (
              <article key={`${title}-${index}-${text}`} className="rounded-2xl bg-[color:var(--surface-muted)]/30 p-3">
                <p className="text-sm font-semibold text-[var(--text-primary)]">{text}</p>
                {detail && <p className="mt-1 text-xs leading-relaxed text-[var(--text-secondary)]">{detail}</p>}
              </article>
            );
          })}
        </div>
      ) : (
        <p className="rounded-2xl bg-[color:var(--surface-muted)]/25 p-3 text-sm text-[var(--text-secondary)]">{emptyText}</p>
      )}
    </CollapsibleAnalyticsSection>
  );
}

function analyticsActionLabel(item) {
  const type = String(item?.type || '').toLowerCase();
  if (type.includes('example') || type.includes('intro')) return 'Add examples';
  if (type.includes('narration') || type.includes('segment') || type.includes('pacing')) return 'Expand narration';
  if (type.includes('cover') || type.includes('discovery') || type.includes('category')) return 'Improve organization';
  if (type.includes('completion') || type.includes('progress') || type.includes('retention')) return 'Review complex lessons';
  if (type.includes('engagement') || type.includes('comment')) return 'Prompt learner response';
  return humanizeAnalyticsSignal(type).replace(/\b\w/g, (char) => char.toUpperCase()) || 'Review priority';
}

function analyticsPriorityItems(report) {
  const recommendations = Array.isArray(report?.recommendations) ? report.recommendations : [];
  const lessonActions = Array.isArray(report?.lesson_actions) ? report.lesson_actions : [];
  return [...recommendations, ...lessonActions].filter(Boolean).slice(0, 4);
}

function PriorityFixList({ report }) {
  const items = analyticsPriorityItems(report);
  return (
    <section className="space-y-3">
      <div>
        <p className="text-sm font-bold text-[var(--text-primary)]">What to fix first</p>
        <p className="mt-1 text-xs text-[var(--text-secondary)]">Start with the highest-signal lesson improvements from this range.</p>
      </div>
      {items.length > 0 ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {items.map((item, index) => {
            const text = intelligenceItemText(item);
            const detail = intelligenceItemDetail(item);
            const lessonTitle = humanizeAnalyticsSignal(item?.lesson_title || '');
            return (
              <article key={`priority-fix-${index}-${text}`} className="rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-muted)]/25 p-4">
                <span className="inline-flex rounded-full bg-[color:rgba(208,188,255,0.14)] px-3 py-1 text-[0.68rem] font-semibold uppercase tracking-[0.1em] text-[var(--accent-primary)]">
                  {analyticsActionLabel(item)}
                </span>
                {lessonTitle && <p className="mt-3 text-xs font-semibold text-[var(--text-secondary)]">{lessonTitle}</p>}
                <p className="mt-2 text-sm font-semibold leading-relaxed text-[var(--text-primary)]">{text || 'Review this analytics signal.'}</p>
                {detail && <p className="mt-2 text-xs leading-relaxed text-[var(--text-secondary)]">{detail}</p>}
              </article>
            );
          })}
        </div>
      ) : (
        <p className="rounded-xl bg-[color:var(--surface-muted)]/25 p-4 text-sm text-[var(--text-secondary)]">
          No priority fixes yet. More learner activity will make this section more specific.
        </p>
      )}
    </section>
  );
}

function analyticsIntelligenceCopyText(report) {
  if (!report || report.status !== 'done') return '';
  const lines = [
    `Analytics Intelligence (${report.provider || 'unknown'})`,
    `Health score: ${toNumber(report.health_score)} / 100`,
    `Risk: ${report.risk_level || 'unknown'}`,
    '',
    humanizeAnalyticsSignal(report.summary || ''),
    '',
    'Insights:',
    ...(Array.isArray(report.insights) ? report.insights : []).map((item) => `- ${intelligenceItemText(item)}`),
    '',
    'Recommendations:',
    ...(Array.isArray(report.recommendations) ? report.recommendations : []).map((item) => `- ${intelligenceItemText(item)}`),
    '',
    'Lesson actions:',
    ...(Array.isArray(report.lesson_actions) ? report.lesson_actions : []).map((item) => `- ${item.lesson_title ? `${item.lesson_title}: ` : ''}${intelligenceItemText(item)}`),
    '',
    'Category actions:',
    ...(Array.isArray(report.category_actions) ? report.category_actions : []).map((item) => `- ${item.category ? `${item.category}: ` : ''}${intelligenceItemText(item)}`),
  ];
  return lines.filter((line, index) => line || lines[index - 1]).join('\n').trim();
}

function CategoryDonut({ categories }) {
  const { t, formatNumber } = useI18n();
  const compact = useCallback((value) => formatNumber(value, {
    notation: 'compact',
    maximumFractionDigits: 1,
  }), [formatNumber]);
  const visibleCategories = categories.slice(0, 6);
  const total = visibleCategories.reduce((sum, category) => sum + Math.max(0, toNumber(category.value, 0)), 0);
  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;

  if (total <= 0 || visibleCategories.length === 0) return null;

  return (
    <div className="flex flex-col gap-5 rounded-2xl bg-[color:var(--surface-muted)]/25 p-4 sm:flex-row sm:items-center">
      <div className="relative h-28 w-28 shrink-0">
        <svg viewBox="0 0 112 112" className="h-full w-full -rotate-90">
          <circle
            cx="56"
            cy="56"
            r={radius}
            fill="none"
            stroke="var(--surface-muted)"
            strokeWidth="12"
          />
          {visibleCategories.map((category, index) => {
            const value = Math.max(0, toNumber(category.value, 0));
            const segmentLength = (value / total) * circumference;
            const dashOffset = -offset;
            offset += segmentLength;
            return (
              <circle
                key={`donut-${category.id}`}
                cx="56"
                cy="56"
                r={radius}
                fill="none"
                stroke={DONUT_COLORS[index % DONUT_COLORS.length]}
                strokeWidth="12"
                strokeLinecap="butt"
                strokeDasharray={`${segmentLength} ${circumference - segmentLength}`}
                strokeDashoffset={dashOffset}
              />
            );
          })}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-['Manrope'] text-lg font-extrabold tracking-[-0.03em] text-[var(--text-primary)]">
            {compact(total)}
          </span>
          <span className="text-[0.62rem] font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">{t('analytics.events')}</span>
        </div>
      </div>
      <div className="min-w-0 flex-1 space-y-2">
        {visibleCategories.slice(0, 4).map((category, index) => (
          <div key={`legend-${category.id}`} className="flex items-center justify-between gap-3 text-xs">
            <span className="flex min-w-0 items-center gap-2">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: DONUT_COLORS[index % DONUT_COLORS.length] }}
              />
              <span className="line-clamp-1 font-semibold text-[var(--text-primary)]">{category.name}</span>
            </span>
            <span className="font-semibold text-[var(--text-secondary)]">{compact(category.value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function KpiCard({ icon: Icon, label, value, trend, hint, emptyHint, active, children }) {
  return (
    <SurfaceCard className="min-h-[10.5rem] space-y-4">
      <div className="flex items-start justify-between gap-3">
        <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-[color:rgba(208,188,255,0.1)] text-[var(--accent-primary)]">
          <Icon size={18} />
        </span>
        <TrendBadge value={trend} />
      </div>
      <div>
        <p className="text-[0.66rem] font-semibold uppercase tracking-[0.13em] text-[var(--text-secondary)]">{label}</p>
        <p className="mt-1 font-['Manrope'] text-3xl font-extrabold tracking-[-0.03em] text-[var(--text-primary)]">{value}</p>
        <p className="mt-2 min-h-[1rem] text-[0.72rem] leading-relaxed text-[var(--text-secondary)]">
          {active ? hint : emptyHint}
        </p>
      </div>
      {children}
    </SurfaceCard>
  );
}

export default function Analytics({ user }) {
  const { t, formatDate, formatDateTime, formatDuration, formatNumber, formatViews } = useI18n();
  const location = useLocation();
  const { capabilities } = useCapabilities();
  const intelligenceFeatureEnabled = featureEnabled(capabilities, 'intelligence');
  const avatarFeatureEnabled = featureEnabled(capabilities, 'avatar');
  const directAnalyticsState = useMemo(() => {
    const params = new URLSearchParams(location.search || '');
    const range = String(params.get('range') || '').trim();
    const category = String(params.get('category') || '').trim();
    return {
      hasDirectState: Boolean(range || category),
      rangeKey: RANGE_OPTIONS.some((option) => option.key === range) ? range : '',
      categorySlug: category,
    };
  }, [location.search]);
  const storedAnalyticsState = useMemo(
    () => (directAnalyticsState.hasDirectState ? {} : readRouteSessionState('analytics', user)),
    [directAnalyticsState.hasDirectState, user],
  );
  const [rangeKey, setRangeKey] = useState(
    () => directAnalyticsState.rangeKey || String(storedAnalyticsState.rangeKey || '7'),
  );
  const [categorySlug, setCategorySlug] = useState(
    () => directAnalyticsState.categorySlug || String(storedAnalyticsState.categorySlug || ''),
  );
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [stats, setStats] = useState(() => emptyAnalyticsStats());
  const [categories, setCategories] = useState([]);
  const [analyticsCategories, setAnalyticsCategories] = useState([]);
  const [recentActivityExpanded, setRecentActivityExpanded] = useState(
    () => Boolean(storedAnalyticsState.recentActivityExpanded),
  );
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createError, setCreateError] = useState('');
  const [intelligenceReport, setIntelligenceReport] = useState(null);
  const [intelligenceLoading, setIntelligenceLoading] = useState(false);
  const [intelligenceAnalyzing, setIntelligenceAnalyzing] = useState(false);
  const [intelligenceError, setIntelligenceError] = useState('');
  const [intelligenceCopied, setIntelligenceCopied] = useState(false);
  const [intelligenceLoadedFilterKey, setIntelligenceLoadedFilterKey] = useState('');

  const canCreateLesson = canAccessStudio(user);
  const canReviewModeration = isStaffUser(user);
  const hasActivity = !stats.isEmpty;
  const compact = useCallback((value, options = {}) => formatNumber(value, {
    notation: 'compact',
    maximumFractionDigits: 1,
    ...options,
  }), [formatNumber]);
  const analyticsFilters = useMemo(() => {
    const dateRange = rangeDates(rangeKey);
    return {
      ...dateRange,
      range: rangeKey,
      category: categorySlug || undefined,
    };
  }, [categorySlug, rangeKey]);
  const analyticsFilterKey = useMemo(() => JSON.stringify(analyticsFilters), [analyticsFilters]);

  usePageLoading(loading, 'analytics-dashboard');

  useEffect(() => {
    if (!directAnalyticsState.hasDirectState) return;
    setRangeKey(directAnalyticsState.rangeKey || '7');
    setCategorySlug(directAnalyticsState.categorySlug || '');
  }, [directAnalyticsState]);

  useEffect(() => {
    writeRouteSessionState('analytics', user, {
      rangeKey,
      categorySlug,
      recentActivityExpanded,
      scrollY: typeof window !== 'undefined' ? window.scrollY : 0,
    });
  }, [categorySlug, rangeKey, recentActivityExpanded, user]);

  useEffect(() => onRouteReset('analytics', () => {
    clearRouteSessionState('analytics', user);
    setRangeKey('7');
    setCategorySlug('');
    setRecentActivityExpanded(false);
    window.scrollTo({ top: 0, behavior: 'auto' });
  }), [user]);

  useEffect(() => {
    if (loading || directAnalyticsState.hasDirectState || !storedAnalyticsState.scrollY) return undefined;
    const restoreId = window.requestAnimationFrame(() => {
      window.scrollTo({ top: Number(storedAnalyticsState.scrollY) || 0, behavior: 'auto' });
    });
    return () => window.cancelAnimationFrame(restoreId);
  }, [directAnalyticsState.hasDirectState, loading, storedAnalyticsState.scrollY]);

  useEffect(() => {
    const persistScroll = () => {
      writeRouteSessionState('analytics', user, {
        rangeKey,
        categorySlug,
        recentActivityExpanded,
        scrollY: window.scrollY,
      });
    };
    window.addEventListener('pagehide', persistScroll);
    window.addEventListener('beforeunload', persistScroll);
    return () => {
      persistScroll();
      window.removeEventListener('pagehide', persistScroll);
      window.removeEventListener('beforeunload', persistScroll);
    };
  }, [categorySlug, rangeKey, recentActivityExpanded, user]);

  const loadStats = useCallback(async (activeRef = { current: true }) => {
    setLoading(true);
    setError('');

    try {
      const payload = await fetchMyAnalytics(analyticsFilters);

      if (!activeRef.current) return;
      const normalized = normalizeAnalyticsStats(payload, t);
      setStats(normalized);
      setAnalyticsCategories(normalized.categoryOptions);
    } catch (statsError) {
      if (!activeRef.current) return;
      setStats(emptyAnalyticsStats());
      setAnalyticsCategories([]);
      setError(statsError.message || t('analytics.couldNotLoad'));
    } finally {
      if (activeRef.current) {
        setLoading(false);
      }
    }
  }, [analyticsFilters, t, user]);

  const loadIntelligenceReport = useCallback(async (activeRef = { current: true }) => {
    if (!intelligenceFeatureEnabled) {
      setIntelligenceReport(null);
      setIntelligenceError('');
      setIntelligenceLoadedFilterKey('');
      setIntelligenceLoading(false);
      return;
    }
    setIntelligenceLoading(true);
    setIntelligenceError('');
    setIntelligenceLoadedFilterKey('');

    try {
      const payload = await fetchMyAnalyticsIntelligence(analyticsFilters);
      if (!activeRef.current) return;
      setIntelligenceReport(payload);
      setIntelligenceLoadedFilterKey(analyticsFilterKey);
    } catch (intelligenceLoadError) {
      if (!activeRef.current) return;
      setIntelligenceReport(null);
      setIntelligenceLoadedFilterKey(analyticsFilterKey);
      setIntelligenceError(intelligenceLoadError.message || t('analytics.intelligenceUnavailable'));
    } finally {
      if (activeRef.current) {
        setIntelligenceLoading(false);
      }
    }
  }, [analyticsFilterKey, analyticsFilters, intelligenceFeatureEnabled, t]);

  useEffect(() => {
    const activeRef = { current: true };
    loadStats(activeRef);
    return () => {
      activeRef.current = false;
    };
  }, [loadStats, refreshNonce]);

  useEffect(() => {
    const activeRef = { current: true };
    loadIntelligenceReport(activeRef);
    return () => {
      activeRef.current = false;
    };
  }, [intelligenceFeatureEnabled, loadIntelligenceReport, refreshNonce]);

  useEffect(() => {
    if (!intelligenceFeatureEnabled || !analyticsEnhancementPending(intelligenceReport)) return undefined;

    let active = true;
    const poll = async () => {
      try {
        const payload = await fetchMyAnalyticsIntelligence(analyticsFilters);
        if (!active) return;
        setIntelligenceReport(payload);
        setIntelligenceLoadedFilterKey(analyticsFilterKey);
      } catch {
        // Keep the heuristic report visible if a polling read fails.
      }
    };

    const intervalId = window.setInterval(poll, ANALYTICS_INTELLIGENCE_ENHANCEMENT_POLL_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [analyticsFilterKey, analyticsFilters, intelligenceFeatureEnabled, intelligenceReport]);

  useEffect(() => {
    if (!canCreateLesson) {
      setCategories([]);
      return;
    }

    fetchCategories()
      .then((payload) => setCategories(Array.isArray(payload) ? payload : []))
      .catch(() => setCategories([]));
  }, [canCreateLesson]);

  useEffect(() => {
    const handleCreateLessonRequest = () => {
      if (canCreateLesson) {
        setCreateModalOpen(true);
      }
    };

    window.addEventListener('visus:create-lesson-request', handleCreateLessonRequest);
    return () => window.removeEventListener('visus:create-lesson-request', handleCreateLessonRequest);
  }, [canCreateLesson]);

  const handleCreateLesson = async ({
    file,
    coverFile,
    title,
    category,
    pauseSec,
    whiteboardModeAll,
    avatarEnabled,
  }) => {
    if (!file) return;

    setCreateError('');
    setCreateSubmitting(true);

    const formData = new FormData();
    formData.append('lesson_file', file);
    if (coverFile) formData.append('cover_file', coverFile);
    if (title) formData.append('title', title);
    if (category) formData.append('category', category);
    if (user?.id) formData.append('user_id', user.id);
    if (pauseSec) formData.append('pause_sec', pauseSec);
    if (whiteboardModeAll) formData.append('whiteboard_mode_all', '1');
    formData.append('avatar_enabled', avatarFeatureEnabled && avatarEnabled ? '1' : '0');

    try {
      await createProject(formData);
      setCreateModalOpen(false);
    } catch (createLessonError) {
      setCreateError(createLessonError.message || 'Project upload failed.');
    } finally {
      setCreateSubmitting(false);
    }
  };

  const handleAnalyzeAnalytics = useCallback(async ({ auto = false, force = false } = {}) => {
    if (!intelligenceFeatureEnabled) return null;
    if (analyticsEnhancementPending(intelligenceReport)) return null;
    setIntelligenceAnalyzing(true);
    setIntelligenceError('');
    setIntelligenceCopied(false);

    try {
      const payload = await analyzeMyAnalyticsIntelligence(analyticsFilters, { force: Boolean(force) && !auto });
      setIntelligenceReport((currentReport) => analyticsDisplayReportAfterRefresh(currentReport, payload));
      setIntelligenceLoadedFilterKey(analyticsFilterKey);
      return payload;
    } catch (analyzeError) {
      setIntelligenceError(analyzeError.message || 'Analytics analysis failed.');
      return null;
    } finally {
      setIntelligenceAnalyzing(false);
    }
  }, [analyticsFilterKey, analyticsFilters, intelligenceFeatureEnabled, intelligenceReport]);

  const handleCopyIntelligence = async () => {
    const text = analyticsIntelligenceCopyText(intelligenceReport);
    if (!text) return;
    try {
      await copyTextToClipboard(text);
      setIntelligenceCopied(true);
      window.setTimeout(() => setIntelligenceCopied(false), 2200);
    } catch {
      setIntelligenceError('Could not copy analytics suggestions.');
    }
  };

  const seriesMax = useMemo(
    () => Math.max(1, ...stats.series.map((point) => toNumber(point.value, 0))),
    [stats.series],
  );
  const hasChartActivity = stats.series.some((point) => toNumber(point.value, 0) > 0);
  const categoryMax = useMemo(
    () => Math.max(1, ...stats.categoryBreakdown.map((item) => toNumber(item.value, 0))),
    [stats.categoryBreakdown],
  );
  const visibleRecentActivity = recentActivityExpanded
    ? stats.recentActivity
    : stats.recentActivity.slice(0, 3);
  const intelligenceStale = analyticsIntelligenceIsStale(intelligenceReport);
  const intelligenceCompacted = analyticsInputWasCompacted(intelligenceReport);
  const intelligenceEnhancementPending = analyticsEnhancementPending(intelligenceReport);
  const intelligenceRefreshFailed = Boolean(intelligenceReport?.latest_refresh_failed);
  const intelligenceEnhancementFailed = (
    ANALYTICS_FAILED_ENHANCEMENT_STATUSES.has(analyticsEnhancementStatus(intelligenceReport))
    && !analyticsReportHasUsableResult(intelligenceReport)
  ) || intelligenceRefreshFailed;
  const intelligenceRetryOllama = analyticsOllamaFallbackFailed(intelligenceReport);
  const intelligenceRetryCooldown = analyticsRetryOnCooldown(intelligenceReport);
  const intelligenceUpToDate = analyticsUpToDate(intelligenceReport);
  const intelligenceButtonDisabled = intelligenceAnalyzing
    || intelligenceEnhancementPending
    || intelligenceReport?.enabled === false
    || intelligenceRetryCooldown
    || intelligenceUpToDate;
  const intelligenceButtonLabel = intelligenceAnalyzing
    ? 'Analyzing...'
    : intelligenceEnhancementPending
      ? 'Enhancing...'
      : intelligenceRetryCooldown
        ? 'Retry available soon'
        : intelligenceRetryOllama
          ? 'Retry Ollama'
          : intelligenceUpToDate
            ? 'Up to date'
            : intelligenceStale
              ? 'Re-analyze'
              : 'Analyze analytics';

  return (
    <div className="space-y-7 pb-8">
      <header className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="font-['Manrope'] text-4xl font-extrabold tracking-[-0.04em] text-[var(--text-primary)]">{t('analytics.performanceOverview')}</h1>
          <p className="mt-2 text-sm text-[var(--text-secondary)]">{t('analytics.performanceBody')}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {analyticsCategories.length > 0 && (
            <label className="focus-within:ring-focus inline-flex h-10 items-center gap-2 rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-xs font-semibold text-[var(--text-secondary)]">
              <Filter size={14} />
              <span className="sr-only">{t('analytics.filterByCategory')}</span>
              <select
                value={categorySlug}
                onChange={(event) => setCategorySlug(event.target.value)}
                className="h-8 min-w-[10rem] rounded-full border-0 bg-[var(--surface-elevated)] px-1 text-xs font-semibold text-[var(--text-primary)] outline-none"
                style={{ backgroundColor: 'var(--surface-elevated)', color: 'var(--text-primary)' }}
              >
                <option value="" style={{ backgroundColor: 'var(--surface-elevated)', color: 'var(--text-primary)' }}>{t('analytics.allCategories')}</option>
                {analyticsCategories.map((category) => (
                  <option
                    key={category.slug}
                    value={category.slug}
                    style={{ backgroundColor: 'var(--surface-elevated)', color: 'var(--text-primary)' }}
                  >
                    {category.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <div className="inline-flex items-center gap-1 rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-elevated)] p-1">
            {RANGE_OPTIONS.map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => setRangeKey(option.key)}
                className={`focus-ring rounded-full px-4 py-2 text-xs font-semibold transition ${
                  rangeKey === option.key
                    ? 'bg-[var(--surface-container-highest)] text-[var(--accent-primary)]'
                    : 'text-[var(--text-secondary)] hover:bg-[var(--surface-container-high)] hover:text-[var(--text-primary)]'
                }`}
              >
              {t(option.labelKey)}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={() => setRefreshNonce((value) => value + 1)}
            disabled={loading}
            className="focus-ring inline-flex h-10 items-center gap-2 rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-elevated)] px-4 text-xs font-semibold text-[var(--text-primary)] transition hover:bg-[var(--surface-container-high)] disabled:cursor-wait disabled:opacity-60"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            {t('common.reload')}
          </button>
        </div>
      </header>

      {error && (
        <SurfaceCard className="rounded-2xl bg-[color:var(--feedback-danger-bg)] p-4">
          <p className="text-sm text-[color:var(--feedback-danger-fg)]">{error}</p>
        </SurfaceCard>
      )}

      {!loading && stats.isEmpty && !error && (
        <SurfaceCard className="rounded-3xl border border-[color:rgba(208,188,255,0.22)] bg-[color:rgba(208,188,255,0.08)] p-6">
          <div className="flex flex-col gap-5 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="label-sm">{t('analytics.noAnalyticsYet')}</p>
              <h2 className="mt-1 font-['Manrope'] text-2xl font-extrabold tracking-[-0.03em] text-[var(--text-primary)]">
                {t('analytics.emptyTitle')}
              </h2>
              <p className="mt-2 max-w-2xl text-sm text-[var(--text-secondary)]">
                {t('analytics.emptyBody')}
              </p>
            </div>
            <div className="grid grid-cols-3 gap-2 text-center text-xs text-[var(--text-secondary)]">
              <div className="rounded-2xl bg-[color:var(--surface-muted)]/35 p-3">
                <p className="font-['Manrope'] text-xl font-bold text-[var(--text-primary)]">{compact(stats.metrics.publishedLessons)}</p>
                <p>{t('common.published')}</p>
              </div>
              <div className="rounded-2xl bg-[color:var(--surface-muted)]/35 p-3">
                <p className="font-['Manrope'] text-xl font-bold text-[var(--text-primary)]">{compact(stats.metrics.draftLessons)}</p>
                <p>{t('common.draft')}</p>
              </div>
              <div className="rounded-2xl bg-[color:var(--surface-muted)]/35 p-3">
                <p className="font-['Manrope'] text-xl font-bold text-[var(--text-primary)]">0</p>
                <p>{t('analytics.events')}</p>
              </div>
            </div>
          </div>
        </SurfaceCard>
      )}

      {canReviewModeration && (
        <SurfaceCard className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-start gap-3">
            <span className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[color:rgba(208,188,255,0.14)] text-[var(--accent-primary)]">
              <ShieldCheck size={20} />
            </span>
            <div>
              <p className="label-sm">{t('analytics.moderationReview')}</p>
              <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">
                {t('analytics.staffModerationQueue')}
              </h2>
              <p className="mt-1 text-sm text-[var(--text-secondary)]">
                {t('analytics.moderationReviewBody')}
              </p>
            </div>
          </div>
          <Link
            to="/moderation"
            className="focus-ring inline-flex h-10 items-center justify-center rounded-full bg-[image:var(--accent-gradient)] px-4 text-sm font-bold text-white transition hover:scale-105 active:scale-95"
          >
            {t('analytics.openModeration')}
          </Link>
        </SurfaceCard>
      )}

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          icon={Eye}
          label={t('analytics.views')}
          value={compact(stats.metrics.totalViews)}
          trend={stats.metrics.trendViewsPct}
          active={hasActivity}
          hint={`${compact(stats.metrics.uniqueViewers)} unique viewers`}
          emptyHint={t('analytics.noActivityInsight')}
        />
        <KpiCard
          icon={Clock3}
          label={t('analytics.watchTime')}
          value={formatDuration(stats.metrics.watchHours * 60)}
          trend={stats.metrics.trendWatchPct}
          active={hasActivity}
          hint={stats.meta?.estimated_metrics ? 'Estimated from progress.' : 'Recorded watch time.'}
          emptyHint={t('analytics.noActivityInsight')}
        />
        <SurfaceCard className="flex min-h-[10.5rem] items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-400/15 text-indigo-300">
                <CheckCircle2 size={18} />
              </span>
              <TrendBadge value={stats.metrics.trendCompletionPct} />
            </div>
            <p className="mt-4 text-[0.66rem] font-semibold uppercase tracking-[0.13em] text-[var(--text-secondary)]">{t('analytics.completion')}</p>
            <p className="mt-1 text-sm text-[var(--text-secondary)]">
              {hasActivity ? t('analytics.progressLabel', { value: percent(stats.metrics.averageProgress) }) : t('analytics.noActivityInsight')}
            </p>
          </div>
          <CompletionRing value={stats.metrics.completionRate} />
        </SurfaceCard>
        <KpiCard
          icon={MessageSquare}
          label={t('analytics.engagement')}
          value={compact(stats.metrics.engagementEvents)}
          trend={stats.metrics.trendEngagementPct}
          active={hasActivity}
          hint={t('analytics.likesComments', { likes: compact(stats.metrics.likes), comments: compact(stats.metrics.comments) })}
          emptyHint={t('analytics.noActivityInsight')}
        />
      </section>

      <section className="grid grid-cols-1 items-stretch gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(20rem,0.9fr)]">
        <SurfaceCard className="flex min-h-[24rem] flex-col gap-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">{t('analytics.views')}</h2>
              <p className="text-xs text-[var(--text-secondary)]">Recorded progress activity by day</p>
            </div>
            <span className="rounded-full bg-[color:var(--surface-muted)]/40 px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
              {rangeKey} days
            </span>
          </div>

          {hasChartActivity ? (
            <div data-testid="analytics-chart-body" className="flex min-h-[17rem] flex-1 flex-col gap-3">
              <div className="relative flex min-h-64 flex-1 items-end gap-2 rounded-2xl bg-[linear-gradient(to_bottom,transparent,rgba(127,127,127,0.06))] px-2 pt-8">
                <div className="pointer-events-none absolute inset-x-2 top-1/3 border-t border-dashed border-[color:var(--border-subtle)]" />
                <div className="pointer-events-none absolute inset-x-2 top-2/3 border-t border-dashed border-[color:var(--border-subtle)]" />
                {stats.series.map((point) => {
                  const pointValue = toNumber(point.value, 0);
                  const hasValue = pointValue > 0;
                  const height = Math.max(8, Math.round((pointValue / seriesMax) * 100));
                  return (
                    <div key={`${point.label}-${point.value}`} className="group z-10 flex min-w-0 flex-1 flex-col justify-end gap-2">
                      <div
                        className={`relative rounded-t-xl transition ${
                          hasValue
                            ? 'bg-[var(--accent-primary)] opacity-95 shadow-[0_0_18px_rgba(123,92,255,0.22)] group-hover:opacity-100'
                            : 'bg-[color:var(--surface-muted)]/45'
                        }`}
                        style={{
                          height: hasValue ? `${height}%` : '0.35rem',
                          minHeight: hasValue ? '2.5rem' : '0.35rem',
                        }}
                      >
                        <span className="absolute -top-8 left-1/2 hidden -translate-x-1/2 whitespace-nowrap rounded-lg bg-[var(--surface-elevated)] px-2 py-1 text-[0.62rem] text-[var(--text-primary)] shadow-soft group-hover:block">
                          {compact(point.value)}
                        </span>
                      </div>
                      <span className="truncate text-center text-[0.62rem] font-semibold uppercase tracking-[0.08em] text-[var(--text-secondary)]">{point.label}</span>
                    </div>
                  );
                })}
              </div>
              <div className="flex items-center justify-between text-xs text-[var(--text-secondary)]">
                <span>0</span>
                <span>{formatViews(seriesMax)}</span>
              </div>
            </div>
          ) : (
            <EmptyPanel message="No recorded activity in this range." className="min-h-[17rem] flex-1" />
          )}
        </SurfaceCard>

        <SurfaceCard className="flex min-h-[24rem] flex-col gap-6 xl:max-h-[34rem]">
          <div>
            <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Category Breakdown</h2>
            <p className="text-xs text-[var(--text-secondary)]">{t('analytics.engagement')}</p>
          </div>
          {stats.categoryBreakdown.length > 0 ? (
            <div data-testid="analytics-category-list" className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
              {stats.categoryBreakdown.length <= 6 && (
                <CategoryDonut categories={stats.categoryBreakdown} />
              )}
              {stats.categoryBreakdown.map((category, index) => {
                const width = Math.max(6, Math.round((category.value / categoryMax) * 100));
                const color = DONUT_COLORS[index % DONUT_COLORS.length];
                return (
                  <article key={category.id} data-testid="analytics-category-row" className="space-y-2">
                    <div className="flex items-center justify-between gap-3 text-sm">
                      <p className="line-clamp-1 font-semibold text-[var(--text-primary)]">{category.name}</p>
                      <p className="text-xs font-semibold text-[var(--accent-primary)]">{compact(category.value)}</p>
                    </div>
                    <div className="h-2.5 rounded-full bg-[color:var(--surface-muted)]">
                      <div className="h-full rounded-full" style={{ width: `${width}%`, backgroundColor: color }} />
                    </div>
                    <p className="text-[0.68rem] text-[var(--text-secondary)]">
                      {t('analytics.categoryStats', {
                        views: compact(category.views),
                        events: compact(category.engagement),
                        lessons: compact(category.lessonCount),
                      })}
                    </p>
                  </article>
                );
              })}
            </div>
          ) : (
            <EmptyPanel message={t('analytics.categoryBreakdownEmpty')} className="min-h-[17rem] flex-1" />
          )}
        </SurfaceCard>
      </section>

      <section className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <SurfaceCard className="space-y-6">
          <div>
            <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">{t('analytics.topLessons')}</h2>
            <p className="text-xs text-[var(--text-secondary)]">{t('analytics.rankedByActivity')}</p>
          </div>

          {stats.topLessons.length > 0 ? (
            <div className="space-y-3">
              {stats.topLessons.slice(0, 6).map((lesson, index) => (
                <article key={`top-${lesson.id}`} className="grid grid-cols-[2.25rem_minmax(0,1fr)] gap-3 rounded-2xl bg-[color:var(--surface-muted)]/30 p-3">
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--surface-elevated)] text-sm font-bold text-[var(--accent-primary)]">
                    {index + 1}
                  </span>
                  <div className="min-w-0 space-y-2">
                    <div className="flex items-start justify-between gap-3">
                      <p className="line-clamp-2 text-sm font-semibold text-[var(--text-primary)]">{lesson.title}</p>
                      <span className={`shrink-0 rounded-full px-2 py-1 text-[0.68rem] font-semibold ${
                        lesson.progressPct > 0
                          ? 'bg-emerald-400/15 text-emerald-300'
                          : 'bg-[color:var(--surface-muted)] text-[var(--text-secondary)]'
                      }`}>
                        {lesson.progressPct > 0 ? t('analytics.progressLabel', { value: percent(lesson.progressPct) }) : t('common.noProgressYet')}
                      </span>
                    </div>
                    <div className="h-1.5 rounded-full bg-[color:var(--surface-muted)]">
                      <div className="h-full rounded-full bg-[image:var(--accent-gradient)]" style={{ width: percent(lesson.progressPct) }} />
                    </div>
                    <p className="text-[0.68rem] text-[var(--text-secondary)]">
                      {t('analytics.topLessonStats', {
                        views: compact(lesson.views),
                        events: compact(lesson.engagementEvents),
                        likes: compact(lesson.likes),
                        comments: compact(lesson.comments),
                      })}
                      {lesson.completionPct > 0 ? ` / ${t('analytics.completedLabel', { value: percent(lesson.completionPct) })}` : ''}
                    </p>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyPanel message={t('analytics.topLessonsEmpty')} />
          )}
        </SurfaceCard>

        <SurfaceCard data-testid="analytics-recent-activity-card" className="flex max-h-[34rem] flex-col gap-6 overflow-hidden">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">{t('analytics.recentActivity')}</h2>
              <p className="text-xs text-[var(--text-secondary)]">{t('analytics.aggregateOnly')}</p>
            </div>
            {stats.recentActivity.length > 3 && (
              <button
                type="button"
                onClick={() => setRecentActivityExpanded((value) => !value)}
                className="focus-ring shrink-0 rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-elevated)] px-3 py-1.5 text-xs font-semibold text-[var(--text-primary)] transition hover:bg-[var(--surface-container-high)]"
              >
                {recentActivityExpanded ? t('analytics.showLess') : t('analytics.showMore', { count: formatNumber(stats.recentActivity.length - 3) })}
              </button>
            )}
          </div>
          {stats.recentActivity.length > 0 ? (
            <div data-testid="analytics-recent-activity-list" className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
              {visibleRecentActivity.map((activity) => {
                const activityLabel = {
                  progress: t('analytics.progress'),
                  like: t('analytics.like'),
                  comment: t('analytics.comment'),
                }[activity.type] || t('analytics.activity');
                const activityDescription = {
                  progress: t('analytics.learnerReachedProgress', { progress: activity.progress }),
                  like: t('analytics.learnerLiked'),
                  comment: t('analytics.learnerCommented'),
                }[activity.type] || t('analytics.learnerActivityRecorded');
                return (
                  <article key={activity.id} data-testid="analytics-recent-activity-row" className="grid grid-cols-[0.75rem_minmax(0,1fr)] gap-3">
                    <span className="mt-1.5 h-3 w-3 rounded-full bg-[var(--accent-primary)] shadow-[0_0_0_4px_rgba(208,188,255,0.14)]" />
                    <div className="min-w-0 rounded-2xl bg-[color:var(--surface-muted)]/30 p-4">
                      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
                        <p className="line-clamp-1 min-w-0 text-sm font-semibold text-[var(--text-primary)]">{activity.title}</p>
                        <div className="flex shrink-0 flex-wrap items-center gap-2">
                          <span className="rounded-full bg-[var(--surface-elevated)] px-2 py-1 text-[0.62rem] font-semibold uppercase tracking-[0.1em] text-[var(--text-secondary)]">
                            {activityLabel}
                          </span>
                          <span className="text-[0.68rem] text-[var(--text-secondary)]">{activity.timestamp ? formatDateTime(activity.timestamp) : t('analytics.recent')}</span>
                        </div>
                      </div>
                      <p className="mt-1 text-sm text-[var(--text-secondary)]">{activityDescription}</p>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <EmptyPanel message={t('analytics.recentActivityEmpty')} className="min-h-[17rem] flex-1" />
          )}
        </SurfaceCard>
      </section>

      <section className="overflow-hidden rounded-3xl token-surface-elevated">
        <div className="border-b border-[color:rgba(73,68,84,0.1)] px-5 py-4 sm:px-8 sm:py-6">
          <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">{t('analytics.recentLessons')}</h2>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">{t('analytics.creatorScopedActivity')}</p>
        </div>

        <div className="overflow-x-auto">
          <table className="min-w-full text-left">
            <thead>
              <tr className="text-[0.62rem] uppercase tracking-[0.14em] text-[var(--text-secondary)]">
                <th className="px-5 py-3 font-semibold sm:px-8">{t('analytics.lessonName')}</th>
                <th className="px-5 py-3 font-semibold sm:px-8">{t('analytics.views')}</th>
                <th className="px-5 py-3 font-semibold sm:px-8">{t('analytics.progress')}</th>
                <th className="px-5 py-3 font-semibold sm:px-8">{t('analytics.engagement')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[color:rgba(73,68,84,0.1)]">
              {stats.recentLessons.length > 0 ? (
                stats.recentLessons.map((lesson) => (
                  <tr key={`recent-${lesson.id}`} className="hover:bg-[color:var(--surface-muted)]/40">
                    <td className="px-5 py-4 sm:px-8">
                      <p className="text-sm font-semibold text-[var(--text-primary)]">{lesson.title}</p>
                      <p className="mt-1 text-[0.68rem] text-[var(--text-secondary)]">{t('common.updatedDate', { date: formatDate(lesson.publishedAt) })}</p>
                    </td>
                    <td className="px-5 py-4 text-sm text-[var(--text-primary)] sm:px-8">{compact(lesson.views)}</td>
                    <td className="px-5 py-4 sm:px-8">
                      {lesson.progressPct > 0 ? (
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-14 rounded-full bg-[color:var(--surface-muted)]">
                            <div className="h-full rounded-full bg-emerald-400" style={{ width: percent(lesson.progressPct) }} />
                          </div>
                          <span className="text-xs font-medium text-[var(--text-primary)]">{percent(lesson.progressPct)}</span>
                        </div>
                      ) : (
                        <span className="text-xs text-[var(--text-secondary)]">{t('common.noProgressYet')}</span>
                      )}
                    </td>
                    <td className="px-5 py-4 text-sm text-[var(--text-primary)] sm:px-8">
                      {compact(lesson.engagementEvents)}
                      <p className="mt-1 text-[0.68rem] text-[var(--text-secondary)]">
                        {t('analytics.likesComments', { likes: compact(lesson.likes), comments: compact(lesson.comments) })}
                      </p>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={4} className="px-5 py-8 text-center text-sm text-[var(--text-secondary)] sm:px-8">
                    {t('analytics.recentLessonsEmpty')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {intelligenceFeatureEnabled && (
      <SurfaceCard className="space-y-6 border border-[color:rgba(208,188,255,0.2)] bg-[color:rgba(208,188,255,0.08)] p-6 sm:p-8">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex items-start gap-4">
            <span className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[color:rgba(208,188,255,0.14)] text-[var(--accent-primary)]">
              <Sparkles size={20} />
            </span>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <p className="font-['Manrope'] text-2xl font-extrabold tracking-[-0.03em] text-[var(--text-primary)]">{t('analytics.smartInsights')}</p>
                <ProviderLabel report={intelligenceReport} />
                <AnalyticsEnhancementLabel report={intelligenceReport} />
                <IntelligenceLanguageLabel report={intelligenceReport} />
                {intelligenceStale && (
                  <span className="inline-flex items-center rounded-full bg-amber-400/15 px-3 py-1 text-xs font-semibold text-amber-300">
                    Stale
                  </span>
                )}
              </div>
              <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--text-secondary)]">
                Suggestions are advisory. They do not change your lessons until you edit them.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {intelligenceReport?.status === 'done' && (
              <button
                type="button"
                onClick={handleCopyIntelligence}
                className="focus-ring inline-flex h-10 items-center gap-2 rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-elevated)] px-4 text-xs font-semibold text-[var(--text-primary)] transition hover:bg-[var(--surface-container-high)]"
              >
                <Copy size={14} />
                {intelligenceCopied ? t('common.copied') : t('common.copy')}
              </button>
            )}
            <button
              type="button"
              onClick={() => handleAnalyzeAnalytics({ force: intelligenceStale })}
              disabled={intelligenceButtonDisabled}
              className="focus-ring inline-flex h-10 items-center gap-2 rounded-full bg-[image:var(--accent-gradient)] px-4 text-xs font-bold text-white transition hover:scale-105 active:scale-95 disabled:cursor-wait disabled:opacity-60 disabled:hover:scale-100"
            >
              <RefreshCw size={14} className={(intelligenceAnalyzing || intelligenceEnhancementPending) ? 'animate-spin' : ''} />
              {intelligenceButtonLabel}
            </button>
          </div>
        </div>

        {intelligenceError && (
          <div className="flex items-start gap-2 rounded-2xl bg-[color:var(--feedback-danger-bg)] p-3 text-sm text-[color:var(--feedback-danger-fg)]">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <span>{intelligenceError}</span>
          </div>
        )}
        {intelligenceEnhancementFailed && (
          <div className="flex items-start gap-2 rounded-2xl bg-amber-400/15 p-3 text-sm text-amber-200">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <span>
              {intelligenceRefreshFailed
                ? 'Latest refresh failed; previous insight kept.'
                : (
                  <>
                    {intelligenceRetryOllama ? 'Heuristic fallback shown. Retry Ollama when available. ' : ''}
                    {intelligenceReport?.enhancement_last_failure_reason || intelligenceReport?.enhancement_error_safe || 'Ollama enhancement failed; heuristic analysis kept.'}
                  </>
                )}
            </span>
          </div>
        )}

        {intelligenceLoading && !intelligenceReport ? (
          <div className="flex min-h-28 items-center justify-center rounded-2xl bg-[color:var(--surface-muted)]/25 text-sm text-[var(--text-secondary)]">
            <RefreshCw size={16} className="mr-2 animate-spin" />
            {t('analytics.loadingLatestReport')}
          </div>
        ) : intelligenceReport?.status === 'done' ? (
          <div className="space-y-6">
            {(intelligenceStale || intelligenceCompacted) && (
              <div className="rounded-2xl bg-[color:var(--surface-muted)]/25 p-3 text-sm text-[var(--text-secondary)]">
                {intelligenceStale && (
                  <p className="font-semibold text-amber-300">This analytics report is out of date for the selected filters.</p>
                )}
                {intelligenceCompacted && (
                  <p className={intelligenceStale ? 'mt-1' : ''}>Large analytics dataset summarized before analysis.</p>
                )}
              </div>
            )}
            <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_18rem]">
              <div className="rounded-xl border border-[color:rgba(208,188,255,0.24)] bg-[color:rgba(208,188,255,0.08)] p-5">
                <div className="flex flex-wrap items-center gap-2">
                  <Gauge size={17} className="text-[var(--accent-primary)]" />
                  <p className="text-sm font-bold text-[var(--text-primary)]">Smart insight</p>
                  <RiskBadge
                    level={intelligenceReport.risk_level}
                    outputLanguage={intelligenceReport.output_language}
                  />
                </div>
                <p className="mt-3 text-sm leading-relaxed text-[var(--text-primary)]">
                  {humanizeAnalyticsSignal(intelligenceReport.summary)}
                </p>
                <p className="mt-3 text-xs leading-relaxed text-[var(--text-secondary)]">
                  Based on aggregate creator analytics for the selected range.
                  {intelligenceReport.last_analyzed_at ? ` ${t('analytics.lastAnalyzed', { date: formatDateTime(intelligenceReport.last_analyzed_at) })}` : ''}
                </p>
              </div>
              <div className="flex items-center justify-between gap-4 rounded-xl bg-[color:var(--surface-muted)]/25 p-5">
                <div>
                  <p className="label-sm">Health</p>
                  <p className="mt-2 text-sm font-semibold text-[var(--text-primary)]">
                    {analyticsActionLabel(analyticsPriorityItems(intelligenceReport)[0])}
                  </p>
                  <p className="mt-2 text-xs text-[var(--text-secondary)]">Priority signal</p>
                </div>
                <HealthScoreRing value={intelligenceReport.health_score} />
              </div>
            </div>

            <PriorityFixList report={intelligenceReport} />

            <div className="grid gap-4 lg:grid-cols-2">
              <IntelligenceList
                title="Evidence"
                items={intelligenceReport.insights}
                emptyText="Evidence appears after lessons collect activity."
                icon={Lightbulb}
              />
              <IntelligenceList
                title="More recommendations"
                items={intelligenceReport.recommendations}
                emptyText="No additional recommendations yet."
                icon={CheckCircle2}
              />
              <IntelligenceList
                title="Lesson actions"
                items={intelligenceReport.lesson_actions}
                emptyText="Lesson-specific actions appear after lessons collect activity."
                icon={Eye}
              />
              <IntelligenceList
                title="Category patterns"
                items={intelligenceReport.category_actions}
                emptyText="Category patterns appear when category signals differ."
                icon={Filter}
              />
            </div>

            {(Array.isArray(intelligenceReport.limitations) && intelligenceReport.limitations.length > 0) && (
              <CollapsibleAnalyticsSection
                title="Limitations"
                count={intelligenceReport.limitations.length}
                icon={AlertTriangle}
              >
                <ul className="mt-2 space-y-1 text-xs leading-relaxed text-[var(--text-secondary)]">
                  {intelligenceReport.limitations.slice(0, 4).map((item, index) => (
                    <li key={`analytics-limitation-${index}`}>{String(item)}</li>
                  ))}
                </ul>
              </CollapsibleAnalyticsSection>
            )}
          </div>
        ) : intelligenceReport?.status === 'disabled' || intelligenceReport?.enabled === false ? (
          <div className="rounded-2xl bg-[color:var(--surface-muted)]/25 p-5 text-sm text-[var(--text-secondary)]">
            {t('analytics.intelligenceDisabled')}
          </div>
        ) : (
          <div className="rounded-2xl bg-[color:var(--surface-muted)]/25 p-5">
            <p className="text-sm text-[var(--text-secondary)]">{stats.insight}</p>
            <p className="mt-2 text-xs text-[var(--text-secondary)]">
              {t('analytics.insightsPreparing')}
            </p>
          </div>
        )}
      </SurfaceCard>
      )}

      <SurfaceCard className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-sky-400/15 text-sky-300">
            <Users size={18} />
          </span>
          <div>
            <p className="text-sm font-semibold text-[var(--text-primary)]">
              {t('analytics.publishedDrafts', { published: compact(stats.metrics.publishedLessons) })}
            </p>
            <p className="text-xs text-[var(--text-secondary)]">
              {t('analytics.draftsScope', { drafts: compact(stats.metrics.draftLessons) })}
            </p>
          </div>
        </div>
        {loading && (
          <p className="inline-flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            <RefreshCw size={14} className="animate-spin" />
            {t('analytics.loadingAnalytics')}
          </p>
        )}
      </SurfaceCard>

      <CreateLessonModal
        open={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        categories={categories}
        submitting={createSubmitting}
        submitError={createError}
        onSubmit={handleCreateLesson}
      />
    </div>
  );
}
