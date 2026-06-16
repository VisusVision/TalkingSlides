import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const instances = [];
  const installAll = vi.fn();
  const isBrowserSupported = vi.fn(() => true);
  const loadBehavior = vi.fn(() => Promise.resolve());

  function MockPlayer(video) {
    this.video = video;
    this.configure = vi.fn();
    this.load = vi.fn((...args) => loadBehavior(...args));
    this.destroy = vi.fn(() => Promise.resolve());
    this.addEventListener = vi.fn();
    this.removeEventListener = vi.fn();
    this.getNetworkingEngine = vi.fn(() => ({
      registerRequestFilter: vi.fn(),
    }));
    instances.push(this);
  }

  MockPlayer.isBrowserSupported = isBrowserSupported;

  return {
    instances,
    installAll,
    isBrowserSupported,
    loadBehavior,
    MockPlayer,
    requestMediaKeySystemAccess: vi.fn(() => Promise.resolve({})),
  };
});

vi.mock('shaka-player', () => ({
  default: {
    polyfill: {
      installAll: mocks.installAll,
    },
    Player: mocks.MockPlayer,
    net: {
      NetworkingEngine: {
        RequestType: {
          LICENSE: 'LICENSE',
        },
      },
    },
  },
}));

vi.mock('./AvatarOverlayLayer', () => ({
  AVATAR_OVERLAY_Z_INDEX: {
    baseVideo: 1,
    captions: 2,
    videoControls: 3,
  },
  default: () => <div data-testid="avatar-overlay" />,
}));

vi.mock('./WatermarkOverlay', () => ({
  default: () => <div data-testid="watermark-overlay" />,
}));

vi.mock('../ui/SurfaceCard', () => ({
  default: ({ children }) => <div>{children}</div>,
}));

import DrmShakaPlayer, { browserSupportsAnyDrmSystem } from './DrmShakaPlayer';

const drmSystems = [
  {
    name: 'widevine',
    keySystem: 'com.widevine.alpha',
    licenseUrl: 'https://drm.example.test/widevine/license',
    certificateUrl: 'https://drm.example.test/widevine/cert',
    contentType: 'application/vnd.apple.mpegurl',
  },
];

async function renderPlayer(props = {}) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);

  await act(async () => {
    root.render(
      <DrmShakaPlayer
        lesson={{ id: 101, watermark: { enabled: true } }}
        manifestUrl="https://api.example.test/api/v1/stream/hls-token/"
        drmSystems={drmSystems}
        {...props}
      />,
    );
    await Promise.resolve();
    await Promise.resolve();
  });

  return { host, root };
}

describe('browserSupportsAnyDrmSystem', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window.navigator, 'requestMediaKeySystemAccess', {
      value: mocks.requestMediaKeySystemAccess,
      configurable: true,
    });
    mocks.requestMediaKeySystemAccess.mockResolvedValue({});
  });

  it('returns true when any configured key system is supported', async () => {
    await expect(browserSupportsAnyDrmSystem(drmSystems)).resolves.toBe(true);
    expect(mocks.requestMediaKeySystemAccess).toHaveBeenCalledWith(
      'com.widevine.alpha',
      expect.arrayContaining([
        expect.objectContaining({
          videoCapabilities: [{ contentType: 'application/vnd.apple.mpegurl' }],
        }),
      ]),
    );
  });

  it('returns false when no configured key system is supported', async () => {
    mocks.requestMediaKeySystemAccess.mockRejectedValue(new Error('unsupported'));

    await expect(browserSupportsAnyDrmSystem(drmSystems)).resolves.toBe(false);
  });
});

describe('DrmShakaPlayer', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    vi.clearAllMocks();
    mocks.instances.length = 0;
    mocks.isBrowserSupported.mockReturnValue(true);
    mocks.loadBehavior.mockResolvedValue(undefined);
    Object.defineProperty(window.navigator, 'requestMediaKeySystemAccess', {
      value: mocks.requestMediaKeySystemAccess,
      configurable: true,
    });
    mocks.requestMediaKeySystemAccess.mockResolvedValue({});
  });

  it('configures Shaka with DRM metadata and loads the protected manifest', async () => {
    const { root, host } = await renderPlayer();
    const player = mocks.instances[0];

    expect(mocks.installAll).toHaveBeenCalled();
    expect(player.configure).toHaveBeenCalledWith({
      drm: {
        servers: {
          'com.widevine.alpha': 'https://drm.example.test/widevine/license',
        },
        advanced: {
          'com.widevine.alpha': {
            serverCertificateUri: 'https://drm.example.test/widevine/cert',
          },
        },
      },
    });
    expect(player.load).toHaveBeenCalledWith('https://api.example.test/api/v1/stream/hls-token/');
    expect(host.textContent).not.toContain('MP4');

    await act(async () => root.unmount());
    host.remove();
  });

  it('shows an explicit key-system error when no configured system is supported', async () => {
    mocks.requestMediaKeySystemAccess.mockRejectedValue(new Error('unsupported'));
    const onPlaybackError = vi.fn();

    const { root, host } = await renderPlayer({ onPlaybackError });

    expect(host.textContent).toContain('This browser does not support the protected playback key system for this lesson.');
    expect(onPlaybackError).toHaveBeenCalledWith(expect.objectContaining({
      reason: 'drm_key_system_unsupported',
    }));
    expect(mocks.instances).toHaveLength(0);

    await act(async () => root.unmount());
    host.remove();
  });

  it('surfaces license/init failures as user-visible DRM errors', async () => {
    const loadError = { data: ['license request failed'] };
    mocks.loadBehavior.mockRejectedValueOnce(loadError);

    const onPlaybackError = vi.fn();
    const { root, host } = await renderPlayer({ onPlaybackError });

    expect(host.textContent).toContain('Protected playback license request failed.');
    expect(onPlaybackError).toHaveBeenCalledWith(expect.objectContaining({
      reason: 'drm_license_request_failed',
    }));

    await act(async () => root.unmount());
    host.remove();
  });
});
