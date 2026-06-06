import { CirclePlay } from 'lucide-react';
import { Link } from 'react-router-dom';
import { normalizeLesson } from '../../lib/content';

function lessonBackground(lesson) {
  if (!lesson?.imageUrl) {
    return { backgroundImage: 'var(--hero-fallback)' };
  }
  return {
    backgroundImage: `var(--card-image-overlay), url(${lesson.imageUrl})`,
    backgroundSize: 'cover',
    backgroundPosition: 'center',
  };
}

export function normalizeLearningRows(payload = [], kind = 'history') {
  const rows = Array.isArray(payload) ? payload : payload?.results || [];
  return rows
    .map((row) => {
      const rawLesson = row?.lesson || row?.project || row || {};
      const progressPct = Number(row?.progress_pct ?? rawLesson?.user_progress ?? 0);
      const lesson = normalizeLesson({
        ...rawLesson,
        user_progress: Math.max(0, Math.min(100, progressPct)),
      });
      if (!lesson.id) return null;
      return {
        id: row?.id || `${kind}-${lesson.id}`,
        lesson,
        progressPct: Math.max(0, Math.min(100, progressPct)),
        timestamp: row?.last_watched_at || row?.updated_at || row?.liked_at || lesson.createdAt,
        kind,
      };
    })
    .filter(Boolean);
}

export function formatLearningDate(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

export default function LearningLessonCard({ item, metaLabel = '' }) {
  const lesson = item?.lesson || {};
  const progressPct = Math.max(0, Math.min(100, Number(item?.progressPct || lesson.progress || 0)));
  const roundedProgress = Math.round(progressPct);
  const continueLabel = item?.kind === 'history' && progressPct > 0
    ? `Continue from ${roundedProgress}%`
    : '';
  const dateLabel = formatLearningDate(item?.timestamp);
  const watchUrl = continueLabel
    ? `/watch?lesson=${lesson.id}&resume=1`
    : `/watch?lesson=${lesson.id}`;

  return (
    <Link
      to={watchUrl}
      className="focus-ring group grid gap-4 rounded-xl token-surface-elevated p-3 text-left transition hover:-translate-y-0.5 md:grid-cols-[12rem_minmax(0,1fr)]"
    >
      <div className="relative aspect-video overflow-hidden rounded-lg bg-[var(--surface-container-high)]" style={lessonBackground(lesson)}>
        <div className="absolute inset-0 bg-black/10 transition group-hover:bg-black/0" />
        <span className="absolute inset-0 flex items-center justify-center opacity-0 transition group-hover:opacity-100">
          <span className="inline-flex h-11 w-11 items-center justify-center rounded-full bg-black/55 text-white">
            <CirclePlay size={23} />
          </span>
        </span>
      </div>

      <div className="min-w-0 space-y-2">
        <div>
          <p className="line-clamp-2 text-sm font-semibold text-[var(--text-primary)]">{lesson.title}</p>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            {[lesson.teacherName, lesson.categoryName].filter(Boolean).join(' - ')}
          </p>
        </div>

        <div className="space-y-1">
          <div className="h-1.5 overflow-hidden rounded-full bg-[var(--surface-container-highest)]">
            <div className="h-full rounded-full bg-[image:var(--accent-gradient)]" style={{ width: `${Math.max(4, progressPct)}%` }} />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 text-[0.68rem] font-medium uppercase tracking-[0.12em] text-[var(--text-secondary)]">
            <span>{metaLabel || `${roundedProgress}% watched`}</span>
            {dateLabel ? <span>{dateLabel}</span> : null}
          </div>
        </div>

        {continueLabel && (
          <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-[var(--surface-container-highest)] px-3 py-1 text-xs font-semibold text-[var(--accent-primary)] transition group-hover:bg-[color:var(--hover-accent-soft)]">
            <CirclePlay size={14} />
            {continueLabel}
          </span>
        )}
      </div>
    </Link>
  );
}
