import { useEffect, useMemo, useState } from 'react';
import { Compass, SearchX } from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';
import { fetchCatalog, fetchCategories } from '../api';
import SurfaceCard from '../components/ui/SurfaceCard';
import Button from '../components/ui/Button';
import Badge from '../components/ui/Badge';
import LessonActionButton from '../components/moderation/LessonActionButton';
import { usePageLoading } from '../components/ui/PageLoading';
import Skeleton from '../components/ui/Skeleton';
import { normalizeLesson, formatDuration, formatViews } from '../lib/content';
import {
  clearRouteSessionState,
  onRouteReset,
  readRouteSessionState,
  writeRouteSessionState,
} from '../utils/routeSession';

function BrowseCatalogSkeleton() {
  return (
    <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3" role="status" aria-live="polite" aria-label="Loading browse catalog">
      <span className="sr-only">Loading browse catalog...</span>
      {Array.from({ length: 6 }, (_, index) => (
        <Skeleton.Card key={`browse-skeleton-${index}`} className="token-surface-elevated p-3">
          <div className="space-y-4">
            <Skeleton className="h-36 w-full" rounded="lg" />
            <div className="space-y-2">
              <Skeleton className="h-5 w-5/6" rounded="full" />
              <Skeleton className="h-3 w-1/2" rounded="full" />
            </div>
            <div className="flex flex-wrap gap-2">
              <Skeleton className="h-6 w-20" rounded="full" />
              <Skeleton className="h-6 w-16" rounded="full" />
              <Skeleton className="h-6 w-14" rounded="full" />
            </div>
            <Skeleton className="h-8 w-32" rounded="full" />
          </div>
        </Skeleton.Card>
      ))}
    </section>
  );
}

