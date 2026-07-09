export const APP_LOCALE_STORAGE_KEY = 'visus-ui-locale';
export const APP_LOCALE_CHANGED_EVENT = 'visus-locale-changed';

export const SUPPORTED_APP_LOCALES = Object.freeze([
  { code: 'en', shortLabel: 'EN', label: 'English' },
  { code: 'tr', shortLabel: 'TR', label: 'Türkçe' },
]);

export const DEFAULT_APP_LOCALE = 'en';

export function normalizeAppLocale(value) {
  const baseLocale = String(value || '').trim().toLowerCase().split(/[-_]/)[0];
  return SUPPORTED_APP_LOCALES.some((locale) => locale.code === baseLocale)
    ? baseLocale
    : DEFAULT_APP_LOCALE;
}

export function readStoredAppLocale() {
  if (typeof window === 'undefined') return DEFAULT_APP_LOCALE;
  const stored = window.localStorage.getItem(APP_LOCALE_STORAGE_KEY);
  return normalizeAppLocale(stored || window.navigator?.language || DEFAULT_APP_LOCALE);
}

export function applyAppLocale(locale, { persist = true, announce = true } = {}) {
  const normalized = normalizeAppLocale(locale);
  if (typeof document !== 'undefined') {
    document.documentElement.lang = normalized;
  }
  if (typeof window !== 'undefined') {
    if (persist) {
      window.localStorage.setItem(APP_LOCALE_STORAGE_KEY, normalized);
    }
    if (announce) {
      window.dispatchEvent(new CustomEvent(APP_LOCALE_CHANGED_EVENT, {
        detail: { locale: normalized },
      }));
    }
  }
  return normalized;
}

export function currentAppLocale() {
  if (typeof document !== 'undefined' && document.documentElement.lang) {
    return normalizeAppLocale(document.documentElement.lang);
  }
  return readStoredAppLocale();
}
