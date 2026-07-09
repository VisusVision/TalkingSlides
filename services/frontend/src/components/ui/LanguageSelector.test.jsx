import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { LocaleProvider } from '../../i18n/LocaleProvider';
import {
  APP_LOCALE_STORAGE_KEY,
  SUPPORTED_APP_LOCALES,
} from '../../i18n/locale';
import LanguageSelector from './LanguageSelector';

describe('LanguageSelector', () => {
  let host;
  let root;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    window.localStorage.clear();
    document.documentElement.lang = 'en';
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
  });

  it('shows every supported locale and persists a language switch', async () => {
    await act(async () => {
      root.render(
        <LocaleProvider>
          <LanguageSelector compact />
        </LocaleProvider>,
      );
    });

    const selector = host.querySelector('[data-testid="global-language-selector"]');
    expect(selector).toBeVisible();
    expect(selector).toHaveAccessibleName('Language / Dil');
    expect(selector.querySelectorAll('option')).toHaveLength(SUPPORTED_APP_LOCALES.length);

    await act(async () => {
      selector.value = 'tr';
      selector.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(document.documentElement.lang).toBe('tr');
    expect(window.localStorage.getItem(APP_LOCALE_STORAGE_KEY)).toBe('tr');
  });
});
