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
import { Link } from 'react-router-dom';
import {
  analyzeMyAnalyticsIntelligence,
  createProject,
  fetchAdminStats,
  fetchCategories,
  fetchMyAnalytics,
  fetchMyAnalyticsIntelligence,
} from '../api';
import CreateLessonModal from '../components/studio/CreateLessonModal';
import SurfaceCard from '../components/ui/SurfaceCard';
import { canAccessStudio } from '../lib/auth';
import { copyTextToClipboard } from '../utils/clipboard';

const RANGE_OPTIONS = [
  { key: '7', label: 'Last 7 days' },
  { key: '30', label: '30 days' },
  { key: '90', label: '90 days' },
];

const DONUT_COLORS = [
  'var(--accent-primary)',
  '#38bdf8',
  '#34d399',
  '#f59e0b',
  '#fb7185',
  '#a78bfa',
];

function toNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function compactNumber(value, options = {}) {
  const numeric = toNumber(value);
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 1,
    ...options,
  }).format(numeric);
}

function percent(value) {
  return `${Math.max(0, Math.min(100, Math.round(toNumber(value))))}%`;
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

function formatDate(value) {
  if (!value) return 'Recently updated';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Recently updated';
  return `Updated ${parsed.toLocaleDateString('en-US')}`;
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
    type: String(item?.type || 'activity'),
    label: String(item?.label || item?.type || 'Activity'),
    timestamp: String(item?.timestamp || ''),
    title: String(item?.lesson_title || item?.title || 'Lesson activity'),
    description: String(item?.message || item?.description || 'Activity recorded.'),
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

function buildInsight(metrics, categoryBreakdown) {
  if (metrics.totalLessons <= 0 || (metrics.totalViews <= 0 && metrics.engagementEvents <= 0)) {
    return 'No activity yet. Share a published lesson to collect insights.';
  }
  if (metrics.completionRate > 0 && metrics.completionRate < 50) {
    return 'Completion rate is low. Consider shorter sections or clearer lesson checkpoints.';
  }
  const topCategory = categoryBreakdown[0];
  if (topCategory?.name && topCategory.engagement > 0) {
    return `${topCategory.name} is currently driving the most recorded engagement in this range.`;
  }
  if (metrics.uniqueViewers > 0 && metrics.engagementEvents > metrics.uniqueViewers) {
    return 'Learners are generating repeat engagement through progress, likes, or comments.';
  }
  return 'Analytics are based on recorded progress, likes, and comments. More activity will make these signals more useful.';
}

function normalizeAnalyticsStats(payload) {
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
    insight: buildInsight(metrics, categoryBreakdown),
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

function ProviderLabel({ report }) {
  if (!report || report.status === 'empty') {
    return (
      <span className="inline-flex items-center rounded-full bg-[color:var(--surface-muted)]/45 px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
        No report yet
      </span>
    );
  }
  if (report.enabled === false || report.status === 'disabled') {
    return (
      <span className="inline-flex items-center rounded-full bg-amber-400/15 px-3 py-1 text-xs font-semibold text-amber-300">
        AI provider disabled
      </span>
    );
  }
  if (report.fallback_used) {
    return (
      <span className="inline-flex items-center rounded-full bg-amber-400/15 px-3 py-1 text-xs font-semibold text-amber-300">
        Fallback heuristic used
      </span>
    );
  }
  const provider = String(report.provider || '').toLowerCase();
  const label = provider === 'ollama'
    ? 'Ollama analysis'
    : provider === 'heuristic'
      ? 'Heuristic analysis'
      : provider
        ? `${provider} analysis`
        : 'Analysis';
  return (
    <span className="inline-flex items-center rounded-full bg-emerald-400/15 px-3 py-1 text-xs font-semibold text-emerald-300">
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
  return Boolean(report?.is_stale);
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
  if (typeof item === 'string') return item;
  if (!item || typeof item !== 'object') return '';
  return String(item.message || item.recommendation || item.title || item.type || '');
}

function intelligenceItemDetail(item) {
  if (!item || typeof item !== 'object') return '';
  return String(item.evidence || item.action_label || '');
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

function analyticsIntelligenceCopyText(report) {
  if (!report || report.status !== 'done') return '';
  const lines = [
    `Analytics Intelligence (${report.provider || 'unknown'})`,
    `Health score: ${toNumber(report.health_score)} / 100`,
    `Risk: ${report.risk_level || 'unknown'}`,
    '',
    report.summary || '',
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
  const visibleCategories = categories.slice(0, 6);
  const total = visibleCategories.reduce((sum, category) => sum + Math.max(0, toNumber(category.value, 0)), 0);
  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;

  if (total <= 0 || visibleCategories.length === 0) return null;

  return (
    <div className="flex items-center gap-5 rounded-2xl bg-[color:var(--surface-muted)]/25 p-4">
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
            {compactNumber(total)}
          </span>
          <span className="text-[0.62rem] font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">events</span>
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
            <span className="font-semibold text-[var(--text-secondary)]">{compactNumber(category.value)}</span>
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
  const [rangeKey, setRangeKey] = useState('7');
  const [categorySlug, setCategorySlug] = useState('');
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [stats, setStats] = useState(() => emptyAnalyticsStats());
  const [categories, setCategories] = useState([]);
  const [analyticsCategories, setAnalyticsCategories] = useState([]);
  const [recentActivityExpanded, setRecentActivityExpanded] = useState(false);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createError, setCreateError] = useState('');
  const [intelligenceReport, setIntelligenceReport] = useState(null);
  const [intelligenceLoading, setIntelligenceLoading] = useState(false);
  const [intelligenceAnalyzing, setIntelligenceAnalyzing] = useState(false);
  const [intelligenceError, setIntelligenceError] = useState('');
  const [intelligenceCopied, setIntelligenceCopied] = useState(false);
  const [intelligenceLoadedFilterKey, setIntelligenceLoadedFilterKey] = useState('');
  const analyticsAutoRunKeysRef = useRef(new Set());
  const analyticsInitialAutoRunDoneRef = useRef(false);

  const canCreateLesson = canAccessStudio(user);
  const canReviewModeration = isStaffUser(user);
  const hasActivity = !stats.isEmpty;
  const analyticsFilters = useMemo(() => {
    const dateRange = rangeDates(rangeKey);
    return {
      ...dateRange,
      range: rangeKey,
      category: categorySlug || undefined,
    };
  }, [categorySlug, rangeKey]);
  const analyticsFilterKey = useMemo(() => JSON.stringify(analyticsFilters), [analyticsFilters]);

  const loadStats = useCallback(async (activeRef = { current: true }) => {
    setLoading(true);
    setError('');

    try {
      const payload = isStaffUser(user)
        ? await fetchAdminStats(analyticsFilters)
        : await fetchMyAnalytics(analyticsFilters);

      if (!activeRef.current) return;
      const normalized = normalizeAnalyticsStats(payload);
      setStats(normalized);
      setAnalyticsCategories(normalized.categoryOptions);
    } catch (statsError) {
      if (!activeRef.current) return;
      setStats(emptyAnalyticsStats());
      setAnalyticsCategories([]);
      setError(statsError.message || 'Could not load analytics.');
    } finally {
      if (activeRef.current) {
        setLoading(false);
      }
    }
  }, [analyticsFilters, user]);

  const loadIntelligenceReport = useCallback(async (activeRef = { current: true }) => {
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
      setIntelligenceError(intelligenceLoadError.message || 'Analytics Intelligence is unavailable.');
    } finally {
      if (activeRef.current) {
        setIntelligenceLoading(false);
      }
    }
  }, [analyticsFilterKey, analyticsFilters]);

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
  }, [loadIntelligenceReport, refreshNonce]);

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
    formData.append('avatar_enabled', avatarEnabled ? '1' : '0');

    try {
      await createProject(formData);
      setCreateModalOpen(false);
    } catch (createLessonError) {
      setCreateError(createLessonError.message || 'Project upload failed.');
    } finally {
      setCreateSubmitting(false);
    }
  };

  const handleAnalyzeAnalytics = useCallback(async ({ auto = false } = {}) => {
    setIntelligenceAnalyzing(true);
    setIntelligenceError('');
    setIntelligenceCopied(false);

    try {
      const payload = await analyzeMyAnalyticsIntelligence(analyticsFilters, { force: !auto });
      setIntelligenceReport(payload);
      setIntelligenceLoadedFilterKey(analyticsFilterKey);
      return payload;
    } catch (analyzeError) {
      setIntelligenceError(analyzeError.message || 'Analytics analysis failed.');
      return null;
    } finally {
      setIntelligenceAnalyzing(false);
    }
  }, [analyticsFilterKey, analyticsFilters]);

  useEffect(() => {
    if (!canCreateLesson || analyticsInitialAutoRunDoneRef.current) return;
    if (intelligenceLoadedFilterKey !== analyticsFilterKey || intelligenceLoading || intelligenceAnalyzing) return;

    const report = intelligenceReport || null;
    const status = String(report?.status || '').toLowerCase();
    if (report?.enabled === false || status === 'disabled' || status === 'failed') {
      analyticsInitialAutoRunDoneRef.current = true;
      return;
    }

    const missingReport = !report?.id || status === 'empty';
    const staleReport = analyticsIntelligenceIsStale(report);
    if (!missingReport && !staleReport) {
      analyticsInitialAutoRunDoneRef.current = true;
      return;
    }

    const sourceKey = report?.current_source_hash || report?.report_source_hash || report?.source_hash || 'empty';
    const autoRunKey = `${analyticsFilterKey}:${sourceKey}:${missingReport ? 'missing' : 'stale'}`;
    if (analyticsAutoRunKeysRef.current.has(autoRunKey)) {
      analyticsInitialAutoRunDoneRef.current = true;
      return;
    }

    analyticsInitialAutoRunDoneRef.current = true;
    analyticsAutoRunKeysRef.current.add(autoRunKey);
    handleAnalyzeAnalytics({ auto: true });
  }, [
    analyticsFilterKey,
    canCreateLesson,
    handleAnalyzeAnalytics,
    intelligenceAnalyzing,
    intelligenceLoadedFilterKey,
    intelligenceLoading,
    intelligenceReport,
  ]);

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

  return (
    <div className="space-y-7 pb-8">
      <header className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="font-['Manrope'] text-4xl font-extrabold tracking-[-0.04em] text-[var(--text-primary)]">Performance Overview</h1>
          <p className="mt-2 text-sm text-[var(--text-secondary)]">Real engagement signals from lesson progress, likes, and comments.</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {analyticsCategories.length > 0 && (
            <label className="focus-within:ring-focus inline-flex h-10 items-center gap-2 rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-xs font-semibold text-[var(--text-secondary)]">
              <Filter size={14} />
              <span className="sr-only">Filter by category</span>
              <select
                value={categorySlug}
                onChange={(event) => setCategorySlug(event.target.value)}
                className="h-8 min-w-[10rem] rounded-full border-0 bg-[var(--surface-elevated)] px-1 text-xs font-semibold text-[var(--text-primary)] outline-none"
                style={{ backgroundColor: 'var(--surface-elevated)', color: 'var(--text-primary)' }}
              >
                <option value="" style={{ backgroundColor: 'var(--surface-elevated)', color: 'var(--text-primary)' }}>All categories</option>
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
                {option.label}
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
            Refresh
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
              <p className="label-sm">No analytics yet</p>
              <h2 className="mt-1 font-['Manrope'] text-2xl font-extrabold tracking-[-0.03em] text-[var(--text-primary)]">
                Publish lessons and collect watch activity to see insights.
              </h2>
              <p className="mt-2 max-w-2xl text-sm text-[var(--text-secondary)]">
                This dashboard stays empty until real progress, likes, or comments are recorded.
              </p>
            </div>
            <div className="grid grid-cols-3 gap-2 text-center text-xs text-[var(--text-secondary)]">
              <div className="rounded-2xl bg-[color:var(--surface-muted)]/35 p-3">
                <p className="font-['Manrope'] text-xl font-bold text-[var(--text-primary)]">{compactNumber(stats.metrics.publishedLessons)}</p>
                <p>Published</p>
              </div>
              <div className="rounded-2xl bg-[color:var(--surface-muted)]/35 p-3">
                <p className="font-['Manrope'] text-xl font-bold text-[var(--text-primary)]">{compactNumber(stats.metrics.draftLessons)}</p>
                <p>Drafts</p>
              </div>
              <div className="rounded-2xl bg-[color:var(--surface-muted)]/35 p-3">
                <p className="font-['Manrope'] text-xl font-bold text-[var(--text-primary)]">0</p>
                <p>Events</p>
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
              <p className="label-sm">Moderation Review</p>
              <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">
                Staff moderation queue
              </h2>
              <p className="mt-1 text-sm text-[var(--text-secondary)]">
                Open review requests now live in the dedicated moderation dashboard.
              </p>
            </div>
          </div>
          <Link
            to="/moderation"
            className="focus-ring inline-flex h-10 items-center justify-center rounded-full bg-[image:var(--accent-gradient)] px-4 text-sm font-bold text-white transition hover:scale-105 active:scale-95"
          >
            Open Moderation
          </Link>
        </SurfaceCard>
      )}

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          icon={Eye}
          label="Total Views"
          value={compactNumber(stats.metrics.totalViews)}
          trend={stats.metrics.trendViewsPct}
          active={hasActivity}
          hint={`${compactNumber(stats.metrics.uniqueViewers)} unique viewers`}
          emptyHint="No activity yet"
        />
        <KpiCard
          icon={Clock3}
          label="Watch Time"
          value={`${compactNumber(stats.metrics.watchHours)} hrs`}
          trend={stats.metrics.trendWatchPct}
          active={hasActivity}
          hint={stats.meta?.estimated_metrics ? 'Estimated from progress.' : 'Recorded watch time.'}
          emptyHint="No watch time yet"
        />
        <SurfaceCard className="flex min-h-[10.5rem] items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-400/15 text-indigo-300">
                <CheckCircle2 size={18} />
              </span>
              <TrendBadge value={stats.metrics.trendCompletionPct} />
            </div>
            <p className="mt-4 text-[0.66rem] font-semibold uppercase tracking-[0.13em] text-[var(--text-secondary)]">Completion Rate</p>
            <p className="mt-1 text-sm text-[var(--text-secondary)]">
              {hasActivity ? `${percent(stats.metrics.averageProgress)} average progress` : 'No activity yet'}
            </p>
          </div>
          <CompletionRing value={stats.metrics.completionRate} />
        </SurfaceCard>
        <KpiCard
          icon={MessageSquare}
          label="Engagement Events"
          value={compactNumber(stats.metrics.engagementEvents)}
          trend={stats.metrics.trendEngagementPct}
          active={hasActivity}
          hint={`${compactNumber(stats.metrics.likes)} likes / ${compactNumber(stats.metrics.comments)} comments`}
          emptyHint="No engagement yet"
        />
      </section>

      <section className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(22rem,1fr)]">
        <SurfaceCard className="space-y-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Views over time</h2>
              <p className="text-xs text-[var(--text-secondary)]">Recorded progress activity by day</p>
            </div>
            <span className="rounded-full bg-[color:var(--surface-muted)]/40 px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
              {rangeKey} days
            </span>
          </div>

          {hasChartActivity ? (
            <div className="grid gap-3">
              <div className="relative flex h-64 items-end gap-2 rounded-2xl bg-[linear-gradient(to_bottom,transparent,rgba(127,127,127,0.06))] px-2 pt-8">
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
                          {compactNumber(point.value)}
                        </span>
                      </div>
                      <span className="truncate text-center text-[0.62rem] font-semibold uppercase tracking-[0.08em] text-[var(--text-secondary)]">{point.label}</span>
                    </div>
                  );
                })}
              </div>
              <div className="flex items-center justify-between text-xs text-[var(--text-secondary)]">
                <span>0</span>
                <span>{compactNumber(seriesMax)} views</span>
              </div>
            </div>
          ) : (
            <EmptyPanel message="No recorded activity in this range." className="h-64" />
          )}
        </SurfaceCard>

        <SurfaceCard className="space-y-6">
          <div>
            <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Category Breakdown</h2>
            <p className="text-xs text-[var(--text-secondary)]">Engagement by owned lesson category</p>
          </div>
          {stats.categoryBreakdown.length > 0 ? (
            <div className="space-y-4">
              {stats.categoryBreakdown.length <= 6 && (
                <CategoryDonut categories={stats.categoryBreakdown} />
              )}
              {stats.categoryBreakdown.map((category, index) => {
                const width = Math.max(6, Math.round((category.value / categoryMax) * 100));
                const color = DONUT_COLORS[index % DONUT_COLORS.length];
                return (
                  <article key={category.id} className="space-y-2">
                    <div className="flex items-center justify-between gap-3 text-sm">
                      <p className="line-clamp-1 font-semibold text-[var(--text-primary)]">{category.name}</p>
                      <p className="text-xs font-semibold text-[var(--accent-primary)]">{compactNumber(category.value)}</p>
                    </div>
                    <div className="h-2.5 rounded-full bg-[color:var(--surface-muted)]">
                      <div className="h-full rounded-full" style={{ width: `${width}%`, backgroundColor: color }} />
                    </div>
                    <p className="text-[0.68rem] text-[var(--text-secondary)]">
                      {compactNumber(category.views)} views / {compactNumber(category.engagement)} events / {compactNumber(category.lessonCount)} lessons
                    </p>
                  </article>
                );
              })}
            </div>
          ) : (
            <EmptyPanel message="Category breakdown will appear once lessons collect activity." />
          )}
        </SurfaceCard>
      </section>

      <section className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <SurfaceCard className="space-y-6">
          <div>
            <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Top Lessons</h2>
            <p className="text-xs text-[var(--text-secondary)]">Ranked by recorded lesson activity</p>
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
                        {lesson.progressPct > 0 ? `${percent(lesson.progressPct)} progress` : 'No progress yet'}
                      </span>
                    </div>
                    <div className="h-1.5 rounded-full bg-[color:var(--surface-muted)]">
                      <div className="h-full rounded-full bg-[image:var(--accent-gradient)]" style={{ width: percent(lesson.progressPct) }} />
                    </div>
                    <p className="text-[0.68rem] text-[var(--text-secondary)]">
                      {compactNumber(lesson.views)} views / {compactNumber(lesson.engagementEvents)} events / {compactNumber(lesson.likes)} likes / {compactNumber(lesson.comments)} comments
                      {lesson.completionPct > 0 ? ` / ${percent(lesson.completionPct)} completed` : ''}
                    </p>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyPanel message="Top lessons will appear after viewers start lessons in this range." />
          )}
        </SurfaceCard>

        <SurfaceCard className="space-y-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Recent Activity</h2>
              <p className="text-xs text-[var(--text-secondary)]">Aggregate activity only. Viewer identities are not shown.</p>
            </div>
            {stats.recentActivity.length > 3 && (
              <button
                type="button"
                onClick={() => setRecentActivityExpanded((value) => !value)}
                className="focus-ring shrink-0 rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-elevated)] px-3 py-1.5 text-xs font-semibold text-[var(--text-primary)] transition hover:bg-[var(--surface-container-high)]"
              >
                {recentActivityExpanded ? 'Show less' : `Show ${stats.recentActivity.length - 3} more`}
              </button>
            )}
          </div>
          {stats.recentActivity.length > 0 ? (
            <div className="space-y-3">
              {visibleRecentActivity.map((activity) => (
                <article key={activity.id} className="grid grid-cols-[0.75rem_minmax(0,1fr)] gap-3">
                  <span className="mt-1.5 h-3 w-3 rounded-full bg-[var(--accent-primary)] shadow-[0_0_0_4px_rgba(208,188,255,0.14)]" />
                  <div className="rounded-2xl bg-[color:var(--surface-muted)]/30 p-4">
                    <div className="flex items-center justify-between gap-3">
                      <p className="line-clamp-1 text-sm font-semibold text-[var(--text-primary)]">{activity.title}</p>
                      <span className="shrink-0 rounded-full bg-[var(--surface-elevated)] px-2 py-1 text-[0.62rem] font-semibold uppercase tracking-[0.1em] text-[var(--text-secondary)]">
                        {activity.label}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-[var(--text-secondary)]">{activity.description}</p>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyPanel message="Activity will appear here after viewers like, comment, or make progress on your lessons." />
          )}
        </SurfaceCard>
      </section>

      <section className="overflow-hidden rounded-3xl token-surface-elevated">
        <div className="border-b border-[color:rgba(73,68,84,0.1)] px-5 py-4 sm:px-8 sm:py-6">
          <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Recent Lessons</h2>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">Creator-scoped lesson activity</p>
        </div>

        <div className="overflow-x-auto">
          <table className="min-w-full text-left">
            <thead>
              <tr className="text-[0.62rem] uppercase tracking-[0.14em] text-[var(--text-secondary)]">
                <th className="px-5 py-3 font-semibold sm:px-8">Lesson Name</th>
                <th className="px-5 py-3 font-semibold sm:px-8">Views</th>
                <th className="px-5 py-3 font-semibold sm:px-8">Progress</th>
                <th className="px-5 py-3 font-semibold sm:px-8">Engagement</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[color:rgba(73,68,84,0.1)]">
              {stats.recentLessons.length > 0 ? (
                stats.recentLessons.map((lesson) => (
                  <tr key={`recent-${lesson.id}`} className="hover:bg-[color:var(--surface-muted)]/40">
                    <td className="px-5 py-4 sm:px-8">
                      <p className="text-sm font-semibold text-[var(--text-primary)]">{lesson.title}</p>
                      <p className="mt-1 text-[0.68rem] text-[var(--text-secondary)]">{formatDate(lesson.publishedAt)}</p>
                    </td>
                    <td className="px-5 py-4 text-sm text-[var(--text-primary)] sm:px-8">{compactNumber(lesson.views)}</td>
                    <td className="px-5 py-4 sm:px-8">
                      {lesson.progressPct > 0 ? (
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-14 rounded-full bg-[color:var(--surface-muted)]">
                            <div className="h-full rounded-full bg-emerald-400" style={{ width: percent(lesson.progressPct) }} />
                          </div>
                          <span className="text-xs font-medium text-[var(--text-primary)]">{percent(lesson.progressPct)}</span>
                        </div>
                      ) : (
                        <span className="text-xs text-[var(--text-secondary)]">No progress yet</span>
                      )}
                    </td>
                    <td className="px-5 py-4 text-sm text-[var(--text-primary)] sm:px-8">
                      {compactNumber(lesson.engagementEvents)}
                      <p className="mt-1 text-[0.68rem] text-[var(--text-secondary)]">
                        {compactNumber(lesson.likes)} likes / {compactNumber(lesson.comments)} comments
                      </p>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={4} className="px-5 py-8 text-center text-sm text-[var(--text-secondary)] sm:px-8">
                    Recent lesson activity will appear after viewers interact with your lessons.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <SurfaceCard className="space-y-6 border border-[color:rgba(208,188,255,0.2)] bg-[color:rgba(208,188,255,0.08)] p-6 sm:p-8">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex items-start gap-4">
            <span className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[color:rgba(208,188,255,0.14)] text-[var(--accent-primary)]">
              <Sparkles size={20} />
            </span>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <p className="font-['Manrope'] text-2xl font-extrabold tracking-[-0.03em] text-[var(--text-primary)]">Smart Insights</p>
                <ProviderLabel report={intelligenceReport} />
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
                {intelligenceCopied ? 'Copied' : 'Copy'}
              </button>
            )}
            <button
              type="button"
              onClick={() => handleAnalyzeAnalytics()}
              disabled={intelligenceAnalyzing || intelligenceReport?.enabled === false}
              className="focus-ring inline-flex h-10 items-center gap-2 rounded-full bg-[image:var(--accent-gradient)] px-4 text-xs font-bold text-white transition hover:scale-105 active:scale-95 disabled:cursor-wait disabled:opacity-60 disabled:hover:scale-100"
            >
              <RefreshCw size={14} className={intelligenceAnalyzing ? 'animate-spin' : ''} />
              {intelligenceAnalyzing ? 'Analyzing...' : intelligenceStale ? 'Re-analyze' : 'Analyze analytics'}
            </button>
          </div>
        </div>

        {intelligenceError && (
          <div className="flex items-start gap-2 rounded-2xl bg-[color:var(--feedback-danger-bg)] p-3 text-sm text-[color:var(--feedback-danger-fg)]">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <span>{intelligenceError}</span>
          </div>
        )}

        {intelligenceLoading && !intelligenceReport ? (
          <div className="flex min-h-28 items-center justify-center rounded-2xl bg-[color:var(--surface-muted)]/25 text-sm text-[var(--text-secondary)]">
            <RefreshCw size={16} className="mr-2 animate-spin" />
            Loading latest report...
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
            <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(22rem,0.8fr)]">
              <div className="rounded-2xl bg-[color:var(--surface-muted)]/25 p-5">
                <div className="flex items-center gap-2">
                  <Gauge size={17} className="text-[var(--accent-primary)]" />
                  <p className="text-sm font-bold text-[var(--text-primary)]">Analytics summary</p>
                </div>
                <p className="mt-3 text-sm leading-relaxed text-[var(--text-secondary)]">{intelligenceReport.summary}</p>
              </div>
              <div className="flex items-center justify-between gap-5 rounded-2xl bg-[color:var(--surface-muted)]/25 p-5">
                <div>
                  <p className="label-sm">Health</p>
                  <RiskBadge
                    level={intelligenceReport.risk_level}
                    outputLanguage={intelligenceReport.output_language}
                  />
                  <p className="mt-3 text-xs leading-relaxed text-[var(--text-secondary)]">
                    Based on aggregate creator analytics for the selected range.
                  </p>
                </div>
                <HealthScoreRing value={intelligenceReport.health_score} />
              </div>
            </div>

            <div className="grid gap-5 lg:grid-cols-2">
              <IntelligenceList
                title="Insights"
                items={intelligenceReport.insights}
                emptyText="No analytics insights yet."
                icon={Lightbulb}
              />
              <IntelligenceList
                title="Recommendations"
                items={intelligenceReport.recommendations}
                emptyText="No recommendations yet."
                icon={CheckCircle2}
              />
              <IntelligenceList
                title="Lesson actions"
                items={intelligenceReport.lesson_actions}
                emptyText="Lesson-specific actions appear after lessons collect activity."
                icon={Eye}
              />
              <IntelligenceList
                title="Category actions"
                items={intelligenceReport.category_actions}
                emptyText="Category actions appear when category signals differ."
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
            Analytics Intelligence is disabled for this environment.
          </div>
        ) : (
          <div className="rounded-2xl bg-[color:var(--surface-muted)]/25 p-5">
            <p className="text-sm text-[var(--text-secondary)]">{stats.insight}</p>
            <p className="mt-2 text-xs text-[var(--text-secondary)]">
              Run analysis when you want advisory insights for the selected analytics range.
            </p>
          </div>
        )}
      </SurfaceCard>

      <SurfaceCard className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-sky-400/15 text-sky-300">
            <Users size={18} />
          </span>
          <div>
            <p className="text-sm font-semibold text-[var(--text-primary)]">
              {compactNumber(stats.metrics.publishedLessons)} published lessons
            </p>
            <p className="text-xs text-[var(--text-secondary)]">
              {compactNumber(stats.metrics.draftLessons)} drafts in this creator scope
            </p>
          </div>
        </div>
        {loading && (
          <p className="inline-flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            <RefreshCw size={14} className="animate-spin" />
            Loading analytics...
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
