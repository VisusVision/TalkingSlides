import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import {
  APP_LOCALE_CHANGED_EVENT,
  applyAppLocale,
  readStoredAppLocale,
  SUPPORTED_APP_LOCALES,
} from './locale';

const LocaleContext = createContext(null);

export function LocaleProvider({ children }) {
  const [locale, setLocaleState] = useState(readStoredAppLocale);

  useEffect(() => {
    applyAppLocale(locale, { persist: true, announce: false });
  }, [locale]);

  useEffect(() => {
    const handleLocaleChange = (event) => {
      if (event.detail?.locale) {
        setLocaleState(event.detail.locale);
      }
    };
    window.addEventListener(APP_LOCALE_CHANGED_EVENT, handleLocaleChange);
    return () => window.removeEventListener(APP_LOCALE_CHANGED_EVENT, handleLocaleChange);
  }, []);

  const setLocale = useCallback((nextLocale) => {
    const normalized = applyAppLocale(nextLocale);
    setLocaleState(normalized);
  }, []);

  const value = useMemo(() => ({
    locale,
    setLocale,
    supportedLocales: SUPPORTED_APP_LOCALES,
  }), [locale, setLocale]);

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale() {
  const context = useContext(LocaleContext);
  if (!context) {
    throw new Error('useLocale must be used within LocaleProvider');
  }
  return context;
}
