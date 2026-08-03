import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AudioLines,
  Check,
  ChevronRight,
  CircleAlert,
  Image as ImageIcon,
  LoaderCircle,
  Mic,
  PlayCircle,
  RefreshCw,
  Settings2,
  Upload,
  UserRound,
  UserRoundPlus,
  Video,
} from 'lucide-react';
import {
  API_BASE_URL,
  fetchAvatarPreviewStatus,
  fetchAvatarProfile,
  prepareAvatarProfile,
  regenerateAvatarPreview,
  updateAvatarProfile,
  uploadAvatarImage,
  uploadAvatarVideo,
  uploadVoiceSample,
} from '../api';
import Button from '../components/ui/Button';
import SurfaceCard from '../components/ui/SurfaceCard';
import { usePageLoading } from '../components/ui/PageLoading';
import AvatarCaptureWizard from '../components/avatar/AvatarCaptureWizard';
import VoiceManager from '../components/avatar/VoiceManager';
import { featureEnabled, featureReason, useCapabilities } from '../lib/capabilities';
import { avatarChecklistItems, normalizeAvatarSetupStatus } from '../utils/avatarSetupStatus';

const API_ORIGIN = API_BASE_URL.replace(/\/api\/v1\/?$/, '');
const DEFAULT_SETTINGS = {
  avatar_enabled: false,
  avatar_consent_confirmed: false,
  avatar_motion_preset: 'natural',
  avatar_lipsync_engine: 'liveportrait+musetalk',
  avatar_quality_preset: 'high',
};

const COPY = {
  en: {
    myAvatars: 'My avatars',
    sharedAvatars: 'Shared avatars',
    newAvatar: 'New Avatar',
    createFirst: 'Create your first avatar',
    manageTitle: 'Manage your avatar',
    helper: 'Create one consistent, moving and speaking identity from your own portrait and voice.',
    guide: 'Setup guide',
    cloneTitle: 'Clone a real person',
    cloneBody: 'Use a portrait and voice you have permission to use to create a personal lesson avatar.',
    personalAvatar: 'My personal avatar',
    readyToManage: 'Your avatar source is saved. Open it to update the source, voice, or preview.',
    openSetup: 'Open avatar setup',
    editSetup: 'Manage avatar',
    sharedEmptyTitle: 'Shared avatars are not available yet',
    sharedEmptyBody: 'Only your personal, consent-based avatar can be managed here.',
    setupTitle: 'Personal avatar setup',
    setupBody: 'Upload a clear portrait or short video and a clean voice sample. You can update either source later.',
    visualLabel: 'Portrait image or video',
    voiceLabel: 'Voice sample',
    consent: 'I confirm that I have permission to use this image, video, and voice.',
    enabled: 'Enable this avatar for lesson rendering',
    save: 'Save avatar',
    saving: 'Saving...',
    cancel: 'Cancel',
    setupProgress: 'Setup progress',
    prepare: 'Prepare avatar',
    preparing: 'Preparing...',
    generate: 'Generate preview',
    generating: 'Generating...',
    retry: 'Try again',
    saved: 'Avatar settings saved.',
    consentRequired: 'Confirm permission before uploading an avatar source.',
    unavailable: 'Avatar tools are not enabled in this deployment.',
    loading: 'Loading your avatar...',
    currentPreview: 'Current preview',
    queued: 'Preview generation was queued.',
  },
  tr: {
    myAvatars: 'Avatarlarım',
    sharedAvatars: 'Genel avatarlar',
    newAvatar: 'Yeni Avatar',
    createFirst: 'İlk avatarını oluştur',
    manageTitle: 'Avatarını yönet',
    helper: 'Kendi portren ve sesinden, her derste tutarlı görünen, hareket eden ve konuşan bir kimlik oluştur.',
    guide: 'Kuruluma göz at',
    cloneTitle: 'Gerçek kişiyi klonla',
    cloneBody: 'Kişisel ders avatarını oluşturmak için kullanma iznine sahip olduğun portreyi ve sesi ekle.',
    personalAvatar: 'Kişisel avatarım',
    readyToManage: 'Avatar kaynağın kayıtlı. Görseli, sesi veya önizlemeyi güncellemek için aç.',
    openSetup: 'Avatar kurulumunu aç',
    editSetup: 'Avatarı yönet',
    sharedEmptyTitle: 'Genel avatarlar henüz kullanıma açık değil',
    sharedEmptyBody: 'Burada yalnızca izin onaylı kişisel avatarını yönetebilirsin.',
    setupTitle: 'Kişisel avatar kurulumu',
    setupBody: 'Net bir portre veya kısa video ile temiz bir ses örneği yükle. Bu kaynakları daha sonra değiştirebilirsin.',
    visualLabel: 'Portre görseli veya videosu',
    voiceLabel: 'Ses örneği',
    consent: 'Bu görseli, videoyu ve sesi kullanma iznine sahip olduğumu onaylıyorum.',
    enabled: 'Bu avatarı ders renderlarında etkinleştir',
    save: 'Avatarı kaydet',
    saving: 'Kaydediliyor...',
    cancel: 'Vazgeç',
    setupProgress: 'Kurulum ilerlemesi',
    prepare: 'Avatarı hazırla',
    preparing: 'Hazırlanıyor...',
    generate: 'Önizleme oluştur',
    generating: 'Oluşturuluyor...',
    retry: 'Tekrar dene',
    saved: 'Avatar ayarları kaydedildi.',
    consentRequired: 'Avatar kaynağı yüklemeden önce kullanım iznini onayla.',
    unavailable: 'Bu kurulumda avatar araçları etkin değil.',
    loading: 'Avatarın yükleniyor...',
    currentPreview: 'Güncel önizleme',
    queued: 'Avatar önizlemesi sıraya alındı.',
  },
};

