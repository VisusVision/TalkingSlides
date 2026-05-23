import { Navigate, Route, Routes } from 'react-router-dom';
import Home from '../pages/Home';
import Watch from '../pages/Watch';
import Studio from '../pages/Studio';
import Browse from '../pages/Browse';
import Channel from '../pages/Channel';
import Playlist from '../pages/Playlist';
import Library from '../pages/Library';
import History from '../pages/History';
import Notifications from '../pages/Notifications';
import Help from '../pages/Help';
import Analytics from '../pages/Analytics';
import ModerationDashboard from '../pages/ModerationDashboard';
import Settings from '../pages/Settings';
import ProtectedRoute from './ProtectedRoute';
import { useNavigationState } from './navigationState';

export default function AppRouter({
  user,
  onLoginRequest,
  onUserRefresh,
}) {
  const { resetCounters } = useNavigationState();
  const routeKey = (section) => `${section}-${resetCounters[section] || 0}`;

  return (
    <Routes>
      <Route path="/" element={<Home key={routeKey('dashboard')} user={user} onLoginRequest={onLoginRequest} />} />
      <Route path="/watch" element={<Watch user={user} onLoginRequest={onLoginRequest} />} />
      <Route
        path="/studio"
        element={(
          <ProtectedRoute
            user={user}
            onLoginRequest={onLoginRequest}
            requireStudioRole
            redirectUnauthorizedTo="/"
          >
            <Studio key={routeKey('studio')} user={user} onLoginRequest={onLoginRequest} />
          </ProtectedRoute>
        )}
      />
      <Route path="/browse" element={<Browse key={routeKey('browse')} user={user} onLoginRequest={onLoginRequest} />} />
      <Route
        path="/channel/:userId"
        element={(
          <Channel
            key={routeKey('channel')}
            user={user}
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
            <Analytics key={routeKey('analytics')} user={user} />
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
            <Library key={routeKey('library')} user={user} onLoginRequest={onLoginRequest} />
          </ProtectedRoute>
        )}
      />
      <Route
        path="/my-lessons"
        element={(
          <ProtectedRoute user={user} onLoginRequest={onLoginRequest}>
            <Library key={routeKey('library')} user={user} onLoginRequest={onLoginRequest} />
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
            <ModerationDashboard key={routeKey('moderation')} user={user} />
          </ProtectedRoute>
        )}
      />
      <Route path="/settings" element={<Settings user={user} onUserRefresh={onUserRefresh} />} />
      <Route path="/help" element={<Help />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
