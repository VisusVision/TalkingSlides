export const ROUTE_RESET_EVENT = 'visus:route-reset';

export function routeUserScope(user) {
  const raw = String(user?.id || user?.pk || user?.email || user?.username || 'anonymous').trim();
  return encodeURIComponent(raw || 'anonymous');
}

export function routeSessionKey(routeId, user) {
  const route = String(routeId || '').trim();
  if (!route) return '';
  return `visus-route-state:${route}:${routeUserScope(user)}`;
}

export function readRouteSessionState(routeId, user) {
  const key = routeSessionKey(routeId, user);
  if (!key || typeof window === 'undefined') return {};
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(key) || '{}');
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    window.sessionStorage.removeItem(key);
    return {};
  }
}

export function writeRouteSessionState(routeId, user, patch) {
  const key = routeSessionKey(routeId, user);
  if (!key || typeof window === 'undefined') return;
  try {
    const previous = readRouteSessionState(routeId, user);
    window.sessionStorage.setItem(key, JSON.stringify({
      ...previous,
      ...(patch && typeof patch === 'object' ? patch : {}),
      updatedAt: Date.now(),
    }));
  } catch {
    // Best-effort only; private browsing and quota limits should not break navigation.
  }
}

export function clearRouteSessionState(routeId, user) {
  const key = routeSessionKey(routeId, user);
  if (!key || typeof window === 'undefined') return;
  window.sessionStorage.removeItem(key);
}

export function routeIdForPath(pathname) {
  const path = String(pathname || '/').split('?')[0] || '/';
  if (path === '/') return 'dashboard';
  if (path === '/studio' || path.startsWith('/studio/')) return 'studio';
  if (path === '/analytics' || path.startsWith('/analytics/')) return 'analytics';
  if (path === '/moderation' || path.startsWith('/moderation/')) return 'moderation';
  if (path === '/browse' || path.startsWith('/browse/')) return 'browse';
  if (path === '/settings' || path.startsWith('/settings/')) return 'settings';
  if (path === '/library' || path.startsWith('/library/')) return 'library';
  if (path === '/my-lessons' || path.startsWith('/my-lessons/')) return 'my-lessons';
  return '';
}

export function requestRouteReset(routeId, user) {
  const route = String(routeId || '').trim();
  if (!route || typeof window === 'undefined') return;
  clearRouteSessionState(route, user);
  window.dispatchEvent(new CustomEvent(ROUTE_RESET_EVENT, { detail: { routeId: route } }));
}

export function onRouteReset(routeId, callback) {
  if (typeof window === 'undefined' || typeof callback !== 'function') return () => {};
  const route = String(routeId || '').trim();
  const handler = (event) => {
    if (!route || event.detail?.routeId === route) {
      callback(event.detail || {});
    }
  };
  window.addEventListener(ROUTE_RESET_EVENT, handler);
  return () => window.removeEventListener(ROUTE_RESET_EVENT, handler);
}

export function safeInternalReturnTo(value, fallback = '') {
  const raw = String(value || '').trim();
  const fallbackValue = String(fallback || '').trim();
  if (!raw || !raw.startsWith('/') || raw.startsWith('//')) return fallbackValue;
  return raw;
}
