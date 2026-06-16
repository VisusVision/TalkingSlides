export const PLAYER_MODES = Object.freeze({
  PUBLIC_MP4: 'public_mp4',
  SECURE_HLS: 'secure_hls',
  DRM_SHAKA: 'drm_shaka',
  UNAVAILABLE: 'unavailable',
});

export const PROTECTED_PLAYBACK_UNAVAILABLE_MESSAGE = 'This lesson requires protected playback, but DRM playback is not available in this browser or environment.';

function cleanString(value) {
  return String(value || '').trim();
}

function normalizeMode(value) {
  const mode = cleanString(value).toLowerCase();
  if (mode === 'drm' || mode === 'protected') return 'drm_protected';
  if (mode === 'secure' || mode === 'secure_hls') return 'secure_stream';
  if (mode === 'mp4') return 'public';
  return mode;
}

function playbackUrl(value) {
  const url = cleanString(value);
  if (!url) return '';
  if (/^(https?:|blob:)/i.test(url) || url.startsWith('/')) return url;
  return '';
}

function absoluteHttpUrl(value) {
  const url = cleanString(value);
  if (!/^https?:\/\//i.test(url)) return '';
  try {
    return new URL(url).href;
  } catch {
    return '';
  }
}

function booleanTrue(value) {
  return value === true || value === 'true' || value === 1 || value === '1';
}

function protectionModeForLesson(lesson) {
  return normalizeMode(
    lesson?.protection_mode
      || lesson?.playback_status?.protection_mode
      || lesson?.protection?.mode
      || '',
  );
}

function mp4UrlForLesson(lesson) {
  return playbackUrl(
    lesson?.stream_url
      || lesson?.video_url
      || lesson?.streaming?.fallback?.url
      || '',
  );
}

function hlsManifestUrlForLesson(lesson) {
  return playbackUrl(lesson?.streaming?.hls?.manifest_url || '');
}

function drmManifestUrlForLesson(lesson) {
  return playbackUrl(
    lesson?.drm?.manifest_url
      || lesson?.streaming?.hls?.manifest_url
      || '',
  );
}

function mp4FallbackAllowed(lesson) {
  if (Object.prototype.hasOwnProperty.call(lesson || {}, 'allow_mp4_fallback')) {
    return booleanTrue(lesson?.allow_mp4_fallback);
  }
  if (Object.prototype.hasOwnProperty.call(lesson?.streaming || {}, 'fallback_allowed')) {
    return booleanTrue(lesson?.streaming?.fallback_allowed);
  }
  if (Object.prototype.hasOwnProperty.call(lesson?.streaming?.fallback || {}, 'allowed')) {
    return booleanTrue(lesson?.streaming?.fallback?.allowed);
  }
  return false;
}

function hlsPlaybackSupported(capabilities = {}) {
  if (capabilities.hlsEnabled === false) return false;
  return Boolean(capabilities.nativeHlsSupported || capabilities.hlsJsSupported);
}

function unavailable(reason, message, extra = {}) {
  return {
    mode: PLAYER_MODES.UNAVAILABLE,
    reason,
    message,
    manifestUrl: '',
    fallbackUrl: '',
    fallbackAllowed: false,
    ...extra,
  };
}

export function getReadyDrmSystems(lesson) {
  const systems = lesson?.drm?.systems;
  if (!systems || typeof systems !== 'object') return [];

  const entries = Array.isArray(systems)
    ? systems.map((system, index) => [system?.name || system?.key_system || String(index), system])
    : Object.entries(systems);

  return entries
    .map(([name, system]) => {
      if (!system || typeof system !== 'object') return null;
      const keySystem = cleanString(system.key_system || system.keySystem || system.system);
      if (!keySystem || system.enabled === false || system.ready !== true) return null;
      const licenseUrl = absoluteHttpUrl(system.license_url || system.licenseUrl);
      if (!licenseUrl) return null;
      return {
        name,
        keySystem,
        licenseUrl,
        certificateUrl: playbackUrl(system.certificate_url || system.certificateUrl),
        contentType: cleanString(system.content_type || system.contentType),
      };
    })
    .filter(Boolean);
}

function getMetadataReadyDrmSystems(lesson) {
  const systems = lesson?.drm?.systems;
  if (!systems || typeof systems !== 'object') return [];

  const entries = Array.isArray(systems)
    ? systems.map((system, index) => [system?.name || system?.key_system || String(index), system])
    : Object.entries(systems);

  return entries
    .map(([name, system]) => {
      if (!system || typeof system !== 'object') return null;
      const keySystem = cleanString(system.key_system || system.keySystem || system.system);
      if (!keySystem || system.enabled === false || system.ready !== true) return null;
      return {
        name,
        keySystem,
        licenseUrl: cleanString(system.license_url || system.licenseUrl),
      };
    })
    .filter(Boolean);
}

function supportedDrmSystemsForCapabilities(drmSystems, capabilities = {}) {
  const supported = Array.isArray(capabilities.supportedDrmKeySystems)
    ? capabilities.supportedDrmKeySystems.map((value) => cleanString(value).toLowerCase()).filter(Boolean)
    : null;
  if (!supported) return drmSystems;
  return drmSystems.filter((system) => supported.includes(system.keySystem.toLowerCase()));
}

export function buildLicenseRequestHeaders() {
  return {};
}

export function buildShakaDrmConfig(drmSystems = []) {
  const servers = {};
  const advanced = {};

  for (const system of drmSystems) {
    const keySystem = cleanString(system?.keySystem || system?.key_system);
    const licenseUrl = absoluteHttpUrl(system?.licenseUrl || system?.license_url);
    if (!keySystem || !licenseUrl) continue;
    servers[keySystem] = licenseUrl;

    const certificateUrl = playbackUrl(system?.certificateUrl || system?.certificate_url);
    if (certificateUrl) {
      advanced[keySystem] = { serverCertificateUri: certificateUrl };
    }
  }

  return {
    drm: {
      servers,
      ...(Object.keys(advanced).length ? { advanced } : {}),
    },
  };
}

export function resolvePlayerMode(lesson, capabilities = {}) {
  if (!lesson) {
    return unavailable('lesson_missing', 'Video source unavailable for this lesson.');
  }

  const protectionMode = protectionModeForLesson(lesson);
  const mp4Url = mp4UrlForLesson(lesson);
  const allowFallback = mp4FallbackAllowed(lesson);

  if (protectionMode === 'drm_protected') {
    const manifestUrl = drmManifestUrlForLesson(lesson);
    const drm = lesson?.drm || {};
    const readyDrmSystems = getReadyDrmSystems(lesson);
    const metadataReadySystems = getMetadataReadyDrmSystems(lesson);

    if (!manifestUrl) {
      return unavailable('drm_manifest_missing', PROTECTED_PLAYBACK_UNAVAILABLE_MESSAGE);
    }
    if (drm.enabled !== true) {
      return unavailable('drm_disabled', PROTECTED_PLAYBACK_UNAVAILABLE_MESSAGE);
    }
    if (drm.ready !== true) {
      return unavailable('drm_not_ready', PROTECTED_PLAYBACK_UNAVAILABLE_MESSAGE);
    }
    if (!readyDrmSystems.length) {
      return unavailable(
        metadataReadySystems.length ? 'drm_license_url_missing' : 'drm_systems_unavailable',
        PROTECTED_PLAYBACK_UNAVAILABLE_MESSAGE,
      );
    }
    if (!capabilities.emeSupported) {
      return unavailable('eme_unavailable', PROTECTED_PLAYBACK_UNAVAILABLE_MESSAGE);
    }
    if (!capabilities.drmShakaEnabled) {
      return unavailable('drm_shaka_disabled', PROTECTED_PLAYBACK_UNAVAILABLE_MESSAGE);
    }

    const supportedDrmSystems = supportedDrmSystemsForCapabilities(readyDrmSystems, capabilities);
    if (!supportedDrmSystems.length) {
      return unavailable('drm_key_system_unsupported', PROTECTED_PLAYBACK_UNAVAILABLE_MESSAGE);
    }

    return {
      mode: PLAYER_MODES.DRM_SHAKA,
      reason: 'drm_shaka_ready',
      message: '',
      manifestUrl,
      fallbackUrl: '',
      fallbackAllowed: false,
      drmSystems: supportedDrmSystems,
    };
  }

  if (protectionMode === 'secure_stream') {
    const manifestUrl = hlsManifestUrlForLesson(lesson);
    if (manifestUrl && hlsPlaybackSupported(capabilities)) {
      return {
        mode: PLAYER_MODES.SECURE_HLS,
        reason: 'secure_hls_available',
        manifestUrl,
        fallbackUrl: allowFallback ? mp4Url : '',
        fallbackAllowed: Boolean(allowFallback && mp4Url),
      };
    }
    if (allowFallback && mp4Url) {
      return {
        mode: PLAYER_MODES.PUBLIC_MP4,
        reason: manifestUrl ? 'secure_hls_unsupported_mp4_fallback' : 'secure_hls_missing_mp4_fallback',
        manifestUrl: '',
        fallbackUrl: mp4Url,
        fallbackAllowed: true,
      };
    }
    return unavailable(
      manifestUrl ? 'secure_hls_unsupported' : 'secure_hls_missing',
      'Secure stream is not available for this lesson.',
      { manifestUrl },
    );
  }

  if (mp4Url) {
    return {
      mode: PLAYER_MODES.PUBLIC_MP4,
      reason: protectionMode ? 'public_mp4_available' : 'default_public_mp4_available',
      manifestUrl: '',
      fallbackUrl: mp4Url,
      fallbackAllowed: true,
    };
  }

  return unavailable('mp4_missing', 'Video source unavailable for this lesson.');
}
