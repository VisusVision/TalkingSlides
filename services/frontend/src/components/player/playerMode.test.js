import { describe, expect, it } from 'vitest';

import {
  PLAYER_MODES,
  buildShakaDrmConfig,
  getReadyDrmSystems,
  resolvePlayerMode,
} from './playerMode.js';

function drmLesson(overrides = {}) {
  return {
    protection_mode: 'drm_protected',
    stream_url: '/api/v1/stream/clear-mp4-token/',
    allow_mp4_fallback: true,
    drm: {
      enabled: true,
      ready: true,
      manifest_url: '/api/v1/stream/encrypted-hls-token/',
      systems: {
        widevine: {
          enabled: true,
          ready: true,
          key_system: 'com.widevine.alpha',
          license_url: 'https://drm.example.test/widevine/license',
          certificate_url: '',
          content_type: 'video/mp4',
        },
      },
    },
    ...overrides,
  };
}

describe('getReadyDrmSystems', () => {
  it('returns only ready DRM systems with usable key systems', () => {
    const systems = getReadyDrmSystems({
      drm: {
        systems: {
          widevine: {
            enabled: true,
            ready: true,
            key_system: 'com.widevine.alpha',
            license_url: 'https://drm.example.test/widevine/license',
            certificate_url: '/certificates/widevine',
            content_type: 'video/mp4',
          },
          playready: {
            enabled: false,
            ready: true,
            key_system: 'com.microsoft.playready',
          },
          fairplay: {
            enabled: true,
            ready: false,
            key_system: 'com.apple.fps.1_0',
          },
          missingKeySystem: {
            enabled: true,
            ready: true,
          },
        },
      },
    });

    expect(systems).toEqual([
      {
        name: 'widevine',
        keySystem: 'com.widevine.alpha',
        licenseUrl: 'https://drm.example.test/widevine/license',
        certificateUrl: '/certificates/widevine',
        contentType: 'video/mp4',
      },
    ]);
  });

  it('normalizes array-backed DRM metadata and rejects relative license URLs', () => {
    const systems = getReadyDrmSystems({
      drm: {
        systems: [
          {
            name: 'widevine',
            ready: true,
            keySystem: 'com.widevine.alpha',
            licenseUrl: 'license/widevine',
            certificateUrl: 'blob:https://app.example.test/cert',
            contentType: 'video/mp4',
          },
        ],
      },
    });

    expect(systems).toEqual([]);
  });
});

describe('buildShakaDrmConfig', () => {
  it('maps DRM systems into Shaka license server config', () => {
    const config = buildShakaDrmConfig([
      {
        keySystem: 'com.widevine.alpha',
        licenseUrl: 'https://drm.example.test/widevine/license',
        certificateUrl: 'https://drm.example.test/widevine/cert',
      },
      {
        keySystem: 'com.microsoft.playready',
        licenseUrl: 'https://drm.example.test/playready/license',
      },
      {
        keySystem: 'com.invalid.empty',
        licenseUrl: '',
      },
    ]);

    expect(config).toEqual({
      drm: {
        servers: {
          'com.widevine.alpha': 'https://drm.example.test/widevine/license',
          'com.microsoft.playready': 'https://drm.example.test/playready/license',
        },
        advanced: {
          'com.widevine.alpha': {
            serverCertificateUri: 'https://drm.example.test/widevine/cert',
          },
        },
      },
    });
  });
});

