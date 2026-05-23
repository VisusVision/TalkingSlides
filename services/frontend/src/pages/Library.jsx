import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, BookOpenText, Heart, History, ListPlus, PlayCircle, Users } from 'lucide-react';
import { Link } from 'react-router-dom';
import { fetchLikedLessons, fetchUserHistory, getFollowingPublishers, getSavedPlaylists } from '../api';
import { useSectionState } from '../app/navigationState';
import LearningLessonCard, { normalizeLearningRows } from '../components/library/LearningLessonCard';
import SurfaceCard from '../components/ui/SurfaceCard';
import { normalizeLesson } from '../lib/content';
import { fuzzySearch } from '../utils/fuzzySearch';

const LIBRARY_TABS = [
  { key: 'history', label: 'History', icon: History },
  { key: 'liked', label: 'Liked Lessons', icon: Heart },
  { key: 'following', label: 'Following', icon: Users },
  { key: 'playlists', label: 'Playlists', icon: ListPlus },
];

function itemSearchText(item) {
  const lesson = item?.lesson || {};
  return [lesson.title, lesson.description, lesson.teacherName, lesson.categoryName]
    .filter(Boolean)
    .join(' ');
}

function normalizeFollowingRows(payload = {}) {
  const rows = Array.isArray(payload) ? payload : payload?.results || [];
  return rows.map((row) => ({
    ...row,
    latestLessons: (row?.latest_lessons || []).map((lesson) => normalizeLesson(lesson)).filter((lesson) => lesson.id),
  }));
}

function normalizeSavedPlaylistRows(payload = {}) {
  const rows = Array.isArray(payload) ? payload : payload?.results || [];
  return rows.map((row) => {
    const items = Array.isArray(row?.items) ? row.items : [];
    const lessons = items
      .map((item) => normalizeLesson(item?.project || item))
      .filter((lesson) => lesson.id);
    return {
      id: row.id,
      title: row.title || `Playlist #${row.id || ''}`,
      description: row.description || '',
      publisherId: row.publisher_id || lessons[0]?.teacherId || null,
      publisherName: row.publisher_name || lessons[0]?.teacherName || 'Publisher',
      publisherUsername: row.publisher_username || '',
      itemCount: Number(row.item_count ?? lessons.length ?? 0),
      coverUrl: row.cover_url || lessons[0]?.imageUrl || '',
      saveCount: Number(row.save_count || 0),
      lessons,
    };
  }).filter((playlist) => playlist.id);
}

function publisherSearchText(item) {
  return [item?.display_name, item?.username, item?.bio]
    .filter(Boolean)
    .join(' ');
}

function playlistSearchText(item) {
  return [
    item?.title,
    item?.description,
    item?.publisherName,
    item?.publisherUsername,
    ...(item?.lessons || []).map((lesson) => lesson.title),
  ].filter(Boolean).join(' ');
}

function EmptyPanel({ icon: Icon, title, body }) {
  return (
    <SurfaceCard elevated className="text-center">
      <Icon className="mx-auto text-[var(--text-secondary)]" size={21} />
      <p className="title-lg mt-2 text-[var(--text-primary)]">{title}</p>
      {body ? <p className="body-md mt-1">{body}</p> : null}
    </SurfaceCard>
  );
}

function playlistCoverStyle(playlist) {
  if (!playlist?.coverUrl) {
    return { backgroundImage: 'var(--hero-fallback)' };
  }
  return {
    backgroundImage: `var(--card-image-overlay), url(${playlist.coverUrl})`,
    backgroundPosition: 'center',
    backgroundSize: 'cover',
  };
}

