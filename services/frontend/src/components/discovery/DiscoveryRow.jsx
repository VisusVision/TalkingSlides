import LessonCard from './LessonCard';

export default function DiscoveryRow({ title, items, onOpenLesson }) {
  if (!items?.length) {
    return null;
  }

  return (
    <section className="cinematic-fade space-y-4">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="headline-md text-[var(--text-primary)]">{title}</h2>
        <p className="label-sm">Swipe to explore</p>
      </div>

      <div className="rail-scroll flex gap-4 overflow-x-auto pb-3">
        {items.map((lesson) => (
          <LessonCard key={`${title}-${lesson.id}`} lesson={lesson} onOpen={onOpenLesson} />
        ))}
      </div>
    </section>
  );
}
