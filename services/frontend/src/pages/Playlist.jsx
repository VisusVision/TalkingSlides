import { useEffect, useMemo, useState } from 'react';
import { Clock3, ListVideo, PlayCircle, UserCircle } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { getPlaylist } from '../api';
import SurfaceCard from '../components/ui/SurfaceCard';
import { formatDuration, normalizeLesson } from '../lib/content';

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

function normalizePlaylist(payload) {
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const lessons = items
    .map((item) => normalizeLesson(item?.project || item))
    .filter((lesson) => lesson.id);
  return {
    id: payload?.id,
    title: payload?.title || 'Playlist',
    description: payload?.description || '',
    isPublic: Boolean(payload?.is_public),
    publisherId: payload?.publisher_id || payload?.user || lessons[0]?.teacherId || null,
    publisherName: payload?.publisher_name || lessons[0]?.teacherName || 'Publisher',
    publisherUsername: payload?.publisher_username || '',
    itemCount: Number(payload?.item_count ?? lessons.length ?? 0),
    lessons,
  };
}

function EmptyState({ title, body }) {
  return (
    <SurfaceCard elevated className="text-center">
      <ListVideo className="mx-auto text-[var(--text-secondary)]" size={24} />
      <p className="title-lg mt-2 text-[var(--text-primary)]">{title}</p>
      {body ? <p className="body-md mt-1">{body}</p> : null}
    </SurfaceCard>
  );
}

function PlaylistLessonCard({ lesson, index }) {
  const published = formatPublishedDate(lesson.createdAt);
  return (
    <Link
      to={`/watch?lesson=${lesson.id}`}
      className="focus-ring group grid gap-3 rounded-xl token-surface-elevated p-3 transition hover:-translate-y-0.5 sm:grid-cols-[2.5rem_12rem_minmax(0,1fr)]"
    >
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--surface-container-high)] text-sm font-bold text-[var(--accent-primary)]">
        {index + 1}
      </span>
      <div className="relative aspect-video overflow-hidden rounded-lg bg-[var(--surface-container-high)]" style={lessonBackground(lesson)}>
        <span className="absolute inset-0 flex items-center justify-center opacity-0 transition group-hover:opacity-100">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-black/55 text-white">
            <PlayCircle size={22} />
          </span>
        </span>
      </div>
      <div className="min-w-0 space-y-2">
        <p className="line-clamp-2 text-sm font-semibold text-[var(--text-primary)]">{lesson.title}</p>
        <p className="line-clamp-2 text-xs text-[var(--text-secondary)]">{lesson.description || 'No description yet.'}</p>
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

export default function Playlist() {
  const { playlistId } = useParams();
  const [playlist, setPlaylist] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');

    getPlaylist(playlistId)
      .then((payload) => {
        if (!active) return;
        setPlaylist(normalizePlaylist(payload));
      })
      .catch((err) => {
        if (!active) return;
        setPlaylist(null);
        setError(err.message || 'Playlist not found.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [playlistId]);

  const publisherLink = useMemo(() => (
    playlist?.publisherId ? `/channel/${playlist.publisherId}` : ''
  ), [playlist?.publisherId]);

  if (loading) {
    return (
      <SurfaceCard elevated>
        <p className="body-md">Loading playlist...</p>
      </SurfaceCard>
    );
  }

  if (error || !playlist) {
    return (
      <EmptyState
        title="Playlist not available."
        body="This playlist may be private or no longer exist."
      />
    );
  }

  return (
    <div className="space-y-5">
      <SurfaceCard className="space-y-4">
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <p className="label-sm">Playlist</p>
            <h1 className="headline-md break-words text-[var(--text-primary)]">{playlist.title}</h1>
            {playlist.description ? (
              <p className="body-md mt-2 max-w-3xl whitespace-pre-wrap">{playlist.description}</p>
            ) : null}
          </div>
          <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-[var(--surface-container-highest)] px-3 py-1.5 text-sm font-semibold text-[var(--accent-primary)]">
            <ListVideo size={15} />
            {compactCount(playlist.itemCount, 'video')}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm text-[var(--text-secondary)]">
          <UserCircle size={16} />
          {publisherLink ? (
            <Link to={publisherLink} className="focus-ring rounded-md font-semibold text-[var(--text-primary)] hover:text-[var(--accent-primary)]">
              {playlist.publisherName}
            </Link>
          ) : (
            <span className="font-semibold text-[var(--text-primary)]">{playlist.publisherName}</span>
          )}
          {playlist.publisherUsername ? <span>@{playlist.publisherUsername}</span> : null}
        </div>
      </SurfaceCard>

      {playlist.lessons.length ? (
        <div className="grid gap-3">
          {playlist.lessons.map((lesson, index) => (
            <PlaylistLessonCard key={lesson.id} lesson={lesson} index={index} />
          ))}
        </div>
      ) : (
        <EmptyState
          title="No public videos in this playlist yet."
          body="Published and ready lessons will appear here when the publisher adds them."
        />
      )}
    </div>
  );
}
