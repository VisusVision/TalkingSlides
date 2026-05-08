import { useEffect, useMemo, useState } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Clock3,
  PlayCircle,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { fetchCatalog, fetchCatalogFeed } from '../api';
import Button from '../components/ui/Button';
import SurfaceCard from '../components/ui/SurfaceCard';
import { fallbackSections, sectionsFromFeed } from '../lib/content';
import { formatDuration, formatViews } from '../lib/content';

const FALLBACK_CATEGORIES = [
  'All Topics',
  'Artificial Intelligence',
  'Creative Design',
  'Advanced Mathematics',
  'Digital Economics',
  'Neuroscience',
];

function lessonBackground(lesson, fallback = 'var(--card-fallback)') {
  if (!lesson?.imageUrl) {
    return { backgroundImage: fallback };
  }

  return {
    backgroundImage: `var(--card-image-overlay), url(${lesson.imageUrl})`,
    backgroundSize: 'cover',
    backgroundPosition: 'center',
  };
}

function flattenUniqueLessons(sections) {
  const seen = new Set();
  const items = [];

  sections.forEach((section) => {
    section.items.forEach((item) => {
      if (!item?.id || seen.has(item.id)) return;
      seen.add(item.id);
      items.push(item);
    });
  });

  return items;
}

export default function Home({ searchQuery, user }) {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [sections, setSections] = useState([]);
  const [activeCategory, setActiveCategory] = useState('All Topics');

  useEffect(() => {
    let active = true;

    async function loadDiscovery() {
      setLoading(true);
      setError('');

      try {
        const feed = await fetchCatalogFeed({ query: searchQuery, limit: 14 });
        const mapped = sectionsFromFeed(feed);
        if (!active) return;
        setSections(mapped);
      } catch (_feedError) {
        try {
          const catalog = await fetchCatalog();
          if (!active) return;
          setSections(fallbackSections(catalog));
        } catch (catalogError) {
          if (!active) return;
          setError(catalogError.message || 'Could not load discovery feed.');
          setSections([]);
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    loadDiscovery();

    return () => {
      active = false;
    };
  }, [searchQuery]);

  const featuredLesson = sections[0]?.items?.[0] || null;
  const allLessons = useMemo(() => flattenUniqueLessons(sections), [sections]);

  const continueLessons = useMemo(() => {
    const inProgress = allLessons.filter((lesson) => lesson.progress > 0);
    if (inProgress.length > 0) {
      return inProgress.slice(0, 4);
    }
    return allLessons.slice(0, 4);
  }, [allLessons]);

  const recommendedLessons = useMemo(() => {
    const bySection = sections.slice(1).flatMap((section) => section.items);
    const pool = bySection.length ? bySection : allLessons;
    return pool.slice(0, 10);
  }, [allLessons, sections]);

  const bentoLessons = useMemo(() => {
    if (allLessons.length >= 5) {
      return allLessons.slice(0, 5);
    }

    if (!allLessons.length) {
      return [];
    }

    const copy = [...allLessons];
    while (copy.length < 5) {
      copy.push(allLessons[copy.length % allLessons.length]);
    }
    return copy;
  }, [allLessons]);

  const trendingLessons = useMemo(() => {
    return [...allLessons]
      .sort((left, right) => Number(right.views || 0) - Number(left.views || 0))
      .slice(0, 5);
  }, [allLessons]);

  const categories = useMemo(() => {
    const unique = Array.from(
      new Set(allLessons.map((lesson) => String(lesson.categoryName || '').trim()).filter(Boolean)),
    );

    if (!unique.length) {
      return FALLBACK_CATEGORIES;
    }

    return ['All Topics', ...unique.slice(0, 5)];
  }, [allLessons]);

  const filteredRecommended = useMemo(() => {
    if (activeCategory === 'All Topics') return recommendedLessons;
    return recommendedLessons.filter((lesson) => lesson.categoryName === activeCategory);
  }, [activeCategory, recommendedLessons]);

  const openLesson = (lessonId) => navigate(`/watch?lesson=${lessonId}`);

  return (
    <div className="space-y-12 pb-10">
      {featuredLesson && (
        <section className="relative -mx-3 overflow-hidden rounded-[1.75rem] sm:-mx-6 lg:-mx-8" aria-label="Featured lesson">
          <div className="relative h-[66vh] min-h-[540px] max-h-[860px]">
            <div className="absolute inset-0" style={lessonBackground(featuredLesson, 'var(--hero-fallback)')} />
            <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(7,10,16,0.1)_6%,rgba(7,10,16,0.44)_44%,rgba(7,10,16,0.86)_100%)]" />
            <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(7,10,16,0.84)_2%,rgba(7,10,16,0.24)_57%,rgba(7,10,16,0.62)_100%)]" />

            <div className="relative z-10 flex h-full items-end px-5 pb-10 pt-24 sm:px-9 sm:pb-14 lg:px-12 lg:pb-16">
              <div className="max-w-3xl space-y-5">
                <div className="flex flex-wrap items-center gap-3 text-xs">
                  <span className="rounded-full border border-[color:rgba(208,188,255,0.3)] bg-[color:rgba(208,188,255,0.2)] px-3 py-1.5 font-semibold uppercase tracking-[0.14em] text-[color:var(--media-text-on-image)]">
                    ✦︎ AI Academy Original
                  </span>
                  <span className="rounded-full bg-[color:var(--media-pill-bg)] px-3 py-1.5 font-semibold uppercase tracking-[0.14em] text-[color:var(--media-text-on-image)]">
                    {featuredLesson.categoryName || 'AI Mastery'}
                  </span>
                </div>

                <h1 className="font-['Manrope'] text-[2.4rem] font-extrabold leading-[0.95] tracking-[-0.05em] text-[color:var(--media-text-on-image)] sm:text-[3.3rem] lg:text-[4.45rem]">
                  {featuredLesson.title}
                </h1>

                <p className="max-w-2xl text-sm leading-relaxed text-[color:var(--media-text-on-image)] opacity-90 sm:text-base">
                  {featuredLesson.description || 'Master the foundations of modern intelligence with guided cinematic lessons, transcript-first context, and practical studio workflows.'}
                </p>

                <div className="flex flex-wrap items-center gap-3">
                  <Button size="lg" onClick={() => openLesson(featuredLesson.id)}>
                    <PlayCircle size={18} />
                    <span>Watch Now</span>
                  </Button>
                  <Button variant="secondary" size="lg" onClick={() => navigate('/watch')}>
                    <span>Lesson Details</span>
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </section>
      )}

      <section className="-mt-4">
        <div className="rail-scroll flex items-center gap-3 overflow-x-auto pb-2">
          {categories.map((category) => {
            const selected = category === activeCategory;
            return (
              <button
                key={category}
                type="button"
                onClick={() => setActiveCategory(category)}
                className={`focus-ring whitespace-nowrap rounded-full px-5 py-2 text-sm font-semibold transition ${
                  selected
                    ? (category === 'All Topics'
                        ? 'bg-[var(--accent-primary)] text-[var(--accent-inverse)]'
                        : 'bg-[image:var(--accent-gradient)] text-[var(--accent-inverse)]'
                      )
                    : 'token-surface-elevated text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                {category}
              </button>
            );
          })}
        </div>
      </section>

      {loading && (
        <SurfaceCard elevated className="space-y-2">
          <p className="label-sm">Loading</p>
          <p className="body-md">Building your personalized dashboard...</p>
        </SurfaceCard>
      )}

      {error && (
        <SurfaceCard elevated className="space-y-2">
          <p className="label-sm">Feed Notice</p>
          <p className="text-sm text-[color:var(--feedback-danger-fg)]">{error}</p>
        </SurfaceCard>
      )}

      {!loading && continueLessons.length > 0 && (
        <section className="space-y-5">
          <div className="flex items-center justify-between">
            <h2 className="font-['Manrope'] text-2xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Continue Watching</h2>
            <button
              type="button"
              onClick={() => navigate('/history')}
              className="focus-ring text-sm font-semibold text-[var(--accent-primary)]"
            >
              View History
            </button>
          </div>

          <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-4">
            {continueLessons.map((lesson) => (
              <article key={`continue-${lesson.id}`} className="group overflow-hidden rounded-2xl token-surface-elevated p-0 transition hover:-translate-y-0.5">
                <button type="button" onClick={() => openLesson(lesson.id)} className="focus-ring block w-full text-left">
                  <div className="relative aspect-video" style={lessonBackground(lesson)}>
                    <div className="absolute inset-0 bg-black/10 transition group-hover:bg-black/0" />
                    <span className="absolute inset-0 flex items-center justify-center opacity-0 transition group-hover:opacity-100">
                      <span className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-black/50 text-white">
                        <PlayCircle size={24} />
                      </span>
                    </span>

                    <div className="absolute bottom-0 left-0 right-0 h-1 bg-black/30">
                      <div className="h-full bg-[image:var(--accent-gradient)]" style={{ width: `${Math.max(8, lesson.progress || 0)}%` }} />
                    </div>
                  </div>

                  <div className="space-y-1.5 p-4">
                    <p className="line-clamp-1 text-sm font-semibold text-[var(--text-primary)]">{lesson.title}</p>
                    <p className="text-xs text-[var(--text-secondary)]">{lesson.teacherName} • {Math.max(1, Math.ceil((100 - (lesson.progress || 0)) * (lesson.durationMinutes || 10) / 100))}m left</p>
                  </div>
                </button>
              </article>
            ))}
          </div>
        </section>
      )}

      {!loading && filteredRecommended.length > 0 && (
        <section className="space-y-5 overflow-hidden">
          <div className="flex items-center justify-between">
            <h2 className="font-['Manrope'] text-2xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Recommended For You</h2>
            <div className="hidden items-center gap-2 sm:flex">
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-full token-surface-elevated text-[var(--text-secondary)]">
                <ChevronLeft size={16} />
              </span>
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-full token-surface-elevated text-[var(--text-secondary)]">
                <ChevronRight size={16} />
              </span>
            </div>
          </div>

          <div className="rail-scroll flex gap-5 overflow-x-auto pb-2">
            {filteredRecommended.map((lesson) => (
              <article key={`recommended-${lesson.id}`} className="w-[300px] shrink-0">
                <button type="button" className="focus-ring block w-full text-left" onClick={() => openLesson(lesson.id)}>
                  <div className="mb-3 aspect-video overflow-hidden rounded-2xl" style={lessonBackground(lesson)} />
                  <p className="line-clamp-2 font-['Manrope'] text-lg font-bold tracking-[-0.02em] text-[var(--text-primary)]">{lesson.title}</p>
                  <div className="mt-2 flex items-center justify-between text-xs text-[var(--text-secondary)]">
                    <span>{lesson.teacherName}</span>
                    <span className="inline-flex items-center gap-1">
                      <Clock3 size={12} />
                      {formatDuration(lesson.durationMinutes)}
                    </span>
                  </div>
                </button>
              </article>
            ))}
          </div>
        </section>
      )}

      {!loading && bentoLessons.length > 0 && (
        <section className="space-y-6">
          <h2 className="inline-flex items-center gap-2 font-['Manrope'] text-2xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">
            <span className="material-symbols-outlined text-[1.2rem] text-[var(--accent-primary)]">auto_awesome</span>
            <span>AI & Machine Learning</span>
          </h2>

          <div className="grid grid-cols-1 gap-5 md:grid-cols-4 md:grid-rows-2 md:[grid-auto-rows:minmax(9rem,auto)]">
            <button
              type="button"
              onClick={() => openLesson(bentoLessons[0].id)}
              className="focus-ring relative overflow-hidden rounded-3xl p-0 text-left md:col-span-2 md:row-span-2"
            >
              <div className="absolute inset-0" style={lessonBackground(bentoLessons[0])} />
              <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(0,0,0,0.1)_0%,rgba(0,0,0,0.72)_100%)]" />
              <div className="relative flex h-full min-h-[320px] flex-col justify-end gap-2 p-6">
                <span className="inline-flex h-9 w-9 items-center justify-center rounded-2xl border border-white/20 bg-black/20 text-white">
                  <span className="material-symbols-outlined">neurology</span>
                </span>
                <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[color:var(--media-text-on-image)]">Masterclass</p>
                <p className="font-['Manrope'] text-2xl font-extrabold tracking-[-0.03em] text-[color:var(--media-text-on-image)]">{bentoLessons[0].title}</p>
                <p className="text-xs text-[color:var(--media-text-on-image)] opacity-85">{bentoLessons[0].teacherName}</p>
              </div>
            </button>

            {bentoLessons.slice(1, 5).map((lesson) => (
              <button
                key={`bento-${lesson.id}-${lesson.title}`}
                type="button"
                onClick={() => openLesson(lesson.id)}
                className="focus-ring relative overflow-hidden rounded-3xl p-0 text-left"
              >
                <div className="absolute inset-0" style={lessonBackground(lesson)} />
                <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(0,0,0,0.12)_0%,rgba(0,0,0,0.7)_100%)]" />
                <div className="relative flex h-full min-h-[150px] flex-col justify-end p-4">
                  <span className="mb-3 inline-flex h-8 w-8 items-center justify-center rounded-xl border border-white/20 bg-black/20 text-white">
                    <span className="material-symbols-outlined text-[1rem]">auto_awesome</span>
                  </span>
                  <p className="line-clamp-2 text-sm font-semibold text-[color:var(--media-text-on-image)]">{lesson.title}</p>
                </div>
              </button>
            ))}
          </div>
        </section>
      )}

      {!loading && trendingLessons.length > 0 && (
        <section className="space-y-5">
          <h2 className="font-['Manrope'] text-2xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Trending Now</h2>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-5">
            {trendingLessons.map((lesson, index) => (
              <button
                key={`trending-${lesson.id}`}
                type="button"
                onClick={() => openLesson(lesson.id)}
                className="focus-ring flex items-center gap-4 rounded-2xl token-surface-elevated p-4 text-left transition hover:bg-[color:var(--surface-muted)]"
              >
                <span className="font-['Manrope'] text-4xl font-extrabold italic tracking-[-0.04em] text-[var(--outline)]">{index + 1}</span>
                <span>
                  <span className="line-clamp-2 block text-sm font-semibold text-[var(--text-primary)]">{lesson.title}</span>
                  <span className="mt-1 block text-[0.66rem] font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">
                    {formatViews(lesson.views)}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      {!loading && allLessons.length === 0 && (
        <SurfaceCard elevated className="space-y-3 text-center">
          <p className="title-lg text-[var(--text-primary)]">No lessons matched your search</p>
          <p className="body-md">Try a broader keyword or continue in the full catalog.</p>
        </SurfaceCard>
      )}
    </div>
  );
}
