import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Clock3,
  PlayCircle,
  SearchX,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { fetchCatalog, fetchCatalogFeed } from '../api';
import { useSectionState } from '../app/navigationState';
import LessonActionButton from '../components/moderation/LessonActionButton';
import Button from '../components/ui/Button';
import SurfaceCard from '../components/ui/SurfaceCard';
import { fallbackSections, sectionsFromFeed } from '../lib/content';
import { formatDuration, formatViews } from '../lib/content';
import { fuzzySearch } from '../utils/fuzzySearch';

const ALL_TOPICS = 'All Topics';

const FALLBACK_CATEGORIES = [
  ALL_TOPICS,
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

function lessonKey(lesson) {
  return String(lesson?.id || '');
}

function uniqueLessons(lessons) {
  const seen = new Set();
  const items = [];

  lessons.forEach((lesson) => {
    const key = lessonKey(lesson);
    if (!key || seen.has(key)) return;
    seen.add(key);
    items.push(lesson);
  });

  return items;
}

function lessonsForCategory(lessons, activeCategory) {
  const unique = uniqueLessons(lessons);
  if (activeCategory === ALL_TOPICS) return unique;
  return unique.filter((lesson) => lesson.categoryName === activeCategory);
}

function takeAvoidingSeen(lessons, seenIds, limit, minUnseen = 3) {
  const unique = uniqueLessons(lessons);
  const unseen = unique.filter((lesson) => !seenIds.has(lessonKey(lesson)));
  const minimum = Math.min(limit, minUnseen);
  const source = unseen.length >= minimum ? unseen : unique;
  return source.slice(0, limit);
}

function lessonSearchText(lesson) {
  return [lesson?.title, lesson?.description, lesson?.teacherName, lesson?.categoryName]
    .filter(Boolean)
    .join(' ');
}

function railScrollState(node) {
  if (!node) return { canScrollLeft: false, canScrollRight: false };
  const maxScrollLeft = Math.max(0, node.scrollWidth - node.clientWidth);
  return {
    canScrollLeft: node.scrollLeft > 2,
    canScrollRight: node.scrollLeft < maxScrollLeft - 2,
  };
}

export default function Home({ user, onLoginRequest }) {
  const navigate = useNavigate();
  const recommendedRailRef = useRef(null);
  const [dashboardState, setDashboardState] = useSectionState('dashboard', {
    search: '',
    activeCategory: ALL_TOPICS,
  });
  const searchQuery = dashboardState.search || '';
  const activeCategory = dashboardState.activeCategory || ALL_TOPICS;
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [sections, setSections] = useState([]);
  const [recommendedRailState, setRecommendedRailState] = useState({
    canScrollLeft: false,
    canScrollRight: false,
  });

  useEffect(() => {
    let active = true;

    async function loadDiscovery() {
      setLoading(true);
      setError('');

      try {
        const feed = await fetchCatalogFeed({ limit: 24 });
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
  }, []);

  const allLessons = useMemo(() => flattenUniqueLessons(sections), [sections]);
  const hasSearch = Boolean(String(searchQuery || '').trim());
  const searchResult = useMemo(
    () => fuzzySearch(allLessons, searchQuery, lessonSearchText),
    [allLessons, searchQuery],
  );
  const searchableLessons = hasSearch ? searchResult.items : allLessons;

  const categories = useMemo(() => {
    const unique = Array.from(
      new Set(allLessons.map((lesson) => String(lesson.categoryName || '').trim()).filter(Boolean)),
    );

    if (!unique.length) {
      return FALLBACK_CATEGORIES;
    }

    return [ALL_TOPICS, ...unique.slice(0, 5)];
  }, [allLessons]);

  useEffect(() => {
    if (!categories.includes(activeCategory)) {
      setDashboardState({ activeCategory: ALL_TOPICS });
    }
  }, [activeCategory, categories, setDashboardState]);

  const scopedLessons = useMemo(
    () => lessonsForCategory(searchableLessons, activeCategory),
    [activeCategory, searchableLessons],
  );

  const featuredLesson = useMemo(() => {
    if (activeCategory !== ALL_TOPICS) {
      return scopedLessons[0] || null;
    }
    if (hasSearch) {
      return searchableLessons[0] || null;
    }
    return sections[0]?.items?.[0] || allLessons[0] || null;
  }, [activeCategory, allLessons, hasSearch, scopedLessons, searchableLessons, sections]);

  const continueLessons = useMemo(() => (
    scopedLessons
      .filter((lesson) => lesson.progress > 0 && lesson.progress < 100)
      .slice(0, 4)
  ), [scopedLessons]);

  const recommendedLessons = useMemo(() => {
    if (hasSearch) return searchableLessons;
    const recommendedSection = sections.find((section) => section.key === 'recommended');
    const bySection = recommendedSection?.items?.length
      ? recommendedSection.items
      : sections.slice(1).flatMap((section) => section.items);
    const pool = bySection.length ? bySection : allLessons;
    return uniqueLessons(pool).slice(0, 10);
  }, [allLessons, hasSearch, searchableLessons, sections]);

  const filteredRecommended = useMemo(() => {
    const candidates = activeCategory === ALL_TOPICS
      ? recommendedLessons
      : uniqueLessons([
          ...recommendedLessons.filter((lesson) => lesson.categoryName === activeCategory),
          ...scopedLessons,
        ]);
    const seenAbove = new Set(
      [featuredLesson, ...continueLessons]
        .map(lessonKey)
        .filter(Boolean),
    );
    return takeAvoidingSeen(candidates, seenAbove, 10, 4);
  }, [activeCategory, continueLessons, featuredLesson, recommendedLessons, scopedLessons]);

  const bentoLessons = useMemo(() => {
    const seenAbove = new Set(
      [featuredLesson, ...continueLessons, ...filteredRecommended]
        .map(lessonKey)
        .filter(Boolean),
    );
    return takeAvoidingSeen(scopedLessons, seenAbove, 5);
  }, [continueLessons, featuredLesson, filteredRecommended, scopedLessons]);

  const trendingLessons = useMemo(() => {
    const sorted = [...scopedLessons]
      .sort((left, right) => Number(right.views || 0) - Number(left.views || 0));
    const seenAbove = new Set(
      [featuredLesson, ...continueLessons, ...filteredRecommended, ...bentoLessons]
        .map(lessonKey)
        .filter(Boolean),
    );
    return takeAvoidingSeen(sorted, seenAbove, 5);
  }, [bentoLessons, continueLessons, featuredLesson, filteredRecommended, scopedLessons]);

  const topicRailTitle = activeCategory === ALL_TOPICS ? 'Explore by topic' : `More in ${activeCategory}`;

  const updateRecommendedRailState = useCallback(() => {
    setRecommendedRailState(railScrollState(recommendedRailRef.current));
  }, []);

  useEffect(() => {
    const node = recommendedRailRef.current;
    if (!node) return undefined;

    updateRecommendedRailState();
    const handleScroll = () => updateRecommendedRailState();
    const resizeTimeout = window.setTimeout(handleScroll, 0);

    node.addEventListener('scroll', handleScroll, { passive: true });
    window.addEventListener('resize', handleScroll);

    return () => {
      window.clearTimeout(resizeTimeout);
      node.removeEventListener('scroll', handleScroll);
      window.removeEventListener('resize', handleScroll);
    };
  }, [filteredRecommended.length, updateRecommendedRailState]);

  const scrollRecommendedRail = useCallback((direction) => {
    const node = recommendedRailRef.current;
    if (!node) return;
    node.scrollBy({
      left: Math.round(node.clientWidth * 0.82) * direction,
      behavior: 'smooth',
    });
    window.setTimeout(updateRecommendedRailState, 350);
  }, [updateRecommendedRailState]);

  const openLesson = (lessonId) => navigate(`/watch?lesson=${lessonId}`);
  const updateActiveCategory = useCallback((category) => {
    setDashboardState({ activeCategory: category });
  }, [setDashboardState]);

  return (
    <div className="space-y-12 pb-10">
      {!loading && hasSearch && searchResult.isFuzzyOnly && searchableLessons.length > 0 && (
        <SurfaceCard className="rounded-2xl p-4">
          <p className="text-sm font-semibold text-[var(--text-primary)]">No exact matches. Showing close matches.</p>
        </SurfaceCard>
      )}

      {featuredLesson && (
        <section className="relative -mx-3 overflow-hidden rounded-[1.75rem] sm:-mx-6 lg:-mx-8" aria-label="Featured lesson">
          <LessonActionButton
            lesson={featuredLesson}
            user={user}
            onLoginRequest={onLoginRequest}
            compact
            className="absolute right-5 top-24 z-20 bg-[color:rgba(255,255,255,0.9)] text-slate-700 sm:right-9"
          />
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
                onClick={() => updateActiveCategory(category)}
                className={`focus-ring whitespace-nowrap rounded-full px-5 py-2 text-sm font-semibold transition ${
                  selected
                    ? (category === ALL_TOPICS
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
              <article key={`continue-${lesson.id}`} className="group relative overflow-hidden rounded-2xl token-surface-elevated p-0 transition hover:-translate-y-0.5">
                <LessonActionButton
                  lesson={lesson}
                  user={user}
                  onLoginRequest={onLoginRequest}
                  compact
                  className="absolute right-3 top-3 z-20 bg-[color:rgba(255,255,255,0.9)] text-slate-700"
                />
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
              <button
                type="button"
                aria-label="Scroll recommended lessons left"
                onClick={() => scrollRecommendedRail(-1)}
                disabled={!recommendedRailState.canScrollLeft}
                className={`focus-ring inline-flex h-9 w-9 items-center justify-center rounded-full token-surface-elevated text-[var(--text-secondary)] transition ${
                  recommendedRailState.canScrollLeft ? 'hover:text-[var(--text-primary)]' : 'cursor-not-allowed opacity-40'
                }`}
              >
                <ChevronLeft size={16} />
              </button>
              <button
                type="button"
                aria-label="Scroll recommended lessons right"
                onClick={() => scrollRecommendedRail(1)}
                disabled={!recommendedRailState.canScrollRight}
                className={`focus-ring inline-flex h-9 w-9 items-center justify-center rounded-full token-surface-elevated text-[var(--text-secondary)] transition ${
                  recommendedRailState.canScrollRight ? 'hover:text-[var(--text-primary)]' : 'cursor-not-allowed opacity-40'
                }`}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>

          <div ref={recommendedRailRef} className="rail-scroll flex gap-5 overflow-x-auto pb-2">
            {filteredRecommended.map((lesson) => (
              <article key={`recommended-${lesson.id}`} className="relative w-[300px] shrink-0">
                <LessonActionButton
                  lesson={lesson}
                  user={user}
                  onLoginRequest={onLoginRequest}
                  compact
                  className="absolute right-3 top-3 z-20 bg-[color:rgba(255,255,255,0.9)] text-slate-700"
                />
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
            <span>{topicRailTitle}</span>
          </h2>

          <div className="grid grid-cols-1 gap-5 md:grid-cols-4 md:grid-rows-2 md:[grid-auto-rows:minmax(9rem,auto)]">
            <article className="relative md:col-span-2 md:row-span-2">
              <LessonActionButton
                lesson={bentoLessons[0]}
                user={user}
                onLoginRequest={onLoginRequest}
                compact
                className="absolute right-4 top-4 z-20 bg-[color:rgba(255,255,255,0.9)] text-slate-700"
              />
              <button
                type="button"
                onClick={() => openLesson(bentoLessons[0].id)}
                className="focus-ring relative h-full w-full overflow-hidden rounded-3xl p-0 text-left"
              >
                <div className="absolute inset-0" style={lessonBackground(bentoLessons[0])} />
                <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(0,0,0,0.1)_0%,rgba(0,0,0,0.72)_100%)]" />
                <div className="relative flex h-full min-h-[320px] flex-col justify-end gap-2 p-6 pr-14">
                  <span className="inline-flex h-9 w-9 items-center justify-center rounded-2xl border border-white/20 bg-black/20 text-white">
                    <span className="material-symbols-outlined">neurology</span>
                  </span>
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[color:var(--media-text-on-image)]">Masterclass</p>
                  <p className="font-['Manrope'] text-2xl font-extrabold tracking-[-0.03em] text-[color:var(--media-text-on-image)]">{bentoLessons[0].title}</p>
                  <p className="text-xs text-[color:var(--media-text-on-image)] opacity-85">{bentoLessons[0].teacherName}</p>
                </div>
              </button>
            </article>

            {bentoLessons.slice(1, 5).map((lesson) => (
              <article key={`bento-${lesson.id}-${lesson.title}`} className="relative">
                <LessonActionButton
                  lesson={lesson}
                  user={user}
                  onLoginRequest={onLoginRequest}
                  compact
                  className="absolute right-3 top-3 z-20 bg-[color:rgba(255,255,255,0.9)] text-slate-700"
                />
                <button
                  type="button"
                  onClick={() => openLesson(lesson.id)}
                  className="focus-ring relative h-full w-full overflow-hidden rounded-3xl p-0 text-left"
                >
                  <div className="absolute inset-0" style={lessonBackground(lesson)} />
                  <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(0,0,0,0.12)_0%,rgba(0,0,0,0.7)_100%)]" />
                  <div className="relative flex h-full min-h-[150px] flex-col justify-end p-4 pr-12">
                    <span className="mb-3 inline-flex h-8 w-8 items-center justify-center rounded-xl border border-white/20 bg-black/20 text-white">
                      <span className="material-symbols-outlined text-[1rem]">auto_awesome</span>
                    </span>
                    <p className="line-clamp-2 text-sm font-semibold text-[color:var(--media-text-on-image)]">{lesson.title}</p>
                  </div>
                </button>
              </article>
            ))}
          </div>
        </section>
      )}

      {!loading && trendingLessons.length > 0 && (
        <section className="space-y-5">
          <h2 className="font-['Manrope'] text-2xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Trending Now</h2>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-5">
            {trendingLessons.map((lesson, index) => (
              <article key={`trending-${lesson.id}`} className="relative">
                <LessonActionButton
                  lesson={lesson}
                  user={user}
                  onLoginRequest={onLoginRequest}
                  compact
                  className="absolute right-3 top-3 z-20 bg-[color:rgba(255,255,255,0.9)] text-slate-700"
                />
                <button
                  type="button"
                  onClick={() => openLesson(lesson.id)}
                  className="focus-ring flex min-h-20 w-full items-center gap-4 rounded-2xl token-surface-elevated p-4 pr-14 text-left transition hover:bg-[color:var(--surface-muted)]"
                >
                  <span className="font-['Manrope'] text-4xl font-extrabold italic tracking-[-0.04em] text-[var(--outline)]">{index + 1}</span>
                  <span>
                    <span className="line-clamp-2 block text-sm font-semibold text-[var(--text-primary)]">{lesson.title}</span>
                    <span className="mt-1 block text-[0.66rem] font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">
                      {formatViews(lesson.views)}
                    </span>
                  </span>
                </button>
              </article>
            ))}
          </div>
        </section>
      )}

      {!loading && hasSearch && searchResult.items.length === 0 && (
        <SurfaceCard elevated className="space-y-3 text-center">
          <SearchX className="mx-auto text-[var(--text-secondary)]" size={20} />
          <p className="title-lg text-[var(--text-primary)]">No lessons matched your search</p>
          <p className="body-md">Try a broader keyword or continue in the full catalog.</p>
        </SurfaceCard>
      )}

      {!loading && hasSearch && searchResult.items.length > 0 && scopedLessons.length === 0 && activeCategory !== ALL_TOPICS && (
        <SurfaceCard elevated className="space-y-3 text-center">
          <SearchX className="mx-auto text-[var(--text-secondary)]" size={20} />
          <p className="title-lg text-[var(--text-primary)]">Filters are too restrictive</p>
          <p className="body-md">Clear the topic filter or try another search.</p>
        </SurfaceCard>
      )}

      {!loading && !hasSearch && allLessons.length === 0 && (
        <SurfaceCard elevated className="space-y-3 text-center">
          <p className="title-lg text-[var(--text-primary)]">No lessons available yet</p>
          <p className="body-md">Check the catalog again after lessons are published.</p>
        </SurfaceCard>
      )}
    </div>
  );
}
