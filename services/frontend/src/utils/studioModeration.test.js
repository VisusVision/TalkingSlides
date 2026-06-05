import { describe, expect, it } from 'vitest';

import {
  editorSaveAvailability,
  isStudioVisualModerationIssue,
  visualModerationRerenderMessage,
} from './studioModeration.js';

describe('studio visual moderation gates', () => {
  it('keeps Save enabled while visual moderation is pending', () => {
    const message = visualModerationRerenderMessage({
      issues: [{
        issue_type: 'visual',
        source_kind: 'slide_image',
        moderation_state: 'pending_scan',
      }],
    });
    const availability = editorSaveAvailability({
      hasChanges: true,
      requiresRerender: true,
      moderationMessage: message,
    });

    expect(message).toBe('Visual scan pending before rerender.');
    expect(availability.canSaveChanges).toBe(true);
    expect(availability.canSaveRerender).toBe(false);
  });

  it('keeps Save enabled while provider-unavailable visual moderation needs review', () => {
    const message = visualModerationRerenderMessage({
      issues: [{
        issue_type: 'visual',
        source_kind: 'lesson_cover',
        category: 'provider_unavailable',
        moderation_state: 'needs_admin_review',
        reason_title: 'Visual safety scan unavailable',
      }],
    });
    const availability = editorSaveAvailability({
      hasChanges: true,
      requiresRerender: true,
      moderationMessage: message,
    });

    expect(message).toBe('Visual safety scan needs admin review before rerender.');
    expect(availability.canSaveChanges).toBe(true);
    expect(availability.canSaveRerender).toBe(false);
  });

  it('locks Save and Rerender with unsafe visual wording for blocked visuals', () => {
    const message = visualModerationRerenderMessage({
      issues: [{
        issue_type: 'visual',
        source_kind: 'scene_background',
        category: 'violence',
        moderation_state: 'blocked',
      }],
    });

    expect(message).toBe('Replace the blocked visual before rerender.');
  });

  it('does not treat text-only moderation findings as visual warnings', () => {
    const issue = {
      issue_type: 'text',
      source_kind: 'transcript_text',
      category: 'violence_text',
      moderation_state: 'blocked',
    };

    expect(isStudioVisualModerationIssue(issue)).toBe(false);
    expect(visualModerationRerenderMessage({ issues: [issue] })).toBe('');
  });

  it('does not treat Azure text safety findings as visual warnings', () => {
    const issue = {
      source_kind: 'transcript_text',
      content_type: 'text',
      provider: 'azure_content_safety',
      category: 'violence',
      moderation_state: 'blocked',
    };

    expect(isStudioVisualModerationIssue(issue)).toBe(false);
    expect(visualModerationRerenderMessage({ issues: [issue] })).toBe('');
  });

  it('unlocks Save and Rerender for safe visual scan state', () => {
    const message = visualModerationRerenderMessage({
      issues: [{
        issue_type: 'visual',
        source_kind: 'slide_image',
        moderation_state: 'scan_passed',
        decision: 'allow',
      }],
    });
    const availability = editorSaveAvailability({
      hasChanges: true,
      requiresRerender: true,
      moderationMessage: message,
    });

    expect(message).toBe('');
    expect(availability.canSaveChanges).toBe(true);
    expect(availability.canSaveRerender).toBe(true);
  });
});
