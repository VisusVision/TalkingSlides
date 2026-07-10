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
    document.documentElement.dir = 'ltr';
    document.documentElement.classList.remove('rtl');
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
          <LanguageSelector />
        </LocaleProvider>,
      );
    });

    const selector = host.querySelector('[data-testid="settings-language-selector"]');
    expect(selector).toBeVisible();
    expect(selector).toHaveAccessibleName('Application language');
    expect(selector.querySelectorAll('option')).toHaveLength(SUPPORTED_APP_LOCALES.length);

    await act(async () => {
      selector.value = 'tr';
      selector.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(document.documentElement.lang).toBe('tr');
    expect(window.localStorage.getItem(APP_LOCALE_STORAGE_KEY)).toBe('tr');
  });

  it('uses the same provider state and direction for Arabic', async () => {
    await act(async () => {
      root.render(
        <LocaleProvider>
          <LanguageSelector />
        </LocaleProvider>,
      );
    });

    const settingsSelector = host.querySelector('[data-testid="settings-language-selector"]');

    await act(async () => {
      settingsSelector.value = 'ar';
      settingsSelector.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(document.documentElement.lang).toBe('ar');
    expect(document.documentElement.dir).toBe('rtl');
    expect(document.documentElement).toHaveClass('rtl');

    await act(async () => {
      settingsSelector.value = 'en';
      settingsSelector.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(document.documentElement.dir).toBe('ltr');
    expect(document.documentElement).not.toHaveClass('rtl');
  });

  it('rehydrates the persisted canonical locale', async () => {
    window.localStorage.setItem(APP_LOCALE_STORAGE_KEY, 'tr');

    await act(async () => {
      root.render(
        <LocaleProvider>
          <LanguageSelector />
        </LocaleProvider>,
      );
    });

    expect(host.querySelector('[data-testid="settings-language-selector"]').value).toBe('tr');
    expect(document.documentElement.lang).toBe('tr');
  });

  it('falls back to English for an unsupported persisted locale', async () => {
    window.localStorage.setItem(APP_LOCALE_STORAGE_KEY, 'xx-YY');

    await act(async () => {
      root.render(
        <LocaleProvider>
          <LanguageSelector />
        </LocaleProvider>,
      );
    });

    expect(host.querySelector('[data-testid="settings-language-selector"]').value).toBe('en');
    expect(document.documentElement.lang).toBe('en');
    expect(window.localStorage.getItem(APP_LOCALE_STORAGE_KEY)).toBe('en');
  });

  it('migrates the legacy storage key', async () => {
    window.localStorage.setItem('talkingslides-ui-language', 'tr-TR');

    await act(async () => {
      root.render(
        <LocaleProvider>
          <LanguageSelector />
        </LocaleProvider>,
      );
    });

    const selector = host.querySelector('[data-testid="settings-language-selector"]');
    expect(selector.value).toBe('tr');
    expect(document.documentElement.lang).toBe('tr');
    expect(window.localStorage.getItem(APP_LOCALE_STORAGE_KEY)).toBe('tr');
    expect(window.localStorage.getItem('talkingslides-ui-language')).toBeNull();
  });
});
