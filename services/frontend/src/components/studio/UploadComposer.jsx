import { useMemo, useState } from 'react';
import { CloudUpload, FileText, Sparkles } from 'lucide-react';
import Button from '../ui/Button';
import SurfaceCard from '../ui/SurfaceCard';
import { useI18n } from '../../i18n/I18nProvider';
import { featureEnabled, useCapabilities } from '../../lib/capabilities';

const ACCEPTED_TYPES = ['.pptx', '.pdf', '.docx', '.txt'];

export default function UploadComposer({ categories, submitting, submitError, onSubmit }) {
  const { t } = useI18n();
  const { capabilities } = useCapabilities();
  const avatarFeatureEnabled = featureEnabled(capabilities, 'avatar');
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
      return t('studio.unsupportedFileType', { types: ACCEPTED_TYPES.join(', ') });
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
      avatarEnabled: avatarFeatureEnabled && avatarEnabled,
    });
  };

  return (
    <SurfaceCard elevated className="space-y-5">
      <div>
        <p className="label-sm">{t('studio.authoring')}</p>
        <h2 className="headline-md mt-1 text-[var(--text-primary)]">{t('studio.createLessonDraftTitle')}</h2>
        <p className="body-md mt-2">
          {t('studio.createLessonDraftBody')}
        </p>
      </div>

      <form className="space-y-4" onSubmit={handleSubmit}>
        <label className="block text-sm text-[var(--text-secondary)]">
          {t('studio.lessonTitle')}
          <input
            type="text"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder={t('studio.lessonTitlePlaceholder')}
            className="focus-ring mt-1 h-11 w-full rounded-2xl border border-[var(--border-subtle)] bg-[color:var(--surface-muted)] px-3 text-[var(--text-primary)]"
          />
        </label>

        <label className="block text-sm text-[var(--text-secondary)]">
          {t('studio.category')}
          <input
            type="text"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            list="studio-category-options"
            placeholder={t('studio.categoryPlaceholder')}
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
            {t('studio.pauseBetweenSlides')}
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
              <span>{t('studio.whiteboardAllSlides')}</span>
            </label>
            {avatarFeatureEnabled && (
              <>
                <label className="flex items-center gap-2 rounded-2xl bg-[color:var(--surface-muted)] px-3 py-2">
                  <input
                    type="checkbox"
                    checked={avatarEnabled}
                    onChange={(event) => setAvatarEnabled(event.target.checked)}
                  />
                  <span>{t('studio.renderWithAvatar')}</span>
                </label>
                <p className="px-3 text-xs text-[var(--text-secondary)]">
                  {t('studio.avatarQueueHint')}
                </p>
              </>
            )}
          </div>
        </div>

        <label className="block text-sm text-[var(--text-secondary)]">
          {t('studio.sourceFile')}
          <div className="mt-1 rounded-2xl border border-dashed border-[var(--border-subtle)] bg-[color:var(--surface-muted)] p-4">
            <input
              type="file"
              accept={ACCEPTED_TYPES.join(',')}
              onChange={(event) => setFile(event.target.files?.[0] || null)}
              className="focus-ring block w-full cursor-pointer text-sm text-[var(--text-primary)]"
            />
            <p className="mt-2 text-xs text-[var(--text-secondary)]">
              {t('studio.supportedTypes', { types: ACCEPTED_TYPES.join(', ') })}
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
          {t('studio.coverImageOptional')}
          <div className="mt-1 rounded-2xl border border-dashed border-[var(--border-subtle)] bg-[color:var(--surface-muted)] p-4">
            <input
              type="file"
              accept="image/*"
              onChange={(event) => setCoverFile(event.target.files?.[0] || null)}
              className="focus-ring block w-full cursor-pointer text-sm text-[var(--text-primary)]"
            />
            <p className="mt-2 text-xs text-[var(--text-secondary)]">
              {t('studio.coverHint')}
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
            <span>{submitting ? t('common.creating') : t('studio.createLessonDraft')}</span>
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
            <span>{t('studio.resetForm')}</span>
          </Button>
        </div>
      </form>
    </SurfaceCard>
  );
}
