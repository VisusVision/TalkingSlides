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
  'transition duration-normal ease-standard hover:border-[color:var(--outline)] focus-visible:border-[color:var(--accent-primary)]',
  'disabled:cursor-not-allowed disabled:border-[color:var(--border-subtle)] disabled:bg-[var(--surface-container-low)] disabled:text-[var(--outline)] disabled:opacity-80',
  'aria-[invalid=true]:border-[color:var(--feedback-danger-fg)]',
].join(' ');

const Select = forwardRef(function Select({
  className,
  size = 'md',
  invalid = false,
  'aria-invalid': ariaInvalid,
  children,
  ...props
}, ref) {
  const invalidState = ariaInvalid ?? (invalid || undefined);

  return (
    <select
      ref={ref}
      aria-invalid={invalidState}
      className={joinClasses(
        BASE_STYLES,
        SIZE_STYLES[size] || SIZE_STYLES.md,
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
});

export default Select;