function readDocumentLocale() {
  if (typeof document === 'undefined') return 'en';
  return document.documentElement.lang || 'en';
}

function useDocumentLocale() {
  const [locale, setLocale] = useState(readDocumentLocale);

  useEffect(() => {
    if (typeof document === 'undefined' || typeof MutationObserver === 'undefined') return undefined;
    const observer = new MutationObserver(() => setLocale(readDocumentLocale()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['lang'] });
    return () => observer.disconnect();
  }, []);

  return String(locale).toLowerCase().split(/[-_]/)[0];
}

function toAbsoluteMediaUrl(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  if (/^(https?:|blob:|data:)/i.test(raw)) return raw;
  return `${API_ORIGIN}${raw.startsWith('/') ? raw : `/${raw}`}`;
}

function profileVisual(payload) {
  const profile = payload?.profile || {};
  const video = profile.avatar_video_processed || profile.avatar_video_original || '';
  const image = profile.avatar_image_processed || profile.avatar_image_original || '';
  return video
    ? { type: 'video', url: toAbsoluteMediaUrl(video) }
    : image
      ? { type: 'image', url: toAbsoluteMediaUrl(image) }
      : null;
}

function previewUrl(payload) {
  const profile = payload?.profile || {};
  return toAbsoluteMediaUrl(
    profile.avatar_preview_video
    || profile.avatar_last_preview_path
    || payload?.avatar_summary?.last_preview_path
    || '',
  );
}