function compactCount(value, noun) {
  const count = Math.max(0, Number(value || 0));
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

function SavedPlaylistCard({ playlist }) {
  return (
    <Link
      to={`/playlist/${playlist.id}`}
      className="focus-ring group grid gap-3 rounded-xl token-surface-elevated p-3 transition hover:-translate-y-0.5 sm:grid-cols-[12rem_minmax(0,1fr)]"
    >
      <div className="relative aspect-video overflow-hidden rounded-lg bg-[var(--surface-container-high)]" style={playlistCoverStyle(playlist)}>
        <span className="absolute left-3 top-3 inline-flex items-center gap-1 rounded-full bg-black/45 px-2.5 py-1 text-xs font-semibold text-white">
          <ListPlus size={13} />
          {compactCount(playlist.itemCount, 'video')}
        </span>
      </div>
      <div className="min-w-0 space-y-2">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <p className="line-clamp-2 text-sm font-semibold text-[var(--text-primary)]">{playlist.title}</p>
            <p className="mt-1 text-xs text-[var(--text-secondary)]">
              {playlist.publisherName}{playlist.publisherUsername ? ` @${playlist.publisherUsername}` : ''}
            </p>
          </div>
          <span className="w-fit shrink-0 rounded-full bg-[var(--surface-container-highest)] px-2.5 py-1 text-xs font-semibold text-[var(--accent-primary)]">
            {compactCount(playlist.saveCount, 'save')}
          </span>
        </div>
        <p className="line-clamp-2 text-xs text-[var(--text-secondary)]">{playlist.description || 'No description yet.'}</p>
        {playlist.lessons.length ? (
          <div className="flex flex-wrap gap-1.5">
            {playlist.lessons.slice(0, 3).map((lesson) => (
              <span key={lesson.id} className="inline-flex min-w-0 items-center gap-1.5 rounded-full bg-[var(--surface-container-high)] px-2.5 py-1 text-xs text-[var(--text-secondary)]">
                <PlayCircle size={12} className="shrink-0 text-[var(--accent-primary)]" />
                <span className="line-clamp-1">{lesson.title}</span>
              </span>
            ))}
          </div>
        ) : (
          <p className="text-xs text-[var(--text-secondary)]">No public videos in this playlist yet.</p>
        )}
      </div>
    </Link>
  );
}

function PublisherCard({ publisher }) {
  const initial = String(publisher?.display_name || publisher?.username || 'P').trim().charAt(0).toUpperCase();
  const latestLessons = publisher?.latestLessons || [];
  return (
    <Link
      to={`/channel/${publisher.id}`}
      className="focus-ring block rounded-xl token-surface-elevated p-4 transition hover:-translate-y-0.5"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 gap-3">
          {publisher.avatar_url ? (
            <img
              src={publisher.avatar_url}
              alt=""
              className="h-12 w-12 shrink-0 rounded-full border border-[var(--border-subtle)] object-cover"
            />
          ) : (
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full border border-[var(--border-subtle)] bg-[var(--surface-container-highest)] text-sm font-bold text-[var(--accent-primary)]">
              {initial}
            </span>
          )}
          <div className="min-w-0">
            <p className="text-sm font-semibold text-[var(--text-primary)]">{publisher.display_name || publisher.username}</p>
            <p className="mt-1 text-xs text-[var(--text-secondary)]">
              {compactCount(publisher.follower_count, 'follower')} - {compactCount(publisher.lesson_count, 'lesson')}
            </p>
            {publisher.bio ? <p className="mt-2 line-clamp-2 text-sm text-[var(--text-secondary)]">{publisher.bio}</p> : null}
          </div>
        </div>
        <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-[var(--surface-container-highest)] px-3 py-1.5 text-xs font-semibold text-[var(--accent-primary)]">
          View channel
          <ArrowRight size={13} />
        </span>
      </div>
      {latestLessons.length > 0 && (
        <div className="mt-4 border-t border-[var(--border-subtle)] pt-3">
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-[var(--outline)]">Latest videos</p>
          <div className="flex flex-wrap gap-2">
          {latestLessons.slice(0, 3).map((lesson) => (
            <span key={lesson.id} className="inline-flex min-w-0 items-center gap-1.5 rounded-full bg-[var(--surface-container-high)] px-2.5 py-1 text-xs text-[var(--text-secondary)]">
              <PlayCircle size={12} className="shrink-0 text-[var(--accent-primary)]" />
              <span className="line-clamp-1">{lesson.title}</span>
            </span>
          ))}
          </div>
        </div>
      )}
    </Link>
  );
}

export default function Library() {
  const [libraryState, setLibraryState] = useSectionState('library', {
    search: '',
    activeTab: 'history',
  });
  const searchQuery = libraryState.search || '';
  const activeTab = libraryState.activeTab || 'history';
  const [historyRows, setHistoryRows] = useState([]);
  const [likedRows, setLikedRows] = useState([]);
  const [followingRows, setFollowingRows] = useState([]);
  const [savedPlaylistRows, setSavedPlaylistRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;

    async function loadLibrary() {
      setLoading(true);
      setError('');
      try {
        const [historyPayload, likedPayload, followingPayload, savedPlaylistsPayload] = await Promise.all([
          fetchUserHistory(),
          fetchLikedLessons(),
          getFollowingPublishers(),
          getSavedPlaylists(),
        ]);
        if (!active) return;
        setHistoryRows(normalizeLearningRows(historyPayload, 'history'));
        setLikedRows(normalizeLearningRows(likedPayload, 'liked'));
        setFollowingRows(normalizeFollowingRows(followingPayload));
        setSavedPlaylistRows(normalizeSavedPlaylistRows(savedPlaylistsPayload));
      } catch (libraryError) {
        if (!active) return;
        setError(libraryError.message || 'Unable to load your library.');
        setHistoryRows([]);
        setLikedRows([]);
        setFollowingRows([]);
        setSavedPlaylistRows([]);
      } finally {
        if (active) setLoading(false);
      }
    }

    loadLibrary();
    return () => {
      active = false;
    };
  }, []);

  const hasSearch = Boolean(String(searchQuery || '').trim());
  const historySearch = useMemo(() => fuzzySearch(historyRows, searchQuery, itemSearchText), [historyRows, searchQuery]);
  const likedSearch = useMemo(() => fuzzySearch(likedRows, searchQuery, itemSearchText), [likedRows, searchQuery]);
  const followingSearch = useMemo(() => fuzzySearch(followingRows, searchQuery, publisherSearchText), [followingRows, searchQuery]);
  const savedPlaylistsSearch = useMemo(
    () => fuzzySearch(savedPlaylistRows, searchQuery, playlistSearchText),
    [savedPlaylistRows, searchQuery],
  );
  const visibleHistory = hasSearch ? historySearch.items : historyRows;
  const visibleLiked = hasSearch ? likedSearch.items : likedRows;
  const visibleFollowing = hasSearch ? followingSearch.items : followingRows;
  const visibleSavedPlaylists = hasSearch ? savedPlaylistsSearch.items : savedPlaylistRows;

  const setActiveTab = (tab) => setLibraryState({ activeTab: tab });
  const activeSearchResult = {
    history: historySearch,
    liked: likedSearch,
    following: followingSearch,
    playlists: savedPlaylistsSearch,
  }[activeTab];

  const emptyPanel = (Icon, title, body = '') => (
    <EmptyPanel
      icon={Icon}
      title={hasSearch ? 'No results found.' : title}
      body={hasSearch ? 'Try another keyword or clear search.' : body}
    />
  );

  const renderActivePanel = () => {
    if (activeTab === 'history') {
      if (!visibleHistory.length) {
        return emptyPanel(BookOpenText, 'No watched lessons yet.', 'Lessons you start watching will appear here.');
      }
      return (
        <div className="grid gap-3">
          {visibleHistory.map((item) => (
            <LearningLessonCard key={item.id} item={item} />
          ))}
        </div>
      );
    }

    if (activeTab === 'liked') {
      if (!visibleLiked.length) {
        return emptyPanel(Heart, 'No liked lessons yet.', 'Liked lessons will appear here after you save them from Watch.');
      }
      return (
        <div className="grid gap-3">
          {visibleLiked.map((item) => (
            <LearningLessonCard key={item.id} item={item} metaLabel="Liked lesson" />
          ))}
        </div>
      );
    }

    if (activeTab === 'following') {
      if (!visibleFollowing.length) {
        return emptyPanel(Users, 'You are not following any publishers yet.');
      }
      return (
        <div className="grid gap-3">
          {visibleFollowing.map((publisher) => (
            <PublisherCard key={publisher.id} publisher={publisher} />
          ))}
        </div>
      );
    }

    if (!visibleSavedPlaylists.length) {
      return emptyPanel(ListPlus, 'No saved playlists yet.');
    }
    return (
      <div className="grid gap-3">
        {visibleSavedPlaylists.map((playlist) => (
          <SavedPlaylistCard key={playlist.id} playlist={playlist} />
        ))}
      </div>
    );
  };

  return (
    <div className="space-y-6">
      <section className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="label-sm">Library</p>
          <h1 className="headline-md text-[var(--text-primary)]">Your Learning Hub</h1>
          <p className="body-md mt-2 max-w-2xl">Continue watched lessons, revisit liked lessons, and keep up with publishers you follow.</p>
        </div>
      </section>

      <SurfaceCard className="space-y-5">
        <div className="rail-scroll flex gap-2 overflow-x-auto pb-1">
          {LIBRARY_TABS.map((tab) => {
            const Icon = tab.icon;
            const active = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                className={`focus-ring inline-flex shrink-0 items-center gap-2 rounded-full px-3.5 py-2 text-sm font-semibold transition ${
                  active
                    ? 'bg-[var(--surface-container-highest)] text-[var(--accent-primary)]'
                    : 'token-surface text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                <Icon size={15} />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>

        {!loading && !error && hasSearch && activeSearchResult?.isFuzzyOnly && (
          <div className="rounded-2xl bg-[color:var(--surface-muted)] px-3 py-2 text-sm font-semibold text-[var(--text-primary)]">
            No exact matches. Showing close matches.
          </div>
        )}

        {loading ? (
          <p className="body-md">Loading your library...</p>
        ) : error ? (
          <p className="text-sm font-medium text-[color:var(--feedback-danger-fg)]">{error}</p>
        ) : (
          renderActivePanel()
        )}
      </SurfaceCard>
    </div>
  );
}
