import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

export const NAVIGATION_STATE_STORAGE_KEY = 'visus-navigation-state-v1';

export const SECTION_DEFAULT_PATHS = {
  dashboard: '/',
  studio: '/studio',
  library: '/library',
  browse: '/browse',
  analytics: '/analytics',
  moderation: '/moderation',
  channel: '',
};

export function getSectionForPath(pathname = '/') {
  const path = pathname || '/';
  if (path === '/') return 'dashboard';
  if (path === '/studio') return 'studio';
  if (path === '/library' || path === '/my-lessons') return 'library';
  if (path === '/browse') return 'browse';
  if (path === '/analytics') return 'analytics';
  if (path === '/moderation') return 'moderation';
  if (path.startsWith('/channel/')) return 'channel';
  return null;
}

export function getSearchStateKeyForPath(pathname = '/') {
  const path = pathname || '/';
  if (path === '/') return 'dashboard';
  if (path === '/studio') return 'studio';
  if (path === '/library' || path === '/my-lessons') return 'library';
  if (path === '/browse') return 'browse';
  if (path === '/moderation') return 'moderation';
  if (path === '/watch') return 'watch';
  if (path.startsWith('/channel/')) return 'channel';
  return null;
}

function getSessionStorage() {
  if (typeof window === 'undefined') return null;
  try {
    return window.sessionStorage || null;
  } catch {
    return null;
  }
}

function normalizeStore(raw) {
  return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
}

export function readNavigationStore(storage = getSessionStorage()) {
  if (!storage) return {};
  try {
    return normalizeStore(JSON.parse(storage.getItem(NAVIGATION_STATE_STORAGE_KEY) || '{}'));
  } catch {
    return {};
  }
}

export function writeNavigationStore(store, storage = getSessionStorage()) {
  if (!storage) return;
  try {
    storage.setItem(NAVIGATION_STATE_STORAGE_KEY, JSON.stringify(normalizeStore(store)));
  } catch {
    // Session state is a convenience; navigation must still work if storage is full or blocked.
  }
}

function shallowEqual(left = {}, right = {}) {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) return false;
  return leftKeys.every((key) => left[key] === right[key]);
}

export function mergeSectionStateInStore(store, section, patchOrUpdater) {
  if (!section) return normalizeStore(store);
  const currentStore = normalizeStore(store);
  const currentEntry = currentStore[section] || {};
  const currentState = normalizeStore(currentEntry.state);
  const patch = typeof patchOrUpdater === 'function'
    ? patchOrUpdater(currentState)
    : patchOrUpdater;
  const nextState = {
    ...currentState,
    ...normalizeStore(patch),
  };

  if (shallowEqual(currentState, nextState)) return currentStore;

  return {
    ...currentStore,
    [section]: {
      ...currentEntry,
      state: nextState,
    },
  };
}

export function setSectionRouteInStore(store, section, route) {
  if (!section || !route?.pathname) return normalizeStore(store);
  const currentStore = normalizeStore(store);
  const currentEntry = currentStore[section] || {};
  const currentRoute = currentEntry.route || {};
  const nextRoute = {
    pathname: route.pathname,
    search: route.search || '',
    hash: route.hash || '',
  };

  if (shallowEqual(currentRoute, nextRoute)) return currentStore;

  return {
    ...currentStore,
    [section]: {
      ...currentEntry,
      route: nextRoute,
    },
  };
}

export function resetSectionInStore(store, section) {
  const currentStore = normalizeStore(store);
  if (!section || !Object.prototype.hasOwnProperty.call(currentStore, section)) {
    return currentStore;
  }
  const nextStore = { ...currentStore };
  delete nextStore[section];
  return nextStore;
}

function routeToPath(route) {
  if (!route?.pathname) return '';
  return `${route.pathname}${route.search || ''}${route.hash || ''}`;
}

const NavigationStateContext = createContext(null);

