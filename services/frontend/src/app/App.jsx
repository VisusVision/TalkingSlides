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
import { ThemeProvider } from '../components/ui/ThemeProvider';
import SurfaceCard from '../components/ui/SurfaceCard';
import { CapabilitiesProvider, useCapabilities } from '../lib/capabilities';

function getRedirectFromSearch(search) {
  const params = new URLSearchParams(search || '');
  const redirect = String(params.get('redirect') || '').trim();

  if (!redirect || !redirect.startsWith('/') || redirect.startsWith('//')) {
    return '';
  }
  return redirect;
}

function AppWithRouter() {
  const navigate = useNavigate();
  const location = useLocation();
  const { refreshCapabilities } = useCapabilities();

  const [searchQuery, setSearchQuery] = useState('');
  const [user, setUser] = useState(() => getStoredAuthUser());
  const [authLoading, setAuthLoading] = useState(() => !getStoredAuthUser());
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [pendingRedirect, setPendingRedirect] = useState('');

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
        onSearchQueryChange={setSearchQuery}
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
          <AppRouter
            user={user}
            searchQuery={searchQuery}
            onLoginRequest={handleLoginRequest}
            onUserRefresh={refreshCurrentUser}
          />
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
          <AppWithRouter />
        </BrowserRouter>
      </CapabilitiesProvider>
    </ThemeProvider>
  );
}
