const CREATOR_ALLOWED_ROLES = new Set(['teacher', 'publisher']);

export function normalizeUserRole(user) {
  return String(user?.profile?.role || user?.role || '').trim().toLowerCase();
}

export function isStaffOrAdmin(user) {
  return Boolean(user?.is_staff || user?.is_superuser);
}

export function canAccessStudio(user) {
  if (!user) return false;
  if (isStaffOrAdmin(user)) {
    return true;
  }
  return CREATOR_ALLOWED_ROLES.has(normalizeUserRole(user));
}

export function canAccessAnalytics(user) {
  return canAccessStudio(user);
}

export function canAccessModeration(user) {
  return isStaffOrAdmin(user);
}

export function isSignedIn(user) {
  return Boolean(user && user.id);
}
