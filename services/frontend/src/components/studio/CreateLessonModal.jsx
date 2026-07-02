import { X } from 'lucide-react';
import UploadComposer from './UploadComposer';
import { useI18n } from '../../i18n/I18nProvider';

export default function CreateLessonModal({
  open,
  onClose,
  categories,
  submitting,
  submitError,
  onSubmit,
}) {
  const { t } = useI18n();
  if (!open) return null;

  const handleBackdropMouseDown = (event) => {
    if (event.target === event.currentTarget && !submitting) {
      onClose();
    }
  };

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-[color:var(--modal-backdrop)] p-4"
      onMouseDown={handleBackdropMouseDown}
    >
      <div className="relative w-full max-w-2xl">
        <button
          type="button"
          onClick={onClose}
          disabled={submitting}
          className="focus-ring absolute right-4 top-4 z-10 inline-flex h-8 w-8 items-center justify-center rounded-full text-[var(--text-secondary)] hover:bg-[color:var(--surface-muted)] disabled:cursor-not-allowed disabled:opacity-60"
          aria-label={t('studio.closeCreateLesson')}
        >
          <X size={16} />
        </button>

        <UploadComposer
          categories={categories}
          submitting={submitting}
          submitError={submitError}
          onSubmit={onSubmit}
        />
      </div>
    </div>
  );
}
