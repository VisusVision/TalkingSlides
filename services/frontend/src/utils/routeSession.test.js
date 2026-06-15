import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  readRouteSessionState,
  requestRouteReset,
  routeIdForPath,
  writeRouteSessionState,
} from './routeSession';

describe('route session state', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('stores state per route and clears it when reset is requested', () => {
    const user = { id: 42 };
    const listener = vi.fn();
    window.addEventListener('visus:route-reset', listener);

    writeRouteSessionState('studio', user, { selectedLessonId: 33 });
    expect(readRouteSessionState('studio', user).selectedLessonId).toBe(33);

    requestRouteReset('studio', user);

    expect(readRouteSessionState('studio', user)).toEqual({});
    expect(listener).toHaveBeenCalledWith(expect.objectContaining({
      detail: { routeId: 'studio' },
    }));

    window.removeEventListener('visus:route-reset', listener);
  });

  it('maps primary app paths to route ids', () => {
    expect(routeIdForPath('/')).toBe('dashboard');
    expect(routeIdForPath('/studio?view=editor')).toBe('studio');
    expect(routeIdForPath('/moderation')).toBe('moderation');
    expect(routeIdForPath('/settings')).toBe('settings');
  });

  it('drops malformed stored state instead of throwing during route restore', () => {
    const key = 'visus-route-state:studio:42';
    window.sessionStorage.setItem(key, '{bad json');

    expect(readRouteSessionState('studio', { id: 42 })).toEqual({});
    expect(window.sessionStorage.getItem(key)).toBeNull();
  });
});
