import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import {
  DEFAULT_LANGUAGE,
  LANGUAGE_OPTIONS,
  LANGUAGE_STORAGE_KEY,
  SUPPORTED_LANGUAGES,
  translations,
} from './translations';

function normalizeLanguage(value) {
  const code = String(value || '').trim().toLowerCase().split('-')[0];
  return SUPPORTED_LANGUAGES.includes(code) ? code : '';
}

export function resolveInitialLanguage() {
  if (typeof window !== 'undefined') {
    const stored = normalizeLanguage(window.localStorage.getItem(LANGUAGE_STORAGE_KEY));
    if (stored) return stored;
  }

  const browserLanguages = typeof navigator !== 'undefined'
    ? (Array.isArray(navigator.languages) && navigator.languages.length ? navigator.languages : [navigator.language])
    : [];
  for (const language of browserLanguages) {
    const normalized = normalizeLanguage(language);
    if (normalized) return normalized;
  }

  return DEFAULT_LANGUAGE;
}

function readPath(source, path) {
  return String(path || '').split('.').reduce((current, part) => {
    if (!current || typeof current !== 'object') return undefined;
    return current[part];
  }, source);
}

function interpolate(template, params = {}) {
  return String(template).replace(/\{\{\s*(\w+)\s*\}\}/g, (_, key) => (
    Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : ''
  ));
}

export const I18nContext = createContext({
  language: DEFAULT_LANGUAGE,
  locale: 'en-US',
  languageOptions: LANGUAGE_OPTIONS,
  formatDate: () => '',
  formatDateTime: () => '',
  formatDuration: () => '',
  formatNumber: (value) => String(value ?? ''),
  formatViews: () => '',
  setLanguage: () => {},
  t: (key, params) => interpolate(readPath(translations[DEFAULT_LANGUAGE], key) || key, params),
});

const LOCALES = {
  en: 'en-US',
  tr: 'tr-TR',
};

function parseDate(value) {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function I18nProvider({ children }) {
  const [language, setLanguageState] = useState(resolveInitialLanguage);

  const setLanguage = (nextLanguage) => {
    const normalized = normalizeLanguage(nextLanguage) || DEFAULT_LANGUAGE;
    setLanguageState(normalized);
  };

  useEffect(() => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
    document.documentElement.lang = language;
  }, [language]);

  const value = useMemo(() => {
    const locale = LOCALES[language] || LOCALES[DEFAULT_LANGUAGE];

    const t = (key, params = {}) => {
      const localized = readPath(translations[language], key);
      const fallback = readPath(translations[DEFAULT_LANGUAGE], key);
      return interpolate(localized ?? fallback ?? key, params);
    };

    const formatNumber = (value, options = {}) => {
      const numeric = Number(value);
      return new Intl.NumberFormat(locale, options).format(Number.isFinite(numeric) ? numeric : 0);
    };

    const formatDate = (value, options = {}) => {
      const date = parseDate(value);
      if (!date) return '';
      return new Intl.DateTimeFormat(locale, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        ...options,
      }).format(date);
    };

    const formatDateTime = (value, options = {}) => {
      const date = parseDate(value);
      if (!date) return '';
      return new Intl.DateTimeFormat(locale, {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        ...options,
      }).format(date);
    };

    const formatDuration = (minutes) => {
      const totalMinutes = Math.max(1, Number(minutes || 0));
      if (totalMinutes >= 60) {
        const hours = totalMinutes / 60;
        return t('common.durationHours', {
          value: formatNumber(hours, {
            maximumFractionDigits: hours % 1 === 0 ? 0 : 1,
          }),
        });
      }
      return t('common.durationMinutes', {
        value: formatNumber(Math.round(totalMinutes), { maximumFractionDigits: 0 }),
      });
    };

    const formatViews = (value) => t('common.viewsCount', {
      count: formatNumber(Math.max(0, Number(value || 0))),
    });

    return {
      language,
      locale,
      languageOptions: LANGUAGE_OPTIONS,
      formatDate,
      formatDateTime,
      formatDuration,
      formatNumber,
      formatViews,
      setLanguage,
      t,
    };
  }, [language]);

  return (
    <I18nContext.Provider value={value}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  return useContext(I18nContext);
}
