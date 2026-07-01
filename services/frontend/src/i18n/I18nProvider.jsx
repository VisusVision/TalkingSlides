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
  languageOptions: LANGUAGE_OPTIONS,
  setLanguage: () => {},
  t: (key, params) => interpolate(readPath(translations[DEFAULT_LANGUAGE], key) || key, params),
});

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
    const t = (key, params = {}) => {
      const localized = readPath(translations[language], key);
      const fallback = readPath(translations[DEFAULT_LANGUAGE], key);
      return interpolate(localized ?? fallback ?? key, params);
    };

    return {
      language,
      languageOptions: LANGUAGE_OPTIONS,
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
