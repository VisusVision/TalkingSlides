const SAFE_MOTION_PRESETS = new Set(['natural_conservative', 'natural_visible', 'subtle_blink', 'subtle_gaze']);

export const DEFAULT_AVATAR_RUNTIME_SETTINGS = {
  motion_preset: 'natural_conservative',
  restoration_enabled: false,
  liveportrait_enabled: true,
};

export const AVATAR_MOTION_STYLE_OPTIONS = [
  { value: 'natural_conservative', label: 'Natural' },
  { value: 'natural_visible', label: 'Visible natural' },
  { value: 'subtle_blink', label: 'Blink only' },
  { value: 'subtle_gaze', label: 'Subtle gaze' },
];

function boolValue(value, fallback) {
  if (typeof value === 'boolean') return value;
  if (value === undefined || value === null) return Boolean(fallback);
  const text = String(value).trim().toLowerCase();
  if (['1', 'true', 'yes', 'on'].includes(text)) return true;
  if (['0', 'false', 'no', 'off'].includes(text)) return false;
  return Boolean(fallback);
}

export function normalizeAvatarRuntimeSettings(raw = null, fallback = DEFAULT_AVATAR_RUNTIME_SETTINGS) {
  const source = raw?.avatar_runtime_settings || raw || {};
  const base = {
    ...DEFAULT_AVATAR_RUNTIME_SETTINGS,
    ...(fallback || {}),
  };
  const motionPreset = String(source.motion_preset || base.motion_preset || 'natural_conservative').trim().toLowerCase();
  return {
    motion_preset: SAFE_MOTION_PRESETS.has(motionPreset) ? motionPreset : 'natural_conservative',
    restoration_enabled: boolValue(source.restoration_enabled, base.restoration_enabled),
    liveportrait_enabled: boolValue(source.liveportrait_enabled, base.liveportrait_enabled),
  };
}

export function avatarRuntimeStatusMessage(project) {
  const status = String(project?.avatar_processing_status || 'none').trim().toLowerCase();
  const runtimeStatus = project?.avatar_runtime_status || {};
  if (status === 'queued' || status === 'processing') return 'Avatar processing...';
  if (status === 'failed') return 'Avatar failed. Base video is still published.';
  if (runtimeStatus.warning) return runtimeStatus.warning;
  if (status === 'ready') {
    if (runtimeStatus.musetalk_only_used) return 'Avatar lip-sync completed; motion fallback was used.';
    if (runtimeStatus.static_fallback_used) return 'Avatar used static fallback because motion stage failed.';
    if (runtimeStatus.liveportrait_used) return 'Avatar ready. LivePortrait motion was used.';
    return 'Avatar ready.';
  }
  return 'Avatar disabled.';
}
