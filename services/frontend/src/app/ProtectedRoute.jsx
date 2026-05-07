import { useEffect } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { getToken } from '../api';
import { canAccessStudio } from '../lib/auth';

export default function ProtectedRoute({
  user,
  onLoginRequest,
  children,
  requireStudioRole = false,
  redirectUnauthorizedTo = '/',
}) {
  const location = useLocation();
  const hasToken = Boolean(getToken());
  const isAuthenticated = Boolean(user || hasToken);
  const isAuthorized = requireStudioRole
    ? user
      ? canAccessStudio(user)
      : hasToken
    : true;

  useEffect(() => {
    if (isAuthenticated) return;
    if (typeof onLoginRequest === 'function') {
      onLoginRequest(`${location.pathname}${location.search}`);
    }
  }, [isAuthenticated, location.pathname, location.search, onLoginRequest]);

  if (!isAuthenticated) {
    const redirectParam = encodeURIComponent(`${location.pathname}${location.search}`);
    return <Navigate to={`/?redirect=${redirectParam}`} replace />;
  }

  if (!isAuthorized) {
    return <Navigate to={redirectUnauthorizedTo} replace state={{ forbidden: 'studio' }} />;
  }

  return children;
}
