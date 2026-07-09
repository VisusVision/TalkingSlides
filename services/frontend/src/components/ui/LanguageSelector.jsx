import { Languages } from 'lucide-react';
import { useLocale } from '../../i18n/LocaleProvider';

export default function LanguageSelector({ compact = false, id, testId }) {
  const { locale, setLocale, supportedLocales, t } = useLocale();
  const accessibleLabel = t('languageSelector');

  return (
    <label
      className={compact
        ? 'focus-within:focus-ring inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full border border-[var(--border-subtle)] bg-[var(--surface-container-low)] px-2 text-[var(--text-secondary)]'
        : 'block w-full text-sm text-[var(--text-secondary)]'}
      title={accessibleLabel}
    >
      <Languages size={15} aria-hidden="true" />
      <span className={compact ? 'sr-only' : 'mb-2 ml-1 inline-block font-semibold text-[var(--text-primary)]'}>
        {accessibleLabel}
      </span>
      <select
        id={id}
        data-testid={testId || (compact ? 'global-language-selector' : 'settings-language-selector')}
        aria-label={accessibleLabel}
        value={locale}
        onChange={(event) => setLocale(event.target.value)}
        className={compact
          ? 'h-full max-w-[2.25rem] cursor-pointer appearance-none border-0 bg-transparent pr-1 text-xs font-bold uppercase text-[var(--text-primary)] focus:outline-none'
          : 'focus-ring block h-11 w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-container-high)] px-3 text-sm font-medium text-[var(--text-primary)]'}
      >
        {supportedLocales.map((option) => (
          <option key={option.code} value={option.code}>
            {compact
              ? option.shortLabel
              : `${option.label}${option.nativeLabel ? ` — ${option.nativeLabel}` : ''}`}
          </option>
        ))}
      </select>
    </label>
  );
}
