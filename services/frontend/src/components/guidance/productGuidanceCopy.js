import { useEffect, useState } from 'react';

const EN = {
  eyebrow: 'Smart guidance',
  actionTitle: 'Next action',
  itemsLabel: 'Guidance items',
  statusReady: 'Ready',
  statusNeedsAttention: 'Needs attention',
  statusProcessing: 'Processing',
  statusFailed: 'Failed',
  statusCompleted: 'Completed',
  statusNeutral: 'Current state',
  libraryEyebrow: 'Learning guidance',
  libraryContinueTitle: 'Continue where you left off.',
  libraryContinueDescription: 'Your latest watched lesson has real progress saved.',
  libraryContinueAction: 'Continue lesson',
  libraryContinueItemTitle: 'In progress',
  libraryContinueItemDescription: 'Progress is saved from your watch history.',
  libraryLikedTitle: 'Saved lessons are ready.',
  libraryLikedDescription: 'Open a liked lesson or use the tabs to review your collections.',
  libraryLikedAction: 'Open liked lessons',
  libraryFollowingTitle: 'Followed publishers have activity.',
  libraryFollowingDescription: 'Check the publishers you follow and their latest public lessons.',
  libraryFollowingAction: 'Open following',
  libraryPlaylistsTitle: 'Saved playlists are ready.',
  libraryPlaylistsDescription: 'Pick up a saved playlist from your library.',
  libraryPlaylistsAction: 'Open playlists',
  libraryEmptyTitle: 'Start building your library.',
  libraryEmptyDescription: 'Watched, liked, followed, and saved lessons will appear here after you use them.',
  libraryEmptyAction: 'Browse catalog',
  browseEyebrow: 'Browse guidance',
  browseReadyTitle: 'Published lessons are available.',
  browseReadyDescription: 'Open a visible lesson or narrow the catalog with real categories.',
  browseReadyAction: 'Open first lesson',
  browseCategoryItemTitle: 'Active category',
  browseCategoryItemDescription: 'The catalog is filtered by your selected category.',
  browseFilteredTitle: 'No lessons match this view.',
  browseFilteredDescription: 'Clear the category filter or try a different search term.',
  browseFilteredAction: 'Clear category',
  browseEmptyTitle: 'No published lessons are visible yet.',
  browseEmptyDescription: 'The catalog will show public lessons when they are available.',
  browseEmptyAction: 'Show all categories',
  notificationsEyebrow: 'Notification guidance',
  notificationsFailedTitle: 'A render update needs attention.',
  notificationsFailedDescription: 'Review the unread failure before clearing the notification queue.',
  notificationsUnreadTitle: 'Unread notifications are waiting.',
  notificationsUnreadDescription: 'Open the newest unread item or mark the queue read when finished.',
  notificationsUnreadAction: 'Mark all read',
  notificationsCaughtUpTitle: 'You are all caught up.',
  notificationsCaughtUpDescription: 'There are no unread notifications right now.',
  notificationsEmptyTitle: 'No notifications yet.',
  notificationsEmptyDescription: 'Comments, followed-publisher updates, and render status changes will appear here.',
  notificationsFailureItemTitle: 'Unread failure',
  notificationsUnreadItemTitle: 'Unread item',
  notificationsOpenAction: 'Open notification',
};

