const TEXT_CONTENT_TYPES = new Set(['text', 'ocr', 'transcript', 'subtitle', 'language']);
const TEXT_CATEGORIES = new Set([
  'abusive_language',
  'copyright_text',
  'dangerous_instruction',
  'hate_or_harassment',
  'inappropriate_language',
  'language',
  'profanity',
  'self_harm_instruction',
  'sexual_text',
  'text_moderation',
  'violence_text',
]);
const VISUAL_CATEGORIES = new Set(['sexual', 'violence', 'graphic_content', 'self_harm', 'provider_unavailable']);
const VISUAL_ASSET_KINDS = new Set([
  'cover',
  'custom_background',
  'slide_image',
  'draft_visual_asset',
  'video_frame',
  'profile_image',
  'channel_logo',
  'channel_banner',
]);
const VISUAL_SOURCE_KINDS = new Set([
  'lesson_cover',
  'scene_background',
  'slide_image',
  'video_frame',
  'profile_image',
  'channel_logo',
  'channel_banner',
]);
const PENDING_STATES = new Set(['pending_scan', 'pending', 'processing', 'running', 'needs_rescan', 'not_scanned']);
const UNSAFE_STATES = new Set(['blocked', 'block', 'rejected', 'reject', 'revision_required', 'admin_rejected']);
const REVIEW_STATES = new Set(['needs_admin_review']);

function textValue(value) {
  if (value === null || value === undefined) return '';
  return String(value);
}

function normalized(value) {
  return textValue(value).trim().toLowerCase();
}

export function isStudioTextModerationIssue(issue) {
  const sourceKind = normalized(issue?.source_kind);
  const contentType = normalized(issue?.content_type);
  const provider = normalized(issue?.provider);
  const objectType = normalized(issue?.object_type);
  const category = normalized(issue?.category);
  const issueType = normalized(issue?.issue_type || issue?.finding_type || issue?.type);
  return issueType === 'text'
    || sourceKind === 'transcript_text'
    || TEXT_CONTENT_TYPES.has(contentType)
    || provider.includes('ocr')
    || provider.includes('text')
    || objectType.includes('ocr')
    || TEXT_CATEGORIES.has(category);
}

export function isStudioVisualModerationIssue(issue) {
  if (!issue || isStudioTextModerationIssue(issue)) return false;
  const assetKind = normalized(issue?.asset_kind || issue?.object_type);
  const sourceKind = normalized(issue?.source_kind);
  const contentType = normalized(issue?.content_type);
  const provider = normalized(issue?.provider);
  const category = normalized(issue?.category);
  return VISUAL_ASSET_KINDS.has(assetKind)
    || VISUAL_SOURCE_KINDS.has(sourceKind)
    || contentType === 'image'
    || contentType === 'video_frame'
    || provider.includes('visual')
    || VISUAL_CATEGORIES.has(category);
}

export function isStudioProviderUnavailableVisualIssue(issue) {
  if (!isStudioVisualModerationIssue(issue)) return false;
  const category = normalized(issue?.category);
  const title = normalized(issue?.reason_title);
  const technicalReason = normalized(issue?.technical_reason || issue?.evidence_excerpt);
  const provider = normalized(issue?.provider);
  return category === 'provider_unavailable'
    || title === 'visual safety scan unavailable'
    || technicalReason.includes('semantic_visual_provider_unavailable')
    || technicalReason.includes('azure_content_safety_missing_config')
    || technicalReason.includes('azure_content_safety_timeout')
    || technicalReason.includes('azure_content_safety_request_error')
    || technicalReason.includes('azure_content_safety_invalid_response')
    || provider.includes('provider_unavailable');
}

function issueState(issue) {
  return normalized(
    issue?.moderation_state
      || issue?.decision
      || issue?.status
      || issue?.moderation_status
      || issue?.final_decision,
  );
}

function markerState(marker) {
  if (!marker || typeof marker !== 'object') return '';
  return normalized(marker.status || marker.state || marker.final_decision);
}

function markerNeedsVisualScan(marker) {
  if (!marker || typeof marker !== 'object') return false;
  return Boolean(
    marker.needs_rescan
      || marker.needs_recheck
      || marker.stale
      || PENDING_STATES.has(markerState(marker)),
  );
}

export function visualModerationRerenderMessage({
  issues = [],
  moderationStatus = '',
  draftModerationStatus = '',
  visualMarker = null,
} = {}) {
  const visualIssues = (Array.isArray(issues) ? issues : []).filter(isStudioVisualModerationIssue);
  if (visualIssues.some((issue) => UNSAFE_STATES.has(issueState(issue)) && !isStudioProviderUnavailableVisualIssue(issue))) {
    return 'Replace the blocked visual before rerender.';
  }
  if (visualIssues.some((issue) => isStudioProviderUnavailableVisualIssue(issue) || REVIEW_STATES.has(issueState(issue)))) {
    return 'Visual safety scan needs admin review before rerender.';
  }
  if (
    visualIssues.some((issue) => PENDING_STATES.has(issueState(issue)))
    || PENDING_STATES.has(normalized(moderationStatus))
    || PENDING_STATES.has(normalized(draftModerationStatus))
    || markerNeedsVisualScan(visualMarker)
  ) {
    return 'Visual scan pending before rerender.';
  }
  if (REVIEW_STATES.has(normalized(draftModerationStatus))) {
    return 'Visual safety scan needs admin review before rerender.';
  }
  if (UNSAFE_STATES.has(normalized(draftModerationStatus))) {
    return 'Replace the blocked visual before rerender.';
  }
  return '';
}

export function editorSaveAvailability({
  hasChanges = false,
  requiresRerender = false,
  moderationMessage = '',
} = {}) {
  return {
    canSaveChanges: Boolean(hasChanges),
    canSaveRerender: Boolean(requiresRerender && !moderationMessage),
  };
}

export function adminReviewBackLabel({
  reportId = null,
  source = '',
  sourceItem = '',
  returnTo = '',
} = {}) {
  const normalizedSource = normalized(source);
  const normalizedSourceItem = normalized(sourceItem);
  const normalizedReturnTo = normalized(returnTo);
  const hasReportContext = Boolean(Number(reportId || 0))
    || normalizedSource === 'report'
    || normalizedSourceItem.startsWith('report:')
    || normalizedReturnTo.includes('/moderation/reports')
    || normalizedReturnTo.includes('report=');
  return hasReportContext ? 'Back to report' : 'Back to moderation';
}
