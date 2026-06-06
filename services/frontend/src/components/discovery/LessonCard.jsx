import { Clock3, PlayCircle } from 'lucide-react';
import LessonActionButton from '../moderation/LessonActionButton';
import { formatDuration, formatViews } from '../../lib/content';

function backgroundFromLesson(lesson) {
  if (lesson.imageUrl) {
    return {
      backgroundImage: `var(--card-image-overlay), url(${lesson.imageUrl})`,
      backgroundSize: 'cover',
      backgroundPosition: 'center',
    };
  }

  return {
    backgroundImage: 'var(--card-fallback)',
  };
}

export default function LessonCard({ lesson, onOpen, compact = false, user, onLoginRequest }) {
  return (
    <article
      className="group reveal-up relative w-[252px] shrink-0 overflow-hidden rounded-3xl token-surface-elevated p-3 shadow-soft transition duration-300 hover:-translate-y-1.5 hover:shadow-lift"
      style={{ animationDelay: '80ms' }}
    >
      <LessonActionButton
        lesson={lesson}
        user={user}
        onLoginRequest={onLoginRequest}
        compact
        className="absolute right-5 top-5 z-20 bg-[color:rgba(255,255,255,0.9)] text-slate-700"
      />
      <button
        type="button"
        className="focus-ring w-full cursor-pointer text-left"
        onClick={() => onOpen(lesson.id)}
      >
        <div
          className="relative mb-3 overflow-hidden rounded-2xl"
          style={{ ...backgroundFromLesson(lesson), height: compact ? '8rem' : '9.5rem' }}
          role="img"
          aria-label={`Cover for ${lesson.title}`}
        >
          <span className="absolute left-3 top-3 inline-flex items-center rounded-full bg-[color:var(--media-pill-bg)] px-2 py-1 text-[11px] font-medium text-[color:var(--media-text-on-image)] backdrop-blur-sm">
            {lesson.categoryName}
          </span>
          <span className="absolute bottom-3 left-3 inline-flex items-center gap-1 rounded-full bg-[color:var(--media-pill-bg)] px-2.5 py-1 text-xs text-[color:var(--media-text-on-image)] backdrop-blur-sm">
            <Clock3 size={12} />
            {formatDuration(lesson.durationMinutes)}
          </span>

          <span className="absolute bottom-3 right-3 inline-flex h-9 w-9 items-center justify-center rounded-full bg-[image:var(--accent-gradient)] text-[var(--accent-inverse)] opacity-95 transition group-hover:scale-105">
            <PlayCircle size={18} />
          </span>
        </div>

        <div>
          <p className="title-lg line-clamp-2 text-[var(--text-primary)]">{lesson.title}</p>
          <p className="mt-2 text-sm text-[var(--text-secondary)]">{lesson.teacherName}</p>
          <div className="mt-3 flex items-center justify-between text-xs text-[var(--text-secondary)]">
            <span>{formatViews(lesson.views)}</span>
            {lesson.badge && (
              <span className="rounded-full bg-[color:color-mix(in_srgb,var(--accent-primary),transparent_82%)] px-2 py-1 text-[11px] font-medium text-[var(--text-primary)]">
                {lesson.badge}
              </span>
            )}
          </div>

          {lesson.progress > 0 && (
            <div className="mt-3 h-1.5 rounded-full bg-[color:var(--surface-muted)]">
              <div
                className="h-full rounded-full bg-[image:var(--accent-gradient)]"
                style={{ width: `${lesson.progress}%` }}
              />
            </div>
          )}
        </div>
      </button>
    </article>
  );
}
