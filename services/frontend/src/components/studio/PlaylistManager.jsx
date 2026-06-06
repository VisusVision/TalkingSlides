import { useEffect, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ChevronDown, ChevronUp, Eye, EyeOff, ListPlus, Plus, Search, Trash2, X } from 'lucide-react';
import {
  addPlaylistItem,
  createPlaylist,
  deletePlaylist,
  listPlaylists,
  removePlaylistItem,
  reorderPlaylistItems,
  updatePlaylist,
} from '../../api';
import Button from '../ui/Button';
import SurfaceCard from '../ui/SurfaceCard';

function normalizeRows(payload) {
  return Array.isArray(payload) ? payload : payload?.results || [];
}

function itemProjectId(item) {
  return Number(item?.project_id || item?.project?.id || 0);
}

function publicLabel(playlist) {
  return playlist?.is_public ? 'Public' : 'Private';
}

function projectLabel(project) {
  const status = project?.is_published ? 'Published' : 'Draft';
  return `${project?.title || `Lesson #${project?.id}`} - ${status}`;
}

function lessonImageUrl(project) {
  const url = String(project?.thumbnail_url || project?.cover_url || '').trim();
  if (!url) return '';
  if (/storage_local|\/api\/v1\/stream\/|\.mp4(?:[?#]|$)/i.test(url)) return '';
  return url;
}

export default function PlaylistManager({ projects = [] }) {
  const [playlists, setPlaylists] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [isPublic, setIsPublic] = useState(true);
  const [drafts, setDrafts] = useState({});
  const [selectedProjectByPlaylist, setSelectedProjectByPlaylist] = useState({});
  const [expandedPlaylistIds, setExpandedPlaylistIds] = useState(new Set());
  const [lessonSearchQuery, setLessonSearchQuery] = useState({});
  const [showPickerFor, setShowPickerFor] = useState(null);

  const lessonOptions = useMemo(
    () => [...projects].sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''))),
    [projects],
  );

  const refreshPlaylists = async () => {
    setLoading(true);
    setError('');
    try {
      const payload = await listPlaylists();
      const rows = normalizeRows(payload);
      setPlaylists(rows);
      setDrafts(Object.fromEntries(rows.map((playlist) => [playlist.id, {
        title: playlist.title || '',
        description: playlist.description || '',
        is_public: Boolean(playlist.is_public),
      }])));
    } catch (err) {
      setError(err.message || 'Could not load playlists.');
      setPlaylists([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshPlaylists();
  }, []);

  const replacePlaylist = (updated) => {
    setPlaylists((current) => current.map((playlist) => (
      Number(playlist.id) === Number(updated.id) ? updated : playlist
    )));
    setDrafts((current) => ({
      ...current,
      [updated.id]: {
        title: updated.title || '',
        description: updated.description || '',
        is_public: Boolean(updated.is_public),
      },
    }));
  };

  const handleCreate = async (event) => {
    event.preventDefault();
    if (!title.trim() || busy) return;
    setBusy('create');
    setError('');
    try {
      const created = await createPlaylist({
        title: title.trim(),
        description: description.trim(),
        is_public: isPublic,
      });
      setPlaylists((current) => [created, ...current]);
      setDrafts((current) => ({
        ...current,
        [created.id]: {
          title: created.title || '',
          description: created.description || '',
          is_public: Boolean(created.is_public),
        },
      }));
      setTitle('');
      setDescription('');
      setIsPublic(true);
    } catch (err) {
      setError(err.message || 'Could not create playlist.');
    } finally {
      setBusy('');
    }
  };

  const handleUpdate = async (playlist) => {
    const draft = drafts[playlist.id] || {};
    if (!draft.title?.trim() || busy) return;
    setBusy(`update:${playlist.id}`);
    setError('');
    try {
      const updated = await updatePlaylist(playlist.id, {
        title: draft.title.trim(),
        description: String(draft.description || '').trim(),
        is_public: Boolean(draft.is_public),
      });
      replacePlaylist(updated);
    } catch (err) {
      setError(err.message || 'Could not update playlist.');
    } finally {
      setBusy('');
    }
  };

  const handleDelete = async (playlist) => {
    if (busy) return;
    if (!window.confirm(`Delete "${playlist.title}"?`)) return;
    setBusy(`delete:${playlist.id}`);
    setError('');
    try {
      await deletePlaylist(playlist.id);
      setPlaylists((current) => current.filter((item) => Number(item.id) !== Number(playlist.id)));
    } catch (err) {
      setError(err.message || 'Could not delete playlist.');
    } finally {
      setBusy('');
    }
  };

  const handleAddLesson = async (playlist, projectIdOverride) => {
    const projectId = Number(projectIdOverride ?? selectedProjectByPlaylist[playlist.id] ?? 0);
    if (!projectId || busy) return false;
    const existingIds = new Set((playlist.items || []).map(itemProjectId));
    if (existingIds.has(projectId)) {
      setSelectedProjectByPlaylist((current) => ({ ...current, [playlist.id]: '' }));
      return false;
    }
    setBusy(`add:${playlist.id}`);
    setError('');
    try {
      const updated = await addPlaylistItem(playlist.id, projectId);
      replacePlaylist(updated);
      setSelectedProjectByPlaylist((current) => ({ ...current, [playlist.id]: '' }));
      return true;
    } catch (err) {
      setError(err.message || 'Could not add lesson.');
      return false;
    } finally {
      setBusy('');
    }
  };

  const handleRemoveLesson = async (playlist, projectId) => {
    if (!projectId || busy) return;
    setBusy(`remove:${playlist.id}:${projectId}`);
    setError('');
    try {
      await removePlaylistItem(playlist.id, projectId);
      replacePlaylist({
        ...playlist,
        item_count: Math.max(0, Number(playlist.item_count || 0) - 1),
        items: (playlist.items || []).filter((item) => itemProjectId(item) !== Number(projectId)),
      });
    } catch (err) {
      setError(err.message || 'Could not remove lesson.');
    } finally {
      setBusy('');
    }
  };

  const handleMoveLesson = async (playlist, index, direction) => {
    const items = playlist.items || [];
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= items.length || busy) return;
    const reordered = [...items];
    const [moved] = reordered.splice(index, 1);
    reordered.splice(nextIndex, 0, moved);
    const projectIds = reordered.map(itemProjectId);

    setBusy(`reorder:${playlist.id}`);
    setError('');
    try {
      const updated = await reorderPlaylistItems(playlist.id, projectIds);
      replacePlaylist(updated);
    } catch (err) {
      setError(err.message || 'Could not reorder playlist.');
    } finally {
      setBusy('');
    }
  };

  const updateDraft = (playlistId, patch) => {
    setDrafts((current) => ({
      ...current,
      [playlistId]: {
        ...(current[playlistId] || {}),
        ...patch,
      },
    }));
  };

  const toggleExpanded = (playlistId) => {
    setExpandedPlaylistIds((current) => {
      const next = new Set(current);
      if (next.has(playlistId)) {
        next.delete(playlistId);
      } else {
        next.add(playlistId);
      }
      return next;
    });
  };

  return (
    <div className="space-y-5">
      <SurfaceCard className="space-y-4">
        <div>
          <p className="label-sm">Playlists</p>
          <h2 className="title-lg text-[var(--text-primary)]">Channel playlist manager</h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">Create public channel playlists and organize your own lessons.</p>
        </div>

        <form className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto_auto]" onSubmit={handleCreate}>
          <label className="text-sm text-[var(--text-secondary)]">
            Title
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={255}
              className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
              placeholder="Starter path"
            />
          </label>
          <label className="text-sm text-[var(--text-secondary)]">
            Description
            <input
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
              placeholder="What this playlist covers"
            />
          </label>
          <label className="mt-6 inline-flex h-10 items-center gap-2 rounded-xl token-surface px-3 text-sm text-[var(--text-secondary)]">
            <input type="checkbox" checked={isPublic} onChange={(event) => setIsPublic(event.target.checked)} />
            Public
          </label>
          <Button className="mt-6" type="submit" disabled={busy === 'create' || !title.trim()}>
            <Plus size={15} />
            <span>{busy === 'create' ? 'Creating...' : 'Create'}</span>
          </Button>
        </form>

        {error ? <p className="text-sm font-medium text-[color:var(--feedback-danger-fg)]">{error}</p> : null}
      </SurfaceCard>

      {loading ? (
        <SurfaceCard elevated>
          <p className="body-md">Loading playlists...</p>
        </SurfaceCard>
      ) : playlists.length === 0 ? (
        <SurfaceCard elevated className="text-center">
          <ListPlus className="mx-auto text-[var(--text-secondary)]" size={23} />
          <p className="title-lg mt-2 text-[var(--text-primary)]">No playlists yet.</p>
          <p className="body-md mt-1">Create one to publish a sequence on your channel.</p>
        </SurfaceCard>
      ) : (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
          {playlists.map((playlist) => {
            const draft = drafts[playlist.id] || {};
            const isExpanded = expandedPlaylistIds.has(playlist.id);
            const searchQuery = lessonSearchQuery[playlist.id] || '';
            const existingIds = new Set((playlist.items || []).map(itemProjectId));
            const availableLessons = lessonOptions.filter((project) => {
              if (existingIds.has(Number(project.id))) return false;
              if (!searchQuery) return true;
              const q = searchQuery.toLowerCase();
              return (
                String(project.title || '').toLowerCase().includes(q)
                || String(project.id).includes(q)
              );
            });

            return (
              <SurfaceCard
                key={playlist.id}
                className={`flex flex-col transition-all duration-300 ${isExpanded ? 'md:col-span-2 xl:col-span-3' : ''}`}
                elevated={isExpanded}
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <h3 className="truncate title-md text-[var(--text-primary)]">
                        {draft.title || playlist.title || 'Untitled Playlist'}
                      </h3>
                      <span className="shrink-0 rounded-full bg-[var(--surface-container-high)] px-2 py-0.5 text-[0.65rem] font-bold uppercase tracking-wider text-[var(--text-secondary)]">
                        {playlist.item_count || 0} Lessons
                      </span>
                    </div>
                    {!isExpanded && (
                      <p className="mt-1 line-clamp-1 text-xs text-[var(--text-secondary)]">
                        {draft.description || playlist.description || 'No description provided.'}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <div className={`flex h-8 items-center gap-1.5 rounded-full px-2.5 text-[0.65rem] font-bold uppercase tracking-wider ${
                      draft.is_public ? 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]' : 'bg-[var(--surface-container-high)] text-[var(--text-secondary)]'
                    }`}>
                      {draft.is_public ? <Eye size={12} /> : <EyeOff size={12} />}
                      <span>{publicLabel({ is_public: draft.is_public })}</span>
                    </div>
                    <button
                      type="button"
                      onClick={() => toggleExpanded(playlist.id)}
                      className="focus-ring flex h-8 w-8 items-center justify-center rounded-full text-[var(--text-secondary)] transition hover:bg-[color:var(--hover-surface-strong)] hover:text-[var(--text-primary)]"
                    >
                      {isExpanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
                    </button>
                  </div>
                </div>

                {isExpanded ? (
                  <div className="mt-6 space-y-6 animate-in fade-in slide-in-from-top-2 duration-300">
                    <div className="grid gap-4 lg:grid-cols-[1fr_1.5fr_auto]">
                      <label className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
                        Title
                        <input
                          value={draft.title || ''}
                          onChange={(event) => updateDraft(playlist.id, { title: event.target.value })}
                          maxLength={255}
                          className="focus-ring mt-1.5 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
                        />
                      </label>
                      <label className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
                        Description
                        <input
                          value={draft.description || ''}
                          onChange={(event) => updateDraft(playlist.id, { description: event.target.value })}
                          className="focus-ring mt-1.5 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
                        />
                      </label>
                      <div className="flex flex-col gap-1.5">
                        <span className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">Visibility</span>
                        <label className="flex h-10 items-center gap-2 rounded-xl token-surface px-3 text-sm text-[var(--text-secondary)]">
                          <input
                            type="checkbox"
                            checked={Boolean(draft.is_public)}
                            onChange={(event) => updateDraft(playlist.id, { is_public: event.target.checked })}
                          />
                          <span>Public visible</span>
                        </label>
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center justify-between gap-4 border-t border-[var(--border-subtle)] pt-4">
                      <div className="flex flex-wrap gap-2">
                        <Button size="sm" onClick={() => handleUpdate(playlist)} disabled={busy === `update:${playlist.id}` || !draft.title?.trim()}>
                          <span>{busy === `update:${playlist.id}` ? 'Saving...' : 'Save Changes'}</span>
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => handleDelete(playlist)} disabled={busy === `delete:${playlist.id}`}>
                          <Trash2 size={14} className="text-[color:var(--feedback-danger-fg)]" />
                          <span className="text-[color:var(--feedback-danger-fg)]">Delete Playlist</span>
                        </Button>
                      </div>
                      <div className="relative">
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => setShowPickerFor(showPickerFor === playlist.id ? null : playlist.id)}
                        >
                          <Plus size={14} />
                          <span>Add Lessons</span>
                        </Button>

                        {showPickerFor === playlist.id && (
                          <div className="absolute right-0 top-full z-20 mt-2 w-80 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-container-high)] p-3 shadow-2xl animate-in fade-in zoom-in-95 duration-200">
                            <div className="relative mb-3 flex items-center gap-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3">
                              <Search size={14} className="text-[var(--text-secondary)]" />
                              <input
                                autoFocus
                                value={searchQuery}
                                onChange={(e) => setLessonSearchQuery((prev) => ({ ...prev, [playlist.id]: e.target.value }))}
                                placeholder="Search your lessons..."
                                className="h-9 w-full bg-transparent text-sm text-[var(--text-primary)] outline-none"
                              />
                            </div>
                            <div className="max-h-64 overflow-y-auto space-y-1 pr-1">
                              {availableLessons.length === 0 ? (
                                <p className="py-8 text-center text-xs text-[var(--text-secondary)]">
                                  {searchQuery ? 'No lessons match your search.' : 'All your lessons are already in this playlist.'}
                                </p>
                              ) : (
                                availableLessons.map((project) => {
                                  const imageUrl = lessonImageUrl(project);
                                  return (
                                    <button
                                      key={project.id}
                                      type="button"
                                      onClick={async () => {
                                        setSelectedProjectByPlaylist((prev) => ({ ...prev, [playlist.id]: project.id }));
                                        const added = await handleAddLesson(playlist, project.id);
                                        if (added) {
                                          setShowPickerFor(null);
                                          setLessonSearchQuery((prev) => ({ ...prev, [playlist.id]: '' }));
                                        }
                                      }}
                                      className="flex w-full items-center gap-3 rounded-lg p-2 text-left transition hover:bg-[color:var(--hover-surface-strong)]"
                                    >
                                      <div className="h-10 w-16 shrink-0 overflow-hidden rounded bg-[var(--surface-container-highest)]">
                                        {imageUrl ? (
                                          <img
                                            src={imageUrl}
                                            alt=""
                                            className="h-full w-full object-cover"
                                            onError={(event) => {
                                              event.currentTarget.hidden = true;
                                            }}
                                          />
                                        ) : (
                                          <div className="h-full w-full bg-[var(--surface-container-highest)]" />
                                        )}
                                      </div>
                                      <div className="min-w-0">
                                        <p className="truncate text-sm font-medium text-[var(--text-primary)]">{project.title || `Project #${project.id}`}</p>
                                        <p className="mt-0.5 text-[0.65rem] text-[var(--text-secondary)] uppercase font-bold tracking-tight">
                                          {project.is_published ? 'Published' : 'Draft'}
                                        </p>
                                      </div>
                                    </button>
                                  );
                                })
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="space-y-3">
                      <p className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">Lessons</p>
                      {(playlist.items || []).length === 0 ? (
                        <div className="rounded-2xl border-2 border-dashed border-[var(--border-subtle)] py-12 text-center">
                          <p className="text-sm text-[var(--text-secondary)]">No lessons in this playlist yet.</p>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="mt-2"
                            onClick={() => setShowPickerFor(playlist.id)}
                          >
                            Add your first lesson
                          </Button>
                        </div>
                      ) : (
                        <div className="space-y-2">
                          {(playlist.items || []).map((item, index) => {
                            const project = item.project || {};
                            const projectId = itemProjectId(item);
                            const imageUrl = lessonImageUrl(project);
                            return (
                              <div key={`${playlist.id}-${projectId}`} className="flex items-center gap-4 rounded-2xl bg-[var(--surface-container-high)] p-3">
                                <div className="h-12 w-20 shrink-0 overflow-hidden rounded-lg bg-[var(--surface-container-highest)]">
                                  {imageUrl ? (
                                    <img
                                      src={imageUrl}
                                      alt=""
                                      className="h-full w-full object-cover"
                                      onError={(event) => {
                                        event.currentTarget.hidden = true;
                                      }}
                                    />
                                  ) : (
                                    <div className="h-full w-full flex items-center justify-center text-[var(--text-secondary)]">
                                      <ListPlus size={18} />
                                    </div>
                                  )}
                                </div>
                                <div className="min-w-0 flex-1">
                                  <p className="truncate text-sm font-semibold text-[var(--text-primary)]">{project.title || `Lesson #${projectId}`}</p>
                                  <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                    {project.is_published ? 'Published' : 'Draft'} • {project.category_name || 'Uncategorized'}
                                  </p>
                                </div>
                                <div className="flex shrink-0 items-center gap-1">
                                  <button
                                    type="button"
                                    onClick={() => handleMoveLesson(playlist, index, -1)}
                                    disabled={index === 0 || busy === `reorder:${playlist.id}`}
                                    className="focus-ring flex h-8 w-8 items-center justify-center rounded-full text-[var(--text-secondary)] transition hover:bg-[color:var(--hover-surface-strong)] hover:text-[var(--text-primary)] disabled:opacity-30"
                                  >
                                    <ArrowUp size={16} />
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => handleMoveLesson(playlist, index, 1)}
                                    disabled={index === (playlist.items || []).length - 1 || busy === `reorder:${playlist.id}`}
                                    className="focus-ring flex h-8 w-8 items-center justify-center rounded-full text-[var(--text-secondary)] transition hover:bg-[color:var(--hover-surface-strong)] hover:text-[var(--text-primary)] disabled:opacity-30"
                                  >
                                    <ArrowDown size={16} />
                                  </button>
                                  <div className="mx-1 h-6 w-[1px] bg-[var(--border-subtle)]" />
                                  <button
                                    type="button"
                                    onClick={() => handleRemoveLesson(playlist, projectId)}
                                    disabled={busy === `remove:${playlist.id}:${projectId}`}
                                    className="focus-ring flex h-8 w-8 items-center justify-center rounded-full text-[color:var(--feedback-danger-fg)] transition hover:bg-[color:var(--hover-surface-strong)] disabled:opacity-30"
                                    title="Remove from playlist"
                                  >
                                    <X size={16} />
                                  </button>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="mt-auto pt-4">
                    <Button
                      variant="secondary"
                      size="sm"
                      fullWidth
                      onClick={() => toggleExpanded(playlist.id)}
                    >
                      Manage Playlist
                    </Button>
                  </div>
                )}
              </SurfaceCard>
            );
          })}
        </div>
      )}
    </div>
  );
}
