import { Navigate, Route, Routes } from 'react-router-dom';
import Home from '../pages/Home';
import Watch from '../pages/Watch';
import Studio from '../pages/Studio';
import Browse from '../pages/Browse';
import Library from '../pages/Library';
import Analytics from '../pages/Analytics';
import Settings from '../pages/Settings';
import ProtectedRoute from './ProtectedRoute';

export default function AppRouter({
  user,
  searchQuery,
  onLoginRequest,
  onUserRefresh,
}) {
  return (
    <Routes>
      <Route path="/" element={<Home searchQuery={searchQuery} user={user} />} />
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
            <Studio user={user} onLoginRequest={onLoginRequest} />
          </ProtectedRoute>
        )}
      />
      <Route path="/browse" element={<Browse searchQuery={searchQuery} />} />
      <Route
        path="/analytics"
        element={(
          <ProtectedRoute user={user} onLoginRequest={onLoginRequest}>
            <Analytics user={user} />
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
      <Route path="/settings" element={<Settings user={user} onUserRefresh={onUserRefresh} />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
