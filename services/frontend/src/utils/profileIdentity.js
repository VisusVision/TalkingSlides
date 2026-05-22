import { API_BASE_URL } from '../api';

export function displayNameFromUser(user) {
  const profileName = String(user?.profile?.display_name || user?.display_name || '').trim();
  if (profileName) return profileName;

  const firstName = String(user?.first_name || '').trim();
  const lastName = String(user?.last_name || '').trim();
  const fullName = [firstName, lastName].filter(Boolean).join(' ').trim();
  if (fullName) return fullName;

  const username = String(user?.username || '').trim();
  if (username) return username;

  const emailPrefix = String(user?.email || '').split('@')[0]?.trim();
  return emailPrefix || 'VISUS User';
}

export function initialsFromUser(user) {
  const explicit = String(user?.profile_initials || user?.initials || '').trim();
  if (explicit) return explicit.slice(0, 2).toUpperCase();
  const parts = displayNameFromUser(user).split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return `${parts[0][0] || ''}${parts[parts.length - 1][0] || ''}`.toUpperCase();
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return 'VU';
}

export function toAbsoluteMediaLikeUrl(url) {
  if (!url) return '';
  if (/^(https?:|data:|blob:)/i.test(url)) return url;
  const origin = API_BASE_URL.replace(/\/api\/v1\/?$/, '');
  return `${origin}${url.startsWith('/') ? url : `/${url}`}`;
}

export function profilePhotoFromUser(user, uploadedAvatarUrl = '') {
  const profilePhoto =
    uploadedAvatarUrl ||
    user?.profile_photo_url ||
    user?.profile?.logo_url ||
    user?.profile?.avatar_url ||
    user?.auth_picture_url ||
    user?.provider_picture ||
    user?.picture ||
    user?.photo_url ||
    user?.avatar_url ||
    user?.image_url ||
    user?.profile?.provider_avatar_url ||
    '';
  return toAbsoluteMediaLikeUrl(profilePhoto);
}
