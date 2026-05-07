import { useEffect, useMemo, useState } from 'react';
import {
  BookmarkCheck,
  CirclePlay,
  LogIn,
  SplitSquareHorizontal,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { fetchCatalog, fetchCatalogFeed, fetchProjectTranscript, fetchProjects } from '../api';
import Button from '../components/ui/Button';
import SurfaceCard from '../components/ui/SurfaceCard';
import { fallbackSections, formatDuration, sectionsFromFeed } from '../lib/content';

const LESSON_TABS = ['overview', 'transcript', 'slides', 'notes'];

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

function myLessonNotesKey(lessonId) {
  return `visus-library-notes-${lessonId || 'none'}`;
}

function lessonBackground(lesson, fallback = 'var(--hero-fallback)') {
  if (!lesson?.imageUrl) {
    return { backgroundImage: fallback };
  }

  return {
    backgroundImage: `var(--card-image-overlay), url(${lesson.imageUrl})`,
    backgroundSize: 'cover',
    backgroundPosition: 'center',
  };
}

function projectToLesson(project) {
  return {
    id: Number(project?.id || 0),
    title: String(project?.title || `Project #${project?.id || ''}`),
    description: String(project?.description || ''),
    category_name: String(project?.category_name || 'My Draft'),
    teacher_name: String(project?.user_name || 'You'),
    cover_url: String(project?.cover_url || project?.thumbnail_url || ''),
    thumbnail_url: String(project?.thumbnail_url || project?.cover_url || ''),
    duration_minutes: 8,
    progress_pct: 0,
    stream_url: '',
  };
}

export default function Library({ user, searchQuery, onLoginRequest }) {
  const navigate = useNavigate();
  const [sections, setSections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedLessonId, setSelectedLessonId] = useState(null);
  const [activeTab, setActiveTab] = useState('overview');
  const [transcriptPages, setTranscriptPages] = useState([]);
  const [loadingTranscript, setLoadingTranscript] = useState(false);
  const [lessonNotes, setLessonNotes] = useState('');
  const [notesSavedAt, setNotesSavedAt] = useState('');

  useEffect(() => {
    let active = true;

    async function loadLibrary() {
      setLoading(true);
      setError('');

      if (!user) {
        if (!active) return;
        setSections([]);
        setLoading(false);
        return;
      }

      try {
        let myDraftSection = [];
        try {
          const ownProjects = await fetchProjects();
          const ownItems = (Array.isArray(ownProjects) ? ownProjects : [])
            .map(projectToLesson)
            .filter((item) => item.id > 0);
          if (ownItems.length) {
            myDraftSection = [{ id: 'my-drafts', title: 'My Drafts', items: ownItems }];
          }
        } catch {
          myDraftSection = [];
        }

        const feed = await fetchCatalogFeed({
          query: searchQuery,
          limit: 18,
          rankBy: 'recent',
          watchedOnly: true,
        });
        if (!active) return;
        const watchedSections = sectionsFromFeed(feed);

        if (watchedSections.length > 0) {
          setSections([...myDraftSection, ...watchedSections]);
          return;
        }

        const fallbackFeed = await fetchCatalogFeed({
          query: searchQuery,
          limit: 18,
          rankBy: 'recent',
        });
        if (!active) return;
        setSections([...myDraftSection, ...sectionsFromFeed(fallbackFeed)]);
      } catch (_feedError) {
        try {
          const catalog = await fetchCatalog();
          if (!active) return;
          const fallback = fallbackSections(catalog);
          try {
            const ownProjects = await fetchProjects();
            const ownItems = (Array.isArray(ownProjects) ? ownProjects : [])
              .map(projectToLesson)
              .filter((item) => item.id > 0);
            if (ownItems.length) {
              setSections([{ id: 'my-drafts', title: 'My Drafts', items: ownItems }, ...fallback]);
            } else {
              setSections(fallback);
            }
          } catch {
            setSections(fallback);
          }
          setError('Personalized feed unavailable. Showing catalog fallback.');
        } catch (catalogError) {
          if (!active) return;
          setError(catalogError.message || 'Unable to load your library.');
          setSections([]);
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    loadLibrary();
    return () => {
      active = false;
    };
  }, [searchQuery, user]);

  const lessons = useMemo(() => flattenUniqueLessons(sections), [sections]);

  useEffect(() => {
    if (!lessons.length) {
      setSelectedLessonId(null);
      return;
    }

    setSelectedLessonId((previous) => {
      if (previous && lessons.some((lesson) => lesson.id === previous)) {
        return previous;
      }
      return lessons[0].id;
    });
  }, [lessons]);

  const selectedLesson = useMemo(() => {
    if (!lessons.length) return null;
    return lessons.find((lesson) => lesson.id === selectedLessonId) || lessons[0];
  }, [lessons, selectedLessonId]);

  const upNextLessons = useMemo(() => {
    if (!selectedLesson) return lessons.slice(0, 8);
    return lessons.filter((lesson) => lesson.id !== selectedLesson.id).slice(0, 8);
  }, [lessons, selectedLesson]);

  useEffect(() => {
    if (!selectedLesson?.id || !user) {
      setTranscriptPages([]);
      return;
    }

    let active = true;
    setLoadingTranscript(true);

    fetchProjectTranscript(selectedLesson.id)
      .then((payload) => {
        if (!active) return;
        setTranscriptPages(Array.isArray(payload?.pages) ? payload.pages : []);
      })
      .catch(() => {
        if (!active) return;
        setTranscriptPages([]);
      })
      .finally(() => {
        if (active) {
          setLoadingTranscript(false);
        }
      });

    return () => {
      active = false;
    };
  }, [selectedLesson?.id, user]);

  useEffect(() => {
    if (!selectedLesson?.id) {
      setLessonNotes('');
      setNotesSavedAt('');
      return;
    }

    const stored = window.localStorage.getItem(myLessonNotesKey(selectedLesson.id)) || '';
    setLessonNotes(stored);
    setNotesSavedAt(stored ? 'Loaded saved notes' : 'No notes saved yet');
  }, [selectedLesson?.id]);

  const saveNotes = () => {
    if (!selectedLesson?.id) return;
    window.localStorage.setItem(myLessonNotesKey(selectedLesson.id), lessonNotes);
    setNotesSavedAt(`Saved at ${new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`);
  };

  return (
    <div className="space-y-6">
      {!user && (
        <SurfaceCard elevated className="mx-auto max-w-2xl space-y-4 text-center">
          <p className="label-sm">My Lessons</p>
          <h1 className="headline-md text-[var(--text-primary)]">Sign In To Access Your Lesson Workspace</h1>
          <p className="body-md">My Lessons keeps your watch flow, lesson notes, and transcript context synchronized.</p>
          <div className="flex justify-center">
            <Button onClick={onLoginRequest}>
              <LogIn size={15} />
              <span>Sign In</span>
            </Button>
          </div>
        </SurfaceCard>
      )}

      {loading && (
        <SurfaceCard elevated>
          <p className="body-md">Syncing your lessons...</p>
        </SurfaceCard>
      )}

      {!loading && error && (
        <SurfaceCard elevated className="space-y-2">
          <p className="label-sm">Library Notice</p>
          <p className="text-sm text-[color:var(--feedback-danger-fg)]">{error}</p>
        </SurfaceCard>
      )}

      {!loading && lessons.length === 0 && (
        <SurfaceCard elevated className="text-center">
          <BookmarkCheck className="mx-auto text-[var(--text-secondary)]" size={20} />
          <p className="title-lg mt-2 text-[var(--text-primary)]">No lessons in your workspace yet</p>
          <p className="body-md mt-1">Open the dashboard and start learning to populate this page.</p>
          <Button className="mt-3" onClick={() => navigate('/')}>Go To Dashboard</Button>
        </SurfaceCard>
      )}

      {!loading && selectedLesson && (
        <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
          <div className="space-y-6">
            <SurfaceCard className="space-y-4 rounded-[1.85rem]">
              <div className="flex items-center justify-between px-1">
                <div className="flex items-center gap-2 text-xs uppercase tracking-[0.14em] text-[var(--text-secondary)]">
                  <span className="inline-block h-2 w-2 rounded-full bg-[image:var(--accent-gradient)]" />
                  Live Interactive Session
                </div>
                <button type="button" className="focus-ring inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold text-[var(--accent-primary)] hover:bg-[color:var(--hover-accent-soft)]">
                  <SplitSquareHorizontal size={14} />
                  Split View
                </button>
              </div>

              <div className="relative aspect-video overflow-hidden rounded-2xl bg-[var(--video-stage-bg)]" style={lessonBackground(selectedLesson)}>
                <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(0,0,0,0.04)_0%,rgba(0,0,0,0.56)_100%)]" />
                <button
                  type="button"
                  onClick={() => navigate(`/watch?lesson=${selectedLesson.id}`)}
                  className="focus-ring absolute inset-0 flex items-center justify-center"
                  aria-label="Open lesson"
                >
                  <span className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-black/45 text-white">
                    <CirclePlay size={28} />
                  </span>
                </button>

                <div className="absolute bottom-5 left-5 right-5">
                  <div className="h-1 w-full rounded-full bg-white/25">
                    <div className="h-full rounded-full bg-[image:var(--accent-gradient)]" style={{ width: `${Math.max(10, selectedLesson.progress || 0)}%` }} />
                  </div>
                </div>
              </div>
            </SurfaceCard>

            <SurfaceCard className="space-y-6">
              <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                <div className="space-y-3">
                  <h1 className="font-['Manrope'] text-[1.55rem] font-bold tracking-[-0.03em] text-[var(--text-primary)] sm:text-[1.95rem]">
                    {selectedLesson.title}
                  </h1>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--text-secondary)]">
                    <span className="rounded-full token-surface px-3 py-1">{selectedLesson.teacherName}</span>
                    <span className="rounded-full token-surface px-3 py-1">{selectedLesson.categoryName}</span>
                    <span className="rounded-full token-surface px-3 py-1">{formatDuration(selectedLesson.durationMinutes)}</span>
                  </div>
                </div>

                <div className="inline-flex items-center gap-2 rounded-full border border-[color:rgba(208,188,255,0.3)] bg-[color:rgba(208,188,255,0.1)] px-3 py-1.5 text-[0.68rem] font-semibold uppercase tracking-[0.14em] text-[var(--text-secondary)]">
                  <span className="material-symbols-outlined text-[1rem] text-[var(--accent-primary)]">auto_awesome</span>
                  AI Notes Ready
                </div>
              </div>

              <div className="space-y-4">
                <div className="rail-scroll flex items-center gap-2 overflow-x-auto pb-1">
                  {LESSON_TABS.map((tab) => (
                    <button
                      key={tab}
                      type="button"
                      onClick={() => setActiveTab(tab)}
                      className={`focus-ring whitespace-nowrap rounded-full px-3.5 py-2 text-sm font-medium capitalize transition ${
                        activeTab === tab
                          ? 'bg-[var(--surface-container-highest)] text-[var(--accent-primary)]'
                          : 'token-surface text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      {tab}
                    </button>
                  ))}
                </div>

                {activeTab === 'overview' && (
                  <div className="rounded-2xl border border-[color:rgba(73,68,84,0.1)] bg-[var(--surface-container-high)] p-5">
                    <h2 className="title-lg inline-flex items-center gap-2 text-[var(--text-primary)]">
                      <span className="material-symbols-outlined text-[1.05rem] text-[var(--accent-primary)]">psychology</span>
                      <span>AI Summary & Key Takeaways</span>
                    </h2>
                    <p className="body-md mt-3">
                      {selectedLesson.description || 'This lesson is ready for transcript and notes-based review. Open the player for chapter-level progression and synchronized captions.'}
                    </p>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2">
                      <article className="rounded-xl token-surface p-3 text-xs text-[var(--text-secondary)]">
                        <span className="mb-2 inline-flex h-7 w-7 items-center justify-center rounded-lg bg-[color:rgba(208,188,255,0.1)] text-[var(--accent-primary)]">
                          <span className="material-symbols-outlined text-[0.95rem]">lightbulb</span>
                        </span>
                        <p>Understand key concepts and the sequence of ideas before deep practice.</p>
                      </article>
                      <article className="rounded-xl token-surface p-3 text-xs text-[var(--text-secondary)]">
                        <span className="mb-2 inline-flex h-7 w-7 items-center justify-center rounded-lg bg-[color:rgba(173,198,255,0.1)] text-[var(--accent-secondary)]">
                          <span className="material-symbols-outlined text-[0.95rem]">tips_and_updates</span>
                        </span>
                        <p>Use notes and transcript checkpoints to retain the essential takeaways.</p>
                      </article>
                    </div>
                  </div>
                )}

                {activeTab === 'transcript' && (
                  <div className="space-y-2">
                    {loadingTranscript ? (
                      <p className="text-sm text-[var(--text-secondary)]">Loading transcript pages...</p>
                    ) : transcriptPages.length === 0 ? (
                      <p className="text-sm text-[var(--text-secondary)]">Transcript will appear here once it is generated for this lesson.</p>
                    ) : (
                      transcriptPages.slice(0, 6).map((page, index) => (
                        <article key={page.id || `${selectedLesson.id}-page-${index}`} className="rounded-xl token-surface p-3">
                          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">Slide {index + 1}</p>
                          <p className="mt-1 text-sm text-[var(--text-primary)]">{page.narration_text || page.original_text || 'No transcript text available.'}</p>
                        </article>
                      ))
                    )}
                  </div>
                )}

                {activeTab === 'slides' && (
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                    {[selectedLesson, ...upNextLessons].slice(0, 6).map((lesson, index) => (
                      <button
                        key={`slide-${lesson.id}-${index}`}
                        type="button"
                        onClick={() => setSelectedLessonId(lesson.id)}
                        className="focus-ring text-left"
                      >
                        <div className="aspect-video overflow-hidden rounded-xl border border-[color:rgba(73,68,84,0.2)]" style={lessonBackground(lesson)} />
                        <p className="mt-1 text-[0.72rem] text-[var(--text-secondary)]">Slide {index + 1}</p>
                      </button>
                    ))}
                  </div>
                )}

                {activeTab === 'notes' && (
                  <div className="space-y-3">
                    <textarea
                      value={lessonNotes}
                      onChange={(event) => setLessonNotes(event.target.value)}
                      placeholder="Write your lesson notes, takeaways, and revision checkpoints..."
                      className="focus-ring min-h-[180px] w-full resize-y rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 text-sm text-[var(--text-primary)]"
                    />
                    <div className="flex items-center justify-between">
                      <p className="text-xs text-[var(--text-secondary)]">{notesSavedAt || 'Local notes are not saved yet'}</p>
                      <Button size="sm" variant="secondary" onClick={saveNotes}>Save Notes</Button>
                    </div>
                  </div>
                )}
              </div>
            </SurfaceCard>
          </div>

          <aside className="space-y-4">
            <SurfaceCard className="space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">Up Next</h2>
                <span className="text-xs font-semibold text-[var(--accent-primary)]">Auto-play ON</span>
              </div>

              <div className="space-y-3">
                {upNextLessons.map((lesson) => (
                  <button
                    key={`next-${lesson.id}`}
                    type="button"
                    onClick={() => setSelectedLessonId(lesson.id)}
                    className="focus-ring flex w-full items-start gap-3 rounded-xl p-2 text-left transition hover:bg-[color:var(--surface-muted)]"
                  >
                    <div className="h-20 w-32 shrink-0 overflow-hidden rounded-lg" style={lessonBackground(lesson)} />
                    <div>
                      <p className="line-clamp-2 text-sm font-semibold text-[var(--text-primary)]">{lesson.title}</p>
                      <p className="mt-1 text-[0.68rem] uppercase tracking-[0.1em] text-[var(--text-secondary)]">{lesson.categoryName}</p>
                    </div>
                  </button>
                ))}
              </div>
            </SurfaceCard>

            <SurfaceCard className="relative overflow-hidden border border-[color:rgba(208,188,255,0.2)] bg-[color:rgba(208,188,255,0.1)]">
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-2xl bg-[color:rgba(208,188,255,0.1)] text-[var(--accent-primary)]">
                <span className="material-symbols-outlined">workspace_premium</span>
              </span>
              <h3 className="title-lg text-[var(--text-primary)]">Master Course Certificate</h3>
              <p className="mt-2 text-xs text-[var(--text-secondary)]">Complete more lessons in this track to unlock your visual AI specialist badge.</p>
              <div className="mt-4 h-1 rounded-full bg-[color:rgba(208,188,255,0.1)]">
                <div className="h-full w-3/4 rounded-full bg-[image:var(--accent-gradient)]" />
              </div>
              <button type="button" disabled className="mt-4 inline-flex h-10 w-full items-center justify-center rounded-full bg-[var(--text-primary)] text-xs font-semibold text-[var(--accent-inverse)] opacity-75">
                View Learning Path
              </button>
            </SurfaceCard>
          </aside>
        </section>
      )}
    </div>
  );
}
