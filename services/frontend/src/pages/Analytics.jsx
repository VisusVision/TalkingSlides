import { useEffect, useMemo, useState } from 'react';
import {
  CalendarRange,
  CheckCircle2,
  Clock3,
  Eye,
  Filter,
  Lightbulb,
  MessageSquare,
  ShieldCheck,
  Users,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  createProject,
  fetchAdminStats,
  fetchCategories,
  fetchMyAnalytics,
} from '../api';
import CreateLessonModal from '../components/studio/CreateLessonModal';
import SurfaceCard from '../components/ui/SurfaceCard';
import { canAccessStudio } from '../lib/auth';

const RANGE_OPTIONS = [
  { key: '7', label: 'Last 7 days' },
  { key: '30', label: '30 days' },
  { key: '90', label: '90 days' },
];

function toNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function compactNumber(value) {
  const numeric = toNumber(value);
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(numeric);
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
      trendViewsPct: 0,
      trendWatchPct: 0,
      trendCompletionPct: 0,
      trendEngagementPct: 0,
    },
    series: [],
    topLessons: [],
    recentLessons: [],
    recentActivity: [],
    categoryOptions: [],
    insight: 'No analytics yet. Publish lessons and collect watch activity to see insights.',
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
        label: rawDate.slice(0, 10),
        value: toNumber(point?.total_views ?? point?.views ?? point?.video_plays ?? point?.value, 0),
        engagement: toNumber(point?.engagement ?? point?.engagement_events ?? point?.value, 0),
      };
    })
    .filter((point) => point.value >= 0)
    .slice(-14);
}

function normalizeTopLessons(source) {
  if (!Array.isArray(source)) return [];
  return source.slice(0, 8).map((item, index) => ({
    id: item?.lesson_id || item?.id || `top-${index}`,
    title: String(item?.title || item?.name || `Lesson ${index + 1}`),
    retentionPct: toNumber(
      item?.completion_rate ?? item?.completion_pct ?? item?.average_progress ?? item?.retention_pct,
      0,
    ),
    views: toNumber(item?.views ?? item?.total_views ?? item?.video_plays, 0),
    engagementEvents: toNumber(item?.engagement_events, 0),
  }));
}

function normalizeRecentLessons(source) {
  if (!Array.isArray(source)) return [];
  return source.slice(0, 8).map((item, index) => ({
    id: item?.lesson_id || item?.id || `recent-${index}`,
    title: String(item?.title || item?.name || `Lesson ${index + 1}`),
    publishedAt: String(item?.published_at || item?.created_at || item?.updated_at || ''),
    views: toNumber(item?.views ?? item?.total_views ?? item?.video_plays, 0),
    completionPct: toNumber(item?.completion_rate ?? item?.completion_pct ?? item?.average_progress, 0),
    engagementEvents: toNumber(item?.engagement_events, 0),
    likes: toNumber(item?.likes, 0),
    comments: toNumber(item?.comments, 0),
  }));
}

function normalizeRecentActivity(source) {
  if (!Array.isArray(source)) return [];
  return source.slice(0, 8).map((item, index) => ({
    id: `${item?.type || 'activity'}-${item?.lesson_id || index}-${item?.timestamp || index}`,
    type: String(item?.type || 'activity'),
    timestamp: String(item?.timestamp || ''),
    title: String(item?.lesson_title || item?.title || 'Lesson activity'),
    description: String(item?.description || 'Activity recorded.'),
  }));
}

function buildInsight(metrics) {
  if (metrics.totalLessons <= 0) {
    return 'No analytics yet. Publish lessons and collect watch activity to see insights.';
  }
  if (metrics.totalViews <= 0 && metrics.engagementEvents <= 0) {
    return 'No analytics yet. Publish lessons and collect watch activity to see insights.';
  }
  if (metrics.completionRate > 0 && metrics.completionRate < 50) {
    return 'Rule-based insight: completion is below 50%. Review the lessons with the lowest completion before changing your broader catalog.';
  }
  if (metrics.uniqueViewers > 0 && metrics.engagementEvents > metrics.uniqueViewers) {
    return 'Rule-based insight: learners are generating repeat engagement through progress, likes, or comments in this range.';
  }
  return 'Rule-based insight: analytics are based on recorded progress, likes, and comments. More activity will make these signals more useful.';
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
    likes: toNumber(summary.likes, 0),
    comments: toNumber(summary.comments, 0),
    trendViewsPct: toNumber(trends.video_plays_pct ?? trends.total_views_pct ?? payload.trend_views_pct, 0),
    trendWatchPct: toNumber(trends.watch_time_pct ?? payload.trend_watch_pct, 0),
    trendCompletionPct: toNumber(trends.completion_rate_pct ?? payload.trend_completion_pct, 0),
    trendEngagementPct: toNumber(trends.engagement_events_pct ?? payload.trend_engagement_pct, 0),
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
  const isEmpty = totalViews <= 0 && engagementEvents <= 0 && metrics.likes <= 0 && metrics.comments <= 0;

  return {
    metrics,
    series,
    topLessons,
    recentLessons,
    recentActivity,
    categoryOptions,
    insight: buildInsight(metrics),
    isEmpty,
    meta: payload.meta || {},
  };
}

