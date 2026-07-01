import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Bell,
  ChevronDown,
  Mic,
  MonitorPlay,
  MoonStar,
  Play,
  Save,
  Sparkles,
  Square,
  Sun,
  Trash2,
  Upload,
  UserCircle2,
} from 'lucide-react';
import Button from '../components/ui/Button';
import PublicProfileEditor from '../components/profile/PublicProfileEditor';
import LanguageSelector from '../components/ui/LanguageSelector';
import ModalShell from '../components/ui/ModalShell';
import SurfaceCard from '../components/ui/SurfaceCard';
import { useTheme } from '../components/ui/ThemeProvider';
import { usePageLoading } from '../components/ui/PageLoading';
import { useI18n } from '../i18n/I18nProvider';
import {
  API_BASE_URL,
  deleteAvatarPreview,
  fetchAvatarPreviewStatus,
  fetchAvatarProfile,
  fetchMyProfile,
  prepareAvatarProfile,
  regenerateAvatarPreview,
  updateAvatarProfile,
  updateMyProfile,
  uploadAvatarImage,
  uploadAvatarVideo,
  uploadProfileAssets,
  uploadVoiceSample,
} from '../api';
import { canAccessStudio } from '../lib/auth';
import { featureEnabled, featureStatusLabel, useCapabilities } from '../lib/capabilities';
import {
  clearRouteSessionState,
  onRouteReset,
  readRouteSessionState,
  writeRouteSessionState,
} from '../utils/routeSession';
import {
  SOCIAL_LINK_FIELDS,
  normalizedPublicProfilePayload,
  profileFieldErrorsFromApi,
  socialLinkValue,
  validatePublicProfileDraft,
} from '../utils/profileSocial';
import {
  avatarChecklistItems,
  normalizeAvatarSetupStatus,
} from '../utils/avatarSetupStatus';
import {
  isAutoplayNextEnabled,
  setAutoplayNextEnabled,
} from '../utils/playbackPreferences';

const REDUCED_MOTION_KEY = 'visus-reduced-motion';
const NOTIFICATION_PREFS_KEY = 'visus-notification-preferences';
const API_ORIGIN = API_BASE_URL.replace(/\/api\/v1\/?$/, '');

const DEFAULT_NOTIFICATION_PREFERENCES = {
  commentsOnLessons: true,
  renderUpdates: true,
  followedPublisherLessons: true,
};

const THEME_OPTIONS = [
  {
    id: 'light',
    title: 'Light',
    caption: 'Bright surfaces for reading and review.',
    icon: Sun,
  },
  {
    id: 'dark',
    title: 'Dark',
    caption: 'Low-glare surfaces for focus sessions.',
    icon: MoonStar,
  },
];

const DEFAULT_AVATAR_SETTINGS = {
  avatar_enabled: false,
  avatar_consent_confirmed: false,
  avatar_motion_preset: 'natural',
  avatar_lipsync_engine: 'liveportrait+musetalk',
  avatar_quality_preset: 'high',
  avatar_overlay_visible: true,
  avatar_overlay_default_position: 'top-right',
  avatar_overlay_size: 'medium',
  composite_fallback_allowed: false,
};

const AVATAR_STATUS_MESSAGES = {
  missing_consent: 'Confirm avatar consent before preparing or generating an avatar.',
  missing_portrait: 'Upload an avatar portrait image.',
  missing_voice: 'Upload a voice sample.',
  disabled: 'Enable avatar generation.',
  needs_prepare: 'Avatar needs to be prepared again.',
  preparing: 'Avatar preparation or preview generation is in progress.',
  ready: 'Avatar is prepared and ready for preview generation.',
  failed: 'Avatar preparation failed. Upload a clear portrait or prepare the avatar again.',
};

const VOICE_RECORDING_SAMPLE_TEXT = 'Merhaba, bu ses ornegini avatar ses profilim icin kaydediyorum. Derslerimde sakin, net ve dogal bir anlatim kullanmak istiyorum.';

function formatRecordingDuration(seconds) {
  const safeSeconds = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(safeSeconds / 60);
  const remainingSeconds = safeSeconds % 60;
  return `${minutes}:${String(remainingSeconds).padStart(2, '0')}`;
}

function preferredVoiceRecordingMimeType() {
  if (typeof window === 'undefined' || typeof window.MediaRecorder === 'undefined') {
    return '';
  }
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/ogg;codecs=opus',
    'audio/webm',
    'audio/ogg',
  ];
  return candidates.find((candidate) => window.MediaRecorder.isTypeSupported?.(candidate)) || '';
}

function recordedVoiceFileExtension(mimeType) {
  return String(mimeType || '').toLowerCase().includes('ogg') ? 'ogg' : 'webm';
}

async function voiceRecordingDiagnostics(phase, error = null) {
  let microphonePermission = 'unsupported';
  try {
    if (navigator.permissions?.query) {
      const status = await navigator.permissions.query({ name: 'microphone' });
      microphonePermission = status?.state || 'unknown';
    }
  } catch (permissionError) {
    microphonePermission = `unavailable: ${permissionError?.name || 'unknown'}`;
  }

  return {
    phase,
    origin: window.location.origin,
    href: window.location.href,
    isSecureContext: window.isSecureContext,
    hasMediaDevices: Boolean(navigator.mediaDevices),
    hasGetUserMedia: typeof navigator.mediaDevices?.getUserMedia === 'function',
    mediaRecorderType: typeof window.MediaRecorder,
    mediaRecorderSupported: typeof window.MediaRecorder === 'function',
    webmOpusSupported: Boolean(window.MediaRecorder?.isTypeSupported?.('audio/webm;codecs=opus')),
    oggOpusSupported: Boolean(window.MediaRecorder?.isTypeSupported?.('audio/ogg;codecs=opus')),
    microphonePermission,
    errorName: error?.name || '',
    errorMessage: error?.message || '',
    errorStack: error?.stack || '',
  };
}

function logVoiceRecordingError(phase, diagnostics, error) {
  console.warn('voice_recording_error', {
    phase,
    name: error?.name,
    message: error?.message,
    diagnostics,
  });
}

function voiceRecordingErrorMessage(error, phase, diagnostics = {}) {
  const name = String(error?.name || '').trim();
  if (phase === 'preflight' && diagnostics.isSecureContext === false) {
    return 'Microphone recording requires a secure context. Use localhost, 127.0.0.1, or HTTPS.';
  }
  if (phase === 'preflight' && !diagnostics.hasMediaDevices) {
    return 'This browser cannot access microphone devices from the current page.';
  }
  if (phase === 'preflight' && !diagnostics.hasGetUserMedia) {
    return 'This browser does not support microphone capture.';
  }
  if (phase === 'preflight' && !diagnostics.mediaRecorderSupported) {
    return 'This browser does not support microphone recording.';
  }

  if ((name === 'NotAllowedError' || name === 'PermissionDeniedError') && phase === 'getUserMedia') {
    return 'Microphone permission was denied by the browser or operating system.';
  }
  if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
    return 'The browser allowed microphone capture but blocked recording from the captured stream.';
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return 'No microphone was found. Connect or enable a microphone and try again.';
  }
  if (name === 'NotReadableError' || name === 'TrackStartError') {
    return 'The microphone is busy or unavailable. Close other apps using it and try again.';
  }
  if (name === 'AbortError') {
    return 'Microphone capture was interrupted before recording could start.';
  }
  if (name === 'SecurityError') {
    return 'The browser blocked microphone access for this page or origin.';
  }
  if (name === 'NotSupportedError') {
    return 'This browser cannot record the selected microphone audio format.';
  }
  if (name === 'TypeError') {
    return diagnostics.isSecureContext === false
      ? 'Microphone recording requires localhost, 127.0.0.1, or HTTPS.'
      : 'The browser rejected the microphone capture request.';
  }

  return 'Unable to start microphone recording. Check browser permissions and try again.';
}

