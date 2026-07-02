import { useEffect, useMemo, useState } from 'react';
import { Compass, SearchX } from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';
import { fetchCatalog, fetchCategories } from '../api';
import SurfaceCard from '../components/ui/SurfaceCard';
import Button from '../components/ui/Button';
import LessonActionButton from '../components/moderation/LessonActionButton';
import { usePageLoading } from '../components/ui/PageLoading';
import { useI18n } from '../i18n/I18nProvider';
import { normalizeLesson } from '../lib/content';
import {
  clearRouteSessionState,
  onRouteReset,
  readRouteSessionState,
  writeRouteSessionState,
} from '../utils/routeSession';

export default function Browse({ searchQuery, user, onLoginRequest }) {
  const { t, formatDuration, formatViews } = useI18n();
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
        setError(err.message || t('browse.loadError'));
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
  }, [activeCategory, t]);

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
    <div className="space-y-6">
      <section className="layout-grid-12">
        <SurfaceCard elevated className="lg:col-span-8">
          <p className="label-sm">{t('browse.explore')}</p>
          <h1 className="display-lg mt-2 text-[var(--text-primary)]">{t('browse.title')}</h1>
          <p className="body-md mt-3 max-w-2xl">
            {t('browse.subtitle')}
          </p>
        </SurfaceCard>

        <SurfaceCard className="lg:col-span-4">
          <p className="label-sm">{t('browse.results')}</p>
          <p className="mt-3 text-4xl font-['Manrope'] font-bold tracking-[-0.04em] text-[var(--text-primary)]">
            {filteredLessons.length}
          </p>
          <p className="body-md mt-2">{t('browse.resultsBody')}</p>
        </SurfaceCard>
      </section>

      <SurfaceCard className="space-y-3">
        <p className="label-sm">{t('browse.categories')}</p>
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
            {t('browse.all')}
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

      {loading && (
        <SurfaceCard elevated>
          <p className="body-md">{t('browse.loading')}</p>
        </SurfaceCard>
      )}

      {error && (
        <SurfaceCard elevated>
          <p className="text-sm text-[color:var(--feedback-danger-fg)]">{error}</p>
        </SurfaceCard>
      )}

      {!loading && !error && filteredLessons.length === 0 && (
        <SurfaceCard elevated className="space-y-2 text-center">
          <SearchX className="mx-auto text-[var(--text-secondary)]" size={20} />
          <p className="title-lg text-[var(--text-primary)]">{t('browse.noResultsTitle')}</p>
          <p className="body-md">{t('browse.noResultsBody')}</p>
        </SurfaceCard>
      )}

      {!loading && !error && filteredLessons.length > 0 && (
        <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {filteredLessons.map((lesson) => (
            <article
              key={lesson.id}
              className="relative rounded-3xl token-surface-elevated p-4 shadow-soft transition hover:-translate-y-1 hover:shadow-lift"
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
                <span className="rounded-full bg-[color:var(--surface-muted)] px-2 py-1">{lesson.categoryName}</span>
                <span className="rounded-full bg-[color:var(--surface-muted)] px-2 py-1">{formatDuration(lesson.durationMinutes)}</span>
                <span className="rounded-full bg-[color:var(--surface-muted)] px-2 py-1">{formatViews(lesson.views)}</span>
              </div>
              <Button className="mt-4" size="sm" onClick={() => navigate(`/watch?lesson=${lesson.id}`)}>
                <Compass size={14} />
                <span>{t('browse.openInPlayer')}</span>
              </Button>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
