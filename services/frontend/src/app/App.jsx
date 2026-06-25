import { useCallback, useEffect, useState } from 'react';
import { BrowserRouter, useLocation, useNavigate } from 'react-router-dom';
import {
  fetchCurrentUser,
  getStoredAuthUser,
  logout,
  setGoogleAuthProvider,
  setToken,
} from '../api';
import AppRouter from './router';
import AppShell from '../components/ui/AppShell';
import AuthModal from '../components/ui/AuthModal';
import RouteErrorBoundary from '../components/ui/RouteErrorBoundary';
import { ThemeProvider } from '../components/ui/ThemeProvider';
import SurfaceCard from '../components/ui/SurfaceCard';
import { PageLoadingProvider } from '../components/ui/PageLoading';
import { CapabilitiesProvider, useCapabilities } from '../lib/capabilities';
import { ROUTE_RESET_EVENT, readRouteSessionState, writeRouteSessionState } from '../utils/routeSession';

function getRedirectFromSearch(search) {
  const params = new URLSearchParams(search || '');
  const redirect = String(params.get('redirect') || '').trim();

  if (!redirect || !redirect.startsWith('/') || redirect.startsWith('//')) {
    return '';
  }
  return redirect;
}

export function searchScopeForPathname(pathname) {
  const path = String(pathname || '/').split('?')[0] || '/';
  const matches = (prefix) => path === prefix || path.startsWith(`${prefix}/`);
  if (matches('/moderation')) return 'moderation';
  if (matches('/studio')) return 'studio';
  if (matches('/analytics')) return 'analytics';
  if (matches('/browse')) return 'browse';
  if (matches('/watch')) return 'watch';
  if (matches('/library')) return 'library';
  if (matches('/my-lessons')) return 'my-lessons';
  if (path.startsWith('/channel/')) return `channel:${path}`;
  return 'home';
}

function scopedSearchValue(searchQueries, pathname) {
  const scope = searchScopeForPathname(pathname);
  return String(searchQueries?.[scope] || '');
}

function searchScopeForRouteReset(routeId) {
  if (routeId === 'dashboard') return 'home';
  return routeId || '';
}

function searchSessionUserKey(user) {
  return String(user?.id || user?.pk || user?.email || user?.username || 'anonymous');
}