describe('resolvePlayerMode', () => {
  it('does not silently downgrade DRM-required lessons to clear MP4 fallback', () => {
    const mode = resolvePlayerMode(drmLesson(), {
      emeSupported: true,
      drmShakaEnabled: true,
      hlsJsSupported: true,
    });

    expect(mode.mode).toBe(PLAYER_MODES.DRM_SHAKA);
    expect(mode.reason).toBe('drm_shaka_ready');
    expect(mode.manifestUrl).toBe('/api/v1/stream/encrypted-hls-token/');
    expect(mode.fallbackUrl).toBe('');
    expect(mode.fallbackAllowed).toBe(false);
  });

  it('keeps DRM unavailable when the Shaka feature flag is disabled', () => {
    const mode = resolvePlayerMode(drmLesson(), {
      emeSupported: true,
      drmShakaEnabled: false,
      hlsJsSupported: true,
    });

    expect(mode.mode).toBe(PLAYER_MODES.UNAVAILABLE);
    expect(mode.reason).toBe('drm_shaka_disabled');
    expect(mode.fallbackUrl).toBe('');
    expect(mode.fallbackAllowed).toBe(false);
  });

  it('reports EME unavailable for DRM-required lessons without using MP4 fallback', () => {
    const mode = resolvePlayerMode(drmLesson(), {
      emeSupported: false,
      drmShakaEnabled: true,
      hlsJsSupported: true,
    });

    expect(mode.mode).toBe(PLAYER_MODES.UNAVAILABLE);
    expect(mode.reason).toBe('eme_unavailable');
    expect(mode.fallbackUrl).toBe('');
    expect(mode.fallbackAllowed).toBe(false);
  });

  it('requires valid absolute license URLs before selecting protected playback', () => {
    const mode = resolvePlayerMode(drmLesson({
      drm: {
        enabled: true,
        ready: true,
        manifest_url: '/api/v1/stream/encrypted-hls-token/',
        systems: {
          widevine: {
            enabled: true,
            ready: true,
            key_system: 'com.widevine.alpha',
            license_url: '/relative/license',
          },
        },
      },
    }), {
      emeSupported: true,
      drmShakaEnabled: true,
    });

    expect(mode.mode).toBe(PLAYER_MODES.UNAVAILABLE);
    expect(mode.reason).toBe('drm_license_url_missing');
    expect(mode.fallbackUrl).toBe('');
  });

  it('requires DRM metadata readiness before selecting protected playback', () => {
    const mode = resolvePlayerMode(drmLesson({
      drm: {
        enabled: true,
        ready: true,
        manifest_url: '/api/v1/stream/encrypted-hls-token/',
        systems: {
          widevine: {
            enabled: true,
            ready: false,
            key_system: 'com.widevine.alpha',
          },
        },
      },
    }), {
      emeSupported: true,
      drmShakaEnabled: true,
    });

    expect(mode.mode).toBe(PLAYER_MODES.UNAVAILABLE);
    expect(mode.reason).toBe('drm_systems_unavailable');
    expect(mode.fallbackUrl).toBe('');
  });

  it('reports unsupported key systems without using clear fallback', () => {
    const mode = resolvePlayerMode(drmLesson(), {
      emeSupported: true,
      drmShakaEnabled: true,
      supportedDrmKeySystems: ['com.microsoft.playready'],
    });

    expect(mode.mode).toBe(PLAYER_MODES.UNAVAILABLE);
    expect(mode.reason).toBe('drm_key_system_unsupported');
    expect(mode.fallbackUrl).toBe('');
    expect(mode.fallbackAllowed).toBe(false);
  });

  it('uses secure HLS when secure stream is required and HLS is supported', () => {
    const mode = resolvePlayerMode({
      protection_mode: 'secure_stream',
      stream_url: '/api/v1/stream/mp4-token/',
      allow_mp4_fallback: true,
      streaming: {
        hls: {
          manifest_url: '/api/v1/stream/hls-token/',
        },
      },
    }, {
      hlsJsSupported: true,
    });

    expect(mode.mode).toBe(PLAYER_MODES.SECURE_HLS);
    expect(mode.reason).toBe('secure_hls_available');
    expect(mode.manifestUrl).toBe('/api/v1/stream/hls-token/');
    expect(mode.fallbackUrl).toBe('/api/v1/stream/mp4-token/');
    expect(mode.fallbackAllowed).toBe(true);
  });

  it('uses explicit MP4 fallback for secure stream only when HLS is unavailable and fallback is allowed', () => {
    const mode = resolvePlayerMode({
      protection_mode: 'secure_stream',
      stream_url: '/api/v1/stream/mp4-token/',
      streaming: {
        fallback_allowed: 'true',
        hls: {
          manifest_url: '/api/v1/stream/hls-token/',
        },
      },
    }, {
      hlsEnabled: false,
    });

    expect(mode.mode).toBe(PLAYER_MODES.PUBLIC_MP4);
    expect(mode.reason).toBe('secure_hls_unsupported_mp4_fallback');
    expect(mode.fallbackUrl).toBe('/api/v1/stream/mp4-token/');
    expect(mode.fallbackAllowed).toBe(true);
  });

  it('keeps secure stream unavailable when HLS is unavailable and fallback is not allowed', () => {
    const mode = resolvePlayerMode({
      protection_mode: 'secure_stream',
      stream_url: '/api/v1/stream/mp4-token/',
      allow_mp4_fallback: false,
      streaming: {
        hls: {
          manifest_url: '/api/v1/stream/hls-token/',
        },
      },
    }, {
      hlsEnabled: false,
    });

    expect(mode.mode).toBe(PLAYER_MODES.UNAVAILABLE);
    expect(mode.reason).toBe('secure_hls_unsupported');
    expect(mode.fallbackUrl).toBe('');
    expect(mode.fallbackAllowed).toBe(false);
  });

  it('uses clear MP4 when no protected mode is required', () => {
    const mode = resolvePlayerMode({
      stream_url: '/api/v1/stream/public-mp4-token/',
    });

    expect(mode.mode).toBe(PLAYER_MODES.PUBLIC_MP4);
    expect(mode.reason).toBe('default_public_mp4_available');
    expect(mode.fallbackUrl).toBe('/api/v1/stream/public-mp4-token/');
    expect(mode.fallbackAllowed).toBe(true);
  });
});
