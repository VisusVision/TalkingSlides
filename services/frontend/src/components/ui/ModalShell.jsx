import Dialog from './Dialog';

export default function ModalShell({
  open,
  eyebrow,
  title,
  titleId,
  closeLabel = 'Close dialog',
  onClose,
  canBackdropClose = true,
  closeDisabled = false,
  maxWidthClass = 'max-w-3xl',
  bodyClassName = '',
  footer,
  children,
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      closeOnBackdrop={canBackdropClose}
      closeOnEscape={canBackdropClose}
      closeDisabled={closeDisabled}
      titleId={titleId}
      className={maxWidthClass}
    >
      <Dialog.Header>
        <div>
          {eyebrow ? <p className="label-sm">{eyebrow}</p> : null}
          <Dialog.Title className={eyebrow ? 'mt-1' : ''}>{title}</Dialog.Title>
        </div>
        <Dialog.Close
          onClose={onClose}
          disabled={closeDisabled}
          closeLabel={closeLabel}
        />
      </Dialog.Header>

      <Dialog.Body className={bodyClassName}>
        {children}
      </Dialog.Body>

      {footer ? (
        <Dialog.Footer>
          {footer}
        </Dialog.Footer>
      ) : null}
    </Dialog>
  );
}
