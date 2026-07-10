import UploadComposer from './UploadComposer';
import Dialog from '../ui/Dialog';

const CREATE_LESSON_MODAL_TITLE_ID = 'create-lesson-modal-title';

export default function CreateLessonModal({
  open,
  onClose,
  categories,
  submitting,
  submitError,
  onSubmit,
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      titleId={CREATE_LESSON_MODAL_TITLE_ID}
      closeOnBackdrop={!submitting}
      closeOnEscape={!submitting}
      closeDisabled={submitting}
      size="lg"
      className="max-w-2xl border-0 bg-transparent shadow-none"
    >
      <Dialog.Title className="sr-only">
        Create A New Lesson Draft
      </Dialog.Title>
      <Dialog.Body className="relative p-0 sm:p-0">
        <Dialog.Close
          onClose={onClose}
          disabled={submitting}
          closeLabel="Close create lesson"
          className="absolute right-4 top-4 z-10 rtl:left-4 rtl:right-auto"
        />
        <UploadComposer
          categories={categories}
          submitting={submitting}
          submitError={submitError}
          onSubmit={onSubmit}
        />
      </Dialog.Body>
    </Dialog>
  );
}
