import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  fetchVoiceSample: vi.fn(),
  uploadVoiceSample: vi.fn(),
}));

vi.mock('../../api', () => apiMocks);

import VoiceManager from './VoiceManager';

function buttonWithText(host, text) {
  return Array.from(host.querySelectorAll('button')).find((button) => button.textContent.includes(text));
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('VoiceManager', () => {
  let host;
  let root;

  beforeEach(() => {
    vi.clearAllMocks();
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:voice-preview'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  });

  it('shows only personal voices and removes the HeyGen library', async () => {
    await act(async () => {
      root.render(<VoiceManager user={{ id: 42 }} profilePayload={{}} />);
    });

    expect(host.textContent).toContain('Seslerim');
    expect(host.textContent).not.toContain('HeyGen kütüphanesi');
  });

  it('replaces the start button with Kaydı bitir while recording', async () => {
    const stream = { getTracks: () => [{ stop: vi.fn() }] };
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue(stream) },
    });
    class FakeMediaRecorder {
      constructor() {
        this.state = 'inactive';
        this.mimeType = 'audio/webm';
      }

      start() {
        this.state = 'recording';
      }

      stop() {
        this.state = 'inactive';
        this.onstop?.();
      }
    }
    vi.stubGlobal('MediaRecorder', FakeMediaRecorder);
    await act(async () => {
      root.render(<VoiceManager user={{ id: 42 }} profilePayload={{}} />);
    });
    await act(async () => buttonWithText(host, 'Sesinizi klonlayın').click());
    await act(async () => buttonWithText(host, 'Kaydı başlat').click());
    await flush();

    expect(buttonWithText(host, 'Kaydı bitir')).toBeTruthy();
    expect(buttonWithText(host, 'Kaydı başlat')).toBeFalsy();
  });

  it('downloads and plays the real cloned voice sample', async () => {
    let audio;
    let constructedSrc = '';
    class FakeAudio {
      constructor(src) {
        constructedSrc = src;
        this.currentTime = 0;
        this.pause = vi.fn();
        this.play = vi.fn().mockResolvedValue(undefined);
        this.onended = null;
        this.onerror = null;
        audio = this;
      }
    }
    vi.stubGlobal('Audio', FakeAudio);
    apiMocks.fetchVoiceSample.mockResolvedValue(new Blob(['voice'], { type: 'audio/wav' }));
    await act(async () => {
      root.render(
        <VoiceManager
          user={{ id: 42 }}
          profilePayload={{ avatar_setup_status: { checklist: { voice_uploaded: true } } }}
        />,
      );
    });

    const playButton = host.querySelector('button[aria-label="Klonlanmış sesim sesini dinle"]');
    await act(async () => playButton.click());
    await flush();

    expect(apiMocks.fetchVoiceSample).toHaveBeenCalledWith(42);
    expect(URL.createObjectURL).toHaveBeenCalled();
    expect(constructedSrc).toBe('blob:voice-preview');
    expect(audio.play).toHaveBeenCalled();
  });
});
