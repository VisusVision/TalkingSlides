import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  fetchAvatarPreviewStatus: vi.fn(),
  fetchAvatarProfile: vi.fn(),
  fetchVoiceSample: vi.fn(),
  prepareAvatarProfile: vi.fn(),
  regenerateAvatarPreview: vi.fn(),
  updateAvatarProfile: vi.fn(),
  uploadAvatarImage: vi.fn(),
  uploadAvatarVideo: vi.fn(),
  uploadVoiceSample: vi.fn(),
}));

vi.mock('../api', () => ({
  API_BASE_URL: 'http://localhost:8000/api/v1',
  ...apiMocks,
}));

vi.mock('../lib/capabilities', () => ({
  featureEnabled: () => true,
  featureReason: () => '',
  useCapabilities: () => ({
    capabilities: { features: { avatar: { enabled: true } } },
    capabilitiesLoading: false,
  }),
}));

vi.mock('../components/ui/PageLoading', () => ({
  usePageLoading: vi.fn(),
}));

import Avatar from './Avatar';

const user = { id: 42, role: 'teacher', profile: { role: 'teacher' } };

function profilePayload({ portrait = false, voice = false, preview = false, qualityReport = {} } = {}) {
  return {
    profile: {
      avatar_enabled: portrait && voice,
      avatar_consent_confirmed: portrait,
      avatar_image_original: portrait ? '/media/avatar/person.png' : '',
      avatar_preview_video: preview ? '/media/avatar/preview.mp4' : '',
      avatar_preview_quality_report: qualityReport,
    },
    avatar_setup_status: {
      state: portrait ? (voice ? 'needs_prepare' : 'missing_voice') : 'missing_consent',
      message: portrait ? (voice ? 'Avatar needs to be prepared again.' : 'Upload a voice sample.') : 'Confirm avatar consent.',
      checklist: {
        portrait_uploaded: portrait,
        voice_uploaded: voice,
        consent_confirmed: portrait,
        avatar_generation_enabled: portrait && voice,
        avatar_prepared: false,
      },
      can_prepare: portrait && voice,
      can_generate_preview: false,
    },
  };
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function buttonWithText(host, text) {
  return Array.from(host.querySelectorAll('button')).find((button) => button.textContent.includes(text));
}

describe('Avatar page', () => {
  let host;
  let root;

  beforeEach(async () => {
    vi.clearAllMocks();
    apiMocks.fetchAvatarProfile.mockResolvedValue(profilePayload());
    apiMocks.updateAvatarProfile.mockResolvedValue({ status: 'updated' });
    apiMocks.uploadAvatarImage.mockResolvedValue({ status: 'uploaded' });
    apiMocks.uploadVoiceSample.mockResolvedValue({ status: 'ready' });
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:avatar-preview'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    });
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => {
      root.render(<Avatar user={user} />);
    });
    await flush();
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
  });

  it('shows only the consent-based real-person avatar option', () => {
    expect(host.textContent).toContain('Clone a real person');
    expect(host.textContent).toContain('My avatars');
    expect(host.textContent).toContain('Shared avatars');
    expect(host.textContent).not.toContain('Create a virtual character');
    expect(host.textContent).not.toContain('Virtual character');
  });

  it('opens setup and saves portrait, voice, consent, and enabled state', async () => {
    await act(async () => {
      buttonWithText(host, 'Setup guide').click();
    });

    const portrait = new File(['portrait'], 'portrait.png', { type: 'image/png' });
    const panel = host.querySelector('[data-testid="avatar-setup-panel"]');
    expect(panel).toBeTruthy();
    const importInput = panel.querySelector('input[accept="image/*,video/*"]');
    const voiceInput = panel.querySelector('input[accept="audio/*"]');
    const checkboxes = panel.querySelectorAll('input[type="checkbox"]');
    const voice = new File(['voice'], 'voice.wav', { type: 'audio/wav' });

    Object.defineProperty(voiceInput, 'files', { configurable: true, value: [voice] });
    Object.defineProperty(importInput, 'files', { configurable: true, value: [portrait] });
    await act(async () => {
      importInput.dispatchEvent(new Event('change', { bubbles: true }));
      voiceInput.dispatchEvent(new Event('change', { bubbles: true }));
      checkboxes[0].click();
    });
    await act(async () => {
      checkboxes[1].click();
    });

    apiMocks.fetchAvatarProfile.mockResolvedValue(profilePayload({ portrait: true, voice: true }));
    await act(async () => {
      buttonWithText(panel, 'Save avatar').click();
    });
    await flush();

    expect(apiMocks.uploadAvatarImage).toHaveBeenCalledWith(42, portrait, expect.objectContaining({
      avatar_consent_confirmed: true,
      avatar_enabled: true,
    }));
    expect(apiMocks.uploadVoiceSample).toHaveBeenCalledWith(42, voice);
    expect(apiMocks.updateAvatarProfile).toHaveBeenCalledWith(42, expect.objectContaining({
      avatar_consent_confirmed: true,
      avatar_enabled: true,
    }));
    expect(host.textContent).toContain('Avatar settings saved.');
  });

  it('opens voice cloning and uploads an audio file', async () => {
    await act(async () => {
      buttonWithText(host, 'Sesler').click();
    });
    expect(host.textContent).not.toContain('HeyGen kütüphanesi');

    await act(async () => {
      buttonWithText(host, 'Sesinizi klonlayın').click();
    });
    expect(host.querySelector('[role="dialog"]')).toBeTruthy();

    await act(async () => {
      buttonWithText(host, 'Sesi yükle').click();
    });
    const uploadInput = host.querySelector('input[accept*=".mp3"]');
    const voice = new File(['voice-clone'], 'voice-clone.wav', { type: 'audio/wav' });
    Object.defineProperty(uploadInput, 'files', { configurable: true, value: [voice] });
    await act(async () => {
      uploadInput.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => {
      buttonWithText(host, 'Ses klonunu oluştur').click();
    });
    await flush();

    expect(apiMocks.uploadVoiceSample).toHaveBeenCalledWith(42, voice);
    expect(host.textContent).toContain('Sesiniz başarıyla yüklendi');
  });

  it('shows structured quality scores for the latest real render', async () => {
    apiMocks.fetchAvatarProfile.mockResolvedValue(profilePayload({
      portrait: true,
      voice: true,
      preview: true,
      qualityReport: {
        decision: 'review_required',
        identity: { passed: true, score: 0.84 },
        lip_sync: { passed: true, score: 0.91 },
        temporal: { passed: true, score: 1 },
        technical: { strict_validation_passed: true },
        engine_trace: { engine_used: 'liveportrait+musetalk' },
      },
    }));

    await act(async () => root.unmount());
    root = createRoot(host);
    await act(async () => {
      root.render(<Avatar user={user} />);
    });
    await flush();

    await act(async () => {
      buttonWithText(host, 'Setup guide').click();
    });
    await flush();

    expect(host.querySelector('[data-testid="avatar-quality-report"]')).toBeTruthy();
    expect(host.textContent).toContain('Render quality');
    expect(host.textContent).toContain('Review recommended');
    expect(host.textContent).toContain('84%');
    expect(host.textContent).toContain('91%');
    expect(host.textContent).toContain('liveportrait+musetalk');
  });
});
