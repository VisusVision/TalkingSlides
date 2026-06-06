import { useEffect, useMemo, useState } from 'react';
import { BookOpenCheck, PlayCircle } from 'lucide-react';
import Button from '../ui/Button';
import { formatDuration } from '../../lib/content';

function reducedMotionEnabled() {
  if (typeof window === 'undefined') return false;
  const mediaPrefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const classBasedReduced = document.documentElement.classList.contains('reduced-motion');
  return mediaPrefersReduced || classBasedReduced;
}

function resolveHeroMedia(lesson) {
  const poster = String(lesson?.imageUrl || '').trim();
  const video = String(
    lesson?.hero_video_url
      || lesson?.preview_video_url
      || lesson?.trailer_url
      || lesson?.stream_url
      || lesson?.video_url
      || '',
  ).trim();

  return { poster, video };
}

export default function FeaturedHero({ lesson, onStartLesson, onBrowse }) {
  if (!lesson) return null;

  const { poster, video } = useMemo(() => resolveHeroMedia(lesson), [lesson]);
  const [reducedMotion, setReducedMotion] = useState(reducedMotionEnabled);
  const [videoFailed, setVideoFailed] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;

    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(reducedMotionEnabled());

    update();
    mediaQuery.addEventListener('change', update);

    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class'],
    });

    return () => {
      mediaQuery.removeEventListener('change', update);
      observer.disconnect();
    };
  }, []);

  useEffect(() => {
    setVideoFailed(false);
  }, [video]);

  const shouldPlayVideo = useMemo(
    () => Boolean(video && !reducedMotion && !videoFailed),
    [video, reducedMotion, videoFailed],
  );

  return (
    <section
      className="cinematic-fade relative -mx-3 overflow-hidden sm:-mx-6 lg:-mx-8"
      aria-label="Featured lecture"
    >
      <div className="relative min-h-[430px] max-h-[780px] h-[68vh]">
        {poster ? (
          <img
            src={poster}
            alt={lesson.title}
            className="absolute inset-0 h-full w-full object-cover"
          />
        ) : (
          <div className="absolute inset-0" style={{ background: 'var(--hero-fallback)' }} />
        )}

        {shouldPlayVideo && (
          <video
            className="absolute inset-0 h-full w-full object-cover"
            src={video}
            poster={poster || undefined}
            autoPlay
            loop
            muted
            playsInline
            onError={() => setVideoFailed(true)}
          />
        )}

        <div className="absolute inset-0 bg-[image:var(--hero-image-overlay)]" />
        <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(6,10,16,0.1)_0%,rgba(6,10,16,0.38)_48%,rgba(6,10,16,0.82)_100%)]" />

        <div className="relative z-10 mx-auto flex h-full max-w-[1600px] items-end px-4 pb-10 pl-20 pt-24 sm:px-7 sm:pb-14 sm:pl-24 lg:px-10 lg:pb-16 lg:pl-28">
          <div className="max-w-3xl space-y-4">
            <p className="label-sm text-[color:var(--media-text-on-image)] opacity-80">Featured Lecture</p>
            <h1 className="display-lg text-[color:var(--media-text-on-image)]">{lesson.title}</h1>
            <p className="max-w-2xl text-sm leading-relaxed text-[color:var(--media-text-on-image)] opacity-90 sm:text-base">
              {lesson.description ||
                'Explore a cinematic lesson experience built for clarity: chapter-led learning, transcript context, and quick notes in one focused workspace.'}
            </p>

            <div className="flex flex-wrap gap-2 text-xs text-[color:var(--media-text-on-image)] opacity-85 sm:text-sm">
              <span className="rounded-full bg-[color:var(--media-pill-bg)] px-3 py-1.5 backdrop-blur-sm">
                {lesson.categoryName}
              </span>
              <span className="rounded-full bg-[color:var(--media-pill-bg)] px-3 py-1.5 backdrop-blur-sm">
                {formatDuration(lesson.durationMinutes)}
              </span>
              <span className="rounded-full bg-[color:var(--media-pill-bg)] px-3 py-1.5 backdrop-blur-sm">
                with {lesson.teacherName}
              </span>
            </div>

            <div className="flex flex-wrap items-center gap-3 pt-2">
              <Button size="lg" onClick={() => onStartLesson(lesson.id)}>
                <PlayCircle size={18} />
                <span>Start Watching</span>
              </Button>
              <Button variant="secondary" size="lg" onClick={onBrowse}>
                <BookOpenCheck size={18} />
                <span>Browse Curriculum</span>
              </Button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