function toAbsoluteMediaUrl(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  if (/^https?:\/\//i.test(raw)) return raw;
  return `${API_ORIGIN}${raw.startsWith('/') ? raw : `/${raw}`}`;
}

function resolvePreviewVideoUrl(payload) {
  if (!payload) return '';
  const profile = payload.profile || {};
  const summary = payload.avatar_summary || {};

  const candidate =
    profile.avatar_preview_video ||
    profile.avatar_last_preview_path ||
    summary.last_preview_path ||
    '';

  return toAbsoluteMediaUrl(candidate);
}

function profileDraftFromUser(user) {
  const profile = user?.profile || {};
  return {
    first_name: String(user?.first_name || ''),
    last_name: String(user?.last_name || ''),
    display_name: String(profile.display_name || ''),
    bio: String(profile.bio || ''),
    website_url: String(profile.website_url || ''),
    contact_email: String(profile.contact_email || ''),
    social_links: SOCIAL_LINK_FIELDS.reduce((acc, field) => ({
      ...acc,
      [field.key]: socialLinkValue(profile.social_links, field.key),
    }), {}),
    is_public_profile: Boolean(profile.is_public_profile),
    banner_url: String(profile.banner_url || ''),
    logo_url: String(profile.logo_url || ''),
    banner_moderation_status: String(profile.banner_moderation_status || profile.banner_image_moderation_status || ''),
    banner_moderation_summary: profile.banner_moderation_summary || profile.banner_image_moderation_summary || {},
    logo_moderation_status: String(profile.logo_moderation_status || profile.logo_image_moderation_status || ''),
    logo_moderation_summary: profile.logo_moderation_summary || profile.logo_image_moderation_summary || {},
  };
}

function profileDraftFromPayload(payload) {
  return {
    first_name: String(payload?.first_name || ''),
    last_name: String(payload?.last_name || ''),
    display_name: String(payload?.display_name || ''),
    bio: String(payload?.bio || ''),
    website_url: String(payload?.website_url || ''),
    contact_email: String(payload?.contact_email || ''),
    social_links: SOCIAL_LINK_FIELDS.reduce((acc, field) => ({
      ...acc,
      [field.key]: socialLinkValue(payload?.social_links, field.key),
    }), {}),
    is_public_profile: Boolean(payload?.is_public_profile),
    banner_url: String(payload?.banner_url || ''),
    logo_url: String(payload?.logo_url || ''),
    banner_moderation_status: String(payload?.banner_moderation_status || ''),
    banner_moderation_summary: payload?.banner_moderation_summary || {},
    logo_moderation_status: String(payload?.logo_moderation_status || ''),
    logo_moderation_summary: payload?.logo_moderation_summary || {},
  };
}

function profileAssetModerationMessage(payload) {
  const statuses = [
    ['Banner', payload?.banner_moderation_status, payload?.banner_moderation_summary],
    ['Logo', payload?.logo_moderation_status, payload?.logo_moderation_summary],
  ];
  const pending = statuses.find(([, status]) => status === 'needs_admin_review');
  if (pending) {
    return pending[2]?.publisher_reason_message
      || pending[2]?.reason_message
      || `${pending[0]} image needs manual admin review before it can become public.`;
  }
  const blocked = statuses.find(([, status]) => status === 'rejected');
  if (blocked) {
    return blocked[2]?.publisher_reason_message
      || blocked[2]?.reason_message
      || `${blocked[0]} image blocked by moderation.`;
  }
  return 'Public profile saved.';
}

function displayNameFromDraft(draft, user) {
  const customName = String(draft.display_name || '').trim();
  if (customName) return customName;
  const fullName = [draft.first_name, draft.last_name].map((value) => String(value || '').trim()).filter(Boolean).join(' ');
  return fullName || user?.username || 'VISUS User';
}

function readNotificationPreferences() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(NOTIFICATION_PREFS_KEY) || '{}');
    return {
      ...DEFAULT_NOTIFICATION_PREFERENCES,
      ...(parsed && typeof parsed === 'object' ? parsed : {}),
    };
  } catch {
    return DEFAULT_NOTIFICATION_PREFERENCES;
  }
}

function AvatarActionCard({ title, caption, icon: Icon, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="focus-ring flex h-full items-start gap-3 rounded-2xl token-surface p-4 text-left transition hover:bg-[color:var(--hover-surface)]"
    >
      <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--surface-container-highest)] text-[var(--accent-primary)]">
        <Icon size={18} />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-[var(--text-primary)]">{title}</span>
        <span className="mt-1 block text-xs leading-5 text-[var(--text-secondary)]">{caption}</span>
      </span>
    </button>
  );
}

function AvatarSetupChecklist({ items }) {
  const { t } = useI18n();
  return (
    <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
      {items.map((item) => (
        <li
          key={item.key}
          className={`flex min-h-12 items-center justify-between gap-2 rounded-xl border px-3 py-2 text-xs ${
            item.complete
              ? 'border-[color:var(--status-success-fg)] bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]'
              : 'border-[var(--border-subtle)] bg-[var(--surface-container-high)] text-[var(--text-secondary)]'
          }`}
        >
          <span className="font-medium">{item.label}</span>
          <span className="shrink-0 text-[0.68rem] uppercase tracking-normal">
            {item.complete ? t('common.done') : t('common.pending')}
          </span>
        </li>
      ))}
    </ul>
  );
}

function AvatarConsentControls({
  settings,
  onConsentChange,
  onEnabledChange,
  disabled = false,
}) {
  const { t } = useI18n();
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <label className="flex items-start gap-3 rounded-xl bg-[var(--surface-container-high)] px-3 py-3 text-sm text-[var(--text-secondary)]">
        <input
          type="checkbox"
          checked={Boolean(settings.avatar_consent_confirmed)}
          onChange={(event) => onConsentChange(event.target.checked)}
          disabled={disabled}
          className="mt-1"
        />
        <span>
          <span className="block font-semibold text-[var(--text-primary)]">{t('avatar.explicitConsent')}</span>
          <span className="mt-1 block text-xs leading-5">
            {t('avatar.explicitConsentCaption')}
          </span>
        </span>
      </label>

      <label className="flex items-start gap-3 rounded-xl bg-[var(--surface-container-high)] px-3 py-3 text-sm text-[var(--text-secondary)]">
        <input
          type="checkbox"
          checked={Boolean(settings.avatar_enabled && settings.avatar_consent_confirmed)}
          onChange={(event) => onEnabledChange(event.target.checked)}
          disabled={disabled || !settings.avatar_consent_confirmed}
          className="mt-1"
        />
        <span>
          <span className="block font-semibold text-[var(--text-primary)]">{t('avatar.generationEnabled')}</span>
          <span className="mt-1 block text-xs leading-5">
            {t('avatar.generationEnabledCaption')}
          </span>
        </span>
      </label>
    </div>
  );
}

function SettingsSection({
  sectionId,
  eyebrow,
  title,
  caption,
  icon: Icon,
  defaultOpen = false,
  openState,
  onOpenStateChange,
  className = '',
  contentClassName = 'space-y-4',
  children,
}) {
  const controlledOpen = sectionId && Object.prototype.hasOwnProperty.call(openState || {}, sectionId)
    ? Boolean(openState[sectionId])
    : null;
  const [open, setOpen] = useState(controlledOpen ?? defaultOpen);

  useEffect(() => {
    setOpen(controlledOpen ?? defaultOpen);
  }, [controlledOpen, defaultOpen]);

  const handleToggle = () => {
    const nextOpen = !open;
    setOpen(nextOpen);
    if (sectionId && typeof onOpenStateChange === 'function') {
      onOpenStateChange(sectionId, nextOpen);
    }
  };

  return (
    <SurfaceCard className={`space-y-4 ${className}`}>
      <button
        type="button"
        onClick={handleToggle}
        className="focus-ring flex w-full items-start justify-between gap-3 rounded-2xl text-left"
        aria-expanded={open}
      >
        <span className="min-w-0">
          <span className="inline-flex items-center gap-2">
            {Icon ? <Icon size={16} className="text-[var(--accent-primary)]" /> : null}
            <span className="label-sm">{eyebrow}</span>
          </span>
          <span className="title-lg mt-2 block text-[var(--text-primary)]">{title}</span>
          {caption ? (
            <span className="mt-1 block text-sm font-normal text-[var(--text-secondary)]">{caption}</span>
          ) : null}
        </span>
        <ChevronDown size={18} className={`mt-1 shrink-0 text-[var(--text-secondary)] transition ${open ? 'rotate-180' : ''}`} />
      </button>

      {open ? <div className={contentClassName}>{children}</div> : null}
    </SurfaceCard>
  );
}

