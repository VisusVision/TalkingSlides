import { beforeEach, describe, expect, it } from 'vitest';
import { apiErrorMessage } from './api';
import { applyAppLocale } from './i18n/locale';
import { translateAppMessage } from './i18n/messages';

describe('localized API error normalization', () => {
  beforeEach(() => {
    window.localStorage.clear();
    applyAppLocale('en');
  });

  it('maps invalid credentials to localized frontend copy', () => {
    expect(apiErrorMessage({ error: 'Invalid credentials' }, 'Login failed', 401))
      .toBe(translateAppMessage('en', 'apiErrorInvalidCredentials'));

    applyAppLocale('tr');
    expect(apiErrorMessage({ error: 'Invalid credentials' }, 'Login failed', 401))
      .toBe(translateAppMessage('tr', 'apiErrorInvalidCredentials'));
  });

  it('maps common permission, not-found, render, upload, playback, share, and validation failures', () => {
    expect(apiErrorMessage({ detail: 'Permission denied' }, 'Denied', 403))
      .toBe(translateAppMessage('en', 'apiErrorPermissionDenied'));
    expect(apiErrorMessage({ detail: 'Not found' }, 'Missing', 404))
      .toBe(translateAppMessage('en', 'apiErrorNotFound'));
    expect(apiErrorMessage({ error: 'Render already active' }, 'Rerender failed', 409))
      .toBe(translateAppMessage('en', 'apiErrorRenderAlreadyActive'));
    expect(apiErrorMessage({ error: 'Upload failed' }, 'Upload failed', 400))
      .toBe(translateAppMessage('en', 'apiErrorUploadFailed'));
    expect(apiErrorMessage({ error: 'Playback unavailable' }, 'Playback failed', 503))
      .toBe(translateAppMessage('en', 'apiErrorPlaybackUnavailable'));
    expect(apiErrorMessage({ code: 'share_expired' }, 'Share invalid', 400))
      .toBe(translateAppMessage('en', 'apiErrorShareExpired'));
    expect(apiErrorMessage({ code: 'share_revoked' }, 'Share invalid', 400))
      .toBe(translateAppMessage('en', 'apiErrorShareRevoked'));
    expect(apiErrorMessage({ detail: { title: ['This field is required.'] } }, 'Validation failed', 400))
      .toBe(translateAppMessage('en', 'apiErrorValidationFailed'));
  });

  it('does not expose technical stack details as user copy', () => {
    applyAppLocale('ar');
    expect(apiErrorMessage({ error: 'Traceback: SQL syntax error at line 1' }, 'Failed', 500))
      .toBe(translateAppMessage('ar', 'apiErrorUnexpected'));
  });
});
