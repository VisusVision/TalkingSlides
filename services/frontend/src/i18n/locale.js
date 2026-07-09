export const APP_LOCALE_STORAGE_KEY = 'visus-ui-locale';
export const APP_LOCALE_CHANGED_EVENT = 'visus-locale-changed';
export const LEGACY_APP_LOCALE_STORAGE_KEYS = Object.freeze([
  'talkingslides-ui-language',
]);

export const SUPPORTED_APP_LOCALES = Object.freeze([
  { code: 'en', shortLabel: 'EN', label: 'English' },
  { code: 'tr', shortLabel: 'TR', label: 'Turkish', nativeLabel: 'Türkçe' },
  { code: 'es', shortLabel: 'ES', label: 'Spanish', nativeLabel: 'Español' },
  { code: 'fr', shortLabel: 'FR', label: 'French', nativeLabel: 'Français' },
  { code: 'de', shortLabel: 'DE', label: 'German', nativeLabel: 'Deutsch' },
  { code: 'it', shortLabel: 'IT', label: 'Italian', nativeLabel: 'Italiano' },
  { code: 'pt', shortLabel: 'PT', label: 'Portuguese', nativeLabel: 'Português' },
  { code: 'ru', shortLabel: 'RU', label: 'Russian', nativeLabel: 'Русский' },
  { code: 'ja', shortLabel: 'JA', label: 'Japanese', nativeLabel: '日本語' },
  { code: 'ko', shortLabel: 'KO', label: 'Korean', nativeLabel: '한국어' },
  { code: 'zh-CN', shortLabel: 'ZH', label: 'Simplified Chinese', nativeLabel: '简体中文' },
  { code: 'ar', shortLabel: 'AR', label: 'Arabic', nativeLabel: 'العربية' },
]);

export const DEFAULT_APP_LOCALE = 'en';
export const RTL_APP_LOCALES = Object.freeze(['ar']);

export function normalizeAppLocale(value) {
  const normalized = String(value || '').trim().replace(/_/g, '-').toLowerCase();
  if (normalized === 'zh' || normalized.startsWith('zh-cn') || normalized.startsWith('zh-hans')) {
    return 'zh-CN';
  }
  const baseLocale = normalized.split('-')[0];
  return SUPPORTED_APP_LOCALES.find((locale) => locale.code.toLowerCase() === normalized)?.code
    || SUPPORTED_APP_LOCALES.find((locale) => locale.code.toLowerCase() === baseLocale)?.code
    || DEFAULT_APP_LOCALE;
}

export function readStoredAppLocale() {
  if (typeof window === 'undefined') return DEFAULT_APP_LOCALE;
  const stored = window.localStorage.getItem(APP_LOCALE_STORAGE_KEY)
    || LEGACY_APP_LOCALE_STORAGE_KEYS
      .map((key) => window.localStorage.getItem(key))
      .find(Boolean);
  return normalizeAppLocale(stored || window.navigator?.language || DEFAULT_APP_LOCALE);
}

export function applyAppLocale(locale, { persist = true, announce = true } = {}) {
  const normalized = normalizeAppLocale(locale);
  const direction = RTL_APP_LOCALES.includes(normalized) ? 'rtl' : 'ltr';
  if (typeof document !== 'undefined') {
    document.documentElement.lang = normalized;
    document.documentElement.dir = direction;
    document.documentElement.classList.toggle('rtl', direction === 'rtl');
  }
  if (typeof window !== 'undefined') {
    if (persist) {
      window.localStorage.setItem(APP_LOCALE_STORAGE_KEY, normalized);
      LEGACY_APP_LOCALE_STORAGE_KEYS.forEach((key) => window.localStorage.removeItem(key));
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
