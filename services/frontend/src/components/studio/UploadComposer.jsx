import { useMemo, useState } from 'react';
import { CloudUpload, FileText, Sparkles } from 'lucide-react';
import Button from '../ui/Button';
import SurfaceCard from '../ui/SurfaceCard';

const ACCEPTED_TYPES = ['.pptx', '.pdf', '.docx', '.txt'];

export default function UploadComposer({ categories, submitting, submitError, onSubmit }) {
  const [title, setTitle] = useState('');
  const [category, setCategory] = useState('');
  const [pauseSec, setPauseSec] = useState('0.2');
  const [whiteboardModeAll, setWhiteboardModeAll] = useState(false);
  const [avatarEnabled, setAvatarEnabled] = useState(false);
  const [file, setFile] = useState(null);
  const [coverFile, setCoverFile] = useState(null);

  const categoryOptions = useMemo(
    () => (Array.isArray(categories) ? categories.map((item) => item.name).filter(Boolean) : []),
    [categories],
  );

  const localValidationError = (() => {
    if (!file) return '';
    const extension = `.${String(file.name).split('.').pop().toLowerCase()}`;
    if (!ACCEPTED_TYPES.includes(extension)) {
      return `Unsupported file type. Use ${ACCEPTED_TYPES.join(', ')}`;
    }
    return '';
  })();

  const effectiveError = localValidationError || submitError;

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!file || localValidationError) return;

    await onSubmit({
      file,
      coverFile,
      title,
      category,
      pauseSec,
      whiteboardModeAll,
      avatarEnabled,
    });
  };

  return (
    <SurfaceCard elevated className="space-y-5">
      <div>
        <p className="label-sm">Authoring</p>
        <h2 className="headline-md mt-1 text-[var(--text-primary)]">Create A New Lesson Draft</h2>
        <p className="body-md mt-2">
          Upload source material and tune pacing. Publish the lesson after the render is ready.
        </p>
      </div>

      <form className="space-y-4" onSubmit={handleSubmit}>
        <label className="block text-sm text-[var(--text-secondary)]">
          Lesson title
          <input
            type="text"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Designing Cinematic AI Interfaces"
            className="focus-ring mt-1 h-11 w-full rounded-2xl border border-[var(--border-subtle)] bg-[color:var(--surface-muted)] px-3 text-[var(--text-primary)]"
          />
        </label>

        <label className="block text-sm text-[var(--text-secondary)]">
          Category
          <input
            type="text"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            list="studio-category-options"
            placeholder="AI Product, Design, Storytelling"
            className="focus-ring mt-1 h-11 w-full rounded-2xl border border-[var(--border-subtle)] bg-[color:var(--surface-muted)] px-3 text-[var(--text-primary)]"
          />
          <datalist id="studio-category-options">
            {categoryOptions.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm text-[var(--text-secondary)]">
            Pause between slides (sec)
            <input
              type="number"
              min="0"
              step="0.1"
              value={pauseSec}
              onChange={(event) => setPauseSec(event.target.value)}
              className="focus-ring mt-1 h-11 w-full rounded-2xl border border-[var(--border-subtle)] bg-[color:var(--surface-muted)] px-3 text-[var(--text-primary)]"
            />
          </label>

          <div className="space-y-2 pt-2 text-sm text-[var(--text-secondary)]">
            <label className="flex items-center gap-2 rounded-2xl bg-[color:var(--surface-muted)] px-3 py-2">
              <input
                type="checkbox"
                checked={whiteboardModeAll}
                onChange={(event) => setWhiteboardModeAll(event.target.checked)}
              />
              <span>Whiteboard style on all slides</span>
            </label>
            <label className="flex items-center gap-2 rounded-2xl bg-[color:var(--surface-muted)] px-3 py-2">
              <input
                type="checkbox"
                checked={avatarEnabled}
                onChange={(event) => setAvatarEnabled(event.target.checked)}
              />
              <span>Render with avatar</span>
            </label>
            <p className="px-3 text-xs text-[var(--text-secondary)]">
              Avatar jobs use the separate avatar queue and can take longer.
            </p>
          </div>
        </div>

        <label className="block text-sm text-[var(--text-secondary)]">
          Source file
          <div className="mt-1 rounded-2xl border border-dashed border-[var(--border-subtle)] bg-[color:var(--surface-muted)] p-4">
            <input
              type="file"
              accept={ACCEPTED_TYPES.join(',')}
              onChange={(event) => setFile(event.target.files?.[0] || null)}
              className="focus-ring block w-full cursor-pointer text-sm text-[var(--text-primary)]"
            />
            <p className="mt-2 text-xs text-[var(--text-secondary)]">
              Supported: {ACCEPTED_TYPES.join(', ')}
            </p>
            {file && (
              <p className="mt-2 inline-flex items-center gap-1 text-xs text-[var(--text-primary)]">
                <FileText size={12} />
                {file.name}
              </p>
            )}
          </div>
        </label>

        <label className="block text-sm text-[var(--text-secondary)]">
          Cover image (optional)
          <div className="mt-1 rounded-2xl border border-dashed border-[var(--border-subtle)] bg-[color:var(--surface-muted)] p-4">
            <input
              type="file"
              accept="image/*"
              onChange={(event) => setCoverFile(event.target.files?.[0] || null)}
              className="focus-ring block w-full cursor-pointer text-sm text-[var(--text-primary)]"
            />
            <p className="mt-2 text-xs text-[var(--text-secondary)]">
              PNG, JPG, WEBP, or GIF. Recommended 16:9 image.
            </p>
            {coverFile && (
              <p className="mt-2 inline-flex items-center gap-1 text-xs text-[var(--text-primary)]">
                <FileText size={12} />
                {coverFile.name}
              </p>
            )}
          </div>
        </label>

        {effectiveError && (
          <p className="rounded-2xl bg-[color:var(--feedback-danger-bg)] px-3 py-2 text-sm text-[color:var(--feedback-danger-fg)]">
            {effectiveError}
          </p>
        )}

        <div className="flex flex-wrap gap-3">
          <Button type="submit" disabled={!file || submitting || Boolean(localValidationError)}>
            <CloudUpload size={16} />
            <span>{submitting ? 'Creating...' : 'Create Lesson Draft'}</span>
          </Button>
          <Button type="button" variant="secondary" onClick={() => {
            setTitle('');
            setCategory('');
            setPauseSec('0.2');
            setWhiteboardModeAll(false);
            setAvatarEnabled(false);
            setFile(null);
            setCoverFile(null);
          }}>
            <Sparkles size={16} />
            <span>Reset</span>
          </Button>
        </div>
      </form>
    </SurfaceCard>
  );
}
