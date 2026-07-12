import { createContext, useContext, useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

const SIZE_STYLES = {
  sm: 'max-w-md',
  md: 'max-w-lg',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
  full: 'max-w-[calc(100vw-2rem)]',
};

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'area[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  'iframe',
  'object',
  'embed',
  '[contenteditable="true"]',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

let scrollLockCount = 0;
let previousBodyOverflow = '';
const dialogStack = [];
const DialogContext = createContext({ titleId: undefined, descriptionId: undefined });

function lockBodyScroll() {
  if (typeof document === 'undefined') return;
  if (scrollLockCount === 0) {
    previousBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
  }
  scrollLockCount += 1;
}

function unlockBodyScroll() {
  if (typeof document === 'undefined' || scrollLockCount === 0) return;
  scrollLockCount -= 1;
  if (scrollLockCount === 0) {
    document.body.style.overflow = previousBodyOverflow;
    previousBodyOverflow = '';
  }
}

function getFocusableElements(container) {
  if (!container) return [];
  return Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR)).filter((element) => {
    if (element.hasAttribute('disabled')) return false;
    if (element.getAttribute('aria-hidden') === 'true') return false;
    return true;
  });
}

function isTopDialog(id) {
  return dialogStack[dialogStack.length - 1] === id;
}

function Dialog({
  open,
  onClose,
  size = 'md',
  closeOnBackdrop = true,
  closeOnEscape = true,
  closeDisabled = false,
  titleId,
  descriptionId,
  ariaLabel,
  className,
  overlayClassName,
  children,
  ...props
}) {
  const generatedTitleId = useId();
  const resolvedTitleId = titleId || generatedTitleId;
  const panelRef = useRef(null);
  const restoreFocusRef = useRef(null);
  const dialogIdRef = useRef(Symbol('dialog'));
  const optionsRef = useRef({
    closeDisabled,
    closeOnEscape,
    onClose,
  });

  optionsRef.current = {
    closeDisabled,
    closeOnEscape,
    onClose,
  };

  useEffect(() => {
    if (!open || typeof document === 'undefined') return undefined;

    const dialogId = dialogIdRef.current;
    restoreFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    dialogStack.push(dialogId);
    lockBodyScroll();

    const focusTimer = window.setTimeout(() => {
      if (!isTopDialog(dialogId)) return;
      const focusable = getFocusableElements(panelRef.current);
      const target = focusable[0] || panelRef.current;
      target?.focus({ preventScroll: true });
    }, 0);

    const handleKeyDown = (event) => {
      if (!isTopDialog(dialogId)) return;

      if (event.key === 'Escape') {
        const { closeDisabled: escapeDisabled, closeOnEscape: escapeEnabled, onClose: closeDialog } = optionsRef.current;
        if (escapeEnabled && !escapeDisabled) {
          event.preventDefault();
          closeDialog?.();
        }
        return;
      }

      if (event.key !== 'Tab') return;

      const focusable = getFocusableElements(panelRef.current);
      if (focusable.length === 0) {
        event.preventDefault();
        panelRef.current?.focus({ preventScroll: true });
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      if (event.shiftKey && (active === first || !panelRef.current?.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);

    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener('keydown', handleKeyDown);
      const index = dialogStack.lastIndexOf(dialogId);
      if (index >= 0) dialogStack.splice(index, 1);
      unlockBodyScroll();

      const restoreTarget = restoreFocusRef.current;
      if (restoreTarget?.isConnected) {
        restoreTarget.focus({ preventScroll: true });
      }
      restoreFocusRef.current = null;
    };
  }, [open]);

  if (!open || typeof document === 'undefined') return null;

  const handleBackdropMouseDown = (event) => {
    if (event.target === event.currentTarget && closeOnBackdrop && !closeDisabled) {
      onClose?.();
    }
  };

  return createPortal(
    <div
      className={joinClasses(
        'motion-fade fixed inset-0 z-[80] flex items-center justify-center bg-[color:var(--modal-backdrop)] p-3 sm:p-4',
        overlayClassName,
      )}
      onMouseDown={handleBackdropMouseDown}
      data-dialog-overlay=""
    >
      <section
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={ariaLabel ? undefined : resolvedTitleId}
        aria-describedby={descriptionId}
        aria-label={ariaLabel}
        tabIndex={-1}
        className={joinClasses(
          'motion-scale-in flex max-h-[calc(100dvh-1.5rem)] w-full flex-col overflow-hidden rounded-dialog border border-[var(--border-subtle)] bg-[var(--surface-container)] text-start text-[var(--text-primary)] shadow-dialog outline-none sm:max-h-[calc(100dvh-2rem)]',
          SIZE_STYLES[size] || SIZE_STYLES.md,
          className,
        )}
        {...props}
      >
        <DialogContext.Provider value={{ titleId: resolvedTitleId, descriptionId }}>
          {children}
        </DialogContext.Provider>
      </section>
    </div>,
    document.body,
  );
}

function DialogHeader({ className, children, ...props }) {
  return (
    <div
      className={joinClasses(
        'flex shrink-0 items-start justify-between gap-3 border-b border-[var(--border-subtle)] px-4 py-4 sm:px-6',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

function DialogTitle({ as: Component = 'h2', className, children, ...props }) {
  const { titleId } = useContext(DialogContext);
  return (
    <Component
      id={props.id || titleId}
      className={joinClasses('title-lg text-[var(--text-primary)]', className)}
      {...props}
    >
      {children}
    </Component>
  );
}

function DialogDescription({ as: Component = 'p', className, children, ...props }) {
  const { descriptionId } = useContext(DialogContext);
  return (
    <Component
      id={props.id || descriptionId}
      className={joinClasses('body-md mt-2', className)}
      {...props}
    >
      {children}
    </Component>
  );
}

function DialogBody({ className, children, ...props }) {
  return (
    <div
      className={joinClasses('rail-scroll min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6', className)}
      {...props}
    >
      {children}
    </div>
  );
}

function DialogFooter({ className, children, ...props }) {
  return (
    <div
      className={joinClasses(
        'shrink-0 border-t border-[var(--border-subtle)] bg-[var(--surface-container)] px-4 py-4 sm:px-6',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

function DialogClose({
  closeLabel = 'Close dialog',
  disabled = false,
  onClose,
  className,
  children,
  ...props
}) {
  return (
    <button
      type="button"
      onClick={onClose}
      disabled={disabled}
      className={joinClasses(
        'focus-ring inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-pill text-[var(--text-secondary)] transition duration-normal ease-standard hover:bg-[color:var(--surface-muted)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-60',
        className,
      )}
      aria-label={closeLabel}
      {...props}
    >
      {children || <X size={16} />}
    </button>
  );
}

Dialog.Header = DialogHeader;
Dialog.Title = DialogTitle;
Dialog.Description = DialogDescription;
Dialog.Body = DialogBody;
Dialog.Footer = DialogFooter;
Dialog.Close = DialogClose;

export default Dialog;
