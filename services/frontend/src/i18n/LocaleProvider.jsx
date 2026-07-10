import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import {
  APP_LOCALE_CHANGED_EVENT,
  applyAppLocale,
  normalizeAppLocale,
  readStoredAppLocale,
  SUPPORTED_APP_LOCALES,
} from './locale';
import { canonicalizeStaticUiText, localizeStaticUiText, translateAppMessage } from './messages';

const LocaleContext = createContext(null);
const staticTextOriginals = new WeakMap();
const staticAttributeOriginals = new WeakMap();
const LOCALIZED_ATTRIBUTES = ['aria-label', 'title', 'placeholder', 'alt'];

function scheduleStaticTextLocalization(locale) {
  const run = () => localizeDocumentStaticText(locale);
  if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
    window.requestAnimationFrame(run);
  } else {
    run();
  }
}

function localizeDocumentStaticText(locale) {
  if (import.meta.env.MODE === 'test') return;
  if (typeof document === 'undefined' || !document.body) return;

  const shouldSkipElement = (element) => (
    element.closest?.('[data-i18n-skip], [data-user-content]')
    || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(element.tagName)
  );

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent || shouldSkipElement(parent)) return NodeFilter.FILTER_REJECT;
      return node.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
    },
  });

  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);

  textNodes.forEach((node) => {
    const original = staticTextOriginals.get(node) || canonicalizeStaticUiText(node.nodeValue);
    staticTextOriginals.set(node, original);
    const nextValue = locale === 'en' ? original : localizeStaticUiText(locale, original);
    if (node.nodeValue !== nextValue) node.nodeValue = nextValue;
  });

  document.body.querySelectorAll(LOCALIZED_ATTRIBUTES.map((attr) => `[${attr}]`).join(',')).forEach((element) => {
    if (shouldSkipElement(element)) return;
    const originals = staticAttributeOriginals.get(element) || {};
    LOCALIZED_ATTRIBUTES.forEach((attr) => {
      if (!element.hasAttribute(attr)) return;
      const original = originals[attr] || canonicalizeStaticUiText(element.getAttribute(attr));
      originals[attr] = original;
      const nextValue = locale === 'en' ? original : localizeStaticUiText(locale, original);
      if (element.getAttribute(attr) !== nextValue) element.setAttribute(attr, nextValue);
    });
    staticAttributeOriginals.set(element, originals);
  });
}

export function LocaleProvider({ children }) {
  const [locale, setLocaleState] = useState(readStoredAppLocale);

  useEffect(() => {
    applyAppLocale(locale, { persist: true, announce: false });
    scheduleStaticTextLocalization(locale);
  }, [locale]);

  useEffect(() => {
    if (import.meta.env.MODE === 'test') return undefined;
    if (typeof MutationObserver === 'undefined') return undefined;
    const observer = new MutationObserver(() => {
      scheduleStaticTextLocalization(locale);
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: LOCALIZED_ATTRIBUTES,
    });
    return () => observer.disconnect();
  }, [locale]);

  useEffect(() => {
    const handleLocaleChange = (event) => {
      if (event.detail?.locale) {
        setLocaleState(normalizeAppLocale(event.detail.locale));
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
    t: (key, params) => translateAppMessage(locale, key, params),
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
