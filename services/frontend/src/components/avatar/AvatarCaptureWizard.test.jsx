import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AvatarCaptureWizard from './AvatarCaptureWizard';

function findButton(host, text) {
  return [...host.querySelectorAll('button')].find((button) => button.textContent.includes(text));
}

function findTab(host, text) {
  return [...host.querySelectorAll('[role="tab"]')].find((tab) => tab.textContent.includes(text));
}

describe('AvatarCaptureWizard', () => {
  let host;
  let root;

  beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:avatar-preview'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    });
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn() },
    });
    Object.defineProperty(navigator, 'permissions', {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(window, 'MediaRecorder', {
      configurable: true,
      value: class FakeMediaRecorder {},
    });
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('shows only webcam and computer import choices', async () => {
    await act(async () => root.render(<AvatarCaptureWizard locale="tr" />));

    expect(findTab(host, 'Webcam ile kaydet')).toBeTruthy();
    expect(findTab(host, 'Görüntü içe aktar')).toBeTruthy();
    expect(host.textContent).not.toMatch(/telefonda kaydet/i);
    expect(host.textContent).toContain('Adres çubuğundaki kamera veya site ayarları simgesine tıkla');
    expect(findButton(host, 'Chrome için adresi kopyala')).toBeTruthy();
    expect(host.querySelectorAll('[role="tab"]')).toHaveLength(2);
  });

  it('requests camera and microphone together after the explicit enable action', async () => {
    const stream = { getTracks: vi.fn(() => [{ stop: vi.fn() }]) };
    navigator.mediaDevices.getUserMedia.mockResolvedValue(stream);
    await act(async () => root.render(<AvatarCaptureWizard locale="tr" />));

    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
    await act(async () => findButton(host, 'Kamera ve mikrofonu kontrol et').click());

    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledOnce();
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
      video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    expect(findButton(host, '30 saniyelik performans kaydını başlat')).toBeTruthy();
  });

  it('receives a recording from the separate local camera origin', async () => {
    const popup = { close: vi.fn(), focus: vi.fn() };
    vi.spyOn(window, 'open').mockReturnValue(popup);
    await act(async () => root.render(
      <AvatarCaptureWizard
        locale="tr"
        externalCaptureOrigin="http://camera.localhost:3000"
      />,
    ));

    await act(async () => findButton(host, 'Kamera kayıt penceresini aç').click());
    expect(window.open).toHaveBeenCalledWith(
      expect.stringContaining('http://camera.localhost:3000/avatar-camera-capture.html'),
      'visus-avatar-camera-capture',
      'popup=yes,width=1100,height=820',
    );

    const blob = new Blob(['avatar recording'], { type: 'video/webm' });
    await act(async () => {
      window.dispatchEvent(new MessageEvent('message', {
        origin: 'http://camera.localhost:3000',
        source: popup,
        data: { type: 'visus-avatar-camera-recording', blob, mimeType: 'video/webm' },
      }));
    });

    expect(findButton(host, 'Devam et')).not.toHaveAttribute('disabled');
    expect(host.querySelector('video[src="blob:avatar-preview"]')).toBeTruthy();
  });

  it('explains when camera or microphone permission is denied', async () => {
    navigator.mediaDevices.getUserMedia.mockRejectedValue(
      Object.assign(new Error('denied'), { name: 'NotAllowedError' }),
    );
    await act(async () => root.render(<AvatarCaptureWizard locale="tr" />));
    await act(async () => findButton(host, 'Kamera ve mikrofonu kontrol et').click());

    expect(host.querySelector('[role="alert"]')?.textContent).toContain('Kamera veya mikrofon izni engellenmiş');
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('Kamera izniEngellendi');
    expect(findButton(host, 'İzinleri tekrar dene')).toBeTruthy();
    expect(findButton(host, 'Dosya yükleyerek devam et')).toBeTruthy();
  });

  it('discards a late camera response after switching to computer import', async () => {
    let resolveMedia;
    const trackStop = vi.fn();
    navigator.mediaDevices.getUserMedia.mockImplementation(() => new Promise((resolve) => {
      resolveMedia = resolve;
    }));
    await act(async () => root.render(<AvatarCaptureWizard locale="tr" />));

    await act(async () => findButton(host, 'Kamera ve mikrofonu kontrol et').click());
    expect(findButton(host, 'Tarayıcı izni bekleniyor...').disabled).toBe(true);
    await act(async () => findTab(host, 'Görüntü içe aktar').click());
    await act(async () => {
      resolveMedia({ getTracks: () => [{ stop: trackStop }] });
    });

    expect(trackStop).toHaveBeenCalledOnce();
    expect(findTab(host, 'Görüntü içe aktar').getAttribute('aria-selected')).toBe('true');
    expect(findButton(host, '30 saniyelik performans kaydını başlat')).toBeFalsy();
  });

  it('accepts a local computer file and passes it to the existing setup flow', async () => {
    const onComplete = vi.fn();
    await act(async () => root.render(<AvatarCaptureWizard locale="tr" hasSavedVoice onComplete={onComplete} />));
    await act(async () => findTab(host, 'Görüntü içe aktar').click());

    const input = host.querySelector('input[type="file"]');
    expect(input).toBeTruthy();
    expect(input.hasAttribute('capture')).toBe(false);

    const file = new File(['avatar'], 'portre.png', { type: 'image/png' });
    await act(async () => {
      Object.defineProperty(input, 'files', { configurable: true, value: [file] });
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => findButton(host, 'Devam et').click());

    const nameInput = host.querySelector('input[placeholder="Örneğin: Ders avatarım"]');
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(nameInput, 'Fen Bilimleri Avatarım');
      nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => findButton(host, 'Ses seçimine geç').click());
    const consent = host.querySelector('input[type="checkbox"]');
    await act(async () => consent.click());
    await act(async () => findButton(host, 'Avatar profilini oluştur').click());

    expect(onComplete).toHaveBeenCalledWith({
      file,
      avatarName: 'Fen Bilimleri Avatarım',
      voiceSource: 'existing',
      consentConfirmed: true,
    });
  });

  it('shows a read-along script and moves it downward as webcam recording progresses', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-02T12:00:00Z'));
    const stream = {
      getTracks: () => [{ stop: vi.fn() }],
      getVideoTracks: () => [{ label: 'Camera', stop: vi.fn() }],
      getAudioTracks: () => [{ label: 'Microphone', stop: vi.fn() }],
    };
    navigator.mediaDevices.getUserMedia.mockResolvedValue(stream);
    class RecordingMediaRecorder {
      static isTypeSupported() { return true; }

      constructor() {
        this.state = 'inactive';
        this.mimeType = 'video/webm';
      }

      start() { this.state = 'recording'; }

      stop() { this.state = 'inactive'; }
    }
    Object.defineProperty(window, 'MediaRecorder', { configurable: true, value: RecordingMediaRecorder });
    await act(async () => root.render(<AvatarCaptureWizard locale="tr" />));
    await act(async () => findButton(host, 'Kamera ve mikrofonu kontrol et').click());

    const prompt = host.querySelector('[data-testid="avatar-teleprompter"]');
    expect(prompt.textContent).toContain('Merhaba, dijital avatarımı oluşturuyorum');
    expect(prompt.dataset.offset).toBe('0');

    await act(async () => findButton(host, '30 saniyelik performans kaydını başlat').click());
    await act(async () => vi.advanceTimersByTimeAsync(5000));

    expect(Number(prompt.dataset.offset)).toBeGreaterThan(0);
  });
});