export function NavigationStateProvider({ children }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [store, setStore] = useState(() => readNavigationStore());
  const [resetCounters, setResetCounters] = useState({});
  const storeRef = useRef(store);

  useEffect(() => {
    storeRef.current = store;
  }, [store]);

  const commitStore = useCallback((updater) => {
    setStore((previous) => {
      const nextStore = typeof updater === 'function' ? updater(previous) : updater;
      const normalized = normalizeStore(nextStore);
      if (normalized === previous) return previous;
      writeNavigationStore(normalized);
      return normalized;
    });
  }, []);

  const updateSectionState = useCallback((section, patchOrUpdater) => {
    if (!section) return;
    commitStore((previous) => mergeSectionStateInStore(previous, section, patchOrUpdater));
  }, [commitStore]);

  const clearSectionState = useCallback((section) => {
    if (!section) return;
    commitStore((previous) => resetSectionInStore(previous, section));
    setResetCounters((previous) => ({
      ...previous,
      [section]: (previous[section] || 0) + 1,
    }));
  }, [commitStore]);

  useEffect(() => {
    const section = getSectionForPath(location.pathname);
    if (!section) return;
    commitStore((previous) => setSectionRouteInStore(previous, section, location));
  }, [commitStore, location.hash, location.pathname, location.search]);

  useEffect(() => {
    const section = getSectionForPath(location.pathname);
    if (!section) return;

    const routeState = location.state || {};
    const shouldResetScroll = routeState.resetSection === section;
    const shouldRestoreScroll = routeState.restoreSection === section;
    if (!shouldResetScroll && !shouldRestoreScroll) return;

    window.requestAnimationFrame(() => {
      if (shouldResetScroll) {
        window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
        return;
      }

      const y = Number(storeRef.current?.[section]?.state?.scrollY || 0);
      window.scrollTo({ top: Math.max(0, y), left: 0, behavior: 'auto' });
    });
  }, [location.key, location.pathname, location.state]);

  useEffect(() => {
    let timeoutId = null;

    const saveScrollPosition = () => {
      if (timeoutId !== null) return;
      timeoutId = window.setTimeout(() => {
        timeoutId = null;
        const section = getSectionForPath(window.location.pathname);
        if (!section) return;
        updateSectionState(section, { scrollY: Math.max(0, Math.round(window.scrollY || 0)) });
      }, 160);
    };

    window.addEventListener('scroll', saveScrollPosition, { passive: true });
    return () => {
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
      window.removeEventListener('scroll', saveScrollPosition);
    };
  }, [updateSectionState]);

  const currentSection = getSectionForPath(location.pathname);
  const currentSearchKey = getSearchStateKeyForPath(location.pathname);
  const searchQuery = currentSearchKey ? String(store?.[currentSearchKey]?.state?.search || '') : '';

  const setSearchQuery = useCallback((value) => {
    if (!currentSearchKey) return;
    updateSectionState(currentSearchKey, { search: String(value || '') });
  }, [currentSearchKey, updateSectionState]);

  const clearCurrentSearch = useCallback(() => {
    if (!currentSearchKey) return;
    updateSectionState(currentSearchKey, { search: '' });
  }, [currentSearchKey, updateSectionState]);

  const navigateToSection = useCallback((section, options = {}) => {
    if (!section) return;
    const defaultPath = SECTION_DEFAULT_PATHS[section] || '/';
    const reset = Boolean(options.reset);

    if (reset) {
      clearSectionState(section);
      navigate(defaultPath || '/', {
        state: { resetSection: section, resetAt: Date.now() },
      });
      return;
    }

    const storedRoute = storeRef.current?.[section]?.route;
    const target = routeToPath(storedRoute) || defaultPath || '/';
    navigate(target, {
      state: { restoreSection: section, restoredAt: Date.now() },
    });
  }, [clearSectionState, navigate]);

  const value = useMemo(() => ({
    store,
    resetCounters,
    currentSection,
    currentSearchKey,
    searchQuery,
    setSearchQuery,
    clearCurrentSearch,
    updateSectionState,
    clearSectionState,
    navigateToSection,
  }), [
    clearCurrentSearch,
    clearSectionState,
    currentSearchKey,
    currentSection,
    navigateToSection,
    resetCounters,
    searchQuery,
    setSearchQuery,
    store,
    updateSectionState,
  ]);

  return createElement(NavigationStateContext.Provider, { value }, children);
}

export function useNavigationState() {
  const context = useContext(NavigationStateContext);
  if (!context) {
    throw new Error('useNavigationState must be used inside NavigationStateProvider');
  }
  return context;
}

export function useSectionState(section, defaults = {}) {
  const { store, updateSectionState, clearSectionState } = useNavigationState();
  const storedState = section ? store?.[section]?.state || {} : {};
  const state = {
    ...defaults,
    ...storedState,
  };
  const setSectionState = useCallback((patchOrUpdater) => {
    if (!section) return;
    updateSectionState(section, (previous) => {
      if (typeof patchOrUpdater === 'function') {
        return patchOrUpdater({ ...defaults, ...previous });
      }
      return patchOrUpdater;
    });
  }, [defaults, section, updateSectionState]);
  const resetSectionState = useCallback(() => {
    if (section) clearSectionState(section);
  }, [clearSectionState, section]);

  return [state, setSectionState, resetSectionState];
}