function AvatarArtwork({ visual = null }) {
  return (
    <div className="relative h-56 overflow-hidden bg-[linear-gradient(135deg,#c9d6ff_0%,#f4efe9_45%,#b8f5e5_100%)] sm:h-60">
      <div className="absolute -left-12 top-7 h-52 w-44 rotate-[18deg] overflow-hidden rounded-[2rem] border border-emerald-400 bg-[linear-gradient(145deg,#d8c7ff,#8de5d1)] shadow-2xl">
        <div className="flex h-full items-center justify-center text-white/80"><UserRound size={82} strokeWidth={1.2} /></div>
      </div>
      <div className="absolute left-[28%] -top-8 h-72 w-44 -rotate-[8deg] overflow-hidden rounded-[2.2rem] border border-cyan-400 bg-[linear-gradient(160deg,#f5eee7,#9ed8ff)] shadow-2xl">
        <div className="flex h-full items-center justify-center text-slate-700/65"><UserRound size={96} strokeWidth={1.15} /></div>
      </div>
      <div className="absolute -right-8 top-10 h-56 w-48 rotate-[17deg] overflow-hidden rounded-[2rem] border border-violet-400 bg-[linear-gradient(145deg,#ffe0ce,#c8b7ff)] shadow-2xl">
        {visual?.type === 'image' ? (
          <img src={visual.url} alt="" className="h-full w-full object-cover -rotate-[17deg] scale-125" />
        ) : visual?.type === 'video' ? (
          <video src={visual.url} muted playsInline className="h-full w-full object-cover -rotate-[17deg] scale-125" />
        ) : (
          <div className="flex h-full items-center justify-center text-slate-700/65"><UserRound size={90} strokeWidth={1.15} /></div>
        )}
      </div>
      <div className="absolute inset-x-0 bottom-0 h-20 bg-gradient-to-t from-black/25 to-transparent" />
    </div>
  );
}

function UploadField({ icon: Icon, label, accept, file, onChange }) {
  const inputRef = useRef(null);
  return (
    <button
      type="button"
      onClick={() => inputRef.current?.click()}
      className="focus-ring flex min-h-28 w-full items-center gap-4 rounded-2xl border border-dashed border-[var(--outline-variant)] bg-[var(--surface-container-low)] p-4 text-left transition hover:border-[var(--accent-primary)] hover:bg-[var(--hover-surface)]"
    >
      <span className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-[var(--surface-container-highest)] text-[var(--accent-primary)]">
        <Icon size={20} />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-[var(--text-primary)]">{label}</span>
        <span className="mt-1 block truncate text-xs text-[var(--text-secondary)]">{file?.name || accept}</span>
      </span>
      <Upload size={17} className="ml-auto shrink-0 text-[var(--outline)]" />
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={(event) => onChange(event.target.files?.[0] || null)}
        className="sr-only"
        tabIndex={-1}
      />
    </button>
  );
}

