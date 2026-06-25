export const AUTOPLAY_NEXT_KEY = 'visus-watch-autoplay-next';

export function isAutoplayNextEnabled() {
  if (typeof window === 'undefined') return true;
  return window.localStorage.getItem(AUTOPLAY_NEXT_KEY) !== '0';
}

export function setAutoplayNextEnabled(enabled) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(AUTOPLAY_NEXT_KEY, enabled ? '1' : '0');
}
