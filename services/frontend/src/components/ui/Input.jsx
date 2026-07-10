import { forwardRef } from 'react';

function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

const SIZE_STYLES = {
  sm: 'h-control-sm text-sm',
  md: 'h-control-md text-sm',
  lg: 'h-control-lg text-base',
};

const BASE_STYLES = [
  'focus-ring w-full rounded-control border border-[var(--border-subtle)]',
  'bg-[var(--surface-container-lowest)] px-3 text-[var(--text-primary)]',
  'placeholder:text-[var(--outline)] transition duration-normal ease-standard',
  'disabled:cursor-not-allowed disabled:opacity-60',
  'aria-[invalid=true]:border-[color:var(--feedback-danger-fg)]',
].join(' ');

const Input = forwardRef(function Input({
  className,
  size = 'md',
  invalid = false,
  'aria-invalid': ariaInvalid,
  ...props
}, ref) {
  const invalidState = ariaInvalid ?? (invalid || undefined);

  return (
    <input
      ref={ref}
      aria-invalid={invalidState}
      className={joinClasses(
        BASE_STYLES,
        SIZE_STYLES[size] || SIZE_STYLES.md,
        className,
      )}
      {...props}
    />
  );
});

export default Input;
