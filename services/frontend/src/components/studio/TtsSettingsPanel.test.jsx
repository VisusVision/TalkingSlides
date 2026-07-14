import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TtsSettingsPanel, {
  ttsProviderDisplayLabel,
  ttsPreviewLanguageLabel,
} from './TtsSettingsPanel';

const apiMocks = vi.hoisted(() => ({
  fetchTtsPronunciationSuggestions: vi.fn(),
  previewTtsAudio: vi.fn(),
  previewTtsNormalization: vi.fn(),
  updateProjectTtsSettings: vi.fn(),
}));

vi.mock('../../api', () => apiMocks);

const project = {
  id: 42,
  title: 'Voice summary lesson',
  tts_settings: {
    provider_preference: 'xtts_v2',
    normalization_enabled: true,
    normalization_mode: 'loose',
    unknown_word_strategy: 'keep',
    overrides: {
      technical: {},
      abbreviation: {},
      mixed_word: {},
    },
    speech_speed: 1.2,
    volume_gain_db: 0,
    pause_seconds: null,
  },
};

const transcriptPages = [
  {
    id: 1,
    page_key: 'page-1',
    narration_text: 'Preview narration text.',
  },
];

async function renderPanel(props = {}) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);

  await act(async () => {
    root.render(
      <TtsSettingsPanel
        project={project}
        transcriptPages={transcriptPages}
        selectedPageKey="page-1"
        {...props}
      />,
    );
  });

  return { host, root };
}

describe('TtsSettingsPanel voice summary', () => {
  beforeEach(() => {
    document.documentElement.lang = 'en';
    document.documentElement.removeAttribute('dir');
    apiMocks.fetchTtsPronunciationSuggestions.mockReset();
    apiMocks.previewTtsAudio.mockReset();
    apiMocks.previewTtsNormalization.mockReset();
    apiMocks.updateProjectTtsSettings.mockReset();
  });

  it('renders the selected provider, language, speed, and preview availability without renaming providers', async () => {
    const { host, root } = await renderPanel();

    const summary = host.querySelector('[data-testid="tts-voice-summary"]');
    expect(summary).toBeTruthy();
    expect(summary.textContent).toContain('Current voice');
    expect(summary.textContent).toContain('XTTS v2');
    expect(summary.textContent).toContain('Auto');
    expect(summary.textContent).toContain('1.20x');
    expect(summary.textContent).toContain('Preview available');
    expect(ttsProviderDisplayLabel('gtts', {})).toBe('gTTS');
    expect(ttsProviderDisplayLabel('xtts_v2', {})).toBe('XTTS v2');

    await act(async () => root.unmount());
  });

  it('preserves the preview audio callback and payload provider value', async () => {
    apiMocks.previewTtsAudio.mockResolvedValue({
      audio_data_url: 'data:audio/wav;base64,AAA=',
      provider: 'xtts_v2',
      resolved_language: 'en',
      fallback_used: false,
    });
    const { host, root } = await renderPanel();

    const listenButton = host.querySelector('button[aria-label="Listen preview"]');
    expect(listenButton).toBeTruthy();
    await act(async () => {
      listenButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(apiMocks.previewTtsAudio).toHaveBeenCalledWith(expect.objectContaining({
      provider_preference: 'xtts_v2',
      language: 'auto',
      text: 'Preview narration text.',
    }));
    expect(host.textContent).toContain('Preview ready');

    await act(async () => root.unmount());
  });

  it('uses localized Turkish and Arabic summary copy in the normal path', async () => {
    document.documentElement.lang = 'tr-TR';
    const turkish = await renderPanel();
    expect(turkish.host.querySelector('[data-testid="tts-voice-summary"]').textContent).toContain('Geçerli ses');
    expect(turkish.host.querySelector('[data-testid="tts-voice-summary"]').textContent).not.toContain('Current voice');
    expect(ttsPreviewLanguageLabel('tr', { ttsLanguageTurkish: 'Türkçe' })).toBe('Türkçe');
    await act(async () => turkish.root.unmount());

    document.documentElement.lang = 'ar';
    document.documentElement.dir = 'rtl';
    const arabic = await renderPanel();
    expect(arabic.host.querySelector('[data-testid="tts-voice-summary"]').textContent).toContain('الصوت الحالي');
    expect(arabic.host.querySelector('[data-testid="tts-voice-summary"]').textContent).not.toContain('Current voice');
    await act(async () => arabic.root.unmount());
  });
});
