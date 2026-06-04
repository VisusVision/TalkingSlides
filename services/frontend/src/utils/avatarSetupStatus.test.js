import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

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

    assert.equal(status.state, 'ready');
    assert.equal(status.can_generate_preview, true);
    assert.equal(status.can_prepare, false);
    assert.deepEqual(
      avatarChecklistItems(status).map((item) => [item.label, item.complete]),
      [
        ['Portrait uploaded', true],
        ['Voice uploaded', true],
        ['Consent confirmed', true],
        ['Avatar generation enabled', true],
        ['Avatar prepared', true],
      ],
    );
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

    assert.equal(status.state, 'needs_prepare');
    assert.equal(status.action_required, 'prepare_avatar');
    assert.equal(status.primary_action_label, 'Prepare avatar');
    assert.equal(status.can_prepare, true);
    assert.equal(status.can_generate_preview, false);
    assert.equal(status.message, 'Avatar needs to be prepared again.');
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

    assert.equal(status.primary_action_label, 'Re-prepare avatar');
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

    assert.equal(message, 'Avatar needs to be prepared again.');
  });
});
