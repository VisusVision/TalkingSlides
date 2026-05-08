import { useEffect, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, Eye, EyeOff, ListPlus, Plus, Trash2, X } from 'lucide-react';
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

  const handleAddLesson = async (playlist) => {
    const projectId = Number(selectedProjectByPlaylist[playlist.id] || 0);
    if (!projectId || busy) return;
    setBusy(`add:${playlist.id}`);
    setError('');
    try {
      const updated = await addPlaylistItem(playlist.id, projectId);
      replacePlaylist(updated);
      setSelectedProjectByPlaylist((current) => ({ ...current, [playlist.id]: '' }));
    } catch (err) {
      setError(err.message || 'Could not add lesson.');
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
        <div className="grid gap-4">
          {playlists.map((playlist) => {
            const draft = drafts[playlist.id] || {};
            const existingIds = new Set((playlist.items || []).map(itemProjectId));
            const availableLessons = lessonOptions.filter((project) => !existingIds.has(Number(project.id)));
            return (
              <SurfaceCard key={playlist.id} className="space-y-4">
                <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto]">
                  <label className="text-sm text-[var(--text-secondary)]">
                    Title
                    <input
                      value={draft.title || ''}
                      onChange={(event) => updateDraft(playlist.id, { title: event.target.value })}
                      maxLength={255}
                      className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
                    />
                  </label>
                  <label className="text-sm text-[var(--text-secondary)]">
                    Description
                    <input
                      value={draft.description || ''}
                      onChange={(event) => updateDraft(playlist.id, { description: event.target.value })}
                      className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
                    />
                  </label>
                  <label className="mt-6 inline-flex h-10 items-center gap-2 rounded-xl token-surface px-3 text-sm text-[var(--text-secondary)]">
                    <input
                      type="checkbox"
                      checked={Boolean(draft.is_public)}
                      onChange={(event) => updateDraft(playlist.id, { is_public: event.target.checked })}
                    />
                    {draft.is_public ? <Eye size={14} /> : <EyeOff size={14} />}
                    {publicLabel({ is_public: draft.is_public })}
                  </label>
                </div>

                <div className="flex flex-wrap gap-2">
                  <Button size="sm" onClick={() => handleUpdate(playlist)} disabled={busy === `update:${playlist.id}` || !draft.title?.trim()}>
                    <span>{busy === `update:${playlist.id}` ? 'Saving...' : 'Save playlist'}</span>
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => handleDelete(playlist)} disabled={busy === `delete:${playlist.id}`}>
                    <Trash2 size={14} />
                    <span>Delete</span>
                  </Button>
                </div>

                <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
                  <select
                    value={selectedProjectByPlaylist[playlist.id] || ''}
                    onChange={(event) => setSelectedProjectByPlaylist((current) => ({ ...current, [playlist.id]: event.target.value }))}
                    disabled={!availableLessons.length}
                    className="focus-ring h-10 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <option value="">{availableLessons.length ? 'Select a lesson to add' : 'All lessons added'}</option>
                    {availableLessons.map((project) => (
                      <option key={project.id} value={project.id}>{projectLabel(project)}</option>
                    ))}
                  </select>
                  <Button size="sm" onClick={() => handleAddLesson(playlist)} disabled={!selectedProjectByPlaylist[playlist.id] || busy === `add:${playlist.id}`}>
                    <Plus size={14} />
                    <span>Add lesson</span>
                  </Button>
                </div>

                {(playlist.items || []).length === 0 ? (
                  <p className="rounded-xl bg-[var(--surface-container-high)] px-3 py-2 text-sm text-[var(--text-secondary)]">No lessons in this playlist yet.</p>
                ) : (
                  <div className="space-y-2">
                    {(playlist.items || []).map((item, index) => {
                      const project = item.project || {};
                      const projectId = itemProjectId(item);
                      return (
                        <div key={`${playlist.id}-${projectId}`} className="flex flex-col gap-2 rounded-xl bg-[var(--surface-container-high)] p-3 sm:flex-row sm:items-center sm:justify-between">
                          <div className="min-w-0">
                            <p className="line-clamp-1 text-sm font-semibold text-[var(--text-primary)]">{project.title || `Lesson #${projectId}`}</p>
                            <p className="mt-1 text-xs text-[var(--text-secondary)]">{projectLabel(project)}</p>
                          </div>
                          <div className="flex shrink-0 flex-wrap gap-2">
                            <Button size="sm" variant="secondary" onClick={() => handleMoveLesson(playlist, index, -1)} disabled={index === 0 || busy === `reorder:${playlist.id}`}>
                              <ArrowUp size={14} />
                            </Button>
                            <Button size="sm" variant="secondary" onClick={() => handleMoveLesson(playlist, index, 1)} disabled={index === (playlist.items || []).length - 1 || busy === `reorder:${playlist.id}`}>
                              <ArrowDown size={14} />
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => handleRemoveLesson(playlist, projectId)} disabled={busy === `remove:${playlist.id}:${projectId}`}>
                              <X size={14} />
                              <span>Remove</span>
                            </Button>
                          </div>
                        </div>
                      );
                    })}
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
