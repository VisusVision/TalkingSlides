import { useEffect, useMemo, useState } from 'react';
import { CalendarDays, Check, Clock3, ListVideo, PlayCircle, UserPlus, Users } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { getPublisherLessons, getPublisherPlaylists, getPublisherProfile, toggleFollowPublisher } from '../api';
import Button from '../components/ui/Button';
import SurfaceCard from '../components/ui/SurfaceCard';
import { formatDuration, normalizeLesson } from '../lib/content';

const CHANNEL_TABS = [
  { key: 'home', label: 'Home' },
  { key: 'videos', label: 'Videos' },
  { key: 'playlists', label: 'Playlists' },
  { key: 'about', label: 'About' },
];

const SORT_OPTIONS = [
  { value: 'date:desc', label: 'Date newest' },
  { value: 'date:asc', label: 'Date oldest' },
  { value: 'name:asc', label: 'Name A-Z' },
  { value: 'name:desc', label: 'Name Z-A' },
];

function lessonBackground(lesson) {
  if (!lesson?.imageUrl) {
    return { backgroundImage: 'var(--hero-fallback)' };
  }
  return {
    backgroundImage: `var(--card-image-overlay), url(${lesson.imageUrl})`,
    backgroundPosition: 'center',
    backgroundSize: 'cover',
  };
}

function normalizeLessonRows(payload) {
  const rows = Array.isArray(payload) ? payload : payload?.results || [];
  return rows.map((row) => normalizeLesson(row)).filter((lesson) => lesson.id);
}

function normalizePlaylistRows(payload) {
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
      itemCount: Number(row.item_count ?? lessons.length ?? 0),
      coverUrl: row.cover_url || lessons[0]?.imageUrl || '',
      lessons,
    };
  }).filter((playlist) => playlist.id);
}

function compactCount(value, noun) {
  const count = Math.max(0, Number(value || 0));
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

function formatPublishedDate(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function sortLessonsByNewest(items) {
  return [...items].sort((a, b) => {
    const aTime = new Date(a?.createdAt || 0).getTime() || 0;
    const bTime = new Date(b?.createdAt || 0).getTime() || 0;
    if (aTime !== bTime) return bTime - aTime;
    return Number(b?.id || 0) - Number(a?.id || 0);
  });
}

function selectFeaturedLesson(items) {
  const rankedByViews = items.filter((lesson) => Number(lesson?.views || 0) > 0);
  if (rankedByViews.length) {
    return [...rankedByViews].sort((a, b) => (
      Number(b.views || 0) - Number(a.views || 0)
      || new Date(b?.createdAt || 0).getTime() - new Date(a?.createdAt || 0).getTime()
    ))[0];
  }
  return sortLessonsByNewest(items)[0] || null;
}

function ChannelAvatar({ imageUrl, name, size = 'large' }) {
  const initial = String(name || 'P').trim().charAt(0).toUpperCase() || 'P';
  const sizeClass = size === 'small' ? 'h-10 w-10 text-sm' : 'h-20 w-20 text-2xl sm:h-24 sm:w-24 sm:text-3xl';
  if (imageUrl) {
    return (
      <img
        src={imageUrl}
        alt=""
        className={`${sizeClass} shrink-0 rounded-full border border-[var(--border-subtle)] object-cover`}
      />
    );
  }
  return (
    <span className={`${sizeClass} flex shrink-0 items-center justify-center rounded-full border border-[var(--border-subtle)] bg-[var(--surface-container-highest)] font-bold text-[var(--accent-primary)]`}>
      {initial}
    </span>
  );
}

function EmptyState({ icon: Icon = PlayCircle, title, body }) {
  return (
    <SurfaceCard elevated className="text-center">
      <Icon className="mx-auto text-[var(--text-secondary)]" size={24} />
      <p className="title-lg mt-2 text-[var(--text-primary)]">{title}</p>
      {body ? <p className="body-md mt-1">{body}</p> : null}
    </SurfaceCard>
  );
}

function StatPill({ icon: Icon, label }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-[var(--surface-container-high)] px-3 py-1.5 text-sm font-semibold text-[var(--text-primary)]">
      <Icon size={15} className="text-[var(--accent-primary)]" />
      {label}
    </span>
  );
}

function ChannelLessonCard({ lesson, compact = false }) {
  const published = formatPublishedDate(lesson.createdAt);
  return (
    <Link
      to={`/watch?lesson=${lesson.id}`}
      className={`focus-ring group grid gap-3 rounded-xl token-surface-elevated p-3 transition hover:-translate-y-0.5 ${compact ? 'sm:grid-cols-[8.5rem_minmax(0,1fr)]' : 'sm:grid-cols-[12rem_minmax(0,1fr)]'}`}
    >
      <div className="relative aspect-video overflow-hidden rounded-lg bg-[var(--surface-container-high)]" style={lessonBackground(lesson)}>
        <span className="absolute inset-0 flex items-center justify-center opacity-0 transition group-hover:opacity-100">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-black/55 text-white">
            <PlayCircle size={22} />
          </span>
        </span>
      </div>
      <div className="min-w-0 space-y-2">
        <p className="line-clamp-2 text-sm font-semibold text-[var(--text-primary)]">{lesson.title}</p>
        {!compact && (
          <p className="line-clamp-2 text-xs text-[var(--text-secondary)]">{lesson.description || 'No description yet.'}</p>
        )}
        <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--text-secondary)]">
          <span className="rounded-full bg-[var(--surface-container-high)] px-2.5 py-1">{lesson.categoryName || 'General'}</span>
          <span className="rounded-full bg-[var(--surface-container-high)] px-2.5 py-1">{formatDuration(lesson.durationMinutes || 8)}</span>
          {published ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-[var(--surface-container-high)] px-2.5 py-1">
              <Clock3 size={12} />
              {published}
            </span>
          ) : null}
        </div>
      </div>
    </Link>
  );
}

