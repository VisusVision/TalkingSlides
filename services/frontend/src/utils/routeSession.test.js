import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  readRouteSessionState,
  requestRouteReset,
  routeIdForPath,
  safeInternalReturnTo,
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

describe('internal navigation boundary', () => {
  const unsafe = [
    '//redirect.invalid', '///redirect.invalid', '/\\redirect.invalid', '/\t/redirect.invalid',
    '/\r/redirect.invalid', '/\n/redirect.invalid', '\\redirect.invalid',
    'https://redirect.invalid', 'http://redirect.invalid', 'javascript:alert(1)',
    'data:text/html,hello', 'moderation', '?q=1', '#section', ' /moderation', '/moderation ',
    '/%2fredirect.invalid', '/%2F%2Fredirect.invalid', '/%5credirect.invalid',
    '/%255credirect.invalid', '/%25252fredirect.invalid', '/%09/redirect.invalid',
    '/%00path', '/%7fpath', '/%C2%85path', '/%E2%80%A8path',
    '/./moderation', '/a/../moderation', '/a/%2e%2e//redirect.invalid',
    '/a/.%2e/moderation', '/a/%2E/moderation', '/%23fragment', '/%3fquery',
    '/%EF%BC%8Fredirect.invalid', '/％2fredirect.invalid/..',
    '/bad%', '/bad%2', '/bad%GG', '/%FF', '/x?q=%', '/x?q=%0ahello',
    '/x#%00', '/'.repeat(4097), null, undefined, {}, ['/moderation'], 42,
  ];

  it.each(unsafe)('rejects unsafe target %j with a safe fallback', (value) => {
    expect(safeInternalReturnTo(value, '/moderation')).toBe('/moderation');
    expect(safeInternalReturnTo(value)).toBe('');
    // Fallback is the same boundary, not a trusted bypass.
    expect(safeInternalReturnTo('', value)).toBe('');
  });

  it.each([
    '/', '/moderation', '/moderation?tab=reports&status=pending#report-42',
    '/watch?lesson=501&resume=1', '/studio?view=editor&lesson=501',
    '/library?q=hello%20world&next=https%3A%2F%2Fexample.invalid',
    '/channel/42', '/share/token-123', '/ders/çalışma', '/ders/%C3%A7al%C4%B1%C5%9Fma',
    '/chapter%20one', '/file%2Epdf', '/library?q=100%25&tags=a%2Fb#saved',
  ])('preserves legitimate target exactly: %s', (value) => {
    expect(safeInternalReturnTo(value, '//redirect.invalid')).toBe(value);
    expect(new URL(value, window.location.origin).origin).toBe(window.location.origin);
  });

  it('rejects separators after the query parser has decoded the outer returnTo layer', () => {
    for (const value of ['%2F%5Credirect.invalid', '%2F%252fredirect.invalid', '%2F%2509%2Fredirect.invalid']) {
      const params = new URLSearchParams(`returnTo=${value}`);
      expect(safeInternalReturnTo(params.get('returnTo'), '/moderation')).toBe('/moderation');
    }
  });
});
