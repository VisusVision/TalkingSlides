import { Languages } from 'lucide-react';
import { useI18n } from '../../i18n/I18nProvider';

export default function LanguageSelector({ compact = false, className = '' }) {
  const { language, languageOptions, setLanguage, t } = useI18n();

  return (
    <label
      className={`focus-within:ring-2 focus-within:ring-[var(--accent-primary)] ${
        compact
          ? 'inline-flex h-10 items-center gap-2 rounded-full bg-[var(--surface-container-high)] px-3 text-xs font-semibold text-[var(--text-secondary)]'
          : 'block text-sm text-[var(--text-secondary)]'
      } ${className}`}
    >
      <span className={compact ? 'sr-only' : 'mb-1 block font-medium text-[var(--text-primary)]'}>
        {t('common.languageSelector')}
      </span>
      {compact ? <Languages size={15} aria-hidden="true" /> : null}
      <select
        value={language}
        onChange={(event) => setLanguage(event.target.value)}
        className={
          compact
            ? 'cursor-pointer border-0 bg-transparent text-xs font-semibold text-[var(--text-primary)] focus:outline-none'
            : 'h-11 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 text-sm text-[var(--text-primary)] focus:outline-none'
        }
        aria-label={t('common.languageSelector')}
      >
        {languageOptions.map((option) => (
          <option key={option.code} value={option.code}>
            {option.nativeLabel}
          </option>
        ))}
      </select>
    </label>
  );
}
