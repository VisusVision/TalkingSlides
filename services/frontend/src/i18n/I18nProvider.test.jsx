import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it } from 'vitest';
import LanguageSelector from '../components/ui/LanguageSelector';
import { I18nProvider, useI18n } from './I18nProvider';
import { LANGUAGE_STORAGE_KEY } from './translations';

function Probe() {
  const { formatDate, formatDuration, formatNumber, formatViews, t } = useI18n();
  return (
    <div>
      <LanguageSelector />
      <p data-testid="save-label">{t('common.save')}</p>
      <p data-testid="dashboard-label">{t('dashboard.continueWatching')}</p>
      <p data-testid="studio-label">{t('studio.createLessonDraft')}</p>
      <p data-testid="settings-label">{t('common.settings')}</p>
      <p data-testid="watch-label">{t('watch.focusedContext')}</p>
      <p data-testid="subtitle-label">{t('watch.subtitles')}</p>
      <p data-testid="moderation-label">{t('moderation.reportIssue')}</p>
      <p data-testid="recorder-label">{t('avatar.recorderStatuses.idle')}</p>
      <p data-testid="date-label">{formatDate('2026-06-22T12:00:00Z')}</p>
      <p data-testid="duration-label">{formatDuration(90)}</p>
      <p data-testid="number-label">{formatNumber(1250)}</p>
      <p data-testid="views-label">{formatViews(1250)}</p>
    </div>
  );
}

async function renderProbe() {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);

  await act(async () => {
    root.render(
      <I18nProvider>
        <Probe />
      </I18nProvider>,
    );
  });

  return { host, root };
}

async function selectLanguage(host, language) {
  const select = host.querySelector('select[aria-label="UI language"], select[aria-label="Arayuz dili"]');
  expect(select).toBeTruthy();

  await act(async () => {
    select.value = language;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

describe('I18nProvider', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    window.localStorage.clear();
    document.documentElement.removeAttribute('lang');
    Object.defineProperty(navigator, 'languages', {
      configurable: true,
      value: ['en-US'],
    });
    Object.defineProperty(navigator, 'language', {
      configurable: true,
      value: 'en-US',
    });
  });

  it('renders the language switcher and uses browser language when supported', async () => {
    Object.defineProperty(navigator, 'languages', {
      configurable: true,
      value: ['tr-TR', 'en-US'],
    });

    const { host, root } = await renderProbe();

    expect(host.querySelector('select')).toBeTruthy();
    expect(host.querySelector('[data-testid="save-label"]')).toHaveTextContent('Kaydet');
    expect(document.documentElement.lang).toBe('tr');

    await act(async () => root.unmount());
    host.remove();
  });

  it('switches to Turkish, switches back to English, and persists the selected language', async () => {
    const { host, root } = await renderProbe();

    expect(host.querySelector('[data-testid="save-label"]')).toHaveTextContent('Save');

    await selectLanguage(host, 'tr');
    expect(host.querySelector('[data-testid="save-label"]')).toHaveTextContent('Kaydet');
    expect(host.querySelector('[data-testid="dashboard-label"]')).toHaveTextContent('Izlemeye Devam Et');
    expect(host.querySelector('[data-testid="studio-label"]')).toHaveTextContent('Ders Taslagi Olustur');
    expect(host.querySelector('[data-testid="settings-label"]')).toHaveTextContent('Ayarlar');
    expect(host.querySelector('[data-testid="watch-label"]')).toHaveTextContent('Odakli Baglamla Calis');
    expect(host.querySelector('[data-testid="subtitle-label"]')).toHaveTextContent('Altyazilar');
    expect(host.querySelector('[data-testid="moderation-label"]')).toHaveTextContent('Ders sorununu bildir');
    expect(host.querySelector('[data-testid="recorder-label"]')).toHaveTextContent('Bos');
    expect(host.querySelector('[data-testid="date-label"]')).toHaveTextContent('22 Haz 2026');
    expect(host.querySelector('[data-testid="duration-label"]')).toHaveTextContent('1,5 sa');
    expect(host.querySelector('[data-testid="number-label"]')).toHaveTextContent('1.250');
    expect(host.querySelector('[data-testid="views-label"]')).toHaveTextContent('1.250 goruntulenme');
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('tr');

    await selectLanguage(host, 'en');
    expect(host.querySelector('[data-testid="save-label"]')).toHaveTextContent('Save');
    expect(host.querySelector('[data-testid="dashboard-label"]')).toHaveTextContent('Continue Watching');
    expect(host.querySelector('[data-testid="studio-label"]')).toHaveTextContent('Create Lesson Draft');
    expect(host.querySelector('[data-testid="date-label"]')).toHaveTextContent('Jun 22, 2026');
    expect(host.querySelector('[data-testid="duration-label"]')).toHaveTextContent('1.5 h');
    expect(host.querySelector('[data-testid="number-label"]')).toHaveTextContent('1,250');
    expect(host.querySelector('[data-testid="views-label"]')).toHaveTextContent('1,250 views');
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('en');

    await act(async () => root.unmount());
    host.remove();

    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'tr');
    const remount = await renderProbe();
    expect(remount.host.querySelector('[data-testid="settings-label"]')).toHaveTextContent('Ayarlar');

    await act(async () => remount.root.unmount());
    remount.host.remove();
  });

  it('falls back to English for unsupported stored and browser languages', async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'de');
    Object.defineProperty(navigator, 'languages', {
      configurable: true,
      value: ['de-DE'],
    });
    Object.defineProperty(navigator, 'language', {
      configurable: true,
      value: 'de-DE',
    });

    const { host, root } = await renderProbe();

    expect(host.querySelector('[data-testid="save-label"]')).toHaveTextContent('Save');
    expect(document.documentElement.lang).toBe('en');

    await act(async () => root.unmount());
    host.remove();
  });
});
