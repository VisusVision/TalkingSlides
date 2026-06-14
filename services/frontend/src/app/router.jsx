import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import ProtectedRoute from './ProtectedRoute';
import { RouteLoadingFallback } from '../components/ui/PageLoading';

const Home = lazy(() => import('../pages/Home'));
const Watch = lazy(() => import('../pages/Watch'));
const Studio = lazy(() => import('../pages/Studio'));
const Browse = lazy(() => import('../pages/Browse'));
const Channel = lazy(() => import('../pages/Channel'));
const Playlist = lazy(() => import('../pages/Playlist'));
const Library = lazy(() => import('../pages/Library'));
const History = lazy(() => import('../pages/History'));
const Notifications = lazy(() => import('../pages/Notifications'));
const Help = lazy(() => import('../pages/Help'));
const Analytics = lazy(() => import('../pages/Analytics'));
const ModerationDashboard = lazy(() => import('../pages/ModerationDashboard'));
const Settings = lazy(() => import('../pages/Settings'));

export default function AppRouter({
  user,
  searchQuery,
  onLoginRequest,
  onUserRefresh,
}) {
  return (
    <Suspense fallback={<RouteLoadingFallback />}>
      <Routes>
        <Route path="/" element={<Home searchQuery={searchQuery} user={user} onLoginRequest={onLoginRequest} />} />
        <Route path="/watch" element={<Watch searchQuery={searchQuery} user={user} onLoginRequest={onLoginRequest} />} />
        <Route
          path="/studio"
          element={(
            <ProtectedRoute
              user={user}
              onLoginRequest={onLoginRequest}
              requireStudioRole
              redirectUnauthorizedTo="/"
            >
              <Studio user={user} searchQuery={searchQuery} onLoginRequest={onLoginRequest} />
            </ProtectedRoute>
          )}
        />
        <Route path="/browse" element={<Browse searchQuery={searchQuery} user={user} onLoginRequest={onLoginRequest} />} />
        <Route
          path="/channel/:userId"
          element={(
            <Channel
              user={user}
              searchQuery={searchQuery}
              onLoginRequest={onLoginRequest}
              onUserRefresh={onUserRefresh}
            />
          )}
        />
        <Route path="/playlist/:playlistId" element={<Playlist user={user} onLoginRequest={onLoginRequest} />} />
        <Route
          path="/analytics"
          element={(
            <ProtectedRoute
              user={user}
              onLoginRequest={onLoginRequest}
              requireAnalyticsRole
              redirectUnauthorizedTo="/"
            >
              <Analytics user={user} />
            </ProtectedRoute>
          )}
        />
        <Route
          path="/history"
          element={(
            <ProtectedRoute user={user} onLoginRequest={onLoginRequest}>
              <History user={user} />
            </ProtectedRoute>
          )}
        />
        <Route
          path="/notifications"
          element={(
            <ProtectedRoute user={user} onLoginRequest={onLoginRequest}>
              <Notifications user={user} />
            </ProtectedRoute>
          )}
        />
        <Route
          path="/library"
          element={(
            <ProtectedRoute user={user} onLoginRequest={onLoginRequest}>
              <Library user={user} searchQuery={searchQuery} onLoginRequest={onLoginRequest} />
            </ProtectedRoute>
          )}
        />
        <Route
          path="/my-lessons"
          element={(
            <ProtectedRoute user={user} onLoginRequest={onLoginRequest}>
              <Library user={user} searchQuery={searchQuery} onLoginRequest={onLoginRequest} />
            </ProtectedRoute>
          )}
        />
        <Route
          path="/moderation"
          element={(
            <ProtectedRoute
              user={user}
              onLoginRequest={onLoginRequest}
              requireStaffRole
              redirectUnauthorizedTo="/"
            >
              <ModerationDashboard user={user} searchQuery={searchQuery} />
            </ProtectedRoute>
          )}
        />
        <Route path="/settings" element={<Settings user={user} onUserRefresh={onUserRefresh} />} />
        <Route path="/help" element={<Help />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
