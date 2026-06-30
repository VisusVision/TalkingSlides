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

const capabilityMockState = vi.hoisted(() => ({
  avatarEnabled: false,
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
          avatar: { enabled: capabilityMockState.avatarEnabled },
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

async function openVoiceSampleModal(host) {
  let voiceSampleButton = findButton(host, 'Voice Sample');
  if (!voiceSampleButton) {
    const avatarSectionButton = findButton(host, 'Voice and avatar samples');
    expect(avatarSectionButton).toBeTruthy();

    await act(async () => {
      avatarSectionButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    voiceSampleButton = findButton(host, 'Voice Sample');
  }

  expect(voiceSampleButton).toBeTruthy();

  await act(async () => {
    voiceSampleButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

async function clickSettingsButton(label) {
  await act(async () => {
    findButton(document.body, label).dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function installMediaRecorderMock({ withAudio = true, constructorError = null, startError = null } = {}) {
  const instances = [];
  class FakeMediaRecorder {
    static isTypeSupported = vi.fn(() => true);

    constructor(_stream, options) {
      if (constructorError) {
        throw constructorError;
      }
      this.requestedOptions = options;
      this.mimeType = options?.mimeType || 'audio/webm';
      this.state = 'inactive';
      instances.push(this);
    }

    start() {
      if (startError) {
        throw startError;
      }
      this.state = 'recording';
    }

    stop() {
      this.state = 'inactive';
      if (withAudio) {
        this.ondataavailable?.({
          data: new Blob(['browser audio'], { type: this.mimeType }),
        });
      }
      this.onstop?.();
    }
  }

  Object.defineProperty(window, 'MediaRecorder', {
    configurable: true,
    value: FakeMediaRecorder,
  });
  return instances;
}

const teacherUser = {
  id: 42,
  username: 'teacher',
  profile: { role: 'teacher' },
};

const staffUser = {
  id: 43,
  username: 'demo.staff',
  email: 'demo.staff@example.com',
  is_staff: true,
  profile: { role: 'student' },
};

const studentUser = {
  id: 44,
  username: 'student',
  profile: { role: 'student' },
};

async function renderSettings({ user = null } = {}) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);

  await act(async () => {
    root.render(
      <MemoryRouter>
        <ThemeProvider>
          <Settings user={user} onUserRefresh={vi.fn()} />
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
    capabilityMockState.avatarEnabled = false;
    window.localStorage.clear();
    window.sessionStorage.clear();
    document.documentElement.className = '';
    document.documentElement.removeAttribute('data-theme');
    window.matchMedia = vi.fn().mockImplementation(() => ({
      matches: false,
      media: '',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    apiMocks.fetchAvatarProfile.mockResolvedValue({
      profile: {
        avatar_consent_confirmed: true,
        avatar_enabled: true,
      },
      avatar_setup_status: {
        state: 'missing_voice',
        checklist: {
          portrait_uploaded: true,
          voice_uploaded: false,
          consent_confirmed: true,
          avatar_generation_enabled: true,
          avatar_prepared: false,
        },
      },
    });
    apiMocks.fetchMyProfile.mockResolvedValue({
      first_name: '',
      last_name: '',
      display_name: 'Teacher',
      bio: '',
      website_url: '',
      contact_email: '',
      social_links: {},
      is_public_profile: false,
      banner_url: '',
      logo_url: '',
    });
    apiMocks.uploadVoiceSample.mockResolvedValue({ status: 'ready' });
    URL.createObjectURL = vi.fn(() => 'blob:voice-preview');
    URL.revokeObjectURL = vi.fn();
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    });
    const microphoneTrack = {
      kind: 'audio',
      label: 'Test microphone',
      enabled: true,
      muted: false,
      readyState: 'live',
      addEventListener: vi.fn(),
      stop: vi.fn(),
    };
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        enumerateDevices: vi.fn().mockResolvedValue([
          {
            kind: 'audioinput',
            label: 'Test microphone',
            deviceId: 'default',
            groupId: 'test-group',
          },
        ]),
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [microphoneTrack],
          getAudioTracks: () => [microphoneTrack],
        }),
      },
    });
    Object.defineProperty(navigator, 'permissions', {
      configurable: true,
      value: {
        query: vi.fn().mockResolvedValue({ state: 'granted' }),
      },
    });
    Object.defineProperty(window, 'MediaRecorder', {
      configurable: true,
      value: undefined,
    });
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

  it('renders microphone controls in Avatar Preferences voice sample modal', async () => {
    capabilityMockState.avatarEnabled = true;
    const { host, root } = await renderSettings({ user: teacherUser });

    await openVoiceSampleModal(host);

    expect(document.body.textContent).toContain('Record from microphone');
    expect(findButton(document.body, 'Start recording')).toBeTruthy();
    expect(findButton(document.body, 'Play preview')).toBeTruthy();
    expect(findButton(document.body, 'Use recording')).toBeTruthy();
    expect(findButton(document.body, 'Discard recording')).toBeTruthy();

    await act(async () => root.unmount());
    host.remove();
  });

  it('renders microphone controls in Avatar Preferences for staff users', async () => {
    capabilityMockState.avatarEnabled = true;
    const { host, root } = await renderSettings({ user: staffUser });

    await openVoiceSampleModal(host);

    expect(apiMocks.fetchAvatarProfile).toHaveBeenCalledWith(staffUser.id);
    expect(document.body.textContent).toContain('Record from microphone');
    expect(findButton(document.body, 'Start recording')).toBeTruthy();

    await act(async () => root.unmount());
    host.remove();
  });

  it('does not expose Avatar Preferences to student users', async () => {
    capabilityMockState.avatarEnabled = true;
    const { host, root } = await renderSettings({ user: studentUser });

    expect(findButton(host, 'Voice and avatar samples')).toBeFalsy();
    expect(host.textContent).not.toContain('Avatar Preferences');
    expect(host.textContent).not.toContain('Voice Sample');
    expect(apiMocks.fetchAvatarProfile).not.toHaveBeenCalled();

    await act(async () => root.unmount());
    host.remove();
  });

  it('shows a fallback when MediaRecorder is unsupported', async () => {
    capabilityMockState.avatarEnabled = true;
    const { host, root } = await renderSettings({ user: teacherUser });
    await openVoiceSampleModal(host);

    await clickSettingsButton('Start recording');

    expect(document.body.textContent).toContain('This browser does not support microphone recording.');
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();

    await act(async () => root.unmount());
    host.remove();
  });

  it('shows an error when microphone permission is denied', async () => {
    capabilityMockState.avatarEnabled = true;
    const consoleWarn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    installMediaRecorderMock();
    navigator.mediaDevices.getUserMedia.mockRejectedValue(Object.assign(new Error('denied'), {
      name: 'NotAllowedError',
    }));
    const { host, root } = await renderSettings({ user: teacherUser });
    await openVoiceSampleModal(host);

    await clickSettingsButton('Start recording');

    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(document.body.textContent).toContain('Microphone permission was denied by the browser or operating system.');
    expect(consoleWarn).toHaveBeenCalledWith('voice_recording_error', expect.objectContaining({
      phase: 'getUserMedia',
      name: 'NotAllowedError',
    }));

    await act(async () => root.unmount());
    host.remove();
    consoleWarn.mockRestore();
  });

  it('clears a stale microphone permission error before a successful retry', async () => {
    capabilityMockState.avatarEnabled = true;
    const instances = installMediaRecorderMock();
    navigator.mediaDevices.getUserMedia.mockRejectedValueOnce(Object.assign(new Error('denied'), {
      name: 'NotAllowedError',
    }));
    const { host, root } = await renderSettings({ user: teacherUser });
    await openVoiceSampleModal(host);

    await clickSettingsButton('Start recording');

    expect(document.body.textContent).toContain('Microphone permission was denied by the browser or operating system.');

    await clickSettingsButton('Start recording');

    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(2);
    expect(document.body.textContent).toContain('Status: recording');
    expect(document.body.textContent).not.toContain('Microphone permission was denied by the browser or operating system.');
    expect(instances).toHaveLength(1);

    await act(async () => root.unmount());
    host.remove();
  });

  it('shows an error when no microphone is found', async () => {
    capabilityMockState.avatarEnabled = true;
    installMediaRecorderMock();
    navigator.mediaDevices.getUserMedia.mockRejectedValue(Object.assign(new Error('no device'), {
      name: 'NotFoundError',
    }));
    const { host, root } = await renderSettings({ user: teacherUser });
    await openVoiceSampleModal(host);

    await clickSettingsButton('Start recording');

    expect(document.body.textContent).toContain('No microphone was found.');

    await act(async () => root.unmount());
    host.remove();
  });

  it('shows an error when the microphone is busy', async () => {
    capabilityMockState.avatarEnabled = true;
    installMediaRecorderMock();
    navigator.mediaDevices.getUserMedia.mockRejectedValue(Object.assign(new Error('busy'), {
      name: 'NotReadableError',
    }));
    const { host, root } = await renderSettings({ user: teacherUser });
    await openVoiceSampleModal(host);

    await clickSettingsButton('Start recording');

    expect(document.body.textContent).toContain('The microphone is busy or unavailable.');

    await act(async () => root.unmount());
    host.remove();
  });

  it('shows an insecure context error before requesting microphone access', async () => {
    capabilityMockState.avatarEnabled = true;
    installMediaRecorderMock();
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: false,
    });
    const { host, root } = await renderSettings({ user: teacherUser });
    await openVoiceSampleModal(host);

    await clickSettingsButton('Start recording');

    expect(document.body.textContent).toContain('Microphone recording requires a secure context.');
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();

    await act(async () => root.unmount());
    host.remove();
  });

  it('does not label MediaRecorder construction failures as microphone permission denial', async () => {
    capabilityMockState.avatarEnabled = true;
    installMediaRecorderMock({
      constructorError: Object.assign(new Error('stream blocked'), {
        name: 'NotAllowedError',
      }),
    });
    const { host, root } = await renderSettings({ user: teacherUser });
    await openVoiceSampleModal(host);

    await clickSettingsButton('Start recording');

    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(document.body.textContent).toContain('allowed microphone capture but blocked recording');
    expect(document.body.textContent).not.toContain('permission was denied by the browser');

    await act(async () => root.unmount());
    host.remove();
  });

  it('starts and stops microphone recording, then uploads the recording through the voice sample handler', async () => {
    capabilityMockState.avatarEnabled = true;
    installMediaRecorderMock();
    const { host, root } = await renderSettings({ user: teacherUser });
    await openVoiceSampleModal(host);

    await clickSettingsButton('Start recording');

    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(document.body.textContent).toContain('Status: recording');
    expect(document.body.textContent).not.toContain('Microphone permission was denied by the browser or operating system.');

    await clickSettingsButton('Stop recording');

    expect(document.body.textContent).toContain('Status: recorded');
    expect(URL.createObjectURL).toHaveBeenCalled();

    await clickSettingsButton('Use recording');

    await clickSettingsButton('Upload Voice Sample');

    expect(apiMocks.uploadVoiceSample).toHaveBeenCalledTimes(1);
    expect(apiMocks.uploadVoiceSample.mock.calls[0][0]).toBe(teacherUser.id);
    const uploadedFile = apiMocks.uploadVoiceSample.mock.calls[0][1];
    expect(uploadedFile).toBeInstanceOf(File);
    expect(uploadedFile.name).toBe('voice-sample-recording.webm');
    expect(uploadedFile.type).toContain('audio/webm');

    await act(async () => root.unmount());
    host.remove();
  });

  it('falls back to the browser default MediaRecorder MIME type when no candidate type is supported', async () => {
    capabilityMockState.avatarEnabled = true;
    const instances = installMediaRecorderMock();
    window.MediaRecorder.isTypeSupported.mockReturnValue(false);
    const { host, root } = await renderSettings({ user: teacherUser });
    await openVoiceSampleModal(host);

    await clickSettingsButton('Start recording');

    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(instances).toHaveLength(1);
    expect(instances[0].requestedOptions).toBeUndefined();
    expect(document.body.textContent).toContain('Status: recording');

    await act(async () => root.unmount());
    host.remove();
  });

  it('shows an error when a stopped recording captures no audio', async () => {
    capabilityMockState.avatarEnabled = true;
    installMediaRecorderMock({ withAudio: false });
    const { host, root } = await renderSettings({ user: teacherUser });
    await openVoiceSampleModal(host);

    await clickSettingsButton('Start recording');
    await clickSettingsButton('Stop recording');

    expect(document.body.textContent).toContain('No audio was captured.');
    expect(findButton(document.body, 'Use recording').disabled).toBe(true);

    await act(async () => root.unmount());
    host.remove();
  });

  it('keeps existing voice file upload behavior', async () => {
    capabilityMockState.avatarEnabled = true;
    const { host, root } = await renderSettings({ user: teacherUser });
    await openVoiceSampleModal(host);
    const file = new File(['wav audio'], 'manual.wav', { type: 'audio/wav' });
    const input = findLabel(document.body, 'Voice audio').querySelector('input');

    Object.defineProperty(input, 'files', {
      configurable: true,
      value: [file],
    });
    await act(async () => {
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await clickSettingsButton('Upload Voice Sample');

    expect(apiMocks.uploadVoiceSample).toHaveBeenCalledWith(teacherUser.id, file);

    await act(async () => root.unmount());
    host.remove();
  });
});
