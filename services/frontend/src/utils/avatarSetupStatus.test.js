import { describe, expect, it } from 'vitest';

import {
  avatarChecklistItems,
  avatarSetupErrorMessage,
  normalizeAvatarSetupStatus,
} from './avatarSetupStatus.js';

describe('avatar setup status helpers', () => {
  it('normalizes checklist and preview action from public setup status', () => {
    const status = normalizeAvatarSetupStatus({
      avatar_setup_status: {
        state: 'ready',
        action_required: 'generate_preview',
        checklist: {
          portrait_uploaded: true,
          voice_uploaded: true,
          consent_confirmed: true,
          avatar_generation_enabled: true,
          avatar_prepared: true,
        },
        can_prepare: false,
        can_generate_preview: true,
      },
    });

    expect(status.state).toBe('ready');
    expect(status.can_generate_preview).toBe(true);
    expect(status.can_prepare).toBe(false);
    expect(
      avatarChecklistItems(status).map((item) => [item.label, item.complete]),
    ).toEqual([
        ['Portrait uploaded', true],
        ['Voice uploaded', true],
        ['Consent confirmed', true],
        ['Avatar generation enabled', true],
        ['Avatar prepared', true],
      ]);
  });

  it('maps missing processed-reference legacy requirements to one prepare action', () => {
    const status = normalizeAvatarSetupStatus({
      profile: {
        avatar_enabled: true,
        avatar_consent_confirmed: true,
        avatar_image_original: 'avatars/1/original.png',
      },
      readiness: {
        ready: false,
        missing_requirements: ['missing_processed_reference_file', 'avatar_source_validation_stale'],
        checks: {
          avatar_enabled: true,
          avatar_consent_confirmed: true,
          avatar_image_original: true,
          voice_id_exists: true,
        },
      },
    });

    expect(status.state).toBe('needs_prepare');
    expect(status.action_required).toBe('prepare_avatar');
    expect(status.primary_action_label).toBe('Prepare avatar');
    expect(status.can_prepare).toBe(true);
    expect(status.can_generate_preview).toBe(false);
    expect(status.message).toBe('Avatar needs to be prepared again.');
  });

  it('preserves backend re-prepare labels for stale prepared assets', () => {
    const status = normalizeAvatarSetupStatus({
      avatar_setup_status: {
        state: 'needs_prepare',
        action_required: 'prepare_avatar',
        primary_action_label: 'Re-prepare avatar',
        message: 'Avatar needs to be prepared again.',
        checklist: {
          portrait_uploaded: true,
          voice_uploaded: true,
          consent_confirmed: true,
          avatar_generation_enabled: true,
          avatar_prepared: false,
        },
        can_prepare: true,
      },
    });

    expect(status.primary_action_label).toBe('Re-prepare avatar');
  });

  it('does not surface raw missing-reference backend text', () => {
    const message = avatarSetupErrorMessage({
      readiness: {
        missing_requirements: ['missing_processed_reference_file'],
        checks: {
          avatar_enabled: true,
          avatar_consent_confirmed: true,
          avatar_image_original: true,
          voice_id_exists: true,
        },
      },
      error: 'Processed avatar reference file is missing on disk; re-prepare avatar.',
    });

    expect(message).toBe('Avatar needs to be prepared again.');
  });
});
