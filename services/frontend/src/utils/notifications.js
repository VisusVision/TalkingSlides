export function notificationResults(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.results)) return payload.results;
  return [];
}

export const NOTIFICATIONS_CHANGED_EVENT = 'visus:notifications-changed';

export function notifyNotificationsChanged() {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED_EVENT));
}

export function formatNotificationTime(value) {
  if (!value) return '';
  const created = new Date(value);
  if (Number.isNaN(created.getTime())) return '';
  const seconds = Math.max(0, Math.floor((Date.now() - created.getTime()) / 1000));
  if (seconds < 60) return 'Just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return created.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function isSafeNotificationActionUrl(value) {
  const url = String(value || '').trim();
  return Boolean(url && url.startsWith('/') && !url.startsWith('//'));
}

export function notificationPageInfo(payload, fallback = {}) {
  const results = notificationResults(payload);
  const limit = Number(payload?.limit || fallback.limit || results.length || 0);
  const offset = Number(payload?.offset || fallback.offset || 0);
  const nextOffset = payload?.next_offset === null || payload?.next_offset === undefined
    ? null
    : Number(payload.next_offset);
  return {
    results,
    count: Number(payload?.count ?? results.length),
    limit,
    offset,
    hasMore: Boolean(payload?.has_more),
    nextOffset,
  };
}
