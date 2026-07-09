const STUDIO_WORKSPACE_MESSAGES = {
  en: {
    slides: 'Slides',
    slidesHint: 'Select a slide to edit',
    loadingSlides: 'Loading slides',
    noSlides: 'No slides yet',
    noSlidesHint: 'Upload a source file to create the first slide.',
    slide: 'Slide',
    renderStatus: 'Render status',
    renderReady: 'Ready',
    renderProcessing: 'Processing',
    renderQueued: 'Queued',
    renderFailed: 'Failed',
    renderDraft: 'Not rendered',
    renderIdleHint: 'Render activity will appear here.',
    renderActiveHint: 'You can keep editing while this render continues.',
    renderReadyHint: 'The latest video is ready to preview.',
    renderFailedHint: 'Review the error and try rendering again.',
    inspector: 'Inspector',
    inspectorHint: 'Controls for the selected slide',
    moreActions: 'More actions',
  },
  tr: {
    slides: 'Slaytlar',
    slidesHint: 'Düzenlemek için bir slayt seçin',
    loadingSlides: 'Slaytlar yükleniyor',
    noSlides: 'Henüz slayt yok',
    noSlidesHint: 'İlk slaytı oluşturmak için bir kaynak dosya yükleyin.',
    slide: 'Slayt',
    renderStatus: 'Render durumu',
    renderReady: 'Hazır',
    renderProcessing: 'İşleniyor',
    renderQueued: 'Sırada',
    renderFailed: 'Başarısız',
    renderDraft: 'Render edilmedi',
    renderIdleHint: 'Render etkinliği burada görünecek.',
    renderActiveHint: 'Render devam ederken düzenlemeye devam edebilirsiniz.',
    renderReadyHint: 'En son video önizlemeye hazır.',
    renderFailedHint: 'Hatayı inceleyip yeniden render etmeyi deneyin.',
    inspector: 'Denetleyici',
    inspectorHint: 'Seçili slaytın kontrolleri',
    moreActions: 'Diğer işlemler',
  },
};

export function studioWorkspaceLocale(rawLocale = '') {
  const baseLocale = String(rawLocale || '').trim().toLowerCase().split(/[-_]/)[0];
  return Object.prototype.hasOwnProperty.call(STUDIO_WORKSPACE_MESSAGES, baseLocale) ? baseLocale : 'en';
}

export function studioWorkspaceCopy(rawLocale = '') {
  return STUDIO_WORKSPACE_MESSAGES[studioWorkspaceLocale(rawLocale)];
}
