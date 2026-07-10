import { translateAppMessage } from '../../i18n/messages';

const STUDIO_WORKSPACE_KEYS = Object.freeze([
  'slides',
  'slidesHint',
  'slideCount',
  'loadingSlides',
  'noSlides',
  'noSlidesHint',
  'slide',
  'slideActions',
  'selectSlide',
  'moveUp',
  'moveDown',
  'duplicate',
  'duplicateUnavailable',
  'delete',
  'copy',
  'paste',
  'pasteUnavailable',
  'rename',
  'renameUnavailable',
  'dragToReorder',
  'renderStatus',
  'renderReady',
  'renderProcessing',
  'renderQueued',
  'renderFailed',
  'renderDraft',
  'renderIdleHint',
  'renderActiveHint',
  'renderReadyHint',
  'renderFailedHint',
  'inspector',
  'inspectorHint',
  'moreActions',
  'saving',
  'saved',
  'unsavedChanges',
  'upToDate',
  'lastSaved',
  'neverSaved',
  'noProjectTitle',
  'noProjectHint',
  'noSlideTitle',
  'noSlideHint',
  'noAssetsTitle',
  'noAssetsHint',
  'noAvatarTitle',
  'noAvatarHint',
  'noNarrationTitle',
  'noNarrationHint',
]);

export function studioWorkspaceLocale(rawLocale = '') {
  const normalized = String(rawLocale || '').trim().replace(/_/g, '-').toLowerCase();
  if (normalized === 'zh' || normalized.startsWith('zh-cn') || normalized.startsWith('zh-hans')) return 'zh-CN';
  return normalized.split('-')[0] || 'en';
}

export function studioWorkspaceCopy(rawLocale = '') {
  const locale = studioWorkspaceLocale(rawLocale);
  return Object.fromEntries(STUDIO_WORKSPACE_KEYS.map((key) => [
    key,
    translateAppMessage(locale, `studioWorkspace${key.charAt(0).toUpperCase()}${key.slice(1)}`),
  ]));
}