export default function Settings({ user, onUserRefresh }) {
  const { t } = useI18n();
  const { resolvedTheme, setMode } = useTheme();
  const { capabilities } = useCapabilities();
  const storedSettingsState = useMemo(() => readRouteSessionState('settings', user), [user]);
  const avatarFeatureEnabled = featureEnabled(capabilities, 'avatar');
  const teacherMode = canAccessStudio(user);
  const [settingsOpenSections, setSettingsOpenSections] = useState(
    () => (storedSettingsState.openSections && typeof storedSettingsState.openSections === 'object'
      ? storedSettingsState.openSections
      : {}),
  );
  const [autoplayNextEnabled, setAutoplayNextEnabledState] = useState(isAutoplayNextEnabled);
  const [reducedMotion, setReducedMotion] = useState(
    () => window.localStorage.getItem(REDUCED_MOTION_KEY) === 'true',
  );
  const [profileDraft, setProfileDraft] = useState(() => profileDraftFromUser(user));
  const [profileLoading, setProfileLoading] = useState(() => Boolean(user?.id));
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileMessage, setProfileMessage] = useState('');
  const [profileError, setProfileError] = useState('');
  const [profileEditorOpen, setProfileEditorOpen] = useState(false);
  const [profileEditDraft, setProfileEditDraft] = useState(() => profileDraftFromUser(user));
  const [profileFieldErrors, setProfileFieldErrors] = useState({});
  const [bannerFile, setBannerFile] = useState(null);
  const [logoFile, setLogoFile] = useState(null);
  const [bannerPreviewUrl, setBannerPreviewUrl] = useState('');
  const [logoPreviewUrl, setLogoPreviewUrl] = useState('');
  const [voiceFile, setVoiceFile] = useState(null);
  const [voicePreviewUrl, setVoicePreviewUrl] = useState('');
  const [voiceRecorderStatus, setVoiceRecorderStatus] = useState('idle');
  const [voiceRecorderError, setVoiceRecorderError] = useState('');
  const [voiceRecordingDuration, setVoiceRecordingDuration] = useState(0);
  const [recordedVoiceBlob, setRecordedVoiceBlob] = useState(null);
  const [recordedVoicePreviewUrl, setRecordedVoicePreviewUrl] = useState('');
  const [imageFile, setImageFile] = useState(null);
  const [videoFile, setVideoFile] = useState(null);
  const [mediaPreviewUrl, setMediaPreviewUrl] = useState('');
  const [mediaPreviewType, setMediaPreviewType] = useState('');
  const [avatarProfilePayload, setAvatarProfilePayload] = useState(null);
  const [avatarSettings, setAvatarSettings] = useState(DEFAULT_AVATAR_SETTINGS);
  const [teacherBusy, setTeacherBusy] = useState(false);
  const [teacherMessage, setTeacherMessage] = useState('');
  const [previewJobId, setPreviewJobId] = useState('');
  const [previewStatusLabel, setPreviewStatusLabel] = useState('idle');
  const [previewVideoUrl, setPreviewVideoUrl] = useState('');
  const [localDataMessage, setLocalDataMessage] = useState('');
  const [notificationPreferences, setNotificationPreferences] = useState(readNotificationPreferences);
  const [avatarModal, setAvatarModal] = useState('');
  const voiceRecorderRef = useRef(null);
  const voiceRecordingStreamRef = useRef(null);
  const voiceRecordingChunksRef = useRef([]);
  const voiceRecordingTimerRef = useRef(null);
  const voiceRecordingStartedAtRef = useRef(0);
  const recordedVoiceAudioRef = useRef(null);

  useEffect(() => {
    setSettingsOpenSections(
      storedSettingsState.openSections && typeof storedSettingsState.openSections === 'object'
        ? storedSettingsState.openSections
        : {},
    );
  }, [storedSettingsState]);

  const updateSettingsSectionOpen = useCallback((sectionId, open) => {
    setSettingsOpenSections((current) => ({
      ...current,
      [sectionId]: Boolean(open),
    }));
  }, []);

  useEffect(() => {
    writeRouteSessionState('settings', user, {
      openSections: settingsOpenSections,
      scrollY: typeof window !== 'undefined' ? window.scrollY : 0,
    });
  }, [settingsOpenSections, user]);

  useEffect(() => onRouteReset('settings', () => {
    clearRouteSessionState('settings', user);
    setSettingsOpenSections({});
    setProfileEditorOpen(false);
    setAvatarModal('');
    window.scrollTo({ top: 0, behavior: 'auto' });
  }), [user]);

  useEffect(() => {
    if (!storedSettingsState.scrollY) return undefined;
    const restoreId = window.requestAnimationFrame(() => {
      window.scrollTo({ top: Number(storedSettingsState.scrollY) || 0, behavior: 'auto' });
    });
    return () => window.cancelAnimationFrame(restoreId);
  }, [storedSettingsState.scrollY]);

  useEffect(() => {
    const persistScroll = () => {
      writeRouteSessionState('settings', user, {
        openSections: settingsOpenSections,
        scrollY: window.scrollY,
      });
    };
    window.addEventListener('pagehide', persistScroll);
    window.addEventListener('beforeunload', persistScroll);
    return () => {
      persistScroll();
      window.removeEventListener('pagehide', persistScroll);
      window.removeEventListener('beforeunload', persistScroll);
    };
  }, [settingsOpenSections, user]);

  useEffect(() => {
    window.localStorage.setItem(REDUCED_MOTION_KEY, String(reducedMotion));
    document.documentElement.classList.toggle('reduced-motion', reducedMotion);
  }, [reducedMotion]);

  useEffect(() => {
    setAutoplayNextEnabled(autoplayNextEnabled);
  }, [autoplayNextEnabled]);

  useEffect(() => {
    window.localStorage.setItem(NOTIFICATION_PREFS_KEY, JSON.stringify(notificationPreferences));
  }, [notificationPreferences]);

  useEffect(() => {
    const userDraft = profileDraftFromUser(user);
    setProfileDraft(userDraft);
    setProfileEditDraft(userDraft);
    setProfileMessage('');
    setProfileError('');
    setProfileFieldErrors({});
    setBannerFile(null);
    setLogoFile(null);

    if (!user?.id) {
      setProfileLoading(false);
      return undefined;
    }
    let active = true;
    setProfileLoading(true);
    fetchMyProfile()
      .then((payload) => {
        if (active) {
          const nextDraft = profileDraftFromPayload(payload);
          setProfileDraft(nextDraft);
          setProfileEditDraft(nextDraft);
        }
      })
      .catch(() => {
        if (active) setProfileError('Unable to refresh public profile details.');
      })
      .finally(() => {
        if (active) setProfileLoading(false);
      });
    return () => {
      active = false;
    };
  }, [user?.id]);

  useEffect(() => {
    if (!bannerFile) {
      setBannerPreviewUrl('');
      return undefined;
    }
    const objectUrl = URL.createObjectURL(bannerFile);
    setBannerPreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [bannerFile]);

  useEffect(() => {
    if (!logoFile) {
      setLogoPreviewUrl('');
      return undefined;
    }
    const objectUrl = URL.createObjectURL(logoFile);
    setLogoPreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [logoFile]);

  useEffect(() => {
    if (!voiceFile) {
      setVoicePreviewUrl('');
      return undefined;
    }

    const objectUrl = URL.createObjectURL(voiceFile);
    setVoicePreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [voiceFile]);

  useEffect(() => {
    if (!recordedVoiceBlob) {
      setRecordedVoicePreviewUrl('');
      return undefined;
    }

    const objectUrl = URL.createObjectURL(recordedVoiceBlob);
    setRecordedVoicePreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [recordedVoiceBlob]);

  useEffect(() => {
    const sample = videoFile || imageFile;
    if (!sample) {
      setMediaPreviewType('');
      setMediaPreviewUrl('');
      return undefined;
    }

    const objectUrl = URL.createObjectURL(sample);
    setMediaPreviewType(videoFile ? 'video' : 'image');
    setMediaPreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [imageFile, videoFile]);

  const activeTheme = resolvedTheme === 'dark' ? 'dark' : 'light';
  usePageLoading(profileLoading, 'settings-profile');
  const publicDisplayName = useMemo(
    () => displayNameFromDraft(profileDraft, user),
    [profileDraft, user],
  );
  const profileEditorDisplayName = useMemo(
    () => displayNameFromDraft(profileEditDraft, user),
    [profileEditDraft, user],
  );
  const profileValidationErrors = useMemo(
    () => validatePublicProfileDraft(profileEditDraft),
    [profileEditDraft],
  );
  const profileHasValidationErrors = Object.keys(profileValidationErrors).length > 0;
  const profileFieldError = (field) => profileValidationErrors[field] || profileFieldErrors[field] || '';
  const profileEditorDirty = useMemo(
    () => (
      JSON.stringify(profileEditDraft) !== JSON.stringify(profileDraft)
      || Boolean(bannerFile)
      || Boolean(logoFile)
    ),
    [bannerFile, logoFile, profileDraft, profileEditDraft],
  );
  const systemFeatureRows = useMemo(() => {
    const localTts = capabilities?.features?.local_tts || {};
    const localTtsStatus = String(localTts.status || '').trim().toLowerCase();
    return [
      {
        label: 'Avatar',
        value: featureStatusLabel(capabilities, 'avatar'),
        enabled: featureEnabled(capabilities, 'avatar'),
      },
      {
        label: 'Intelligence',
        value: featureStatusLabel(capabilities, 'intelligence'),
        enabled: featureEnabled(capabilities, 'intelligence'),
      },
      {
        label: 'Visual moderation',
        value: featureStatusLabel(capabilities, 'visual_moderation'),
        enabled: featureEnabled(capabilities, 'visual_moderation'),
      },
      {
        label: 'Local TTS',
        value: localTts.enabled ? t('settings.enabled') : localTtsStatus === 'fallback' ? 'Fallback' : 'Fallback',
        enabled: Boolean(localTts.enabled),
      },
    ];
  }, [capabilities, t]);

  const avatarSetupStatus = useMemo(() => {
    const normalized = normalizeAvatarSetupStatus(avatarProfilePayload || {});
    const checklist = {
      ...normalized.checklist,
      consent_confirmed: Boolean(avatarSettings.avatar_consent_confirmed),
      avatar_generation_enabled: Boolean(avatarSettings.avatar_enabled && avatarSettings.avatar_consent_confirmed),
    };
    let state = normalized.state;
    if (!checklist.consent_confirmed) {
      state = 'missing_consent';
    } else if (!checklist.portrait_uploaded) {
      state = 'missing_portrait';
    } else if (!checklist.voice_uploaded) {
      state = 'missing_voice';
    } else if (!checklist.avatar_generation_enabled) {
      state = 'disabled';
    } else if (['missing_consent', 'disabled'].includes(state)) {
      state = checklist.avatar_prepared ? 'ready' : 'needs_prepare';
    }

    const canPrepare = Boolean(
      checklist.consent_confirmed
      && checklist.portrait_uploaded
      && checklist.voice_uploaded
      && checklist.avatar_generation_enabled
      && ['needs_prepare', 'failed'].includes(state)
    );

    return {
      ...normalized,
      state,
      checklist,
      message: t(`avatar.${state === 'missing_consent' ? 'missingConsent' : state === 'missing_portrait' ? 'missingPortrait' : state === 'missing_voice' ? 'missingVoice' : state === 'needs_prepare' ? 'needsPrepare' : state}`) || AVATAR_STATUS_MESSAGES[state] || normalized.message,
      primary_action_label: state === 'failed'
        ? t('avatar.prepareAvatar')
        : state === 'needs_prepare'
          ? t('avatar.prepareAvatar')
          : normalized.primary_action_label,
      can_prepare: canPrepare,
      can_generate_preview: Boolean(
        state === 'ready'
        && normalized.can_generate_preview
        && checklist.consent_confirmed
        && checklist.avatar_generation_enabled
      ),
    };
  }, [
    avatarProfilePayload,
    avatarSettings.avatar_consent_confirmed,
    avatarSettings.avatar_enabled,
    t,
  ]);
  const avatarChecklist = useMemo(() => {
    const labelKeys = {
      portrait_uploaded: 'avatar.checklistPortrait',
      voice_uploaded: 'avatar.checklistVoice',
      consent_confirmed: 'avatar.checklistConsent',
      avatar_generation_enabled: 'avatar.checklistEnabled',
      avatar_prepared: 'avatar.checklistPrepared',
    };
    return avatarChecklistItems(avatarSetupStatus).map((item) => ({
      ...item,
      label: t(labelKeys[item.key] || item.label),
    }));
  }, [avatarSetupStatus, t]);
  const prepareAvatarButtonLabel = avatarSetupStatus.primary_action_label || t('avatar.prepareAvatar');

  const loadAvatarProfile = useCallback(async () => {
    if (!avatarFeatureEnabled || !teacherMode || !user?.id) {
      setAvatarProfilePayload(null);
      setPreviewVideoUrl('');
      setPreviewJobId('');
      setPreviewStatusLabel('idle');
      return;
    }

    try {
      const payload = await fetchAvatarProfile(user.id);
      setAvatarProfilePayload(payload);
      setPreviewVideoUrl(resolvePreviewVideoUrl(payload));

      const profile = payload?.profile || {};
      setAvatarSettings((previous) => ({
        ...previous,
        avatar_enabled: profile.avatar_enabled ?? previous.avatar_enabled,
        avatar_consent_confirmed: profile.avatar_consent_confirmed ?? previous.avatar_consent_confirmed,
        avatar_motion_preset: profile.avatar_motion_preset || previous.avatar_motion_preset,
        avatar_lipsync_engine: profile.avatar_lipsync_engine || previous.avatar_lipsync_engine,
        avatar_quality_preset: profile.avatar_quality_preset || previous.avatar_quality_preset,
        avatar_overlay_visible: profile.avatar_overlay_visible ?? previous.avatar_overlay_visible,
        avatar_overlay_default_position: profile.avatar_overlay_default_position || previous.avatar_overlay_default_position,
        avatar_overlay_size: profile.avatar_overlay_size || previous.avatar_overlay_size,
      }));
    } catch (error) {
      setTeacherMessage(error.message || 'Unable to load avatar profile settings.');
    }
  }, [avatarFeatureEnabled, teacherMode, user?.id]);

  useEffect(() => {
    loadAvatarProfile();
  }, [loadAvatarProfile]);

  useEffect(() => {
    if (!avatarFeatureEnabled || !previewJobId || !user?.id) return undefined;

    let active = true;
    const interval = window.setInterval(async () => {
      try {
        const statusPayload = await fetchAvatarPreviewStatus(user.id, previewJobId);
        if (!active) return;

        const nextStatus = String(
          statusPayload.status || statusPayload.preview_status || statusPayload.job_status || 'processing',
        ).toLowerCase();
        setPreviewStatusLabel(nextStatus);
        if (statusPayload.avatar_setup_status) {
          setAvatarProfilePayload((previous) => ({
            ...(previous || {}),
            avatar_setup_status: statusPayload.avatar_setup_status,
            readiness: statusPayload.preview_readiness || previous?.readiness,
          }));
        }

        const previewPath =
          statusPayload.preview_rel_path ||
          statusPayload.preview_path ||
          statusPayload.video_rel_path ||
          '';
        if (previewPath) {
          setPreviewVideoUrl(toAbsoluteMediaUrl(previewPath));
        }

        if (['ready', 'done', 'failed', 'error', 'deleted'].includes(nextStatus)) {
          window.clearInterval(interval);
          setPreviewJobId('');
          loadAvatarProfile();
        }
      } catch {
        if (!active) return;
        setPreviewStatusLabel('status-unavailable');
        window.clearInterval(interval);
        setPreviewJobId('');
      }
    }, 2200);

    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [avatarFeatureEnabled, loadAvatarProfile, previewJobId, user?.id]);

  useEffect(() => {
    if (avatarFeatureEnabled) return;
    setAvatarModal('');
    setTeacherBusy(false);
    setTeacherMessage('');
    setVoiceFile(null);
    setVoiceRecorderStatus('idle');
    setVoiceRecorderError('');
    setVoiceRecordingDuration(0);
    setRecordedVoiceBlob(null);
    setImageFile(null);
    setVideoFile(null);
    setMediaPreviewUrl('');
    setMediaPreviewType('');
    setPreviewJobId('');
    setPreviewVideoUrl('');
  }, [avatarFeatureEnabled]);

  const clearVoiceRecordingTimer = useCallback(() => {
    if (voiceRecordingTimerRef.current) {
      window.clearInterval(voiceRecordingTimerRef.current);
      voiceRecordingTimerRef.current = null;
    }
  }, []);

  const stopVoiceRecordingStream = useCallback(() => {
    voiceRecordingStreamRef.current?.getTracks?.().forEach((track) => track.stop());
    voiceRecordingStreamRef.current = null;
  }, []);

  const cancelActiveVoiceRecording = useCallback(() => {
    const recorder = voiceRecorderRef.current;
    if (recorder) {
      recorder.ondataavailable = null;
      recorder.onstop = null;
      recorder.onerror = null;
      if (recorder.state === 'recording') {
        recorder.stop();
      }
      voiceRecorderRef.current = null;
    }
    clearVoiceRecordingTimer();
    stopVoiceRecordingStream();
    voiceRecordingChunksRef.current = [];
  }, [clearVoiceRecordingTimer, stopVoiceRecordingStream]);

  useEffect(() => () => {
    cancelActiveVoiceRecording();
  }, [cancelActiveVoiceRecording]);

  useEffect(() => {
    if (avatarModal === 'voice') return;
    cancelActiveVoiceRecording();
    setRecordedVoiceBlob(null);
    setVoiceRecorderStatus('idle');
    setVoiceRecorderError('');
    setVoiceRecordingDuration(0);
  }, [avatarModal, cancelActiveVoiceRecording]);

  const clearLocalNotes = () => {
    const noteKeys = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const key = window.localStorage.key(i);
      if (key?.startsWith('visus-notes-')) {
        noteKeys.push(key);
      }
    }
    noteKeys.forEach((key) => window.localStorage.removeItem(key));
    setLocalDataMessage(noteKeys.length ? t('settings.localNotesCleared') : t('settings.noLocalNotes'));
  };

  const handleAvatarConsentChange = (checked) => {
    setAvatarSettings((previous) => ({
      ...previous,
      avatar_consent_confirmed: checked,
      avatar_enabled: checked ? true : false,
    }));
  };

  const handleAvatarEnabledChange = (checked) => {
    setAvatarSettings((previous) => ({
      ...previous,
      avatar_enabled: Boolean(checked && previous.avatar_consent_confirmed),
    }));
  };

  const runTeacherAction = async (action, successMessage) => {
    setTeacherBusy(true);
    setTeacherMessage('');
    try {
      await action();
      setTeacherMessage(successMessage);
    } catch (error) {
      setTeacherMessage(error.message || t('common.error'));
    } finally {
      setTeacherBusy(false);
    }
  };

  const refreshSessionUser = useCallback(async () => {
    if (typeof onUserRefresh !== 'function') return;
    try {
      await onUserRefresh();
    } catch {
      // Keep settings flow resilient even if auth/me refresh fails.
    }
  }, [onUserRefresh]);

  const updateProfileDraftField = (field, value) => {
    setProfileEditDraft((previous) => ({ ...previous, [field]: value }));
    setProfileMessage('');
    setProfileError('');
    setProfileFieldErrors({});
  };

  const updateSocialLinkField = (field, value) => {
    setProfileEditDraft((previous) => ({
      ...previous,
      social_links: {
        ...(previous.social_links || {}),
        [field]: value,
      },
    }));
    setProfileMessage('');
    setProfileError('');
    setProfileFieldErrors({});
  };

  const openPublicProfileEditor = () => {
    setProfileEditDraft(profileDraft);
    setBannerFile(null);
    setLogoFile(null);
    setProfileMessage('');
    setProfileError('');
    setProfileFieldErrors({});
    setProfileEditorOpen(true);
  };

  const closePublicProfileEditor = () => {
    if (profileSaving) return;
    setProfileEditorOpen(false);
    setProfileEditDraft(profileDraft);
    setBannerFile(null);
    setLogoFile(null);
    setProfileError('');
    setProfileFieldErrors({});
  };

  const updateNotificationPreference = (field, checked) => {
    setNotificationPreferences((previous) => ({ ...previous, [field]: checked }));
  };

  const handleVoiceFileChange = (file) => {
    setVoiceFile(file || null);
    setVoiceRecorderError('');
  };

  const handleStartVoiceRecording = async () => {
    const preflightDiagnostics = await voiceRecordingDiagnostics('preflight');
    if (
      !preflightDiagnostics.isSecureContext
      || !preflightDiagnostics.hasMediaDevices
      || !preflightDiagnostics.hasGetUserMedia
      || !preflightDiagnostics.mediaRecorderSupported
    ) {
      setVoiceRecorderStatus('error');
      setVoiceRecorderError(voiceRecordingErrorMessage(null, 'preflight', preflightDiagnostics));
      return;
    }

    setVoiceRecorderStatus('requesting permission');
    setVoiceRecorderError('');
    setRecordedVoiceBlob(null);
    setVoiceRecordingDuration(0);
    voiceRecordingChunksRef.current = [];

    let stream = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      const diagnostics = await voiceRecordingDiagnostics('getUserMedia', error);
      logVoiceRecordingError('getUserMedia', diagnostics, error);
      setVoiceRecorderStatus('error');
      setVoiceRecorderError(voiceRecordingErrorMessage(error, 'getUserMedia', diagnostics));
      return;
    }

    if (!stream.getAudioTracks?.().length) {
      stream.getTracks?.().forEach((track) => track.stop());
      setVoiceRecorderStatus('error');
      setVoiceRecorderError('No microphone audio track was captured.');
      return;
    }

    const mimeType = preferredVoiceRecordingMimeType();
    let recorder = null;
    try {
      recorder = new window.MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    } catch (error) {
      stream.getTracks?.().forEach((track) => track.stop());
      const diagnostics = await voiceRecordingDiagnostics('MediaRecorder', error);
      logVoiceRecordingError('MediaRecorder', diagnostics, error);
      setVoiceRecorderStatus('error');
      setVoiceRecorderError(voiceRecordingErrorMessage(error, 'MediaRecorder', diagnostics));
      return;
    }

    voiceRecordingStreamRef.current = stream;
    voiceRecorderRef.current = recorder;

    recorder.ondataavailable = (event) => {
      if (event.data?.size > 0) {
        voiceRecordingChunksRef.current.push(event.data);
      }
    };
    recorder.onerror = async (event) => {
      clearVoiceRecordingTimer();
      stopVoiceRecordingStream();
      const error = event.error || event;
      const diagnostics = await voiceRecordingDiagnostics('MediaRecorder.onerror', error);
      logVoiceRecordingError('MediaRecorder.onerror', diagnostics, error);
      setVoiceRecorderStatus('error');
      setVoiceRecorderError(voiceRecordingErrorMessage(error, 'MediaRecorder.onerror', diagnostics));
    };
    recorder.onstop = () => {
      clearVoiceRecordingTimer();
      stopVoiceRecordingStream();
      const chunks = voiceRecordingChunksRef.current;
      voiceRecordingChunksRef.current = [];
      voiceRecorderRef.current = null;
      if (!chunks.length) {
        setRecordedVoiceBlob(null);
        setVoiceRecorderStatus('error');
        setVoiceRecorderError('No audio was captured. Check the microphone and try again.');
        return;
      }
      const recordingType = recorder.mimeType || mimeType || chunks[0]?.type || 'audio/webm';
      setRecordedVoiceBlob(new Blob(chunks, { type: recordingType }));
      setVoiceRecorderStatus('recorded');
      setVoiceRecorderError('');
    };

    try {
      recorder.start();
    } catch (error) {
      clearVoiceRecordingTimer();
      stopVoiceRecordingStream();
      const diagnostics = await voiceRecordingDiagnostics('MediaRecorder.start', error);
      logVoiceRecordingError('MediaRecorder.start', diagnostics, error);
      setVoiceRecorderStatus('error');
      setVoiceRecorderError(voiceRecordingErrorMessage(error, 'MediaRecorder.start', diagnostics));
      return;
    }

    voiceRecordingStartedAtRef.current = Date.now();
    setVoiceRecorderStatus('recording');
    voiceRecordingTimerRef.current = window.setInterval(() => {
      setVoiceRecordingDuration(Math.floor((Date.now() - voiceRecordingStartedAtRef.current) / 1000));
    }, 250);
  };

  const handleStopVoiceRecording = () => {
    const recorder = voiceRecorderRef.current;
    if (recorder?.state === 'recording') {
      setVoiceRecordingDuration(Math.floor((Date.now() - voiceRecordingStartedAtRef.current) / 1000));
      recorder.stop();
      return;
    }
    clearVoiceRecordingTimer();
    stopVoiceRecordingStream();
  };

  const handlePlayRecordedVoice = () => {
    recordedVoiceAudioRef.current?.play?.();
  };

  const handleUseRecordedVoice = () => {
    if (!recordedVoiceBlob) return;
    const extension = recordedVoiceFileExtension(recordedVoiceBlob.type);
    const file = new File([recordedVoiceBlob], `voice-sample-recording.${extension}`, {
      type: recordedVoiceBlob.type || `audio/${extension}`,
      lastModified: Date.now(),
    });
    handleVoiceFileChange(file);
  };

  const handleDiscardRecordedVoice = () => {
    cancelActiveVoiceRecording();
    setRecordedVoiceBlob(null);
    setVoiceRecorderStatus('idle');
    setVoiceRecorderError('');
    setVoiceRecordingDuration(0);
  };

  const handleSavePublicProfile = async (event) => {
    event.preventDefault();
    if (!user?.id) return;
    if (profileHasValidationErrors) {
      setProfileFieldErrors(profileValidationErrors);
      setProfileError('Fix the highlighted profile fields before saving.');
      return;
    }

    setProfileSaving(true);
    setProfileMessage('');
    setProfileError('');
    setProfileFieldErrors({});
    try {
      let payload = await updateMyProfile(normalizedPublicProfilePayload(profileEditDraft));
      if (bannerFile || logoFile) {
        payload = await uploadProfileAssets({ bannerFile, logoFile });
      }
      const nextDraft = profileDraftFromPayload(payload);
      setProfileDraft(nextDraft);
      setProfileEditDraft(nextDraft);
      setBannerFile(null);
      setLogoFile(null);
      await refreshSessionUser();
      setProfileMessage(profileAssetModerationMessage(payload));
      setProfileEditorOpen(false);
    } catch (error) {
      const fieldErrors = profileFieldErrorsFromApi(error.details);
      if (Object.keys(fieldErrors).length) {
        setProfileFieldErrors(fieldErrors);
      }
      setProfileError(error.message || 'Unable to save public profile.');
    } finally {
      setProfileSaving(false);
    }
  };

  const handleUploadVoice = async () => {
    if (!avatarFeatureEnabled || !user?.id || !voiceFile) return;
    await runTeacherAction(async () => {
      await uploadVoiceSample(user.id, voiceFile);
      await loadAvatarProfile();
    }, t('avatar.voiceSampleUploaded'));
  };

  const handleUploadVisualSample = async () => {
    if (!avatarFeatureEnabled || !user?.id || (!imageFile && !videoFile)) return;
    if (!avatarSettings.avatar_consent_confirmed) {
      setTeacherMessage(t('avatar.confirmBeforePortrait'));
      return;
    }

    await runTeacherAction(async () => {
      if (videoFile) {
        await uploadAvatarVideo(user.id, videoFile, avatarSettings);
      } else if (imageFile) {
        await uploadAvatarImage(user.id, imageFile, avatarSettings);
      }
      await loadAvatarProfile();
      await refreshSessionUser();
    }, t('avatar.sourceSampleUploaded'));
  };

  const handleSaveTeacherDefaults = async () => {
    if (!avatarFeatureEnabled || !user?.id) return;

    await runTeacherAction(async () => {
      await updateAvatarProfile(user.id, avatarSettings);
      await loadAvatarProfile();
      await refreshSessionUser();
    }, t('avatar.settingsSaved'));
  };

  const handlePrepareAvatar = async () => {
    if (!avatarFeatureEnabled || !user?.id) return;
    if (!avatarSetupStatus.can_prepare) {
      setTeacherMessage(avatarSetupStatus.message || t('avatar.needsPrepare'));
      return;
    }

    await runTeacherAction(async () => {
      await prepareAvatarProfile(user.id, {
        avatar_enabled: avatarSettings.avatar_enabled,
        avatar_consent_confirmed: avatarSettings.avatar_consent_confirmed,
        composite_fallback_allowed: avatarSettings.composite_fallback_allowed,
        force_reprocess: avatarSetupStatus.needs_prepare || avatarSetupStatus.state === 'failed',
      });
      await loadAvatarProfile();
    }, t('avatar.prepCompleted'));
  };

  const handleGeneratePreview = async () => {
    if (!avatarFeatureEnabled || !user?.id) return;
    if (!avatarSetupStatus.can_generate_preview) {
      setTeacherMessage(avatarSetupStatus.message || t('avatar.needsPrepare'));
      return;
    }

    await runTeacherAction(async () => {
      const queued = await regenerateAvatarPreview(user.id);
      setPreviewJobId(String(queued?.job_id || ''));
      setPreviewStatusLabel('queued');
    }, t('avatar.previewQueued'));
  };

  const handleDeletePreview = async () => {
    if (!avatarFeatureEnabled || !user?.id) return;

    await runTeacherAction(async () => {
      await deleteAvatarPreview(user.id);
      setPreviewVideoUrl('');
      setPreviewStatusLabel('deleted');
      await loadAvatarProfile();
    }, t('avatar.previewRemoved'));
  };

  const closeAvatarModal = () => {
    if (!teacherBusy) setAvatarModal('');
  };

  return (
    <div className="space-y-5">
      <section className="layout-grid-12">
        <SurfaceCard elevated className="lg:col-span-12">
          <p className="label-sm">{t('settings.title')}</p>
          <h1 className="display-lg mt-2 text-[var(--text-primary)]">{t('settings.workspacePreferences')}</h1>
          <p className="body-md mt-3 max-w-2xl">
            {t('settings.subtitle')}
          </p>
        </SurfaceCard>
      </section>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">
        <SettingsSection
          sectionId="theme"
          openState={settingsOpenSections}
          onOpenStateChange={updateSettingsSectionOpen}
          eyebrow={t('settings.themeEyebrow')}
          title={t('settings.themeTitle')}
          caption={t('settings.themeCaption')}
          icon={activeTheme === 'dark' ? MoonStar : Sun}
        >
          <div className="inline-flex rounded-full bg-[var(--surface-container-high)] p-1">
            {THEME_OPTIONS.map((option) => {
              const Icon = option.icon;
              const selected = activeTheme === option.id;

              return (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setMode(option.id)}
                  className={`focus-ring inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition ${
                    selected
                      ? 'bg-[color:rgba(107,56,212,0.12)] text-[var(--text-primary)] dark:bg-[color:rgba(208,188,255,0.2)]'
                      : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  <Icon size={15} />
                  <span>{option.id === 'dark' ? t('settings.dark') : t('settings.light')}</span>
                </button>
              );
            })}
          </div>

          <div className="space-y-2 text-sm text-[var(--text-secondary)]">
            {THEME_OPTIONS.map((option) => (
              <p key={option.id}>
                <span className="font-semibold text-[var(--text-primary)]">{option.id === 'dark' ? t('settings.dark') : t('settings.light')}:</span> {option.id === 'dark' ? t('settings.darkCaption') : t('settings.lightCaption')}
              </p>
            ))}
          </div>
        </SettingsSection>

        <SettingsSection
          sectionId="language"
          openState={settingsOpenSections}
          onOpenStateChange={updateSettingsSectionOpen}
          eyebrow={t('settings.languageEyebrow')}
          title={t('settings.languageTitle')}
          caption={t('settings.languageCaption')}
          icon={MonitorPlay}
        >
          <LanguageSelector />
        </SettingsSection>

        <SettingsSection
          sectionId="public-profile"
          openState={settingsOpenSections}
          onOpenStateChange={updateSettingsSectionOpen}
          eyebrow={t('settings.publicProfileEyebrow')}
          title={t('settings.publicProfileTitle')}
          caption={t('settings.publicProfileCaption')}
          icon={UserCircle2}
          className="md:col-span-2 2xl:col-span-1"
        >
          <div className="space-y-3">
            <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-container-high)] p-3">
              <div
                className="min-h-24 rounded-xl bg-[var(--surface-container-highest)] bg-cover bg-center"
                style={profileDraft.banner_url ? {
                  backgroundImage: `linear-gradient(90deg, rgba(0,0,0,0.42), rgba(0,0,0,0.12)), url(${profileDraft.banner_url})`,
                } : undefined}
              />
              <div className="-mt-8 flex flex-col gap-3 px-2 sm:flex-row sm:items-end sm:justify-between">
                <div className="flex min-w-0 items-end gap-3">
                  <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full border-4 border-[var(--surface-container-high)] bg-[var(--surface-container-highest)]">
                    {profileDraft.logo_url ? (
                      <img src={profileDraft.logo_url} alt="" className="h-full w-full rounded-full object-cover" />
                    ) : (
                      <UserCircle2 size={34} className="text-[var(--text-secondary)]" />
                    )}
                  </div>
                  <div className="min-w-0 pb-1">
                    <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${
                      profileDraft.is_public_profile
                        ? 'bg-[var(--status-success-bg)] text-[var(--status-success-fg)]'
                        : 'bg-[var(--surface-container-highest)] text-[var(--text-secondary)]'
                    }`}>
                      {profileDraft.is_public_profile ? t('settings.public') : t('settings.private')}
                    </span>
                    <p className="mt-2 truncate text-sm font-semibold text-[var(--text-primary)]">{publicDisplayName}</p>
                    <p className="text-xs text-[var(--text-secondary)]">
                      {profileDraft.website_url || t('settings.noWebsiteSet')}
                    </p>
                  </div>
                </div>
                <Button size="sm" variant="secondary" onClick={openPublicProfileEditor} disabled={!user}>
                  <UserCircle2 size={15} />
                  <span>{t('settings.editPublicProfile')}</span>
                </Button>
              </div>
            </div>

            {!user && (
              <p className="text-sm text-[var(--text-secondary)]">{t('settings.signInEditPublicProfile')}</p>
            )}
            {profileMessage && (
              <p className="rounded-xl bg-[var(--status-success-bg)] px-3 py-2 text-sm text-[var(--status-success-fg)]">{profileMessage}</p>
            )}
            {profileError && !profileEditorOpen && (
              <p className="rounded-xl bg-[var(--status-danger-bg)] px-3 py-2 text-sm text-[var(--status-danger-fg)]">{profileError}</p>
            )}
          </div>
        </SettingsSection>

        <SettingsSection
          sectionId="motion"
          openState={settingsOpenSections}
          onOpenStateChange={updateSettingsSectionOpen}
          eyebrow={t('settings.playbackEyebrow')}
          title={t('settings.playbackTitle')}
          caption={t('settings.playbackCaption')}
          icon={MonitorPlay}
        >
          <div className="space-y-3">
            <label className="flex items-start gap-3 rounded-xl bg-[var(--surface-container-high)] px-3 py-3 text-sm text-[var(--text-secondary)]">
              <input
                type="checkbox"
                checked={autoplayNextEnabled}
                onChange={(event) => setAutoplayNextEnabledState(event.target.checked)}
                className="mt-1"
              />
              <span>
                <span className="block font-semibold text-[var(--text-primary)]">{t('settings.continueNextTitle')}</span>
                <span className="mt-1 block text-xs leading-5">
                  {t('settings.continueNextCaption')}
                </span>
              </span>
            </label>

            <label className="flex items-start gap-3 rounded-xl bg-[var(--surface-container-high)] px-3 py-3 text-sm text-[var(--text-secondary)]">
              <input
                type="checkbox"
                checked={reducedMotion}
                onChange={(event) => setReducedMotion(event.target.checked)}
                className="mt-1"
              />
              <span>
                <span className="block font-semibold text-[var(--text-primary)]">{t('settings.reducedMotionTitle')}</span>
                <span className="mt-1 block text-xs leading-5">
                  {t('settings.reducedMotionCaption')}
                </span>
              </span>
            </label>
          </div>
        </SettingsSection>

        <SettingsSection
          sectionId="notifications"
          openState={settingsOpenSections}
          onOpenStateChange={updateSettingsSectionOpen}
          eyebrow={t('settings.notificationsEyebrow')}
          title={t('settings.notificationsTitle')}
          caption={t('settings.notificationsCaption')}
          icon={Bell}
        >
          <div className="space-y-3">
            <label className="flex items-start gap-3 rounded-xl bg-[var(--surface-container-high)] px-3 py-3 text-sm text-[var(--text-secondary)]">
              <input
                type="checkbox"
                checked={notificationPreferences.commentsOnLessons}
                onChange={(event) => updateNotificationPreference('commentsOnLessons', event.target.checked)}
                className="mt-1"
              />
              <span>
                <span className="block font-semibold text-[var(--text-primary)]">{t('settings.commentsOnLessons')}</span>
                <span className="mt-1 block text-xs">{t('settings.commentsOnLessonsCaption')}</span>
              </span>
            </label>

            <label className="flex items-start gap-3 rounded-xl bg-[var(--surface-container-high)] px-3 py-3 text-sm text-[var(--text-secondary)]">
              <input
                type="checkbox"
                checked={notificationPreferences.renderUpdates}
                onChange={(event) => updateNotificationPreference('renderUpdates', event.target.checked)}
                className="mt-1"
              />
              <span>
                <span className="block font-semibold text-[var(--text-primary)]">{t('settings.renderUpdates')}</span>
                <span className="mt-1 block text-xs">{t('settings.renderUpdatesCaption')}</span>
              </span>
            </label>

            <label className="flex items-start gap-3 rounded-xl bg-[var(--surface-container-high)] px-3 py-3 text-sm text-[var(--text-secondary)]">
              <input
                type="checkbox"
                checked={notificationPreferences.followedPublisherLessons}
                onChange={(event) => updateNotificationPreference('followedPublisherLessons', event.target.checked)}
                className="mt-1"
              />
              <span>
                <span className="block font-semibold text-[var(--text-primary)]">{t('settings.followedPublisherLessons')}</span>
                <span className="mt-1 block text-xs">{t('settings.followedPublisherLessonsCaption')}</span>
              </span>
            </label>
          </div>
        </SettingsSection>

        <SettingsSection
          sectionId="local-data"
          openState={settingsOpenSections}
          onOpenStateChange={updateSettingsSectionOpen}
          eyebrow={t('settings.browserDataEyebrow')}
          title={t('settings.browserDataTitle')}
          caption={t('settings.browserDataCaption')}
          icon={Trash2}
        >
          <Button variant="secondary" onClick={clearLocalNotes}>
            <Trash2 size={15} />
            <span>{t('settings.clearLocalNotes')}</span>
          </Button>

          {localDataMessage && (
            <p className="text-sm text-[var(--text-secondary)]">{localDataMessage}</p>
          )}
        </SettingsSection>

        <SettingsSection
          sectionId="system-features"
          openState={settingsOpenSections}
          onOpenStateChange={updateSettingsSectionOpen}
          eyebrow={t('settings.deploymentEyebrow')}
          title={t('settings.systemFeaturesTitle')}
          caption={t('settings.systemFeaturesCaption')}
          icon={MonitorPlay}
        >
          <div className="grid gap-2 sm:grid-cols-2">
            {systemFeatureRows.map((feature) => (
              <div
                key={feature.label}
                className="flex items-center justify-between gap-3 rounded-xl bg-[var(--surface-container-high)] px-3 py-2"
              >
                <span className="text-sm font-medium text-[var(--text-primary)]">{feature.label}</span>
                <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                  feature.enabled
                    ? 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]'
                    : 'bg-[color:var(--surface-muted)] text-[var(--text-secondary)]'
                }`}>
                  {feature.value}
                </span>
              </div>
            ))}
          </div>
        </SettingsSection>

        {teacherMode && avatarFeatureEnabled && (
          <SettingsSection
            sectionId="avatar"
            openState={settingsOpenSections}
            onOpenStateChange={updateSettingsSectionOpen}
            eyebrow={t('avatar.preferences')}
            title={t('avatar.sectionTitle')}
            caption={t('avatar.sectionCaption')}
            icon={Sparkles}
            className="md:col-span-2 2xl:col-span-3"
            contentClassName="space-y-4"
          >
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
              <AvatarActionCard
                title={t('avatar.voiceSample')}
                caption={t('avatar.voiceSampleCaption')}
                icon={Upload}
                onClick={() => setAvatarModal('voice')}
              />

              <AvatarActionCard
                title={t('avatar.pictureVideoSample')}
                caption={t('avatar.pictureVideoSampleCaption')}
                icon={UserCircle2}
                onClick={() => setAvatarModal('media')}
              />

              <AvatarActionCard
                title={t('avatar.preview')}
                caption={t('avatar.previewCaption')}
                icon={Sparkles}
                onClick={() => setAvatarModal('preview')}
              />
            </div>

            <div className="space-y-3 rounded-2xl bg-[var(--surface-container-low)] p-4">
              <AvatarSetupChecklist items={avatarChecklist} />
              <AvatarConsentControls
                settings={avatarSettings}
                onConsentChange={handleAvatarConsentChange}
                onEnabledChange={handleAvatarEnabledChange}
                disabled={teacherBusy}
              />
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="min-w-0 text-sm text-[var(--text-secondary)]">
                  {avatarSetupStatus.message}
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" onClick={handleSaveTeacherDefaults} disabled={teacherBusy}>
                    <Save size={15} />
                    <span>{t('avatar.saveSettings')}</span>
                  </Button>
                  {avatarSetupStatus.can_prepare && (
                    <Button onClick={handlePrepareAvatar} disabled={teacherBusy}>
                      <Sparkles size={15} />
                      <span>{prepareAvatarButtonLabel}</span>
                    </Button>
                  )}
                  {avatarSetupStatus.can_generate_preview && (
                    <Button onClick={handleGeneratePreview} disabled={teacherBusy}>
                      <Sparkles size={15} />
                      <span>{t('avatar.generatePreview')}</span>
                    </Button>
                  )}
                </div>
              </div>
            </div>

            {teacherMessage && (
              <p className="rounded-xl bg-[color:color-mix(in_srgb,var(--surface-muted),transparent_6%)] px-3 py-2 text-sm text-[var(--text-secondary)]">
                {teacherMessage}
              </p>
            )}
          </SettingsSection>
        )}
      </section>

      <PublicProfileEditor
        open={profileEditorOpen}
        title={t('settings.editPublicProfile')}
        titleId="settings-public-profile-editor-title"
        draft={profileEditDraft}
        displayNamePreview={profileEditorDisplayName}
        bannerPreviewUrl={bannerPreviewUrl}
        logoPreviewUrl={logoPreviewUrl}
        onCancel={closePublicProfileEditor}
        onSubmit={handleSavePublicProfile}
        onFieldChange={updateProfileDraftField}
        onSocialChange={updateSocialLinkField}
        onBannerFileChange={setBannerFile}
        onLogoFileChange={setLogoFile}
        fieldError={profileFieldError}
        error={profileError}
        saving={profileSaving}
        disabled={!user}
        saveDisabled={profileHasValidationErrors}
        cancelLabel={profileEditorDirty ? t('common.discard') : t('common.cancel')}
        canBackdropClose={!profileEditorDirty}
        formId="settings-public-profile-editor-form"
      />

      <ModalShell
        open={avatarFeatureEnabled && avatarModal === 'voice'}
        eyebrow={t('avatar.preferences')}
        title={t('avatar.voiceModalTitle')}
        titleId="avatar-voice-modal-title"
        closeLabel={t('avatar.closeVoiceSample')}
        onClose={closeAvatarModal}
        closeDisabled={teacherBusy || voiceRecorderStatus === 'recording'}
        canBackdropClose={!voiceFile && voiceRecorderStatus !== 'recording' && !recordedVoiceBlob}
        maxWidthClass="max-w-xl"
        footer={(
          <div className="flex justify-end">
            <Button variant="ghost" onClick={closeAvatarModal} disabled={teacherBusy}>
              <span>{voiceFile || recordedVoiceBlob ? t('avatar.discardAndClose') : t('common.close')}</span>
            </Button>
          </div>
        )}
      >
        <div className="space-y-4">
          <label className="block text-sm text-[var(--text-secondary)]">
            {t('avatar.voiceAudio')}
            <input
              type="file"
              accept="audio/*"
              onChange={(event) => handleVoiceFileChange(event.target.files?.[0] || null)}
              className="focus-ring mt-1 block w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] p-2 text-sm text-[var(--text-primary)]"
            />
          </label>

          <div className="space-y-3 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-container-high)] p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-[var(--text-primary)]">{t('avatar.recordFromMicrophone')}</p>
                <p className="mt-1 text-xs text-[var(--text-secondary)]">
                  {t('avatar.recorderStatus', { status: voiceRecorderStatus, duration: formatRecordingDuration(voiceRecordingDuration) })}
                </p>
              </div>
              <Mic size={18} className="text-[var(--accent-primary)]" />
            </div>

            <blockquote className="rounded-xl bg-[var(--surface-container-highest)] px-3 py-2 text-sm leading-6 text-[var(--text-secondary)]">
              {VOICE_RECORDING_SAMPLE_TEXT}
            </blockquote>

            {voiceRecorderError && (
              <p className="rounded-xl bg-[var(--status-danger-bg)] px-3 py-2 text-sm text-[var(--status-danger-fg)]">
                {voiceRecorderError}
              </p>
            )}

            {recordedVoicePreviewUrl && (
              <audio ref={recordedVoiceAudioRef} src={recordedVoicePreviewUrl} className="hidden" />
            )}

            <div className="flex flex-wrap gap-2">
              {voiceRecorderStatus !== 'recording' ? (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleStartVoiceRecording}
                  disabled={teacherBusy || voiceRecorderStatus === 'requesting permission'}
                >
                  <Mic size={15} />
                  <span>{voiceRecorderStatus === 'requesting permission' ? t('common.requesting') : t('avatar.startRecording')}</span>
                </Button>
              ) : (
                <Button variant="secondary" size="sm" onClick={handleStopVoiceRecording} disabled={teacherBusy}>
                  <Square size={15} />
                  <span>{t('avatar.stopRecording')}</span>
                </Button>
              )}

              <Button
                variant="ghost"
                size="sm"
                onClick={handlePlayRecordedVoice}
                disabled={!recordedVoiceBlob || voiceRecorderStatus === 'recording'}
              >
                <Play size={15} />
                <span>{t('avatar.playPreview')}</span>
              </Button>

              <Button
                variant="secondary"
                size="sm"
                onClick={handleUseRecordedVoice}
                disabled={!recordedVoiceBlob || voiceRecorderStatus === 'recording'}
              >
                <Upload size={15} />
                <span>{t('avatar.useRecording')}</span>
              </Button>

              <Button
                variant="ghost"
                size="sm"
                onClick={handleDiscardRecordedVoice}
                disabled={teacherBusy || (!recordedVoiceBlob && voiceRecorderStatus !== 'recording' && voiceRecorderStatus !== 'error')}
              >
                <Trash2 size={15} />
                <span>{t('avatar.discardRecording')}</span>
              </Button>
            </div>
          </div>

          {voicePreviewUrl && (
            <audio controls src={voicePreviewUrl} className="w-full" />
          )}

          <Button onClick={handleUploadVoice} disabled={teacherBusy || !voiceFile}>
            <Upload size={15} />
            <span>{teacherBusy ? t('common.uploading') : t('avatar.uploadVoiceSample')}</span>
          </Button>
        </div>
      </ModalShell>

      <ModalShell
        open={avatarFeatureEnabled && avatarModal === 'media'}
        eyebrow={t('avatar.preferences')}
        title={t('avatar.mediaModalTitle')}
        titleId="avatar-media-modal-title"
        closeLabel={t('avatar.closeMediaModal')}
        onClose={closeAvatarModal}
        closeDisabled={teacherBusy}
        canBackdropClose={!imageFile && !videoFile}
        maxWidthClass="max-w-2xl"
        footer={(
          <div className="flex justify-end">
            <Button variant="ghost" onClick={closeAvatarModal} disabled={teacherBusy}>
              <span>{imageFile || videoFile ? t('avatar.discardAndClose') : t('common.close')}</span>
            </Button>
          </div>
        )}
      >
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm text-[var(--text-secondary)]">
              {t('avatar.portraitImage')}
              <input
                type="file"
                accept="image/*"
                onChange={(event) => setImageFile(event.target.files?.[0] || null)}
                className="focus-ring mt-1 block w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] p-2 text-sm text-[var(--text-primary)]"
              />
            </label>

            <label className="block text-sm text-[var(--text-secondary)]">
              {t('avatar.portraitVideo')}
              <input
                type="file"
                accept="video/*"
                onChange={(event) => setVideoFile(event.target.files?.[0] || null)}
                className="focus-ring mt-1 block w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] p-2 text-sm text-[var(--text-primary)]"
              />
            </label>
          </div>

          <AvatarConsentControls
            settings={avatarSettings}
            onConsentChange={handleAvatarConsentChange}
            onEnabledChange={handleAvatarEnabledChange}
            disabled={teacherBusy}
          />

          {!avatarSettings.avatar_consent_confirmed && (
            <p className="rounded-xl bg-[var(--status-warning-bg)] px-3 py-2 text-sm text-[var(--status-warning-fg)]">
              {t('avatar.confirmBeforePortrait')}
            </p>
          )}

          {mediaPreviewUrl && mediaPreviewType === 'image' && (
            <img
              src={mediaPreviewUrl}
              alt={t('avatar.uploadedAvatarSampleAlt')}
              className="max-h-56 w-full rounded-2xl object-cover token-surface"
            />
          )}

          {mediaPreviewUrl && mediaPreviewType === 'video' && (
            <video
              src={mediaPreviewUrl}
              controls
              className="max-h-56 w-full rounded-2xl object-cover token-surface"
            />
          )}

          <Button onClick={handleUploadVisualSample} disabled={teacherBusy || !avatarSettings.avatar_consent_confirmed || (!imageFile && !videoFile)}>
            <Upload size={15} />
            <span>{teacherBusy ? t('common.uploading') : t('avatar.uploadVisualSample')}</span>
          </Button>
        </div>
      </ModalShell>

      <ModalShell
        open={avatarFeatureEnabled && avatarModal === 'preview'}
        eyebrow={t('avatar.preferences')}
        title={t('avatar.previewModalTitle')}
        titleId="avatar-preview-modal-title"
        closeLabel={t('avatar.closePreview')}
        onClose={closeAvatarModal}
        closeDisabled={teacherBusy}
        maxWidthClass="max-w-2xl"
        footer={(
          <div className="flex justify-end">
            <Button variant="ghost" onClick={closeAvatarModal} disabled={teacherBusy}>
              <span>{t('common.close')}</span>
            </Button>
          </div>
        )}
      >
        <div className="space-y-4">
          <AvatarSetupChecklist items={avatarChecklist} />
          <AvatarConsentControls
            settings={avatarSettings}
            onConsentChange={handleAvatarConsentChange}
            onEnabledChange={handleAvatarEnabledChange}
            disabled={teacherBusy}
          />

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm text-[var(--text-secondary)]">
              {t('avatar.motionPreset')}
              <select
                value={avatarSettings.avatar_motion_preset}
                onChange={(event) => setAvatarSettings((prev) => ({ ...prev, avatar_motion_preset: event.target.value }))}
                className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 text-sm text-[var(--text-primary)]"
              >
                <option value="natural">{t('avatar.natural')}</option>
                <option value="expressive">{t('avatar.expressive')}</option>
                <option value="calm">{t('avatar.calm')}</option>
              </select>
            </label>

            <label className="block text-sm text-[var(--text-secondary)]">
              {t('avatar.qualityPreset')}
              <select
                value={avatarSettings.avatar_quality_preset}
                onChange={(event) => setAvatarSettings((prev) => ({ ...prev, avatar_quality_preset: event.target.value }))}
                className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 text-sm text-[var(--text-primary)]"
              >
                <option value="high">{t('avatar.high')}</option>
                <option value="balanced">{t('avatar.balanced')}</option>
                <option value="fast">{t('avatar.fast')}</option>
              </select>
            </label>
          </div>

          <label className="inline-flex items-center gap-2 rounded-xl bg-[var(--surface-container-high)] px-3 py-2 text-sm text-[var(--text-secondary)]">
            <input
              type="checkbox"
              checked={avatarSettings.composite_fallback_allowed}
              onChange={(event) => {
                setAvatarSettings((prev) => ({
                  ...prev,
                  composite_fallback_allowed: event.target.checked,
                }));
              }}
            />
            <span>{t('avatar.compositeFallback')}</span>
          </label>

          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={handleSaveTeacherDefaults} disabled={teacherBusy}>
              <Save size={15} />
              <span>{t('avatar.saveSettings')}</span>
            </Button>
            {avatarSetupStatus.state !== 'ready' && (
              <Button onClick={handlePrepareAvatar} disabled={teacherBusy || !avatarSetupStatus.can_prepare}>
                <Sparkles size={15} />
                <span>{prepareAvatarButtonLabel}</span>
              </Button>
            )}
            <Button onClick={handleGeneratePreview} disabled={teacherBusy || !avatarSetupStatus.can_generate_preview}>
              <Sparkles size={15} />
              <span>{t('avatar.generatePreview')}</span>
            </Button>
            <Button variant="ghost" onClick={handleDeletePreview} disabled={teacherBusy}>
              <Trash2 size={15} />
              <span>{t('avatar.deletePreview')}</span>
            </Button>
          </div>

          <div className="rounded-xl bg-[var(--surface-container-high)] px-3 py-2 text-sm text-[var(--text-secondary)]">
            <p>
              {t('avatar.previewStatus')} <span className="font-medium text-[var(--text-primary)]">{previewStatusLabel}</span>
            </p>
            <p className="mt-1 text-xs">{avatarSetupStatus.message}</p>
          </div>

          {previewVideoUrl && (
            <video
              src={previewVideoUrl}
              controls
              className="max-h-[22rem] w-full rounded-2xl token-surface object-contain"
            />
          )}
        </div>
      </ModalShell>
    </div>
  );
}
