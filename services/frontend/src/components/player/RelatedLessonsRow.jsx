import LessonCard from '../discovery/LessonCard';
import { useI18n } from '../../i18n/I18nProvider';

export default function RelatedLessonsRow({ lessons, onOpenLesson }) {
  const { t } = useI18n();

  if (!lessons.length) return null;

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="headline-md text-[var(--text-primary)]">{t('watch.relatedClips')}</h2>
        <p className="label-sm">{t('watch.keepMomentum')}</p>
      </div>

      <div className="rail-scroll flex gap-4 overflow-x-auto pb-2">
        {lessons.map((lesson) => (
          <LessonCard key={`related-${lesson.id}`} lesson={lesson} onOpen={onOpenLesson} compact />
        ))}
      </div>
    </section>
  );
}
