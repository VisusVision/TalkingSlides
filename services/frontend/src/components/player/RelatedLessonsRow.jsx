import LessonCard from '../discovery/LessonCard';

export default function RelatedLessonsRow({ lessons, onOpenLesson }) {
  if (!lessons.length) return null;

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="headline-md text-[var(--text-primary)]">Related Clips</h2>
        <p className="label-sm">Keep momentum</p>
      </div>

      <div className="rail-scroll flex gap-4 overflow-x-auto pb-2">
        {lessons.map((lesson) => (
          <LessonCard key={`related-${lesson.id}`} lesson={lesson} onOpen={onOpenLesson} compact />
        ))}
      </div>
    </section>
  );
}