function TrendBadge({ value }) {
  const numeric = toNumber(value);
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

export default function Analytics({ user }) {
  const [rangeKey, setRangeKey] = useState('7');
  const [categorySlug, setCategorySlug] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [stats, setStats] = useState(() => emptyAnalyticsStats());
  const [categories, setCategories] = useState([]);
  const [analyticsCategories, setAnalyticsCategories] = useState([]);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createError, setCreateError] = useState('');

  const canCreateLesson = canAccessStudio(user);
  const canReviewModeration = isStaffUser(user);

  useEffect(() => {
    let active = true;

    async function loadStats() {
      setLoading(true);
      setError('');

      const dateRange = rangeDates(rangeKey);
      const filters = {
        ...dateRange,
        range: rangeKey,
        category: categorySlug || undefined,
      };

      try {
        const payload = isStaffUser(user)
          ? await fetchAdminStats(filters)
          : await fetchMyAnalytics(filters);

        if (!active) return;
        const normalized = normalizeAnalyticsStats(payload);
        setStats(normalized);
        setAnalyticsCategories(normalized.categoryOptions);
      } catch (statsError) {
        if (!active) return;
        setStats(emptyAnalyticsStats());
        setAnalyticsCategories([]);
        setError(statsError.message || 'Could not load analytics.');
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    loadStats();

    return () => {
      active = false;
    };
  }, [rangeKey, categorySlug, user?.id, user?.is_staff, user?.is_superuser]);

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

  const seriesMax = useMemo(
    () => Math.max(1, ...stats.series.map((point) => toNumber(point.value, 0))),
    [stats.series],
  );

  return (
    <div className="space-y-8 pb-8">
      <header className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="font-['Manrope'] text-4xl font-extrabold tracking-[-0.04em] text-[var(--text-primary)]">Performance Overview</h1>
          <p className="mt-2 text-sm text-[var(--text-secondary)]">Detailed insights into recorded lesson engagement.</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {analyticsCategories.length > 0 && (
            <label className="inline-flex items-center gap-2 rounded-full token-surface px-3 py-2 text-xs font-semibold text-[var(--text-secondary)]">
              <Filter size={14} />
              <span className="sr-only">Filter by category</span>
              <select
                value={categorySlug}
                onChange={(event) => setCategorySlug(event.target.value)}
                className="bg-transparent text-xs font-semibold text-[var(--text-primary)] outline-none"
              >
                <option value="">All categories</option>
                {analyticsCategories.map((category) => (
                  <option key={category.slug} value={category.slug}>
                    {category.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <div className="inline-flex items-center gap-1 rounded-full token-surface p-1">
            {RANGE_OPTIONS.map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => setRangeKey(option.key)}
                className={`focus-ring rounded-full px-4 py-2 text-xs font-semibold transition ${
                  rangeKey === option.key
                    ? 'bg-[var(--surface-container-highest)] text-[var(--accent-primary)]'
                    : 'text-[var(--text-secondary)] hover:bg-[var(--surface-container-high)]'
                }`}
              >
                {option.label}
              </button>
            ))}
            <button type="button" disabled className="inline-flex h-8 w-8 items-center justify-center rounded-full text-[var(--text-secondary)]">
              <CalendarRange size={14} />
            </button>
          </div>
        </div>
      </header>

      {error && (
        <SurfaceCard className="rounded-2xl bg-[color:var(--feedback-danger-bg)] p-4">
          <p className="text-sm text-[color:var(--feedback-danger-fg)]">{error}</p>
        </SurfaceCard>
      )}

      {!loading && stats.isEmpty && !error && (
        <SurfaceCard className="rounded-2xl border border-[color:rgba(208,188,255,0.2)] bg-[color:rgba(208,188,255,0.08)] p-5">
          <p className="text-sm font-semibold text-[var(--text-primary)]">No analytics yet.</p>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Publish lessons and collect watch activity to see insights.
          </p>
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
        <SurfaceCard className="space-y-4">
          <div className="flex items-start justify-between">
            <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-[color:rgba(208,188,255,0.1)] text-[var(--accent-primary)]">
              <Eye size={18} />
            </span>
            <TrendBadge value={stats.metrics.trendViewsPct} />
          </div>
          <div>
            <p className="text-[0.66rem] font-semibold uppercase tracking-[0.13em] text-[var(--text-secondary)]">Total Views</p>
            <p className="mt-1 font-['Manrope'] text-3xl font-extrabold tracking-[-0.03em] text-[var(--text-primary)]">{compactNumber(stats.metrics.totalViews)}</p>
          </div>
        </SurfaceCard>

        <SurfaceCard className="space-y-4">
          <div className="flex items-start justify-between">
            <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-sky-400/15 text-sky-300">
              <Clock3 size={18} />
            </span>
            <TrendBadge value={stats.metrics.trendWatchPct} />
          </div>
          <div>
            <p className="text-[0.66rem] font-semibold uppercase tracking-[0.13em] text-[var(--text-secondary)]">Watch Time (Hrs)</p>
            <p className="mt-1 font-['Manrope'] text-3xl font-extrabold tracking-[-0.03em] text-[var(--text-primary)]">{compactNumber(stats.metrics.watchHours)}</p>
            {stats.meta?.estimated_metrics && (
              <p className="mt-1 text-[0.68rem] text-[var(--text-secondary)]">Estimated from progress.</p>
            )}
          </div>
        </SurfaceCard>

        <SurfaceCard className="space-y-4">
          <div className="flex items-start justify-between">
            <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-400/15 text-indigo-300">
              <CheckCircle2 size={18} />
            </span>
            <TrendBadge value={stats.metrics.trendCompletionPct} />
          </div>
          <div>
            <p className="text-[0.66rem] font-semibold uppercase tracking-[0.13em] text-[var(--text-secondary)]">Completion Rate</p>
            <p className="mt-1 font-['Manrope'] text-3xl font-extrabold tracking-[-0.03em] text-[var(--text-primary)]">{percent(stats.metrics.completionRate)}</p>
          </div>
        </SurfaceCard>

        <SurfaceCard className="space-y-4">
          <div className="flex items-start justify-between">
            <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-400/15 text-emerald-300">
              <MessageSquare size={18} />
            </span>
            <TrendBadge value={stats.metrics.trendEngagementPct} />
          </div>
          <div>
            <p className="text-[0.66rem] font-semibold uppercase tracking-[0.13em] text-[var(--text-secondary)]">Engagement Events</p>
            <p className="mt-1 font-['Manrope'] text-3xl font-extrabold tracking-[-0.03em] text-[var(--text-primary)]">{compactNumber(stats.metrics.engagementEvents)}</p>
            <p className="mt-1 text-[0.68rem] text-[var(--text-secondary)]">
              {compactNumber(stats.metrics.uniqueViewers)} unique viewers
            </p>
          </div>
        </SurfaceCard>
      </section>

      <section className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <SurfaceCard className="space-y-6">
          <div>
            <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Views over time</h2>
            <p className="text-xs text-[var(--text-secondary)]">Recorded progress activity by day</p>
          </div>

          <div className="flex h-64 items-end gap-2">
            {stats.series.length > 0 ? (
              stats.series.map((point) => {
                const height = Math.max(18, Math.round((toNumber(point.value, 0) / seriesMax) * 100));
                return (
                  <div key={`${point.label}-${point.value}`} className="group flex min-w-0 flex-1 flex-col justify-end gap-2">
                    <div className="relative rounded-t-lg bg-[color:rgba(208,188,255,0.2)] transition group-hover:bg-[var(--accent-primary)]" style={{ height: `${height}%` }}>
                      <span className="absolute -top-8 left-1/2 hidden -translate-x-1/2 whitespace-nowrap rounded bg-[var(--surface-elevated)] px-2 py-1 text-[0.62rem] text-[var(--text-secondary)] group-hover:block">
                        {compactNumber(point.value)}
                      </span>
                    </div>
                    <span className="text-center text-[0.62rem] font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">{point.label}</span>
                  </div>
                );
              })
            ) : (
              <div className="flex flex-1 items-center justify-center rounded-2xl bg-[color:var(--surface-muted)]/35 text-sm text-[var(--text-secondary)]">
                No recorded activity in this range.
              </div>
            )}
          </div>
        </SurfaceCard>

        <SurfaceCard className="space-y-6">
          <div>
            <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Top Lessons</h2>
            <p className="text-xs text-[var(--text-secondary)]">By completion and progress activity</p>
          </div>

          <div className="space-y-4">
            {stats.topLessons.length > 0 ? (
              stats.topLessons.map((lesson) => (
                <article key={`top-${lesson.id}`} className="space-y-2">
                  <div className="flex items-center justify-between gap-3 text-xs">
                    <p className="line-clamp-1 font-medium text-[var(--text-primary)]">{lesson.title}</p>
                    <p className="font-semibold text-[var(--accent-primary)]">{percent(lesson.retentionPct)}</p>
                  </div>
                  <div className="h-1.5 rounded-full bg-[color:var(--surface-muted)]">
                    <div className="h-full rounded-full bg-[image:var(--accent-gradient)]" style={{ width: percent(lesson.retentionPct) }} />
                  </div>
                  <p className="text-[0.68rem] text-[var(--text-secondary)]">
                    {compactNumber(lesson.views)} views · {compactNumber(lesson.engagementEvents)} events
                  </p>
                </article>
              ))
            ) : (
              <p className="rounded-2xl bg-[color:var(--surface-muted)]/35 p-4 text-sm text-[var(--text-secondary)]">
                Top lessons will appear after viewers start lessons in this range.
              </p>
            )}
          </div>
        </SurfaceCard>
      </section>

      <section className="overflow-hidden rounded-3xl token-surface-elevated">
        <div className="flex items-center justify-between border-b border-[color:rgba(73,68,84,0.1)] px-5 py-4 sm:px-8 sm:py-6">
          <div>
            <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Recent Lessons</h2>
            <p className="mt-1 text-xs text-[var(--text-secondary)]">Creator-scoped lesson activity</p>
          </div>
          <button type="button" disabled className="inline-flex h-9 w-9 items-center justify-center rounded-full token-surface text-[var(--text-secondary)]">
            <Filter size={14} />
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="min-w-full text-left">
            <thead>
              <tr className="text-[0.62rem] uppercase tracking-[0.14em] text-[var(--text-secondary)]">
                <th className="px-5 py-3 font-semibold sm:px-8">Lesson Name</th>
                <th className="px-5 py-3 font-semibold sm:px-8">Total Views</th>
                <th className="px-5 py-3 font-semibold sm:px-8">Completion</th>
                <th className="px-5 py-3 font-semibold sm:px-8">Engagement</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[color:rgba(73,68,84,0.1)]">
              {stats.recentLessons.length > 0 ? (
                stats.recentLessons.map((lesson) => (
                  <tr key={`recent-${lesson.id}`} className="hover:bg-[color:var(--surface-muted)]/40">
                    <td className="px-5 py-4 sm:px-8">
                      <p className="text-sm font-semibold text-[var(--text-primary)]">{lesson.title}</p>
                      <p className="mt-1 text-[0.68rem] text-[var(--text-secondary)]">{lesson.publishedAt ? `Updated ${new Date(lesson.publishedAt).toLocaleDateString('en-US')}` : 'Recently updated'}</p>
                    </td>
                    <td className="px-5 py-4 text-sm text-[var(--text-primary)] sm:px-8">{compactNumber(lesson.views)}</td>
                    <td className="px-5 py-4 sm:px-8">
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-14 rounded-full bg-[color:var(--surface-muted)]">
                          <div className="h-full rounded-full bg-emerald-400" style={{ width: percent(lesson.completionPct) }} />
                        </div>
                        <span className="text-xs font-medium text-[var(--text-primary)]">{percent(lesson.completionPct)}</span>
                      </div>
                    </td>
                    <td className="px-5 py-4 text-sm text-[var(--text-primary)] sm:px-8">
                      {compactNumber(lesson.engagementEvents)}
                      <p className="mt-1 text-[0.68rem] text-[var(--text-secondary)]">
                        {compactNumber(lesson.likes)} likes · {compactNumber(lesson.comments)} comments
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

      {stats.recentActivity.length > 0 && (
        <SurfaceCard className="space-y-5">
          <div>
            <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Recent Activity</h2>
            <p className="text-xs text-[var(--text-secondary)]">Aggregate activity only. Viewer identities are not shown.</p>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {stats.recentActivity.map((activity) => (
              <article key={activity.id} className="rounded-2xl bg-[color:var(--surface-muted)]/35 p-4">
                <p className="line-clamp-1 text-sm font-semibold text-[var(--text-primary)]">{activity.title}</p>
                <p className="mt-1 text-sm text-[var(--text-secondary)]">{activity.description}</p>
              </article>
            ))}
          </div>
        </SurfaceCard>
      )}

      <SurfaceCard className="relative overflow-hidden border border-[color:rgba(208,188,255,0.2)] bg-[color:rgba(208,188,255,0.1)] p-8">
        <div className="relative z-10 flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-start gap-4">
            <span className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[color:rgba(208,188,255,0.14)] text-[var(--accent-primary)]">
              <Lightbulb size={20} />
            </span>
            <div>
              <p className="font-['Manrope'] text-2xl font-extrabold tracking-[-0.03em] text-[var(--text-primary)]">Rule-based Insights</p>
              <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--text-secondary)]">
                {stats.insight}
              </p>
            </div>
          </div>
          <button type="button" disabled className="inline-flex h-11 items-center justify-center rounded-full bg-[image:var(--accent-gradient)] px-5 text-sm font-semibold text-white opacity-70">
            AI analysis coming soon
          </button>
        </div>
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
        {loading && <p className="text-sm text-[var(--text-secondary)]">Loading analytics...</p>}
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