export default function Avatar({ user }) {
  const locale = useDocumentLocale();
  const copy = locale === 'tr' ? COPY.tr : COPY.en;
  const { capabilities, capabilitiesLoading } = useCapabilities();
  const avatarAvailable = featureEnabled(capabilities, 'avatar');
  const [activeManagerView, setActiveManagerView] = useState('avatars');
  const [activeTab, setActiveTab] = useState('mine');
  const [profilePayload, setProfilePayload] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState('');
  const [setupOpen, setSetupOpen] = useState(false);
  const [captureOpen, setCaptureOpen] = useState(false);
  const [visualFile, setVisualFile] = useState(null);
  const [voiceFile, setVoiceFile] = useState(null);
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [previewJobId, setPreviewJobId] = useState('');
  const setupRef = useRef(null);

  usePageLoading(loading || capabilitiesLoading, 'avatar-profile');

  const loadProfile = useCallback(async () => {
    if (!avatarAvailable || !user?.id) {
      setProfilePayload(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const payload = await fetchAvatarProfile(user.id);
      const profile = payload?.profile || {};
      setProfilePayload(payload);
      setSettings((current) => ({
        ...current,
        avatar_enabled: Boolean(profile.avatar_enabled),
        avatar_consent_confirmed: Boolean(profile.avatar_consent_confirmed),
        avatar_motion_preset: profile.avatar_motion_preset || current.avatar_motion_preset,
        avatar_lipsync_engine: profile.avatar_lipsync_engine || current.avatar_lipsync_engine,
        avatar_quality_preset: profile.avatar_quality_preset || current.avatar_quality_preset,
      }));
    } catch (loadError) {
      setError(loadError.message || copy.retry);
    } finally {
      setLoading(false);
    }
  }, [avatarAvailable, copy.retry, user?.id]);

  useEffect(() => {
    void loadProfile();
  }, [loadProfile]);

  useEffect(() => {
    if (!previewJobId || !user?.id) return undefined;
    let active = true;
    const check = async () => {
      try {
        const status = await fetchAvatarPreviewStatus(user.id, previewJobId);
        if (!active) return;
        const state = String(status?.preview_status || status?.status || '').toLowerCase();
        if (['ready', 'completed', 'succeeded', 'failed'].includes(state)) {
          setPreviewJobId('');
          await loadProfile();
        }
      } catch (statusError) {
        if (active) {
          setPreviewJobId('');
          setError(statusError.message || copy.retry);
        }
      }
    };
    const interval = window.setInterval(check, 2500);
    void check();
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [copy.retry, loadProfile, previewJobId, user?.id]);

  const setupStatus = useMemo(() => normalizeAvatarSetupStatus(profilePayload || {}), [profilePayload]);
  const checklist = useMemo(() => avatarChecklistItems(setupStatus), [setupStatus]);
  const visual = useMemo(() => profileVisual(profilePayload), [profilePayload]);
  const currentPreviewUrl = useMemo(() => previewUrl(profilePayload), [profilePayload]);
  const hasAvatar = Boolean(visual || setupStatus.checklist.portrait_uploaded);

  const openSetup = () => {
    setActiveTab('mine');
    setSetupOpen(true);
    setMessage('');
    setError('');
    window.requestAnimationFrame(() => setupRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' }));
  };

  const openCapture = () => {
    setActiveTab('mine');
    setSetupOpen(false);
    setCaptureOpen(true);
    setMessage('');
    setError('');
  };

  const completeCapture = async ({ file, avatarName, voiceSource }) => {
    const captureSettings = {
      ...settings,
      avatar_enabled: true,
      avatar_consent_confirmed: true,
      avatar_name: avatarName,
      avatar_voice_source: voiceSource,
    };
    setBusyAction('capture');
    setMessage('');
    setError('');
    try {
      if (file.type.startsWith('video/')) {
        await uploadAvatarVideo(user.id, file, captureSettings);
      } else {
        await uploadAvatarImage(user.id, file, captureSettings);
      }
      setSettings((current) => ({ ...current, avatar_enabled: true, avatar_consent_confirmed: true }));
      setVisualFile(null);
      setVoiceFile(null);
      await loadProfile();
      setCaptureOpen(false);
      setMessage(copy.saved);
    } catch (captureError) {
      setError(captureError.message || copy.retry);
      throw captureError;
    } finally {
      setBusyAction('');
    }
  };

  const handleSave = async (event) => {
    event.preventDefault();
    if (!settings.avatar_consent_confirmed) {
      setError(copy.consentRequired);
      return;
    }
    setBusyAction('save');
    setMessage('');
    setError('');
    try {
      if (visualFile) {
        if (visualFile.type.startsWith('video/')) {
          await uploadAvatarVideo(user.id, visualFile, settings);
        } else {
          await uploadAvatarImage(user.id, visualFile, settings);
        }
      }
      if (voiceFile) await uploadVoiceSample(user.id, voiceFile);
      await updateAvatarProfile(user.id, settings);
      setVisualFile(null);
      setVoiceFile(null);
      await loadProfile();
      setMessage(copy.saved);
    } catch (saveError) {
      setError(saveError.message || copy.retry);
    } finally {
      setBusyAction('');
    }
  };

  const runProfileAction = async (actionName, action) => {
    setBusyAction(actionName);
    setMessage('');
    setError('');
    try {
      const payload = await action();
      if (actionName === 'preview') {
        const jobId = String(payload?.job_id || '');
        setPreviewJobId(jobId);
        setMessage(copy.queued);
        if (!jobId) await loadProfile();
      } else {
        await loadProfile();
      }
    } catch (actionError) {
      setError(actionError.message || copy.retry);
    } finally {
      setBusyAction('');
    }
  };

  if (loading || capabilitiesLoading) {
    return (
      <div className="flex min-h-[65vh] items-center justify-center" role="status">
        <LoaderCircle size={24} className="animate-spin text-[var(--accent-primary)]" />
        <span className="ml-3 text-sm text-[var(--text-secondary)]">{copy.loading}</span>
      </div>
    );
  }

  return (
    <div className="-mx-3 min-h-[calc(100vh-4rem)] sm:-mx-6 lg:-mx-8 lg:flex">
      <aside className="border-b border-[var(--border-subtle)] bg-[var(--surface-container-low)] px-3 py-3 lg:sticky lg:top-16 lg:h-[calc(100vh-4rem)] lg:w-[14.5rem] lg:shrink-0 lg:border-b-0 lg:border-r lg:px-4 lg:py-6">
        <div className="hidden items-center justify-between px-2 lg:flex">
          <p className="text-xs font-bold text-[var(--outline)]">Avatarları yönet</p>
        </div>
        <nav className="flex gap-2 lg:mt-5 lg:flex-col" aria-label="Avatar yönetimi">
          <button
            type="button"
            onClick={() => setActiveManagerView('avatars')}
            aria-current={activeManagerView === 'avatars' ? 'page' : undefined}
            className={`focus-ring flex h-11 flex-1 items-center gap-3 rounded-full px-4 text-sm font-bold transition lg:flex-none ${activeManagerView === 'avatars' ? 'bg-cyan-950 text-cyan-50 dark:bg-cyan-900/70' : 'text-[var(--text-secondary)] hover:bg-[var(--surface-container-high)] hover:text-[var(--text-primary)]'}`}
          >
            <UserRound size={19} />
            Avatarlar
          </button>
          <button
            type="button"
            onClick={() => setActiveManagerView('voices')}
            aria-current={activeManagerView === 'voices' ? 'page' : undefined}
            className={`focus-ring flex h-11 flex-1 items-center gap-3 rounded-full px-4 text-sm font-bold transition lg:flex-none ${activeManagerView === 'voices' ? 'bg-cyan-950 text-cyan-50 dark:bg-cyan-900/70' : 'text-[var(--text-secondary)] hover:bg-[var(--surface-container-high)] hover:text-[var(--text-primary)]'}`}
          >
            <AudioLines size={19} />
            Sesler
          </button>
        </nav>
      </aside>

      <div className="min-w-0 flex-1 px-3 sm:px-6 lg:px-8">
      {activeManagerView === 'voices' ? (
        <VoiceManager user={user} profilePayload={profilePayload} onProfileRefresh={loadProfile} />
      ) : (
    <section className="min-h-[calc(100vh-4rem)] pb-16 pt-3 sm:pt-5">
      <div className="flex flex-col gap-4 border-b border-[var(--border-subtle)] pb-1 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex gap-7" role="tablist" aria-label="Avatar sections">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'mine'}
            onClick={() => setActiveTab('mine')}
            className={`focus-ring border-b-4 px-2 pb-3 pt-2 text-sm font-bold transition ${activeTab === 'mine' ? 'border-[var(--accent-primary)] text-[var(--text-primary)]' : 'border-transparent text-[var(--outline)] hover:text-[var(--text-primary)]'}`}
          >
            {copy.myAvatars}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'shared'}
            onClick={() => setActiveTab('shared')}
            className={`focus-ring border-b-4 px-2 pb-3 pt-2 text-sm font-bold transition ${activeTab === 'shared' ? 'border-[var(--accent-primary)] text-[var(--text-primary)]' : 'border-transparent text-[var(--outline)] hover:text-[var(--text-primary)]'}`}
          >
            {copy.sharedAvatars}
          </button>
        </div>

        <Button size="sm" onClick={openCapture} disabled={!avatarAvailable}>
          <UserRoundPlus size={16} />
          <span>{copy.newAvatar}</span>
        </Button>
      </div>

      {message ? (
        <p role="status" className="mx-auto mt-4 max-w-4xl rounded-xl bg-[var(--status-success-bg)] px-4 py-3 text-sm text-[var(--status-success-fg)]">
          {message}
        </p>
      ) : null}

      {!avatarAvailable ? (
        <SurfaceCard elevated className="mx-auto mt-14 max-w-2xl text-center">
          <CircleAlert size={28} className="mx-auto text-[var(--status-warning-fg)]" />
          <h1 className="title-lg mt-4 text-[var(--text-primary)]">{copy.unavailable}</h1>
          <p className="body-md mt-2">{featureReason(capabilities, 'avatar')}</p>
        </SurfaceCard>
      ) : activeTab === 'shared' ? (
        <SurfaceCard className="mx-auto mt-14 max-w-2xl text-center">
          <UserRound size={34} className="mx-auto text-[var(--outline)]" />
          <h1 className="title-lg mt-4 text-[var(--text-primary)]">{copy.sharedEmptyTitle}</h1>
          <p className="body-md mt-2">{copy.sharedEmptyBody}</p>
        </SurfaceCard>
      ) : (
        <>
          <header className="mx-auto max-w-3xl pb-8 pt-12 text-center sm:pt-16">
            <h1 className="font-['Manrope'] text-3xl font-extrabold tracking-[-0.04em] text-[var(--text-primary)] sm:text-4xl">
              {hasAvatar ? copy.manageTitle : copy.createFirst}
            </h1>
            <p className="mx-auto mt-3 max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">{copy.helper}</p>
            <button type="button" onClick={openSetup} className="focus-ring mt-2 text-xs font-semibold text-[var(--outline)] underline underline-offset-4 hover:text-[var(--accent-primary)]">
              {copy.guide}
            </button>
          </header>

          <div className="mx-auto max-w-[48rem]">
            <button
              type="button"
              onClick={hasAvatar ? openSetup : openCapture}
              className="focus-ring group block w-full overflow-hidden rounded-3xl border border-[var(--border-subtle)] bg-[var(--surface-container-low)] text-left shadow-[0_20px_65px_rgba(0,0,0,0.13)] transition hover:-translate-y-1 hover:border-[var(--accent-primary)]"
            >
              <AvatarArtwork visual={visual} />
              <span className="flex items-start gap-4 p-5 sm:p-6">
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2 text-xl font-bold text-[var(--text-primary)]">
                    {hasAvatar ? (profilePayload?.profile?.avatar_name || copy.personalAvatar) : copy.cloneTitle}
                    {hasAvatar ? <Check size={17} className="text-[var(--status-success-fg)]" /> : null}
                  </span>
                  <span className="mt-2 block text-sm leading-6 text-[var(--text-secondary)]">
                    {hasAvatar ? copy.readyToManage : copy.cloneBody}
                  </span>
                </span>
                <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--surface-container-highest)] text-[var(--accent-primary)] transition group-hover:translate-x-1">
                  <ChevronRight size={19} />
                </span>
              </span>
            </button>
          </div>

          {setupOpen ? (
            <div ref={setupRef} className="mx-auto mt-10 max-w-4xl scroll-mt-24" data-testid="avatar-setup-panel">
            <SurfaceCard elevated>
              <div className="flex flex-col gap-3 border-b border-[var(--border-subtle)] pb-5 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <p className="label-sm">{copy.openSetup}</p>
                  <h2 className="title-lg mt-2 text-[var(--text-primary)]">{copy.setupTitle}</h2>
                  <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">{copy.setupBody}</p>
                </div>
                <Settings2 size={21} className="text-[var(--accent-primary)]" />
              </div>

              <form onSubmit={handleSave} className="mt-5 space-y-5">
                <div className="grid gap-4 md:grid-cols-2">
                  <UploadField
                    icon={visualFile?.type?.startsWith('video/') ? Video : ImageIcon}
                    label={copy.visualLabel}
                    accept="image/*,video/*"
                    file={visualFile}
                    onChange={setVisualFile}
                  />
                  <UploadField icon={Mic} label={copy.voiceLabel} accept="audio/*" file={voiceFile} onChange={setVoiceFile} />
                </div>

                <div className="grid gap-3 md:grid-cols-2">
                  <label className="flex items-start gap-3 rounded-2xl bg-[var(--surface-container-high)] p-4 text-sm text-[var(--text-secondary)]">
                    <input
                      type="checkbox"
                      checked={settings.avatar_consent_confirmed}
                      onChange={(event) => setSettings((current) => ({
                        ...current,
                        avatar_consent_confirmed: event.target.checked,
                        avatar_enabled: event.target.checked ? current.avatar_enabled : false,
                      }))}
                      className="mt-1"
                    />
                    <span>{copy.consent}</span>
                  </label>
                  <label className="flex items-start gap-3 rounded-2xl bg-[var(--surface-container-high)] p-4 text-sm text-[var(--text-secondary)]">
                    <input
                      type="checkbox"
                      checked={settings.avatar_enabled}
                      disabled={!settings.avatar_consent_confirmed}
                      onChange={(event) => setSettings((current) => ({ ...current, avatar_enabled: event.target.checked }))}
                      className="mt-1"
                    />
                    <span>{copy.enabled}</span>
                  </label>
                </div>

                {error ? <p role="alert" className="rounded-xl bg-[var(--status-danger-bg)] px-4 py-3 text-sm text-[var(--status-danger-fg)]">{error}</p> : null}
                <div className="flex flex-wrap justify-end gap-2">
                  <Button type="button" variant="ghost" onClick={() => setSetupOpen(false)} disabled={Boolean(busyAction)}>{copy.cancel}</Button>
                  <Button type="submit" disabled={Boolean(busyAction)}>
                    {busyAction === 'save' ? <LoaderCircle size={16} className="animate-spin" /> : <Upload size={16} />}
                    <span>{busyAction === 'save' ? copy.saving : copy.save}</span>
                  </Button>
                </div>
              </form>

              <div className="mt-6 border-t border-[var(--border-subtle)] pt-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="label-sm">{copy.setupProgress}</p>
                    <p className="mt-1 text-sm text-[var(--text-secondary)]">{setupStatus.message}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {setupStatus.can_prepare ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={Boolean(busyAction)}
                        onClick={() => runProfileAction('prepare', () => prepareAvatarProfile(user.id, {
                          ...settings,
                          force_reprocess: setupStatus.needs_prepare || setupStatus.state === 'failed',
                        }))}
                      >
                        {busyAction === 'prepare' ? <LoaderCircle size={15} className="animate-spin" /> : <RefreshCw size={15} />}
                        <span>{busyAction === 'prepare' ? copy.preparing : copy.prepare}</span>
                      </Button>
                    ) : null}
                    {setupStatus.can_generate_preview ? (
                      <Button
                        size="sm"
                        disabled={Boolean(busyAction) || Boolean(previewJobId)}
                        onClick={() => runProfileAction('preview', () => regenerateAvatarPreview(user.id))}
                      >
                        {busyAction === 'preview' || previewJobId ? <LoaderCircle size={15} className="animate-spin" /> : <PlayCircle size={15} />}
                        <span>{busyAction === 'preview' || previewJobId ? copy.generating : copy.generate}</span>
                      </Button>
                    ) : null}
                  </div>
                </div>
                <ul className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
                  {checklist.map((item) => (
                    <li key={item.key} className={`rounded-xl border px-3 py-2 text-xs font-semibold ${item.complete ? 'border-[var(--status-success-fg)] bg-[var(--status-success-bg)] text-[var(--status-success-fg)]' : 'border-[var(--border-subtle)] bg-[var(--surface-container-low)] text-[var(--outline)]'}`}>
                      <span className="flex items-center gap-2">{item.complete ? <Check size={13} /> : null}{item.label}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {currentPreviewUrl ? (
                <div className="mt-6 border-t border-[var(--border-subtle)] pt-5">
                  <p className="label-sm mb-3">{copy.currentPreview}</p>
                  <video src={currentPreviewUrl} controls playsInline className="max-h-[28rem] w-full rounded-2xl bg-black object-contain" />
                </div>
              ) : null}
            </SurfaceCard>
            </div>
          ) : null}

          {captureOpen ? (
            <AvatarCaptureWizard
              locale={locale}
              hasSavedVoice={Boolean(setupStatus.checklist.voice_uploaded)}
              externalCaptureOrigin={typeof window !== 'undefined' && window.location.hostname === 'localhost'
                ? `${window.location.protocol}//camera.localhost${window.location.port ? `:${window.location.port}` : ''}`
                : ''}
              onBack={() => setCaptureOpen(false)}
              onComplete={completeCapture}
            />
          ) : null}
        </>
      )}
    </section>
      )}
      </div>
    </div>
  );
}
