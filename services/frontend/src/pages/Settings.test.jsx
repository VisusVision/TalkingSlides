import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ThemeProvider } from '../components/ui/ThemeProvider';
import { AUTOPLAY_NEXT_KEY } from '../utils/playbackPreferences';

const apiMocks = vi.hoisted(() => ({
  deleteAvatarPreview: vi.fn(),
  fetchAvatarPreviewStatus: vi.fn(),
  fetchAvatarProfile: vi.fn(),
  fetchMyProfile: vi.fn(),
  prepareAvatarProfile: vi.fn(),
  regenerateAvatarPreview: vi.fn(),
  updateAvatarProfile: vi.fn(),
  updateMyProfile: vi.fn(),
  uploadAvatarImage: vi.fn(),
  uploadAvatarVideo: vi.fn(),
  uploadProfileAssets: vi.fn(),
  uploadVoiceSample: vi.fn(),
}));

vi.mock('../api', () => ({
  API_BASE_URL: 'http://localhost:8000/api/v1',
  deleteAvatarPreview: apiMocks.deleteAvatarPreview,
  fetchAvatarPreviewStatus: apiMocks.fetchAvatarPreviewStatus,
  fetchAvatarProfile: apiMocks.fetchAvatarProfile,
  fetchMyProfile: apiMocks.fetchMyProfile,
  prepareAvatarProfile: apiMocks.prepareAvatarProfile,
  regenerateAvatarPreview: apiMocks.regenerateAvatarPreview,
  updateAvatarProfile: apiMocks.updateAvatarProfile,
  updateMyProfile: apiMocks.updateMyProfile,
  uploadAvatarImage: apiMocks.uploadAvatarImage,
  uploadAvatarVideo: apiMocks.uploadAvatarVideo,
  uploadProfileAssets: apiMocks.uploadProfileAssets,
  uploadVoiceSample: apiMocks.uploadVoiceSample,
}));

vi.mock('../lib/capabilities', async () => {
  const actual = await vi.importActual('../lib/capabilities');
  return {
    ...actual,
    useCapabilities: () => ({
      capabilities: {
        features: {
          avatar: { enabled: false },
          intelligence: { enabled: true },
          visual_moderation: { enabled: true },
          local_tts: { enabled: true },
        },
      },
    }),
  };
});

import Settings from './Settings';

function findButton(host, text) {
  return [...host.querySelectorAll('button')].find((button) => button.textContent.includes(text));
}

function findLabel(host, text) {
  return [...host.querySelectorAll('label')].find((label) => label.textContent.includes(text));
}

async function renderSettings() {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);

  await act(async () => {
    root.render(
      <MemoryRouter>
        <ThemeProvider>
          <Settings user={null} onUserRefresh={vi.fn()} />
        </ThemeProvider>
      </MemoryRouter>,
    );
  });
  await act(async () => {});

  return { host, root };
}

describe('Settings theme controls', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    window.localStorage.clear();
    document.documentElement.className = '';
    document.documentElement.removeAttribute('data-theme');
    window.matchMedia = vi.fn().mockImplementation(() => ({
      matches: false,
      media: '',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    vi.clearAllMocks();
  });

  it('removes the duplicate current theme summary but keeps theme switching', async () => {
    const { host, root } = await renderSettings();

    expect(host.textContent).toContain('Theme mode');
    expect(host.textContent).not.toContain('Current Theme');
    expect(host.textContent).not.toContain('Support content');
    expect(host.textContent).not.toContain('Open Help');

    const themeModeButton = findButton(host, 'Theme mode');
    expect(themeModeButton).toBeTruthy();

    await act(async () => {
      themeModeButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const darkButton = findButton(host, 'Dark');
    expect(darkButton).toBeTruthy();

    await act(async () => {
      darkButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');

    await act(async () => root.unmount());
    host.remove();
  });

  it('persists the continue-next playback setting from Playback/Accessibility', async () => {
    const { host, root } = await renderSettings();

    const playbackButton = findButton(host, 'Playback & accessibility');
    expect(playbackButton).toBeTruthy();

    await act(async () => {
      playbackButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const continueNextLabel = findLabel(host, 'Continue to next lesson');
    expect(continueNextLabel).toBeTruthy();
    const continueNextInput = continueNextLabel.querySelector('input');

    expect(continueNextInput.checked).toBe(true);
    expect(window.localStorage.getItem(AUTOPLAY_NEXT_KEY)).toBe('1');

    await act(async () => {
      continueNextInput.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(continueNextInput.checked).toBe(false);
    expect(window.localStorage.getItem(AUTOPLAY_NEXT_KEY)).toBe('0');

    await act(async () => root.unmount());
    host.remove();
  });
});
