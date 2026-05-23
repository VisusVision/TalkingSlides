import { useEffect, useMemo, useState } from 'react';
import { Compass, SearchX } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { fetchCatalog, fetchCategories } from '../api';
import { useSectionState } from '../app/navigationState';
import SurfaceCard from '../components/ui/SurfaceCard';
import Button from '../components/ui/Button';
import LessonActionButton from '../components/moderation/LessonActionButton';
import { normalizeLesson, formatDuration, formatViews } from '../lib/content';
import { fuzzySearch } from '../utils/fuzzySearch';

function lessonSearchText(lesson) {
  return [lesson?.title, lesson?.description, lesson?.teacherName, lesson?.categoryName]
    .filter(Boolean)
    .join(' ');
}

export default function Browse({ user, onLoginRequest }) {
  const navigate = useNavigate();
  const [browseState, setBrowseState] = useSectionState('browse', {
    search: '',
    activeCategory: '',
  });
  const searchQuery = browseState.search || '';
  const activeCategory = browseState.activeCategory || '';
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lessons, setLessons] = useState([]);

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

  const hasSearch = Boolean(String(searchQuery || '').trim());
  const lessonSearchResult = useMemo(
    () => fuzzySearch(lessons, searchQuery, lessonSearchText),
    [lessons, searchQuery],
  );
  const filteredLessons = hasSearch ? lessonSearchResult.items : lessons;
  const setActiveCategory = (category) => setBrowseState({ activeCategory: category });

  return (
    <div className="space-y-6">
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

      {!loading && !error && hasSearch && lessonSearchResult.isFuzzyOnly && filteredLessons.length > 0 && (
        <SurfaceCard className="rounded-2xl p-4">
          <p className="text-sm font-semibold text-[var(--text-primary)]">No exact matches. Showing close matches.</p>
        </SurfaceCard>
      )}

      {loading && (
        <SurfaceCard elevated>
          <p className="body-md">Loading browse catalog...</p>
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
          <p className="title-lg text-[var(--text-primary)]">
            {hasSearch && activeCategory ? 'Filters are too restrictive' : hasSearch ? 'No lessons match your search' : 'No lessons found'}
          </p>
          <p className="body-md">
            {hasSearch && activeCategory ? 'Clear the category or search for another keyword.' : 'Try another keyword or category.'}
          </p>
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
                <span>Open In Player</span>
              </Button>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
