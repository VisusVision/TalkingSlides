import { describe, expect, it } from 'vitest';
import { checkI18nCoverage } from '../../scripts/check-i18n-coverage.mjs';
import { APP_MESSAGES, STATIC_UI_MESSAGES, translateAppMessage } from './messages';
import { SUPPORTED_APP_LOCALES } from './locale';

describe('frontend i18n coverage', () => {
  it('keeps every supported locale in parity with English', () => {
    expect(checkI18nCoverage()).toEqual([]);
  });

  it('renders app shell labels differently for every non-English locale', () => {
    for (const { code } of SUPPORTED_APP_LOCALES) {
      if (code === 'en') continue;
      expect(translateAppMessage(code, 'dashboardLabel')).not.toBe(APP_MESSAGES.en.dashboardLabel);
      expect(translateAppMessage(code, 'studioLabel')).not.toBe(APP_MESSAGES.en.studioLabel);
      expect(translateAppMessage(code, 'watchLabel')).not.toBe(APP_MESSAGES.en.watchLabel);
    }
  });

  it('covers exact legacy static phrases used by high-visibility chrome', () => {
    for (const { code } of SUPPORTED_APP_LOCALES) {
      expect(STATIC_UI_MESSAGES[code]['Save Avatar Settings']).toBeTruthy();
      expect(STATIC_UI_MESSAGES[code]['Transcript']).toBeTruthy();
      expect(STATIC_UI_MESSAGES[code]['Loading secure player...']).toBeTruthy();
    }
  });
});
