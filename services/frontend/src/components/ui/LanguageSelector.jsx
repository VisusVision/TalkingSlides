import { Languages } from 'lucide-react';
import { useLocale } from '../../i18n/LocaleProvider';

export default function LanguageSelector({ compact = false }) {
  const { locale, setLocale, supportedLocales } = useLocale();

  return (
    <label
      className="focus-within:focus-ring inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full border border-[var(--border-subtle)] bg-[var(--surface-container-low)] px-2 text-[var(--text-secondary)]"
      title="Language / Dil"
    >
      <Languages size={15} aria-hidden="true" />
      <span className="sr-only">Language / Dil</span>
      <select
        data-testid="global-language-selector"
        aria-label="Language / Dil"
        value={locale}
        onChange={(event) => setLocale(event.target.value)}
        className={`h-full cursor-pointer appearance-none border-0 bg-transparent pr-1 text-xs font-bold uppercase text-[var(--text-primary)] focus:outline-none ${
          compact ? 'max-w-[2.25rem]' : 'max-w-[3rem] sm:max-w-[7rem]'
        }`}
      >
        {supportedLocales.map((option) => (
          <option key={option.code} value={option.code}>
            {compact ? option.shortLabel : `${option.shortLabel} · ${option.label}`}
          </option>
        ))}
      </select>
    </label>
  );
}
