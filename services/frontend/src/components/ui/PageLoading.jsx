import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useI18n } from '../../i18n/I18nProvider';

const PageLoadingContext = createContext({
  setPageLoading: () => {},
});

let sourceCounter = 0;

function useDelayedVisible(active, delayMs = 180) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!active) {
      setVisible(false);
      return undefined;
    }

    const timer = window.setTimeout(() => setVisible(true), delayMs);
    return () => window.clearTimeout(timer);
  }, [active, delayMs]);

  return visible;
}

function PageTransitionIndicator({ active }) {
  const visible = useDelayedVisible(active);
  if (!visible) return null;

  return (
    <div
      aria-hidden="true"
      className="pointer-events-none fixed inset-x-0 top-0 z-[80] h-1 overflow-hidden bg-[color:color-mix(in_srgb,var(--surface-container-high),transparent_35%)]"
    >
      <div className="visus-page-progress h-full w-1/3 rounded-full bg-[image:var(--accent-gradient)]" />
    </div>
  );
}

export function PageLoadingProvider({ children }) {
  const [sources, setSources] = useState({});

  const value = useMemo(() => ({
    setPageLoading: (sourceId, loading) => {
      setSources((current) => {
        const isLoading = Boolean(loading);
        if (isLoading && current[sourceId]) return current;
        if (!isLoading && !current[sourceId]) return current;

        const next = { ...current };
        if (isLoading) {
          next[sourceId] = true;
        } else {
          delete next[sourceId];
        }
        return next;
      });
    },
  }), []);

  const active = Object.keys(sources).length > 0;

  return (
    <PageLoadingContext.Provider value={value}>
      {children}
      <PageTransitionIndicator active={active} />
    </PageLoadingContext.Provider>
  );
}

export function usePageLoading(loading, sourceId = '') {
  const { setPageLoading } = useContext(PageLoadingContext);
  const sourceIdRef = useRef(sourceId);

  if (!sourceIdRef.current) {
    sourceCounter += 1;
    sourceIdRef.current = `page-loading-${sourceCounter}`;
  }

  useEffect(() => {
    const id = sourceIdRef.current;
    setPageLoading(id, loading);
    return () => setPageLoading(id, false);
  }, [loading, setPageLoading]);
}

export function RouteLoadingFallback() {
  const { t } = useI18n();
  usePageLoading(true, 'route-chunk');
  const visible = useDelayedVisible(true);

  if (!visible) return null;

  return (
    <div className="space-y-4 pt-4" role="status" aria-live="polite" aria-label={t('common.loading')}>
      <div className="rounded-3xl token-surface-elevated p-5">
        <div className="visus-loading-sheen h-4 w-36 rounded-full bg-[color:var(--surface-container-high)]" />
        <div className="visus-loading-sheen mt-4 h-8 max-w-md rounded-full bg-[color:var(--surface-container-high)]" />
        <div className="visus-loading-sheen mt-3 h-4 max-w-2xl rounded-full bg-[color:var(--surface-container-high)]" />
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        {Array.from({ length: 3 }, (_, index) => (
          <div key={`route-loading-${index}`} className="rounded-2xl token-surface p-4">
            <div className="visus-loading-sheen h-4 w-2/3 rounded-full bg-[color:var(--surface-container-high)]" />
            <div className="visus-loading-sheen mt-3 h-3 w-full rounded-full bg-[color:var(--surface-container-high)]" />
            <div className="visus-loading-sheen mt-2 h-3 w-4/5 rounded-full bg-[color:var(--surface-container-high)]" />
          </div>
        ))}
      </div>
    </div>
  );
}