export default function Browse({ searchQuery, user, onLoginRequest }) {
  const navigate = useNavigate();
  const location = useLocation();
  const directCategory = useMemo(() => {
    const params = new URLSearchParams(location.search || '');
    return String(params.get('category') || '').trim();
  }, [location.search]);
  const hasDirectBrowseLocation = Boolean(directCategory);
  const storedBrowseState = useMemo(
    () => (hasDirectBrowseLocation ? {} : readRouteSessionState('browse', user)),
    [hasDirectBrowseLocation, user],
  );
  const [categories, setCategories] = useState([]);
  const [activeCategory, setActiveCategory] = useState(() => directCategory || String(storedBrowseState.activeCategory || ''));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lessons, setLessons] = useState([]);

  usePageLoading(loading, 'browse-catalog');

  useEffect(() => {
    if (directCategory) {
      setActiveCategory(directCategory);
    }
  }, [directCategory]);

  useEffect(() => {
    writeRouteSessionState('browse', user, {
      activeCategory,
      scrollY: typeof window !== 'undefined' ? window.scrollY : 0,
    });
  }, [activeCategory, user]);

  useEffect(() => onRouteReset('browse', () => {
    clearRouteSessionState('browse', user);
    setActiveCategory('');
    window.scrollTo({ top: 0, behavior: 'auto' });
  }), [user]);

  useEffect(() => {
    if (loading || hasDirectBrowseLocation || !storedBrowseState.scrollY) return undefined;
    const restoreId = window.requestAnimationFrame(() => {
      window.scrollTo({ top: Number(storedBrowseState.scrollY) || 0, behavior: 'auto' });
    });
    return () => window.cancelAnimationFrame(restoreId);
  }, [hasDirectBrowseLocation, loading, storedBrowseState.scrollY]);

  useEffect(() => {
    const persistScroll = () => {
      writeRouteSessionState('browse', user, {
        activeCategory,
        scrollY: window.scrollY,
      });
    };
    window.addEventListener('pagehide', persistScroll);
    window.addEventListener('beforeunload', persistScroll);
    return () => {
      persistScroll();
      window.removeEventListener('pagehide', persistScroll);
      window.removeEventListener('beforeunload', persistScroll);
    };
  }, [activeCategory, user]);

  useEffect(() => {
    fetchCategories()
      .then((data) => setCategories(Array.isArray(data) ? data : []))
      .catch(() => setCategories([]));
  }, []);

  useEffect(() => {
    let active = true;

    async function loadCatalog() {
      setLoading(true);
      setError('');

      try {
        const payload = await fetchCatalog(activeCategory || null);
        if (!active) return;
        const list = Array.isArray(payload) ? payload : payload.results || [];
        setLessons(list.map((item) => normalizeLesson(item)));
      } catch (err) {
        if (!active) return;
        setError(err.message || 'Unable to load catalog.');
        setLessons([]);
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    loadCatalog();
    return () => {
      active = false;
    };
  }, [activeCategory]);

  const filteredLessons = useMemo(() => {
    const q = String(searchQuery || '').trim().toLowerCase();
    if (!q) return lessons;

    return lessons.filter((lesson) => {
      const blob = [lesson.title, lesson.description, lesson.teacherName, lesson.categoryName]
        .join(' ')
        .toLowerCase();
      return blob.includes(q);
    });
  }, [lessons, searchQuery]);

  return (
    <div className="space-y-6" aria-busy={loading}>
      <section className="layout-grid-12">
        <SurfaceCard elevated className="lg:col-span-8">
          <p className="label-sm">Explore</p>
          <h1 className="display-lg mt-2 text-[var(--text-primary)]">Browse The Catalog</h1>
          <p className="body-md mt-3 max-w-2xl">
            Curated lecture cards built for quick scanning, deep study, and smooth transition into player mode.
          </p>
        </SurfaceCard>

        <SurfaceCard className="lg:col-span-4">
          <p className="label-sm">Results</p>
          <p className="mt-3 text-4xl font-['Manrope'] font-bold tracking-[-0.04em] text-[var(--text-primary)]">
            {filteredLessons.length}
          </p>
          <p className="body-md mt-2">items match your active category and search query.</p>
        </SurfaceCard>
      </section>

      <SurfaceCard className="space-y-3">
        <p className="label-sm">Categories</p>
        <div className="rail-scroll flex gap-2 overflow-x-auto pb-1">
          <button
            type="button"
            onClick={() => setActiveCategory('')}
            className={`focus-ring rounded-full px-3 py-1.5 text-sm ${
              !activeCategory
                ? 'bg-[image:var(--accent-gradient)] text-[var(--accent-inverse)]'
                : 'token-surface text-[var(--text-secondary)]'
            }`}
          >
            All
          </button>
          {categories.map((category) => (
            <button
              key={category.id}
              type="button"
              onClick={() => setActiveCategory(category.slug || '')}
              className={`focus-ring rounded-full px-3 py-1.5 text-sm ${
                activeCategory === (category.slug || '')
                  ? 'bg-[image:var(--accent-gradient)] text-[var(--accent-inverse)]'
                  : 'token-surface text-[var(--text-secondary)]'
              }`}
            >
              {category.name}
            </button>
          ))}
        </div>
      </SurfaceCard>

      {loading && <BrowseCatalogSkeleton />}

      {error && (
        <SurfaceCard elevated>
          <p className="text-sm text-[color:var(--feedback-danger-fg)]">{error}</p>
        </SurfaceCard>
      )}

      {!loading && !error && filteredLessons.length === 0 && (
        <SurfaceCard elevated className="space-y-2 text-center">
          <SearchX className="mx-auto text-[var(--text-secondary)]" size={20} />
          <p className="title-lg text-[var(--text-primary)]">No lessons found</p>
          <p className="body-md">Try another keyword or category.</p>
        </SurfaceCard>
      )}

      {!loading && !error && filteredLessons.length > 0 && (
        <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {filteredLessons.map((lesson) => (
            <SurfaceCard
              as="article"
              key={lesson.id}
              variant="elevated"
              padding="sm"
              interactive
              className="relative"
            >
              <LessonActionButton
                lesson={lesson}
                user={user}
                onLoginRequest={onLoginRequest}
                compact
                className="absolute right-6 top-6 z-20 bg-[color:rgba(255,255,255,0.9)] text-slate-700"
              />
              <div
                className="mb-3 h-36 rounded-2xl"
                style={{
                  background: lesson.imageUrl
                    ? `var(--browse-image-overlay), url(${lesson.imageUrl}) center/cover`
                    : 'var(--browse-fallback)',
                }}
              />
              <p className="title-lg text-[var(--text-primary)]">{lesson.title}</p>
              <p className="mt-1 text-sm text-[var(--text-secondary)]">{lesson.teacherName}</p>
              <div className="mt-3 flex flex-wrap gap-2 text-xs text-[var(--text-secondary)]">
                <Badge>{lesson.categoryName}</Badge>
                <Badge>{formatDuration(lesson.durationMinutes)}</Badge>
                <Badge>{formatViews(lesson.views)}</Badge>
              </div>
              <Button className="mt-4" size="sm" onClick={() => navigate(`/watch?lesson=${lesson.id}`)}>
                <Compass size={14} />
                <span>Open In Player</span>
              </Button>
            </SurfaceCard>
          ))}
        </section>
      )}
    </div>
  );
}
