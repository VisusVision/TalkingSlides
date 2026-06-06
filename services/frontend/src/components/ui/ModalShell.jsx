import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

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
  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (event) => {
      if (event.key === 'Escape' && canBackdropClose && !closeDisabled) {
        onClose?.();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [canBackdropClose, closeDisabled, onClose, open]);

  if (!open) return null;

  const handleBackdropMouseDown = (event) => {
    if (event.target === event.currentTarget && canBackdropClose && !closeDisabled) {
      onClose?.();
    }
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-[color:var(--modal-backdrop)] p-3 sm:p-4"
      onMouseDown={handleBackdropMouseDown}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`flex max-h-[calc(100vh-1.5rem)] w-full ${maxWidthClass} flex-col overflow-hidden rounded-3xl border border-[var(--border-subtle)] bg-[var(--surface-container)] shadow-2xl sm:max-h-[calc(100vh-2rem)]`}
      >
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-[var(--border-subtle)] px-4 py-4 sm:px-6">
          <div>
            {eyebrow ? <p className="label-sm">{eyebrow}</p> : null}
            <h2 id={titleId} className="title-lg mt-1 text-[var(--text-primary)]">{title}</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={closeDisabled}
            className="focus-ring inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[var(--text-secondary)] hover:bg-[color:var(--surface-muted)] disabled:cursor-not-allowed disabled:opacity-60"
            aria-label={closeLabel}
          >
            <X size={16} />
          </button>
        </div>

        <div className={`min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 ${bodyClassName}`}>
          {children}
        </div>

        {footer ? (
          <div className="shrink-0 border-t border-[var(--border-subtle)] bg-[var(--surface-container)] px-4 py-4 sm:px-6">
            {footer}
          </div>
        ) : null}
      </section>
    </div>,
    document.body,
  );
}