function AppWithRouter() {
  const navigate = useNavigate();
  const location = useLocation();
  const { refreshCapabilities } = useCapabilities();

  const [searchQueries, setSearchQueries] = useState({});
  const [user, setUser] = useState(() => getStoredAuthUser());
  const [authLoading, setAuthLoading] = useState(() => !getStoredAuthUser());
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [pendingRedirect, setPendingRedirect] = useState('');
  const searchQuery = scopedSearchValue(searchQueries, location.pathname);
  const searchUserKey = searchSessionUserKey(user);
  const handleSearchQueryChange = useCallback((nextQuery) => {
    const scope = searchScopeForPathname(location.pathname);
    setSearchQueries((current) => ({
      ...current,
      [scope]: nextQuery,
    }));
  }, [location.pathname]);

  const refreshCurrentUser = useCallback(async () => {
    const currentUser = await fetchCurrentUser();
    setUser(currentUser);
    if (currentUser?.auth_provider) {
      setGoogleAuthProvider(currentUser.auth_provider);
    }
    return currentUser;
  }, []);

  useEffect(() => {
    const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ''));
    const redirectToken = String(hashParams.get('auth_token') || '').trim();
    const redirectProvider = String(hashParams.get('provider') || '').trim();

    if (redirectToken) {
      setToken(redirectToken);
      setGoogleAuthProvider(redirectProvider || 'google');
      window.history.replaceState({}, document.title, window.location.pathname + window.location.search);
    }

    let active = true;

    fetchCurrentUser()
      .then((currentUser) => {
        if (!active) return;
        setUser(currentUser);
        if (currentUser?.auth_provider) {
          setGoogleAuthProvider(currentUser.auth_provider);
        }
      })
      .finally(() => {
        if (active) {
          setAuthLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const stored = readRouteSessionState('route-search', user);
    const storedQueries = stored?.queries && typeof stored.queries === 'object' ? stored.queries : {};
    setSearchQueries((current) => (
      Object.keys(current).length > 0 ? { ...storedQueries, ...current } : storedQueries
    ));
  }, [searchUserKey]);

  useEffect(() => {
    writeRouteSessionState('route-search', user, { queries: searchQueries });
  }, [searchQueries, searchUserKey, user]);

  useEffect(() => {
    const handleRouteReset = (event) => {
      const scope = searchScopeForRouteReset(event.detail?.routeId);
      if (!scope) return;
      setSearchQueries((current) => {
        if (!Object.prototype.hasOwnProperty.call(current, scope)) return current;
        const next = { ...current };
        delete next[scope];
        return next;
      });
    };
    window.addEventListener(ROUTE_RESET_EVENT, handleRouteReset);
    return () => window.removeEventListener(ROUTE_RESET_EVENT, handleRouteReset);
  }, []);

  const clearRedirectQuery = useCallback(() => {
    const params = new URLSearchParams(location.search);
    if (!params.has('redirect')) return;

    params.delete('redirect');
    const nextSearch = params.toString();
    navigate(
      {
        pathname: location.pathname,
        search: nextSearch ? `?${nextSearch}` : '',
      },
      { replace: true },
    );
  }, [location.pathname, location.search, navigate]);

  const handleLoginRequest = useCallback((redirectTo = '') => {
    if (redirectTo) {
      setPendingRedirect(redirectTo);
    } else {
      const redirectFromQuery = getRedirectFromSearch(location.search);
      if (redirectFromQuery) {
        setPendingRedirect(redirectFromQuery);
      }
    }
    setAuthModalOpen(true);
  }, [location.search]);

  const handleLogout = async () => {
    await logout();
    await refreshCapabilities({ force: true });
    setUser(null);

    if (['/studio', '/analytics', '/moderation', '/library', '/my-lessons', '/history'].includes(location.pathname)) {
      navigate('/', { replace: true });
    }
  };

  const handleAuthModalClose = useCallback(() => {
    setAuthModalOpen(false);
    setPendingRedirect('');
    clearRedirectQuery();
  }, [clearRedirectQuery]);

  const handleLoginSuccess = useCallback((nextUser) => {
    setUser(nextUser);
    setAuthModalOpen(false);
    void refreshCapabilities({ force: true });

    const redirectTarget = pendingRedirect || getRedirectFromSearch(location.search);
    setPendingRedirect('');

    if (redirectTarget) {
      navigate(redirectTarget, { replace: true });
      return;
    }

    clearRedirectQuery();
  }, [clearRedirectQuery, location.search, navigate, pendingRedirect, refreshCapabilities]);

  return (
    <>
      <AppShell
        searchQuery={searchQuery}
        onSearchQueryChange={handleSearchQueryChange}
        user={user}
        authLoading={authLoading}
        onLoginRequest={handleLoginRequest}
        onLogout={handleLogout}
      >
        {authLoading ? (
          <SurfaceCard elevated className="mx-auto mt-8 max-w-xl text-center">
            <p className="label-sm">Loading Session</p>
            <p className="body-md mt-2">Syncing your profile and theme preferences...</p>
          </SurfaceCard>
        ) : (
          <RouteErrorBoundary resetKey={`${location.pathname}${location.search}`}>
            <AppRouter
              user={user}
              searchQuery={searchQuery}
              onLoginRequest={handleLoginRequest}
              onUserRefresh={refreshCurrentUser}
            />
          </RouteErrorBoundary>
        )}
      </AppShell>

      <AuthModal
        open={authModalOpen}
        onClose={handleAuthModalClose}
        onLoginSuccess={handleLoginSuccess}
      />
    </>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <CapabilitiesProvider>
        <BrowserRouter>
          <PageLoadingProvider>
            <AppWithRouter />
          </PageLoadingProvider>
        </BrowserRouter>
      </CapabilitiesProvider>
    </ThemeProvider>
  );
}