function PlaylistCard({ playlist }) {
  const coverStyle = playlist.coverUrl
    ? {
        backgroundImage: `var(--card-image-overlay), url(${playlist.coverUrl})`,
        backgroundPosition: 'center',
        backgroundSize: 'cover',
      }
    : { backgroundImage: 'var(--hero-fallback)' };
  return (
    <Link
      to={`/playlist/${playlist.id}`}
      className="focus-ring group grid gap-3 rounded-xl token-surface-elevated p-3 transition hover:-translate-y-0.5 sm:grid-cols-[12rem_minmax(0,1fr)]"
    >
      <div className="relative aspect-video overflow-hidden rounded-lg bg-[var(--surface-container-high)]" style={coverStyle}>
        <span className="absolute left-3 top-3 inline-flex items-center gap-1 rounded-full bg-black/45 px-2.5 py-1 text-xs font-semibold text-white">
          <ListVideo size={13} />
          {compactCount(playlist.itemCount, 'video')}
        </span>
      </div>
      <div className="min-w-0 space-y-2">
        <p className="line-clamp-2 text-sm font-semibold text-[var(--text-primary)]">{playlist.title}</p>
        <p className="line-clamp-2 text-xs text-[var(--text-secondary)]">{playlist.description || 'No description yet.'}</p>
        {playlist.lessons.length ? (
          <div className="flex flex-wrap gap-1.5">
            {playlist.lessons.slice(0, 3).map((lesson) => (
              <span key={lesson.id} className="line-clamp-1 rounded-full bg-[var(--surface-container-high)] px-2.5 py-1 text-xs text-[var(--text-secondary)]">
                {lesson.title}
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

export default function Channel({ user, searchQuery, onLoginRequest }) {
  const { userId } = useParams();
  const [activeTab, setActiveTab] = useState('home');
  const [sortValue, setSortValue] = useState('date:desc');
  const [profile, setProfile] = useState(null);
  const [lessons, setLessons] = useState([]);
  const [playlists, setPlaylists] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [followBusy, setFollowBusy] = useState(false);
  const [followError, setFollowError] = useState('');

  const [sort, order] = sortValue.split(':');
  const isOwnChannel = Boolean(user?.id && Number(user.id) === Number(userId));
  const displayName = profile?.display_name || profile?.username || 'Publisher';
  const username = profile?.username ? `@${profile.username}` : '';
  const filteredLessons = useMemo(() => {
    const needle = String(searchQuery || '').trim().toLowerCase();
    if (!needle) return lessons;
    return lessons.filter((lesson) => (
      [lesson.title, lesson.description, lesson.categoryName]
        .some((value) => String(value || '').toLowerCase().includes(needle))
    ));
  }, [lessons, searchQuery]);
  const filteredPlaylists = useMemo(() => {
    const needle = String(searchQuery || '').trim().toLowerCase();
    if (!needle) return playlists;
    return playlists.filter((playlist) => (
      [playlist.title, playlist.description, ...playlist.lessons.map((lesson) => lesson.title)]
        .some((value) => String(value || '').toLowerCase().includes(needle))
    ));
  }, [playlists, searchQuery]);
  const newestLessons = useMemo(() => sortLessonsByNewest(filteredLessons), [filteredLessons]);
  const featuredLesson = useMemo(() => selectFeaturedLesson(filteredLessons), [filteredLessons]);
  const recentLessons = useMemo(
    () => newestLessons.filter((lesson) => lesson.id !== featuredLesson?.id).slice(0, 6),
    [featuredLesson?.id, newestLessons],
  );

  useEffect(() => {
    let active = true;

    async function loadChannel() {
      setLoading(true);
      setError('');
      setFollowError('');
      try {
        const [profilePayload, lessonsPayload, playlistsPayload] = await Promise.all([
          getPublisherProfile(userId),
          getPublisherLessons(userId, { sort, order }),
          getPublisherPlaylists(userId),
        ]);
        if (!active) return;
        setProfile(profilePayload);
        setLessons(normalizeLessonRows(lessonsPayload));
        setPlaylists(normalizePlaylistRows(playlistsPayload));
      } catch (channelError) {
        if (!active) return;
        setError(channelError.message || 'Unable to load this channel.');
        setProfile(null);
        setLessons([]);
        setPlaylists([]);
      } finally {
        if (active) setLoading(false);
      }
    }

    loadChannel();
    return () => {
      active = false;
    };
  }, [order, sort, userId, user?.id]);

  const handleToggleFollow = async () => {
    if (!profile?.id || followBusy || isOwnChannel) return;
    if (!user) {
      setFollowError('Sign in to follow this publisher.');
      if (typeof onLoginRequest === 'function') onLoginRequest(`/channel/${profile.id}`);
      return;
    }
    setFollowBusy(true);
    setFollowError('');
    try {
      const payload = await toggleFollowPublisher(profile.id);
      setProfile((current) => current ? {
        ...current,
        is_following: Boolean(payload?.is_following),
        follower_count: Number(payload?.follower_count ?? current.follower_count ?? 0),
      } : current);
    } catch (followUpdateError) {
      setFollowError(followUpdateError.message || 'Could not update follow.');
    } finally {
      setFollowBusy(false);
    }
  };

  const renderVideos = (items) => {
    if (!items.length) {
      return (
        <EmptyState
          icon={PlayCircle}
          title={searchQuery ? 'No videos match your search.' : 'No public videos yet.'}
          body={searchQuery ? 'Try another search term for this channel.' : 'Published lessons will appear here.'}
        />
      );
    }
    return (
      <div className="grid gap-3 xl:grid-cols-2">
        {items.map((lesson) => (
          <ChannelLessonCard key={lesson.id} lesson={lesson} />
        ))}
      </div>
    );
  };

  if (loading) {
    return (
      <SurfaceCard elevated>
        <p className="body-md">Loading channel...</p>
      </SurfaceCard>
    );
  }

  if (error) {
    return (
      <SurfaceCard elevated>
        <p className="text-sm font-semibold text-[color:var(--feedback-danger-fg)]">{error}</p>
      </SurfaceCard>
    );
  }

  return (
    <div className="space-y-5">
      <SurfaceCard className="overflow-hidden p-0">
        <div className="bg-[var(--surface-container-high)] px-5 py-5 sm:px-6">
          <div className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
            <div className="flex min-w-0 flex-col gap-4 sm:flex-row sm:items-end">
              <ChannelAvatar imageUrl={profile?.avatar_url} name={displayName} />
              <div className="min-w-0 space-y-3">
                <div>
                  <p className="label-sm">Channel</p>
                  <h1 className="headline-md break-words text-[var(--text-primary)]">{displayName}</h1>
                  {username && username !== `@${displayName}` ? (
                    <p className="mt-1 text-sm font-medium text-[var(--text-secondary)]">{username}</p>
                  ) : null}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <StatPill icon={Users} label={compactCount(profile?.follower_count, 'follower')} />
                  <StatPill icon={PlayCircle} label={compactCount(profile?.lesson_count, 'lesson')} />
                </div>
              </div>
            </div>
            {!isOwnChannel && (
              <Button
                size="sm"
                variant={profile?.is_following ? 'primary' : 'secondary'}
                onClick={handleToggleFollow}
                disabled={followBusy}
                className="w-full shrink-0 sm:w-auto"
              >
                {profile?.is_following ? <Check size={14} /> : <UserPlus size={14} />}
                <span>{followBusy ? 'Saving...' : profile?.is_following ? 'Following' : 'Follow'}</span>
              </Button>
            )}
          </div>
        </div>
        <div className="px-5 py-4 sm:px-6">
          <p className="max-w-4xl whitespace-pre-wrap text-sm leading-6 text-[var(--text-secondary)]">
            {profile?.bio || `${displayName} has not added a channel bio yet.`}
          </p>
          {followError ? <p className="mt-3 text-xs font-medium text-[color:var(--feedback-danger-fg)]">{followError}</p> : null}
        </div>
      </SurfaceCard>

      <SurfaceCard className="space-y-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="rail-scroll flex gap-2 overflow-x-auto pb-1">
            {CHANNEL_TABS.map((tab) => {
              const active = activeTab === tab.key;
              return (
                <button
                  key={tab.key}
                  type="button"
                  onClick={() => setActiveTab(tab.key)}
                  className={`focus-ring shrink-0 rounded-full px-3.5 py-2 text-sm font-semibold transition ${
                    active
                      ? 'bg-[var(--surface-container-highest)] text-[var(--accent-primary)]'
                      : 'token-surface text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                  aria-pressed={active}
                >
                  {tab.label}
                </button>
              );
            })}
          </div>
          {activeTab === 'videos' && (
            <label className="flex items-center gap-2 text-xs font-semibold text-[var(--text-secondary)]">
              <CalendarDays size={14} />
              <select
                value={sortValue}
                onChange={(event) => setSortValue(event.target.value)}
                className="focus-ring h-9 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-2 text-sm text-[var(--text-primary)]"
              >
                {SORT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
          )}
        </div>

        {activeTab === 'home' && (
          <div className="space-y-5">
            {featuredLesson ? (
              <>
                <Link
                  to={`/watch?lesson=${featuredLesson.id}`}
                  className="focus-ring group relative flex min-h-[18rem] overflow-hidden rounded-2xl bg-[var(--surface-container-high)] p-5 text-white sm:p-6 lg:min-h-[22rem]"
                  style={lessonBackground(featuredLesson)}
                >
                  <div className="absolute inset-0 bg-black/35 transition group-hover:bg-black/20" />
                  <div className="relative flex max-w-3xl flex-col justify-end gap-3 self-stretch">
                    <span className="inline-flex w-fit rounded-full bg-black/45 px-3 py-1 text-xs font-bold uppercase tracking-[0.12em]">Featured video</span>
                    <h2 className="text-2xl font-bold leading-tight sm:text-3xl">{featuredLesson.title}</h2>
                    {featuredLesson.description ? (
                      <p className="line-clamp-2 text-sm leading-6 text-white/85">{featuredLesson.description}</p>
                    ) : null}
                    <div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-white/90">
                      <span className="rounded-full bg-black/35 px-2.5 py-1">{featuredLesson.categoryName || 'General'}</span>
                      <span className="rounded-full bg-black/35 px-2.5 py-1">{formatDuration(featuredLesson.durationMinutes || 8)}</span>
                      {featuredLesson.createdAt ? (
                        <span className="rounded-full bg-black/35 px-2.5 py-1">{formatPublishedDate(featuredLesson.createdAt)}</span>
                      ) : null}
                    </div>
                    <span className="inline-flex w-fit items-center gap-2 rounded-full bg-white px-4 py-2 text-sm font-bold text-black transition group-hover:translate-x-0.5">
                      <PlayCircle size={16} />
                      Watch video
                    </span>
                  </div>
                </Link>
                <div className="space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-semibold text-[var(--text-primary)]">Recent videos</p>
                    <span className="text-xs font-medium text-[var(--text-secondary)]">
                      {compactCount(filteredLessons.length, 'video')}
                    </span>
                  </div>
                  {recentLessons.length ? (
                    <div className="grid gap-3 xl:grid-cols-2">
                      {recentLessons.map((lesson) => (
                        <ChannelLessonCard key={lesson.id} lesson={lesson} compact />
                      ))}
                    </div>
                  ) : (
                    <p className="rounded-xl bg-[var(--surface-container-high)] px-3 py-2 text-sm text-[var(--text-secondary)]">
                      This is the only public video on the channel.
                    </p>
                  )}
                </div>
              </>
            ) : (
              <EmptyState
                icon={PlayCircle}
                title={searchQuery ? 'No videos match your search.' : 'No public videos yet.'}
                body={searchQuery ? 'Try another search term for this channel.' : 'Published lessons will appear here.'}
              />
            )}
          </div>
        )}

        {activeTab === 'videos' && renderVideos(filteredLessons)}

        {activeTab === 'playlists' && (
          filteredPlaylists.length ? (
            <div className="grid gap-3 xl:grid-cols-2">
              {filteredPlaylists.map((playlist) => (
                <PlaylistCard key={playlist.id} playlist={playlist} />
              ))}
            </div>
          ) : (
            <EmptyState
              icon={ListVideo}
              title={searchQuery ? 'No playlists match your search.' : 'No public playlists yet.'}
              body={searchQuery ? 'Try another search term for this channel.' : 'Publisher playlists will appear here.'}
            />
          )
        )}

        {activeTab === 'about' && (
          <div className="grid gap-4 md:grid-cols-[1fr_18rem]">
            <div>
              <p className="text-sm font-semibold text-[var(--text-primary)]">About</p>
              <p className="mt-2 whitespace-pre-wrap text-sm text-[var(--text-secondary)]">
                {profile?.bio || `${displayName} has not added a channel bio yet.`}
              </p>
            </div>
            <div className="rounded-xl token-surface p-4 text-sm text-[var(--text-secondary)]">
              <p className="font-semibold text-[var(--text-primary)]">Stats</p>
              <p className="mt-3">{compactCount(profile?.follower_count, 'follower')}</p>
              <p className="mt-2">{compactCount(profile?.lesson_count, 'lesson')}</p>
              <p className="mt-2">{compactCount(profile?.stats?.total_likes, 'like')}</p>
            </div>
          </div>
        )}
      </SurfaceCard>
    </div>
  );
}
