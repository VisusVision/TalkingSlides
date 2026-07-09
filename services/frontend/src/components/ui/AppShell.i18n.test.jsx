import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LocaleProvider, useLocale } from '../../i18n/LocaleProvider';
import { APP_LOCALE_STORAGE_KEY } from '../../i18n/locale';
import LanguageSelector from './LanguageSelector';
import AppShell from './AppShell';

vi.mock('../../api', () => ({
  fetchAuthenticatedMediaBlobUrl: vi.fn(),
  fetchNotificationUnreadCount: vi.fn().mockResolvedValue({ unread_count: 0 }),
  fetchNotifications: vi.fn().mockResolvedValue({ results: [] }),
  markAllNotificationsRead: vi.fn().mockResolvedValue({}),
  markNotificationRead: vi.fn().mockResolvedValue({}),
}));

const teacherUser = {
  id: 7,
  username: 'teacher',
  profile: { role: 'teacher' },
};

function LocaleProbe() {
  const { t } = useLocale();
  return (
    <>
      <p>{t('studioLabel')}</p>
      <p>{t('watchLabel')}</p>
    </>
  );
}

async function renderShell() {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);

  await act(async () => {
    root.render(
      <MemoryRouter>
        <LocaleProvider>
          <AppShell
            searchQuery=""
            onSearchQueryChange={vi.fn()}
            user={teacherUser}
            authLoading={false}
            onLoginRequest={vi.fn()}
            onLogout={vi.fn()}
          >
            <section aria-label="settings-language-control">
              <LanguageSelector />
              <LocaleProbe />
            </section>
          </AppShell>
        </LocaleProvider>
      </MemoryRouter>,
    );
  });

  return { host, root };
}

describe('AppShell i18n', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    window.localStorage.clear();
    document.documentElement.lang = 'en';
    document.documentElement.dir = 'ltr';
    document.documentElement.classList.remove('rtl');
  });

  it('keeps language selection in Settings and updates app shell labels immediately', async () => {
    const { host, root } = await renderShell();
    const selector = host.querySelector('[data-testid="settings-language-selector"]');

    expect(selector).toBeVisible();
    expect(host.querySelectorAll('select[aria-label="Application language"]')).toHaveLength(1);
    expect(host.textContent).toContain('Dashboard');
    expect(host.textContent).toContain('Studio Workspace');
    expect(host.textContent).toContain('Watch');

    await act(async () => {
      selector.value = 'tr';
      selector.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(host.textContent).toContain('Kontrol Paneli');
    expect(host.textContent).toContain('Stüdyo Çalışma Alanı');
    expect(host.textContent).toContain('İzle');
    expect(window.localStorage.getItem(APP_LOCALE_STORAGE_KEY)).toBe('tr');

    await act(async () => {
      selector.value = 'ar';
      selector.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(document.documentElement.lang).toBe('ar');
    expect(document.documentElement.dir).toBe('rtl');
    expect(document.documentElement).toHaveClass('rtl');
    expect(host.textContent).toContain('لوحة المعلومات');

    await act(async () => {
      selector.value = 'en';
      selector.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(document.documentElement.lang).toBe('en');
    expect(document.documentElement.dir).toBe('ltr');
    expect(document.documentElement).not.toHaveClass('rtl');
    expect(host.textContent).toContain('Dashboard');

    await act(async () => root.unmount());
    host.remove();
  });
});
