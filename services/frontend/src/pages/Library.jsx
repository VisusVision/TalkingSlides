import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, BookOpenText, Heart, History, ListPlus, PlayCircle, Users } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { fetchLikedLessons, fetchUserHistory, getFollowingPublishers, getSavedPlaylists } from '../api';
import LearningLessonCard, { normalizeLearningRows } from '../components/library/LearningLessonCard';
import { usePageLoading } from '../components/ui/PageLoading';
import Skeleton from '../components/ui/Skeleton';
import SurfaceCard from '../components/ui/SurfaceCard';
import EmptyState from '../components/ui/EmptyState';
import { PageContainer, PageHeader, PageToolbar } from '../components/ui/PageLayout';
import ProductGuidance from '../components/guidance/ProductGuidance';
import { useProductGuidanceCopy } from '../components/guidance/productGuidanceCopy';
import { normalizeLesson } from '../lib/content';

const LIBRARY_TABS = [
  { key: 'history', label: 'History', icon: History },
  { key: 'liked', label: 'Liked Lessons', icon: Heart },
  { key: 'following', label: 'Following', icon: Users },
  { key: 'playlists', label: 'Playlists', icon: ListPlus },
];

function filterItems(items, query) {
  const needle = String(query || '').trim().toLowerCase();
  if (!needle) return items;
  return items.filter((item) => {
    const lesson = item.lesson || {};
    return [lesson.title, lesson.description, lesson.teacherName, lesson.categoryName]
      .some((value) => String(value || '').toLowerCase().includes(needle));
  });
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

function filterPublishers(items, query) {
  const needle = String(query || '').trim().toLowerCase();
  if (!needle) return items;
  return items.filter((item) => (
    [item.display_name, item.username, item.bio]
      .some((value) => String(value || '').toLowerCase().includes(needle))
  ));
}

function filterPlaylists(items, query) {
  const needle = String(query || '').trim().toLowerCase();
  if (!needle) return items;
  return items.filter((item) => (
    [
      item.title,
      item.description,
      item.publisherName,
      item.publisherUsername,
      ...item.lessons.map((lesson) => lesson.title),
    ].some((value) => String(value || '').toLowerCase().includes(needle))
  ));
}

function EmptyPanel({ icon: Icon, title, body }) {
  return (
    <EmptyState
      icon={Icon}
      title={title}
      description={body}
      className="rounded-lg bg-[color:var(--surface-muted)]/25"
    />
  );
}

function LibraryPanelSkeleton({ tab }) {
  const isFollowing = tab === 'following';
  const isPlaylists = tab === 'playlists';

  return (
    <div role="status" aria-live="polite" aria-label="Loading library" className="grid gap-3">
      <span className="sr-only">Loading your library...</span>
      {Array.from({ length: isFollowing ? 3 : 4 }, (_, index) => (
        <Skeleton.Card key={`library-skeleton-${tab}-${index}`} className="token-surface-elevated p-3">
          <div className="grid gap-4 md:grid-cols-[12rem_minmax(0,1fr)]">
            {isFollowing ? (
              <Skeleton.Avatar size="lg" />
            ) : (
              <Skeleton className="aspect-video w-full" rounded="lg" />
            )}
            <div className="min-w-0 space-y-3">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0 flex-1 space-y-2">
                  <Skeleton className="h-4 w-3/4" rounded="full" />
                  <Skeleton className="h-3 w-1/2" rounded="full" />
                </div>
                {isPlaylists && <Skeleton className="h-6 w-16" rounded="full" />}
              </div>
              <Skeleton.Text lines={2} />
              <div className="flex flex-wrap gap-2">
                <Skeleton className="h-6 w-24" rounded="full" />
                <Skeleton className="h-6 w-20" rounded="full" />
                {!isFollowing && <Skeleton className="h-6 w-28" rounded="full" />}
              </div>
            </div>
          </div>
        </Skeleton.Card>
      ))}
    </div>
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
    <SurfaceCard
      as={Link}
      to={`/playlist/${playlist.id}`}
      variant="elevated"
      padding="sm"
      interactive
      className="group grid gap-3 sm:grid-cols-[12rem_minmax(0,1fr)]"
    >
      <div className="relative aspect-video overflow-hidden rounded-lg bg-[var(--surface-container-high)]" style={playlistCoverStyle(playlist)}>
        <span className="absolute left-3 top-3 inline-flex items-center gap-1 rounded-full bg-[color:var(--media-pill-bg)] px-2.5 py-1 text-xs font-semibold text-[color:var(--media-text-on-image)] backdrop-blur-sm">
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
    </SurfaceCard>
  );
}

function PublisherCard({ publisher }) {
  const initial = String(publisher?.display_name || publisher?.username || 'P').trim().charAt(0).toUpperCase();
  const latestLessons = publisher?.latestLessons || [];
  return (
    <SurfaceCard
      as={Link}
      to={`/channel/${publisher.id}`}
      variant="elevated"
      padding="sm"
      interactive
      className="block"
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
    </SurfaceCard>
  );
}

export default function Library({ searchQuery }) {
  const navigate = useNavigate();
  const guidanceCopy = useProductGuidanceCopy();
  const [activeTab, setActiveTab] = useState('history');
  const [historyRows, setHistoryRows] = useState([]);
  const [likedRows, setLikedRows] = useState([]);
  const [followingRows, setFollowingRows] = useState([]);
  const [savedPlaylistRows, setSavedPlaylistRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  usePageLoading(loading, 'library');

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

  const visibleHistory = useMemo(() => filterItems(historyRows, searchQuery), [historyRows, searchQuery]);
  const visibleLiked = useMemo(() => filterItems(likedRows, searchQuery), [likedRows, searchQuery]);
  const visibleFollowing = useMemo(() => filterPublishers(followingRows, searchQuery), [followingRows, searchQuery]);
  const visibleSavedPlaylists = useMemo(
    () => filterPlaylists(savedPlaylistRows, searchQuery),
    [savedPlaylistRows, searchQuery],
  );
  const latestIncompleteHistory = useMemo(() => historyRows.reduce((latest, item) => {
    const progress = Number(item?.progressPct || 0);
    if (!item?.lesson?.id || progress <= 0 || progress >= 100) return latest;
    if (!latest) return item;
    const itemTime = Date.parse(item.timestamp || '') || 0;
    const latestTime = Date.parse(latest.timestamp || '') || 0;
    return itemTime > latestTime ? item : latest;
  }, null), [historyRows]);

  const libraryGuidance = useMemo(() => {
    if (latestIncompleteHistory) {
      return {
        status: 'ready',
        title: guidanceCopy.libraryContinueTitle,
        description: guidanceCopy.libraryContinueDescription,
        primaryAction: {
          label: guidanceCopy.libraryContinueAction,
          onClick: () => navigate('/watch?lesson=' + latestIncompleteHistory.lesson.id + '&resume=1'),
        },
        items: [{
          key: 'continue-history',
          status: 'ready',
          title: guidanceCopy.libraryContinueItemTitle,
          description: guidanceCopy.libraryContinueItemDescription,
        }],
      };
    }

    if (likedRows.length > 0) {
      return {
        status: 'ready',
        title: guidanceCopy.libraryLikedTitle,
        description: guidanceCopy.libraryLikedDescription,
        primaryAction: { label: guidanceCopy.libraryLikedAction, onClick: () => setActiveTab('liked') },
        items: [],
      };
    }

    if (followingRows.length > 0) {
      return {
        status: 'neutral',
        title: guidanceCopy.libraryFollowingTitle,
        description: guidanceCopy.libraryFollowingDescription,
        primaryAction: { label: guidanceCopy.libraryFollowingAction, onClick: () => setActiveTab('following') },
        items: [],
      };
    }

    if (savedPlaylistRows.length > 0) {
      return {
        status: 'ready',
        title: guidanceCopy.libraryPlaylistsTitle,
        description: guidanceCopy.libraryPlaylistsDescription,
        primaryAction: { label: guidanceCopy.libraryPlaylistsAction, onClick: () => setActiveTab('playlists') },
        items: [],
      };
    }

    return {
      status: 'neutral',
      title: guidanceCopy.libraryEmptyTitle,
      description: guidanceCopy.libraryEmptyDescription,
      primaryAction: { label: guidanceCopy.libraryEmptyAction, onClick: () => navigate('/browse') },
      items: [],
    };
  }, [followingRows.length, guidanceCopy, latestIncompleteHistory, likedRows.length, navigate, savedPlaylistRows.length]);

  const renderActivePanel = () => {
    const hasSearchQuery = String(searchQuery || '').trim().length > 0;

    if (activeTab === 'history') {
      if (!visibleHistory.length) {
        return (
          <EmptyPanel
            icon={BookOpenText}
            title={hasSearchQuery && historyRows.length ? 'No watched lessons match your search.' : 'No watched lessons yet.'}
            body={hasSearchQuery && historyRows.length ? 'Try another search term.' : 'Lessons you start watching will appear here.'}
          />
        );
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
        return (
          <EmptyPanel
            icon={Heart}
            title={hasSearchQuery && likedRows.length ? 'No liked lessons match your search.' : 'No liked lessons yet.'}
            body={hasSearchQuery && likedRows.length ? 'Try another search term.' : 'Liked lessons will appear here after you save them from Watch.'}
          />
        );
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
        return (
          <EmptyPanel
            icon={Users}
            title={hasSearchQuery && followingRows.length ? 'No followed publishers match your search.' : 'You are not following any publishers yet.'}
            body={hasSearchQuery && followingRows.length ? 'Try another search term.' : 'Publishers you follow will appear here.'}
          />
        );
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
      return (
        <EmptyPanel
          icon={ListPlus}
          title={hasSearchQuery && savedPlaylistRows.length ? 'No saved playlists match your search.' : 'No saved playlists yet.'}
          body={hasSearchQuery && savedPlaylistRows.length ? 'Try another search term.' : 'Saved playlists will appear here.'}
        />
      );
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
    <PageContainer width="standard" motion="enter" aria-busy={loading}>
      <PageHeader
        eyebrow="Library"
        title="Your Learning Hub"
        titleSize="headline"
        description="Continue watched lessons, revisit liked lessons, and keep up with publishers you follow."
        motion="fade"
      />

      <PageToolbar motion="fade" aria-label="Library sections">
        <div className="rail-scroll flex min-w-0 flex-1 gap-2 overflow-x-auto pb-1">
          {LIBRARY_TABS.map((tab) => {
            const Icon = tab.icon;
            const active = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                className={`focus-ring motion-interactive inline-flex shrink-0 items-center gap-2 rounded-full px-3.5 py-2 text-sm font-semibold active:scale-[0.98] ${
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
      </PageToolbar>


      {!loading && !error && (
        <ProductGuidance
          status={libraryGuidance.status}
          eyebrow={guidanceCopy.libraryEyebrow}
          title={libraryGuidance.title}
          description={libraryGuidance.description}
          primaryAction={libraryGuidance.primaryAction}
          items={libraryGuidance.items}
        />
      )}
      <SurfaceCard className="space-y-5">
        {loading ? (
          <LibraryPanelSkeleton tab={activeTab} />
        ) : error ? (
          <p className="text-sm font-medium text-[color:var(--feedback-danger-fg)]">{error}</p>
        ) : (
          renderActivePanel()
        )}
      </SurfaceCard>
    </PageContainer>
  );
}
