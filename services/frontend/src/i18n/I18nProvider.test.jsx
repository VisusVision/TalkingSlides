import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it } from 'vitest';
import LanguageSelector from '../components/ui/LanguageSelector';
import { I18nProvider, useI18n } from './I18nProvider';
import { LANGUAGE_OPTIONS, LANGUAGE_STORAGE_KEY } from './translations';

function Probe() {
  const { direction, formatDate, formatDuration, formatNumber, formatViews, isRtl, language, locale, t } = useI18n();
  return (
    <div>
      <LanguageSelector />
      <p data-testid="language-label">{language}</p>
      <p data-testid="locale-label">{locale}</p>
      <p data-testid="direction-label">{direction}</p>
      <p data-testid="rtl-label">{isRtl ? 'rtl' : 'ltr'}</p>
      <p data-testid="save-label">{t('common.save')}</p>
      <p data-testid="dashboard-label">{t('dashboard.continueWatching')}</p>
      <p data-testid="fallback-label">{t('dashboard.featuredDescription')}</p>
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
  const select = host.querySelector('select');
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
    document.documentElement.removeAttribute('dir');
    document.documentElement.classList.remove('rtl');
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

  it('renders every supported language option', async () => {
    const { host, root } = await renderProbe();
    const optionValues = [...host.querySelectorAll('option')].map((option) => option.value);

    expect(optionValues).toEqual(LANGUAGE_OPTIONS.map((option) => option.code));

    await act(async () => root.unmount());
    host.remove();
  });

  it('switches to Turkish, switches back to English, and persists the selected language', async () => {
    const { host, root } = await renderProbe();

    expect(host.querySelector('[data-testid="save-label"]')).toHaveTextContent('Save');
    expect(host.querySelector('[data-testid="locale-label"]')).toHaveTextContent('en-US');
    expect(host.querySelector('[data-testid="direction-label"]')).toHaveTextContent('ltr');

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
    expect(document.documentElement.dir).toBe('ltr');

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

  it('switches to new locales and falls back to English for missing keys', async () => {
    const { host, root } = await renderProbe();

    await selectLanguage(host, 'es');
    expect(host.querySelector('[data-testid="language-label"]')).toHaveTextContent('es');
    expect(host.querySelector('[data-testid="locale-label"]')).toHaveTextContent('es-ES');
    expect(host.querySelector('[data-testid="save-label"]')).toHaveTextContent('Guardar');
    expect(host.querySelector('[data-testid="number-label"]')).toHaveTextContent(new Intl.NumberFormat('es-ES').format(1250));
    expect(host.querySelector('[data-testid="fallback-label"]')).toHaveTextContent(/Explore a cinematic lesson experience built for clarity/);
    expect(document.documentElement.lang).toBe('es');
    expect(document.documentElement.dir).toBe('ltr');

    await selectLanguage(host, 'zh-CN');
    expect(host.querySelector('[data-testid="language-label"]')).toHaveTextContent('zh-CN');
    expect(host.querySelector('[data-testid="locale-label"]')).toHaveTextContent('zh-CN');
    expect(host.querySelector('[data-testid="save-label"]')).toHaveTextContent('保存');
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('zh-CN');

    await act(async () => root.unmount());
    host.remove();
  });

  it('enables RTL mode for Arabic and persists it after remount', async () => {
    const { host, root } = await renderProbe();

    await selectLanguage(host, 'ar');
    expect(host.querySelector('[data-testid="language-label"]')).toHaveTextContent('ar');
    expect(host.querySelector('[data-testid="locale-label"]')).toHaveTextContent('ar');
    expect(host.querySelector('[data-testid="direction-label"]')).toHaveTextContent('rtl');
    expect(host.querySelector('[data-testid="rtl-label"]')).toHaveTextContent('rtl');
    expect(host.querySelector('[data-testid="save-label"]')).toHaveTextContent('حفظ');
    expect(host.querySelector('[data-testid="number-label"]')).toHaveTextContent(new Intl.NumberFormat('ar').format(1250));
    expect(document.documentElement.lang).toBe('ar');
    expect(document.documentElement.dir).toBe('rtl');
    expect(document.documentElement.classList.contains('rtl')).toBe(true);
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('ar');

    await act(async () => root.unmount());
    host.remove();

    const remount = await renderProbe();
    expect(remount.host.querySelector('[data-testid="save-label"]')).toHaveTextContent('حفظ');
    expect(document.documentElement.dir).toBe('rtl');

    await act(async () => remount.root.unmount());
    remount.host.remove();
  });

  it('falls back to English for unsupported stored and browser languages', async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'xx');
    Object.defineProperty(navigator, 'languages', {
      configurable: true,
      value: ['xx-YY'],
    });
    Object.defineProperty(navigator, 'language', {
      configurable: true,
      value: 'xx-YY',
    });

    const { host, root } = await renderProbe();

    expect(host.querySelector('[data-testid="save-label"]')).toHaveTextContent('Save');
    expect(document.documentElement.lang).toBe('en');

    await act(async () => root.unmount());
    host.remove();
  });
});
