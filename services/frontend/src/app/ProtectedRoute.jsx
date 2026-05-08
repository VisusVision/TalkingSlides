import { useEffect } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { getToken } from '../api';
import { canAccessAnalytics, canAccessModeration, canAccessStudio } from '../lib/auth';

export default function ProtectedRoute({
  user,
  onLoginRequest,
  children,
  requireStudioRole = false,
  requireAnalyticsRole = false,
  requireStaffRole = false,
  redirectUnauthorizedTo = '/',
}) {
  const location = useLocation();
  const hasToken = Boolean(getToken());
  const isAuthenticated = Boolean(user || hasToken);
  let isAuthorized = true;
  let forbidden = '';
  if (requireStaffRole) {
    isAuthorized = canAccessModeration(user);
    forbidden = 'moderation';
  } else if (requireAnalyticsRole) {
    isAuthorized = user ? canAccessAnalytics(user) : hasToken;
    forbidden = 'analytics';
  } else if (requireStudioRole) {
    isAuthorized = user ? canAccessStudio(user) : hasToken;
    forbidden = 'studio';
  }

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
    return <Navigate to={redirectUnauthorizedTo} replace state={{ forbidden }} />;
  }

  return children;
}
