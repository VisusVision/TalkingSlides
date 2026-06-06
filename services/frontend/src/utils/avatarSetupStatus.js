export const AVATAR_SETUP_STATES = new Set([
  'missing_consent',
  'missing_portrait',
  'missing_voice',
  'disabled',
  'needs_prepare',
  'preparing',
  'ready',
  'failed',
]);

const DEFAULT_CHECKLIST = {
  portrait_uploaded: false,
  voice_uploaded: false,
  consent_confirmed: false,
  avatar_generation_enabled: false,
  avatar_prepared: false,
};

const STATE_MESSAGES = {
  missing_consent: 'Confirm avatar consent before preparing or generating an avatar.',
  missing_portrait: 'Upload an avatar portrait image.',
  missing_voice: 'Upload a voice sample.',
  disabled: 'Enable avatar generation.',
  needs_prepare: 'Avatar needs to be prepared again.',
  preparing: 'Avatar preparation or preview generation is in progress.',
  ready: 'Avatar is prepared and ready for preview generation.',
  failed: 'Avatar preparation failed. Upload a clear portrait or prepare the avatar again.',
};

const ACTION_LABELS = {
  missing_consent: 'Confirm consent',
  missing_portrait: 'Upload portrait',
  missing_voice: 'Upload voice sample',
  disabled: 'Enable avatar generation',
  needs_prepare: 'Prepare avatar',
  preparing: 'Preparing avatar',
  ready: 'Generate preview',
  failed: 'Re-prepare avatar',
};

function boolValue(value) {
  return value === true || value === 'true' || value === '1' || value === 1;
}

function firstObject(...candidates) {
  return candidates.find((candidate) => candidate && typeof candidate === 'object') || {};
}

function checklistFromLegacy(payload = {}) {
  const profile = payload.profile || {};
  const readiness = payload.readiness || payload.preview_readiness || {};
  const checks = readiness.checks || {};
  return {
    portrait_uploaded: boolValue(checks.avatar_image_original) || Boolean(profile.avatar_image_original),
    voice_uploaded: boolValue(checks.voice_id_exists) || Boolean(checks.voice_id),
    consent_confirmed: boolValue(checks.avatar_consent_confirmed) || boolValue(profile.avatar_consent_confirmed),
    avatar_generation_enabled: boolValue(checks.avatar_enabled) || boolValue(profile.avatar_enabled),
    avatar_prepared: boolValue(readiness.ready),
  };
}

function stateFromChecklist(checklist, payload = {}) {
  const readiness = payload.readiness || payload.preview_readiness || {};
  const missing = new Set(Array.isArray(readiness.missing_requirements) ? readiness.missing_requirements : []);
  if (!checklist.consent_confirmed) return 'missing_consent';
  if (!checklist.portrait_uploaded) return 'missing_portrait';
  if (!checklist.voice_uploaded) return 'missing_voice';
  if (!checklist.avatar_generation_enabled) return 'disabled';
  if (
    missing.has('missing_avatar_image_processed')
    || missing.has('missing_processed_reference_file')
    || missing.has('avatar_source_validation_stale')
  ) {
    return 'needs_prepare';
  }
  return checklist.avatar_prepared ? 'ready' : 'needs_prepare';
}

export function normalizeAvatarSetupStatus(payload = {}) {
  const raw = firstObject(
    payload.avatar_setup_status,
    payload.readiness?.avatar_setup_status,
    payload.preview_readiness?.avatar_setup_status,
    payload.avatar_summary?.avatar_setup_status,
  );
  const checklist = {
    ...DEFAULT_CHECKLIST,
    ...checklistFromLegacy(payload),
    ...(raw.checklist && typeof raw.checklist === 'object' ? raw.checklist : {}),
  };
  const rawState = String(raw.state || '').trim();
  const state = AVATAR_SETUP_STATES.has(rawState) ? rawState : stateFromChecklist(checklist, payload);
  const actionRequired = String(raw.action_required || '').trim() || (
    state === 'ready' ? 'generate_preview' : state === 'needs_prepare' ? 'prepare_avatar' : 'review_avatar_setup'
  );

  return {
    state,
    action_required: actionRequired,
    primary_action_label: String(raw.primary_action_label || '').trim() || ACTION_LABELS[state] || 'Review avatar setup',
    message: String(raw.message || '').trim() || STATE_MESSAGES[state] || 'Avatar setup needs attention.',
    checklist,
    can_prepare: Boolean(raw.can_prepare ?? (
      checklist.consent_confirmed
      && checklist.portrait_uploaded
      && checklist.voice_uploaded
      && checklist.avatar_generation_enabled
      && ['needs_prepare', 'failed'].includes(state)
    )),
    can_generate_preview: Boolean(raw.can_generate_preview ?? state === 'ready'),
    needs_prepare: Boolean(raw.needs_prepare ?? state === 'needs_prepare'),
    preview_ready: Boolean(raw.preview_ready ?? payload.readiness?.avatar_ready ?? payload.avatar_ready),
  };
}

export function avatarChecklistItems(status) {
  const checklist = status?.checklist || DEFAULT_CHECKLIST;
  return [
    { key: 'portrait_uploaded', label: 'Portrait uploaded', complete: Boolean(checklist.portrait_uploaded) },
    { key: 'voice_uploaded', label: 'Voice uploaded', complete: Boolean(checklist.voice_uploaded) },
    { key: 'consent_confirmed', label: 'Consent confirmed', complete: Boolean(checklist.consent_confirmed) },
    { key: 'avatar_generation_enabled', label: 'Avatar generation enabled', complete: Boolean(checklist.avatar_generation_enabled) },
    { key: 'avatar_prepared', label: 'Avatar prepared', complete: Boolean(checklist.avatar_prepared) },
  ];
}

export function avatarSetupErrorMessage(payload = {}, fallback = 'Avatar setup needs attention.') {
  const hasSetupPayload = Boolean(
    payload.avatar_setup_status
    || payload.readiness?.avatar_setup_status
    || payload.preview_readiness?.avatar_setup_status
    || payload.avatar_summary?.avatar_setup_status
    || payload.readiness
    || payload.preview_readiness
  );
  const status = normalizeAvatarSetupStatus(payload);
  const raw = String(payload.error || payload.detail || payload.message || '').trim();
  if (hasSetupPayload && status.message) return status.message;
  if (/missing_|source validation|processed reference|processed avatar/i.test(raw)) {
    return 'Avatar needs to be prepared again.';
  }
  return raw || fallback;
}