const PRODUCT_GUIDANCE_MESSAGES = {
  en: EN,
  tr: {
    eyebrow: 'Akilli rehberlik',
    actionTitle: 'Sonraki adim',
    itemsLabel: 'Rehberlik ogeleri',
    statusReady: 'Hazir',
    statusNeedsAttention: 'Ilgi gerekiyor',
    statusProcessing: 'Isleniyor',
    statusFailed: 'Basarisiz',
    statusCompleted: 'Tamamlandi',
    statusNeutral: 'Guncel durum',
    libraryEyebrow: 'Ogrenme rehberligi',
    libraryContinueTitle: 'Kaldigin yerden devam et.',
    libraryContinueDescription: 'Son izlenen derste gercek ilerleme kaydi var.',
    libraryContinueAction: 'Derse devam et',
    libraryContinueItemTitle: 'Devam ediyor',
    libraryContinueItemDescription: 'Ilerleme izleme gecmisinden kaydedildi.',
    libraryLikedTitle: 'Kayitli dersler hazir.',
    libraryLikedDescription: 'Begenilen bir dersi ac veya koleksiyonlarini sekmelerden incele.',
    libraryLikedAction: 'Begenilenleri ac',
    libraryFollowingTitle: 'Takip edilen yayinlarda etkinlik var.',
    libraryFollowingDescription: 'Takip ettigin yayinlari ve en yeni herkese acik dersleri kontrol et.',
    libraryFollowingAction: 'Takip edilenleri ac',
    libraryPlaylistsTitle: 'Kayitli oynatma listeleri hazir.',
    libraryPlaylistsDescription: 'Kutuphane icinden kayitli bir oynatma listesine devam et.',
    libraryPlaylistsAction: 'Listeleri ac',
    libraryEmptyTitle: 'Kutuphane olusturmaya basla.',
    libraryEmptyDescription: 'Izlenen, begenilen, takip edilen ve kaydedilen dersler burada gorunur.',
    libraryEmptyAction: 'Katalogu gez',
    browseEyebrow: 'Gezinme rehberligi',
    browseReadyTitle: 'Yayinlanmis dersler mevcut.',
    browseReadyDescription: 'Gorunen bir dersi ac veya katalogu gercek kategorilerle daralt.',
    browseReadyAction: 'Ilk dersi ac',
    browseCategoryItemTitle: 'Etkin kategori',
    browseCategoryItemDescription: 'Katalog secili kategoriye gore filtrelendi.',
    browseFilteredTitle: 'Bu gorunumle eslesen ders yok.',
    browseFilteredDescription: 'Kategori filtresini temizle veya baska bir arama terimi dene.',
    browseFilteredAction: 'Kategoriyi temizle',
    browseEmptyTitle: 'Henuz gorunen yayinlanmis ders yok.',
    browseEmptyDescription: 'Herkese acik dersler hazir oldugunda katalogda gorunur.',
    browseEmptyAction: 'Tum kategorileri goster',
    notificationsEyebrow: 'Bildirim rehberligi',
    notificationsFailedTitle: 'Bir render guncellemesi ilgi gerektiriyor.',
    notificationsFailedDescription: 'Bildirim sirasini temizlemeden once okunmamis hatayi incele.',
    notificationsUnreadTitle: 'Okunmamis bildirimler bekliyor.',
    notificationsUnreadDescription: 'En yeni okunmamis ogeyi ac veya bitirince sirayi okundu isaretle.',
    notificationsUnreadAction: 'Tumunu okundu yap',
    notificationsCaughtUpTitle: 'Her sey tamam.',
    notificationsCaughtUpDescription: 'Su anda okunmamis bildirim yok.',
    notificationsEmptyTitle: 'Henuz bildirim yok.',
    notificationsEmptyDescription: 'Yorumlar, takip edilen yayin guncellemeleri ve render durumlari burada gorunur.',
    notificationsFailureItemTitle: 'Okunmamis hata',
    notificationsUnreadItemTitle: 'Okunmamis oge',
    notificationsOpenAction: 'Bildirimi ac',
  },
  es: {
    eyebrow: 'Guia inteligente', actionTitle: 'Siguiente accion', itemsLabel: 'Elementos de guia', statusReady: 'Listo', statusNeedsAttention: 'Requiere atencion', statusProcessing: 'Procesando', statusFailed: 'Error', statusCompleted: 'Completado', statusNeutral: 'Estado actual', libraryEyebrow: 'Guia de aprendizaje', libraryContinueTitle: 'Continua donde lo dejaste.', libraryContinueDescription: 'Tu ultima leccion vista tiene progreso real guardado.', libraryContinueAction: 'Continuar leccion', browseEyebrow: 'Guia de exploracion', browseReadyTitle: 'Hay lecciones publicadas disponibles.', browseFilteredTitle: 'No hay lecciones para esta vista.', notificationsEyebrow: 'Guia de notificaciones', notificationsUnreadTitle: 'Hay notificaciones sin leer.', notificationsCaughtUpTitle: 'Estas al dia.'
  },
  fr: {
    eyebrow: 'Guidage intelligent', actionTitle: 'Action suivante', itemsLabel: 'Elements de guidage', statusReady: 'Pret', statusNeedsAttention: 'A verifier', statusProcessing: 'Traitement', statusFailed: 'Echec', statusCompleted: 'Termine', statusNeutral: 'Etat actuel', libraryEyebrow: 'Guidage apprentissage', libraryContinueTitle: 'Reprenez ou vous en etiez.', libraryContinueDescription: 'Votre derniere lecon regardee a une progression reelle.', libraryContinueAction: 'Continuer la lecon', browseEyebrow: 'Guidage catalogue', browseReadyTitle: 'Des lecons publiees sont disponibles.', browseFilteredTitle: 'Aucune lecon ne correspond.', notificationsEyebrow: 'Guidage notifications', notificationsUnreadTitle: 'Des notifications non lues attendent.', notificationsCaughtUpTitle: 'Vous etes a jour.'
  },
  de: {
    eyebrow: 'Smarte Hinweise', actionTitle: 'Naechste Aktion', itemsLabel: 'Hinweise', statusReady: 'Bereit', statusNeedsAttention: 'Braucht Aufmerksamkeit', statusProcessing: 'Wird verarbeitet', statusFailed: 'Fehlgeschlagen', statusCompleted: 'Abgeschlossen', statusNeutral: 'Aktueller Stand', libraryEyebrow: 'Lernhinweise', libraryContinueTitle: 'Weiter an der letzten Stelle.', libraryContinueDescription: 'Die zuletzt angesehene Lektion hat gespeicherten Fortschritt.', libraryContinueAction: 'Lektion fortsetzen', browseEyebrow: 'Kataloghinweise', browseReadyTitle: 'Veroeffentlichte Lektionen sind verfuegbar.', browseFilteredTitle: 'Keine Lektionen passen zu dieser Ansicht.', notificationsEyebrow: 'Benachrichtigungshinweise', notificationsUnreadTitle: 'Ungelesene Benachrichtigungen warten.', notificationsCaughtUpTitle: 'Du bist auf dem neuesten Stand.'
  },
  it: {
    eyebrow: 'Guida intelligente', actionTitle: 'Prossima azione', itemsLabel: 'Elementi guida', statusReady: 'Pronto', statusNeedsAttention: 'Richiede attenzione', statusProcessing: 'In elaborazione', statusFailed: 'Non riuscito', statusCompleted: 'Completato', statusNeutral: 'Stato attuale', libraryEyebrow: 'Guida apprendimento', libraryContinueTitle: 'Continua da dove eri rimasto.', libraryContinueDescription: 'L ultima lezione guardata ha progresso reale salvato.', libraryContinueAction: 'Continua lezione', browseEyebrow: 'Guida catalogo', browseReadyTitle: 'Sono disponibili lezioni pubblicate.', browseFilteredTitle: 'Nessuna lezione corrisponde.', notificationsEyebrow: 'Guida notifiche', notificationsUnreadTitle: 'Ci sono notifiche non lette.', notificationsCaughtUpTitle: 'Sei aggiornato.'
  },
  pt: {
    eyebrow: 'Orientacao inteligente', actionTitle: 'Proxima acao', itemsLabel: 'Itens de orientacao', statusReady: 'Pronto', statusNeedsAttention: 'Precisa de atencao', statusProcessing: 'Processando', statusFailed: 'Falhou', statusCompleted: 'Concluido', statusNeutral: 'Estado atual', libraryEyebrow: 'Orientacao de aprendizagem', libraryContinueTitle: 'Continue de onde parou.', libraryContinueDescription: 'Sua ultima aula vista tem progresso real salvo.', libraryContinueAction: 'Continuar aula', browseEyebrow: 'Orientacao de navegacao', browseReadyTitle: 'Ha aulas publicadas disponiveis.', browseFilteredTitle: 'Nenhuma aula corresponde.', notificationsEyebrow: 'Orientacao de notificacoes', notificationsUnreadTitle: 'Ha notificacoes nao lidas.', notificationsCaughtUpTitle: 'Voce esta em dia.'
  },
  ru: {
    eyebrow: 'Умные подсказки', actionTitle: 'Следующее действие', itemsLabel: 'Подсказки', statusReady: 'Готово', statusNeedsAttention: 'Требует внимания', statusProcessing: 'Обработка', statusFailed: 'Ошибка', statusCompleted: 'Завершено', statusNeutral: 'Текущее состояние', libraryEyebrow: 'Подсказки обучения', libraryContinueTitle: 'Продолжите с последнего места.', libraryContinueDescription: 'У последнего просмотренного урока сохранен реальный прогресс.', libraryContinueAction: 'Продолжить урок', browseEyebrow: 'Подсказки каталога', browseReadyTitle: 'Опубликованные уроки доступны.', browseFilteredTitle: 'В этом виде уроков нет.', notificationsEyebrow: 'Подсказки уведомлений', notificationsUnreadTitle: 'Есть непрочитанные уведомления.', notificationsCaughtUpTitle: 'Все просмотрено.'
  },
  ja: {
    eyebrow: 'スマートガイダンス', actionTitle: '次の操作', itemsLabel: 'ガイダンス項目', statusReady: '準備完了', statusNeedsAttention: '確認が必要', statusProcessing: '処理中', statusFailed: '失敗', statusCompleted: '完了', statusNeutral: '現在の状態', libraryEyebrow: '学習ガイダンス', libraryContinueTitle: '前回の続きから再開できます。', libraryContinueDescription: '最後に視聴したレッスンには実際の進捗が保存されています。', libraryContinueAction: 'レッスンを続ける', browseEyebrow: '閲覧ガイダンス', browseReadyTitle: '公開済みレッスンがあります。', browseFilteredTitle: 'この表示に一致するレッスンはありません。', notificationsEyebrow: '通知ガイダンス', notificationsUnreadTitle: '未読通知があります。', notificationsCaughtUpTitle: 'すべて確認済みです。'
  },
  ko: {
    eyebrow: '스마트 안내', actionTitle: '다음 작업', itemsLabel: '안내 항목', statusReady: '준비됨', statusNeedsAttention: '확인 필요', statusProcessing: '처리 중', statusFailed: '실패', statusCompleted: '완료', statusNeutral: '현재 상태', libraryEyebrow: '학습 안내', libraryContinueTitle: '이어서 학습하세요.', libraryContinueDescription: '최근 시청한 강의에 실제 진행률이 저장되어 있습니다.', libraryContinueAction: '강의 계속 보기', browseEyebrow: '탐색 안내', browseReadyTitle: '게시된 강의가 있습니다.', browseFilteredTitle: '이 보기와 일치하는 강의가 없습니다.', notificationsEyebrow: '알림 안내', notificationsUnreadTitle: '읽지 않은 알림이 있습니다.', notificationsCaughtUpTitle: '모두 확인했습니다.'
  },
  zh: {
    eyebrow: '智能指引', actionTitle: '下一步', itemsLabel: '指引项目', statusReady: '就绪', statusNeedsAttention: '需要关注', statusProcessing: '处理中', statusFailed: '失败', statusCompleted: '已完成', statusNeutral: '当前状态', libraryEyebrow: '学习指引', libraryContinueTitle: '从上次位置继续。', libraryContinueDescription: '最近观看的课程已有真实进度记录。', libraryContinueAction: '继续课程', browseEyebrow: '浏览指引', browseReadyTitle: '已有发布课程可用。', browseFilteredTitle: '此视图没有匹配课程。', notificationsEyebrow: '通知指引', notificationsUnreadTitle: '有未读通知。', notificationsCaughtUpTitle: '你已全部处理。'
  },
  ar: {
    eyebrow: 'إرشاد ذكي',
    actionTitle: 'الخطوة التالية',
    itemsLabel: 'عناصر الإرشاد',
    statusReady: 'جاهز',
    statusNeedsAttention: 'يتطلب الانتباه',
    statusProcessing: 'قيد المعالجة',
    statusFailed: 'فشل',
    statusCompleted: 'مكتمل',
    statusNeutral: 'الحالة الحالية',
    libraryEyebrow: 'إرشاد التعلم',
    libraryContinueTitle: 'تابع من حيث توقفت.',
    libraryContinueDescription: 'آخر درس شاهدته يحتوي على تقدم حقيقي محفوظ.',
    libraryContinueAction: 'تابع الدرس',
    libraryContinueItemTitle: 'قيد التقدم',
    libraryContinueItemDescription: 'تم حفظ التقدم من سجل المشاهدة.',
    libraryLikedTitle: 'الدروس المحفوظة جاهزة.',
    libraryLikedDescription: 'افتح درسا أعجبك أو راجع مجموعاتك من التبويبات.',
    libraryLikedAction: 'افتح الدروس المعجب بها',
    libraryFollowingTitle: 'يوجد نشاط لدى الناشرين المتابعين.',
    libraryFollowingDescription: 'راجع الناشرين الذين تتابعهم وأحدث دروسهم العامة.',
    libraryFollowingAction: 'افتح المتابعات',
    libraryPlaylistsTitle: 'قوائم التشغيل المحفوظة جاهزة.',
    libraryPlaylistsDescription: 'تابع قائمة تشغيل محفوظة من مكتبتك.',
    libraryPlaylistsAction: 'افتح القوائم',
    libraryEmptyTitle: 'ابدأ بناء مكتبتك.',
    libraryEmptyDescription: 'ستظهر هنا الدروس التي شاهدتها أو أعجبتك أو تابعتها أو حفظتها.',
    libraryEmptyAction: 'تصفح الكتالوج',
    browseEyebrow: 'إرشاد التصفح',
    browseReadyTitle: 'توجد دروس منشورة.',
    browseReadyDescription: 'افتح درسا ظاهرا أو ضيق الكتالوج بفئات حقيقية.',
    browseReadyAction: 'افتح أول درس',
    browseCategoryItemTitle: 'الفئة النشطة',
    browseCategoryItemDescription: 'تمت تصفية الكتالوج حسب الفئة المحددة.',
    browseFilteredTitle: 'لا توجد دروس تطابق هذا العرض.',
    browseFilteredDescription: 'امسح فلتر الفئة أو جرب عبارة بحث أخرى.',
    browseFilteredAction: 'امسح الفئة',
    browseEmptyTitle: 'لا توجد دروس منشورة ظاهرة بعد.',
    browseEmptyDescription: 'سيعرض الكتالوج الدروس العامة عند توفرها.',
    browseEmptyAction: 'اعرض كل الفئات',
    notificationsEyebrow: 'إرشاد الإشعارات',
    notificationsFailedTitle: 'تحديث تصيير يحتاج إلى الانتباه.',
    notificationsFailedDescription: 'راجع الفشل غير المقروء قبل تنظيف قائمة الإشعارات.',
    notificationsUnreadTitle: 'توجد إشعارات غير مقروءة.',
    notificationsUnreadDescription: 'افتح أحدث عنصر غير مقروء أو علم القائمة كمقروءة بعد الانتهاء.',
    notificationsUnreadAction: 'علم الكل كمقروء',
    notificationsCaughtUpTitle: 'أنت متابع لكل شيء.',
    notificationsCaughtUpDescription: 'لا توجد إشعارات غير مقروءة الآن.',
    notificationsEmptyTitle: 'لا توجد إشعارات بعد.',
    notificationsEmptyDescription: 'ستظهر هنا التعليقات وتحديثات الناشرين المتابعين وحالات التصيير.',
    notificationsFailureItemTitle: 'فشل غير مقروء',
    notificationsUnreadItemTitle: 'عنصر غير مقروء',
    notificationsOpenAction: 'افتح الإشعار',
  },
};

export function productGuidanceLocale(rawLocale = '') {
  const baseLocale = String(rawLocale || '').trim().toLowerCase().split(/[-_]/)[0];
  return Object.prototype.hasOwnProperty.call(PRODUCT_GUIDANCE_MESSAGES, baseLocale) ? baseLocale : 'en';
}

export function productGuidanceCopy(rawLocale = '') {
  const locale = productGuidanceLocale(rawLocale);
  return { ...EN, ...PRODUCT_GUIDANCE_MESSAGES[locale] };
}

function readDocumentLocale() {
  if (typeof document === 'undefined') return 'en';
  return document.documentElement.lang || 'en';
}

export function useProductGuidanceCopy() {
  const [locale, setLocale] = useState(readDocumentLocale);

  useEffect(() => {
    if (typeof document === 'undefined' || typeof MutationObserver === 'undefined') return undefined;
    const observer = new MutationObserver(() => setLocale(readDocumentLocale()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['lang'] });
    return () => observer.disconnect();
  }, []);

  return productGuidanceCopy(locale);
}
