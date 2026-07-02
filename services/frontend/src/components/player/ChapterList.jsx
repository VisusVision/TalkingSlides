import SurfaceCard from '../ui/SurfaceCard';
import { ChevronDown } from 'lucide-react';
import { formatTimestamp } from '../../lib/watch';
import { useI18n } from '../../i18n/I18nProvider';

export default function ChapterList({
  chapters,
  activeChapterId,
  onJump,
  collapsed = false,
  onToggle,
}) {
  const { t } = useI18n();

  return (
    <SurfaceCard className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="label-sm">{t('watch.chapters')}</p>
          <h2 className="title-lg mt-1 text-[var(--text-primary)]">{t('watch.lectureFlow')}</h2>
        </div>
        {typeof onToggle === 'function' && (
          <button
            type="button"
            onClick={onToggle}
            className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-full token-surface text-[var(--text-secondary)]"
            aria-label={collapsed ? t('watch.expandChapters') : t('watch.collapseChapters')}
          >
            <ChevronDown
              size={15}
              className={`transition ${collapsed ? '-rotate-90' : 'rotate-0'}`}
            />
          </button>
        )}
      </div>

      {!collapsed && (
        <div className="max-h-[13.5rem] space-y-2 overflow-y-auto pr-1">
        {chapters.map((chapter) => {
          const isActive = chapter.id === activeChapterId;
          return (
            <button
              key={chapter.id}
              type="button"
              onClick={() => onJump(chapter.startSeconds)}
              className={`focus-ring w-full rounded-2xl border px-3 py-2 text-left transition ${
                isActive
                  ? 'border-[color:color-mix(in_srgb,var(--accent-primary),transparent_40%)] bg-[color:color-mix(in_srgb,var(--accent-primary),transparent_86%)]'
                  : 'border-transparent bg-[color:var(--surface-muted)] hover:border-[var(--border-subtle)]'
              }`}
            >
              <p className="text-xs text-[var(--text-secondary)]">{formatTimestamp(chapter.startSeconds)}</p>
              <p className="mt-1 text-sm font-medium text-[var(--text-primary)]">{chapter.title}</p>
            </button>
          );
        })}
        </div>
      )}

      {collapsed && (
        <p className="text-xs text-[var(--text-secondary)]">{t('watch.collapsed')}</p>
      )}
    </SurfaceCard>
  );
}
