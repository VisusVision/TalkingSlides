import { forwardRef } from 'react';

function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

const BASE_STYLES = [
  'focus-ring w-full rounded-control border border-[var(--border-subtle)]',
  'bg-[var(--surface-container-lowest)] px-3 py-2 text-sm text-[var(--text-primary)]',
  'placeholder:text-[var(--outline)] transition duration-normal ease-standard',
  'disabled:cursor-not-allowed disabled:opacity-60',
  'aria-[invalid=true]:border-[color:var(--feedback-danger-fg)]',
].join(' ');

const Textarea = forwardRef(function Textarea({
  className,
  invalid = false,
  'aria-invalid': ariaInvalid,
  ...props
}, ref) {
  const invalidState = ariaInvalid ?? (invalid || undefined);

  return (
    <textarea
      ref={ref}
      aria-invalid={invalidState}
      className={joinClasses(BASE_STYLES, className)}
      {...props}
    />
  );
});

export default Textarea;
