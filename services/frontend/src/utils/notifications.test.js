import { describe, expect, it } from 'vitest';
import { isSafeNotificationActionUrl } from './notifications';

describe('notification navigation boundary', () => {
  it.each(['//redirect.invalid', '/\\redirect.invalid', '/\t/redirect.invalid', '/%2fredirect.invalid', '/%255credirect.invalid', '/a/..//redirect.invalid', 'javascript:alert(1)'])('rejects %j before notification navigation', (url) => {
    expect(isSafeNotificationActionUrl(url)).toBe(false);
  });
  it('retains ordinary notification destinations and encoded search data', () => {
    expect(isSafeNotificationActionUrl('/studio?lesson=42&view=editor')).toBe(true);
    expect(isSafeNotificationActionUrl('/library?q=hello%20world')).toBe(true);
  });
});
