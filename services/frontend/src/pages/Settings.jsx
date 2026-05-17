import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Bell,
  ChevronDown,
  CircleHelp,
  MonitorPlay,
  MoonStar,
  Save,
  Sparkles,
  Sun,
  Trash2,
  Upload,
  UserCircle2,
} from 'lucide-react';
import Button from '../components/ui/Button';
import SurfaceCard from '../components/ui/SurfaceCard';
import { useTheme } from '../components/ui/ThemeProvider';
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
  uploadVoiceSample,
} from '../api';
import { canAccessStudio } from '../lib/auth';

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
  avatar_enabled: true,
  avatar_consent_confirmed: true,
  avatar_motion_preset: 'natural',
  avatar_lipsync_engine: 'liveportrait+musetalk',
  avatar_quality_preset: 'high',
  avatar_overlay_visible: true,
  avatar_overlay_default_position: 'top-right',
  avatar_overlay_size: 'medium',
  composite_fallback_allowed: false,
};

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
  return {
    first_name: String(user?.first_name || ''),
    last_name: String(user?.last_name || ''),
    bio: String(user?.profile?.bio || ''),
  };
}

function profileDraftFromPayload(payload) {
  return {
    first_name: String(payload?.first_name || ''),
    last_name: String(payload?.last_name || ''),
    bio: String(payload?.bio || ''),
  };
}

function displayNameFromDraft(draft, user) {
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

function CollapsibleSection({ title, caption, defaultOpen = false, children }) {
  return (
    <details open={defaultOpen} className="group rounded-2xl token-surface px-4 py-3">
      <summary className="focus-ring flex cursor-pointer list-none items-center justify-between gap-3 rounded-xl text-left text-sm font-semibold text-[var(--text-primary)]">
        <span>
          <span>{title}</span>
          {caption && <span className="mt-1 block text-xs font-normal text-[var(--text-secondary)]">{caption}</span>}
        </span>
        <ChevronDown size={15} className="shrink-0 text-[var(--text-secondary)] transition group-open:rotate-180" />
      </summary>
      <div className="mt-3 space-y-3">{children}</div>
    </details>
  );
}

function SettingsSection({
  eyebrow,
  title,
  caption,
  icon: Icon,
  defaultOpen = false,
  className = '',
  contentClassName = 'space-y-4',
  children,
}) {
  const [open, setOpen] = useState(defaultOpen);

  useEffect(() => {
    setOpen(defaultOpen);
  }, [defaultOpen]);

  return (
    <SurfaceCard className={`space-y-4 ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
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
  const { resolvedTheme, setMode } = useTheme();
  const teacherMode = canAccessStudio(user);
  const [reducedMotion, setReducedMotion] = useState(
    () => window.localStorage.getItem(REDUCED_MOTION_KEY) === 'true',
  );
  const [profileDraft, setProfileDraft] = useState(() => profileDraftFromUser(user));
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileMessage, setProfileMessage] = useState('');
  const [profileError, setProfileError] = useState('');
  const [voiceFile, setVoiceFile] = useState(null);
  const [voicePreviewUrl, setVoicePreviewUrl] = useState('');
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

  useEffect(() => {
    window.localStorage.setItem(REDUCED_MOTION_KEY, String(reducedMotion));
    document.documentElement.classList.toggle('reduced-motion', reducedMotion);
  }, [reducedMotion]);

  useEffect(() => {
    window.localStorage.setItem(NOTIFICATION_PREFS_KEY, JSON.stringify(notificationPreferences));
  }, [notificationPreferences]);

  useEffect(() => {
    setProfileDraft(profileDraftFromUser(user));
    setProfileMessage('');
    setProfileError('');

    if (!user?.id) return undefined;
    let active = true;
    fetchMyProfile()
      .then((payload) => {
        if (active) setProfileDraft(profileDraftFromPayload(payload));
      })
      .catch(() => {
        if (active) setProfileError('Unable to refresh public profile details.');
      });
    return () => {
      active = false;
    };
  }, [user?.id]);

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

  const toneDescription = useMemo(() => {
    return resolvedTheme === 'dark'
      ? 'Dark theme active.'
      : 'Light theme active.';
  }, [resolvedTheme]);

  const activeTheme = resolvedTheme === 'dark' ? 'dark' : 'light';
  const publicDisplayName = useMemo(
    () => displayNameFromDraft(profileDraft, user),
    [profileDraft, user],
  );

  const loadAvatarProfile = useCallback(async () => {
    if (!teacherMode || !user?.id) return;

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
  }, [teacherMode, user?.id]);

  useEffect(() => {
    loadAvatarProfile();
  }, [loadAvatarProfile]);

  useEffect(() => {
    if (!previewJobId || !user?.id) return undefined;

    let active = true;
    const interval = window.setInterval(async () => {
      try {
        const statusPayload = await fetchAvatarPreviewStatus(user.id, previewJobId);
        if (!active) return;

        const nextStatus = String(
          statusPayload.status || statusPayload.preview_status || statusPayload.job_status || 'processing',
        ).toLowerCase();
        setPreviewStatusLabel(nextStatus);

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
  }, [loadAvatarProfile, previewJobId, user?.id]);

  const clearLocalNotes = () => {
    const noteKeys = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const key = window.localStorage.key(i);
      if (key?.startsWith('visus-notes-')) {
        noteKeys.push(key);
      }
    }
    noteKeys.forEach((key) => window.localStorage.removeItem(key));
    setLocalDataMessage(noteKeys.length ? 'Local notes cleared for this browser.' : 'No local notes were stored in this browser.');
  };

  const runTeacherAction = async (action, successMessage) => {
    setTeacherBusy(true);
    setTeacherMessage('');
    try {
      await action();
      setTeacherMessage(successMessage);
    } catch (error) {
      setTeacherMessage(error.message || 'Action failed.');
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
    setProfileDraft((previous) => ({ ...previous, [field]: value }));
    setProfileMessage('');
    setProfileError('');
  };

  const updateNotificationPreference = (field, checked) => {
    setNotificationPreferences((previous) => ({ ...previous, [field]: checked }));
  };

  const handleSavePublicProfile = async (event) => {
    event.preventDefault();
    if (!user?.id) return;

    setProfileSaving(true);
    setProfileMessage('');
    setProfileError('');
    try {
      const payload = await updateMyProfile(profileDraft);
      setProfileDraft(profileDraftFromPayload(payload));
      await refreshSessionUser();
      setProfileMessage('Public profile saved.');
    } catch (error) {
      setProfileError(error.message || 'Unable to save public profile.');
    } finally {
      setProfileSaving(false);
    }
  };

  const handleUploadVoice = async () => {
    if (!user?.id || !voiceFile) return;
    await runTeacherAction(async () => {
      await uploadVoiceSample(user.id, voiceFile);
      await loadAvatarProfile();
    }, 'Voice sample uploaded.');
  };

  const handleUploadVisualSample = async () => {
    if (!user?.id || (!imageFile && !videoFile)) return;

    await runTeacherAction(async () => {
      if (videoFile) {
        await uploadAvatarVideo(user.id, videoFile, avatarSettings);
      } else if (imageFile) {
        await uploadAvatarImage(user.id, imageFile, avatarSettings);
      }
      await loadAvatarProfile();
      await refreshSessionUser();
    }, 'Avatar source sample uploaded.');
  };

  const handleSaveTeacherDefaults = async () => {
    if (!user?.id) return;

    await runTeacherAction(async () => {
      await updateAvatarProfile(user.id, avatarSettings);
      await loadAvatarProfile();
      await refreshSessionUser();
    }, 'Teacher avatar defaults saved.');
  };

  const handlePrepareAvatar = async () => {
    if (!user?.id) return;

    await runTeacherAction(async () => {
      await prepareAvatarProfile(user.id, {
        avatar_enabled: avatarSettings.avatar_enabled,
        avatar_consent_confirmed: avatarSettings.avatar_consent_confirmed,
        composite_fallback_allowed: avatarSettings.composite_fallback_allowed,
      });
      await loadAvatarProfile();
    }, 'Avatar prep completed.');
  };

  const handleGeneratePreview = async () => {
    if (!user?.id) return;

    await runTeacherAction(async () => {
      const queued = await regenerateAvatarPreview(user.id);
      setPreviewJobId(String(queued?.job_id || ''));
      setPreviewStatusLabel('queued');
    }, 'Avatar preview queued.');
  };

  const handleDeletePreview = async () => {
    if (!user?.id) return;

    await runTeacherAction(async () => {
      await deleteAvatarPreview(user.id);
      setPreviewVideoUrl('');
      setPreviewStatusLabel('deleted');
      await loadAvatarProfile();
    }, 'Avatar preview removed.');
  };

  return (
    <div className="space-y-5">
      <section className="layout-grid-12">
        <SurfaceCard elevated className="lg:col-span-8">
          <p className="label-sm">Settings</p>
          <h1 className="display-lg mt-2 text-[var(--text-primary)]">Workspace preferences</h1>
          <p className="body-md mt-3 max-w-2xl">
            Manage account details, public profile text, playback accessibility, and publisher avatar preferences.
          </p>
        </SurfaceCard>

        <SurfaceCard className="lg:col-span-4">
          <p className="label-sm">Current Theme</p>
          <div className="mt-3 inline-flex items-center gap-2 rounded-full bg-[var(--surface-container-high)] px-3 py-1.5 text-sm text-[var(--text-primary)]">
            {resolvedTheme === 'dark' ? <MoonStar size={14} /> : <Sun size={14} />}
            <span>{toneDescription}</span>
          </div>
        </SurfaceCard>
      </section>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">
        <SettingsSection
          eyebrow="Account/Profile"
          title="Theme mode"
          caption="Theme choice is stored locally and applied across the workspace."
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
                  <span>{option.title}</span>
                </button>
              );
            })}
          </div>

          <div className="space-y-2 text-sm text-[var(--text-secondary)]">
            {THEME_OPTIONS.map((option) => (
              <p key={option.id}>
                <span className="font-semibold text-[var(--text-primary)]">{option.title}:</span> {option.caption}
              </p>
            ))}
          </div>
        </SettingsSection>

        <SettingsSection
          eyebrow="Publisher/Public Profile"
          title="Display name and bio"
          caption="This updates only your existing first name, last name, and bio fields."
          icon={UserCircle2}
          className="md:col-span-2 2xl:col-span-1"
        >
          <form onSubmit={handleSavePublicProfile} className="space-y-4">
            <fieldset disabled={!user || profileSaving} className="space-y-3 disabled:opacity-60">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="block text-sm text-[var(--text-secondary)]">
                  First name
                  <input
                    type="text"
                    value={profileDraft.first_name}
                    onChange={(event) => updateProfileDraftField('first_name', event.target.value)}
                    className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 text-sm text-[var(--text-primary)]"
                  />
                </label>

                <label className="block text-sm text-[var(--text-secondary)]">
                  Last name
                  <input
                    type="text"
                    value={profileDraft.last_name}
                    onChange={(event) => updateProfileDraftField('last_name', event.target.value)}
                    className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 text-sm text-[var(--text-primary)]"
                  />
                </label>
              </div>

              <label className="block text-sm text-[var(--text-secondary)]">
                Bio
                <textarea
                  value={profileDraft.bio}
                  onChange={(event) => updateProfileDraftField('bio', event.target.value)}
                  rows={5}
                  className="focus-ring mt-1 w-full resize-y rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2 text-sm text-[var(--text-primary)]"
                />
              </label>
            </fieldset>

            <div className="rounded-xl bg-[var(--surface-container-high)] px-3 py-2 text-sm text-[var(--text-secondary)]">
              <p>
                Preview name: <span className="font-semibold text-[var(--text-primary)]">{publicDisplayName}</span>
              </p>
              <p className="mt-1 text-xs">
                Banner, logo, social links, contact fields, and public visibility controls are planned separately.
              </p>
            </div>

            {!user && (
              <p className="text-sm text-[var(--text-secondary)]">Sign in to edit your public profile.</p>
            )}
            {profileMessage && (
              <p className="rounded-xl bg-[var(--status-success-bg)] px-3 py-2 text-sm text-[var(--status-success-fg)]">{profileMessage}</p>
            )}
            {profileError && (
              <p className="rounded-xl bg-[var(--status-danger-bg)] px-3 py-2 text-sm text-[var(--status-danger-fg)]">{profileError}</p>
            )}

            <Button type="submit" disabled={!user || profileSaving}>
              <Save size={15} />
              <span>{profileSaving ? 'Saving...' : 'Save Profile'}</span>
            </Button>
          </form>
        </SettingsSection>

        <SettingsSection
          eyebrow="Playback/Accessibility"
          title="Reduce UI Motion"
          caption="Reduces interface animations and UI motion. Does not affect generated avatar videos."
          icon={MonitorPlay}
        >
          <label className="inline-flex items-center gap-2 rounded-xl bg-[var(--surface-container-high)] px-3 py-2 text-sm text-[var(--text-secondary)]">
            <input
              type="checkbox"
              checked={reducedMotion}
              onChange={(event) => setReducedMotion(event.target.checked)}
            />
            <span>Reduce UI Motion</span>
          </label>
        </SettingsSection>

        <SettingsSection
          eyebrow="In-App Notifications"
          title="Notification preferences"
          caption="Stored in this browser for the notification center."
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
                <span className="block font-semibold text-[var(--text-primary)]">Comments on my lessons</span>
                <span className="mt-1 block text-xs">Notify me about comments on lessons I publish.</span>
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
                <span className="block font-semibold text-[var(--text-primary)]">Render status changes</span>
                <span className="mt-1 block text-xs">Notify me when lesson or avatar renders finish or fail.</span>
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
                <span className="block font-semibold text-[var(--text-primary)]">Followed publisher lessons</span>
                <span className="mt-1 block text-xs">Notify me when publishers I follow post public lessons.</span>
              </span>
            </label>
          </div>
        </SettingsSection>

        <SettingsSection
          eyebrow="Browser Data"
          title="Local notes"
          caption="This removes saved watch notes from this browser only."
          icon={Trash2}
        >
          <Button variant="secondary" onClick={clearLocalNotes}>
            <Trash2 size={15} />
            <span>Clear Local Notes</span>
          </Button>

          {localDataMessage && (
            <p className="text-sm text-[var(--text-secondary)]">{localDataMessage}</p>
          )}
        </SettingsSection>

        <SettingsSection
          eyebrow="Help"
          title="Support content"
          caption="Open support guidance and contact details."
          icon={CircleHelp}
        >
          <Link
            to="/help"
            className="focus-ring inline-flex h-11 items-center justify-center gap-2 rounded-full bg-[var(--surface-container-highest)] px-5 text-sm font-medium text-[var(--text-primary)] transition hover:bg-[color:var(--hover-surface-strong)]"
          >
            <CircleHelp size={15} />
            <span>Open Help</span>
          </Link>
        </SettingsSection>

        {teacherMode && (
          <SettingsSection
            eyebrow="Avatar Preferences"
            title="Voice and avatar samples"
            caption="Advanced avatar controls are collapsed by default and remain separate from UI motion preferences."
            icon={Sparkles}
            className="md:col-span-2 2xl:col-span-3"
            contentClassName="space-y-4"
          >
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
              <CollapsibleSection
                title="Voice Sample"
                caption="Upload one audio sample and preview it locally before saving."
              >
                <label className="block text-sm text-[var(--text-secondary)]">
                  Voice audio
                  <input
                    type="file"
                    accept="audio/*"
                    onChange={(event) => setVoiceFile(event.target.files?.[0] || null)}
                    className="focus-ring mt-1 block w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] p-2 text-sm text-[var(--text-primary)]"
                  />
                </label>

                {voicePreviewUrl && (
                  <audio controls src={voicePreviewUrl} className="w-full" />
                )}

                <Button onClick={handleUploadVoice} disabled={teacherBusy || !voiceFile}>
                  <Upload size={15} />
                  <span>{teacherBusy ? 'Uploading...' : 'Upload Voice Sample'}</span>
                </Button>
              </CollapsibleSection>

              <CollapsibleSection
                title="Picture Or Video Sample"
                caption="Upload an image or short video source and preview before submit."
              >
                <div className="grid gap-3">
                  <label className="block text-sm text-[var(--text-secondary)]">
                    Portrait image
                    <input
                      type="file"
                      accept="image/*"
                      onChange={(event) => setImageFile(event.target.files?.[0] || null)}
                      className="focus-ring mt-1 block w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] p-2 text-sm text-[var(--text-primary)]"
                    />
                  </label>

                  <label className="block text-sm text-[var(--text-secondary)]">
                    Portrait video
                    <input
                      type="file"
                      accept="video/*"
                      onChange={(event) => setVideoFile(event.target.files?.[0] || null)}
                      className="focus-ring mt-1 block w-full cursor-pointer rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] p-2 text-sm text-[var(--text-primary)]"
                    />
                  </label>
                </div>

                {mediaPreviewUrl && mediaPreviewType === 'image' && (
                  <img
                    src={mediaPreviewUrl}
                    alt="Uploaded avatar sample"
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

                <Button onClick={handleUploadVisualSample} disabled={teacherBusy || (!imageFile && !videoFile)}>
                  <Upload size={15} />
                  <span>{teacherBusy ? 'Uploading...' : 'Upload Visual Sample'}</span>
                </Button>
              </CollapsibleSection>

              <CollapsibleSection
                title="Avatar Preview"
                caption="Prepare profile, queue preview, and monitor render state."
              >
                <div className="grid gap-3">
                  <label className="block text-sm text-[var(--text-secondary)]">
                    Motion preset
                    <select
                      value={avatarSettings.avatar_motion_preset}
                      onChange={(event) => setAvatarSettings((prev) => ({ ...prev, avatar_motion_preset: event.target.value }))}
                      className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 text-sm text-[var(--text-primary)]"
                    >
                      <option value="natural">Natural</option>
                      <option value="expressive">Expressive</option>
                      <option value="calm">Calm</option>
                    </select>
                  </label>

                  <label className="block text-sm text-[var(--text-secondary)]">
                    Quality preset
                    <select
                      value={avatarSettings.avatar_quality_preset}
                      onChange={(event) => setAvatarSettings((prev) => ({ ...prev, avatar_quality_preset: event.target.value }))}
                      className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 text-sm text-[var(--text-primary)]"
                    >
                      <option value="high">High</option>
                      <option value="balanced">Balanced</option>
                      <option value="fast">Fast</option>
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
                  <span>Allow composite fallback for preview preparation</span>
                </label>

                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" onClick={handleSaveTeacherDefaults} disabled={teacherBusy}>
                    <Save size={15} />
                    <span>Save Defaults</span>
                  </Button>
                  <Button variant="secondary" onClick={handlePrepareAvatar} disabled={teacherBusy}>
                    <Sparkles size={15} />
                    <span>Prepare Avatar</span>
                  </Button>
                  <Button onClick={handleGeneratePreview} disabled={teacherBusy}>
                    <Sparkles size={15} />
                    <span>Generate Preview</span>
                  </Button>
                  <Button variant="ghost" onClick={handleDeletePreview} disabled={teacherBusy}>
                    <Trash2 size={15} />
                    <span>Delete Preview</span>
                  </Button>
                </div>

                <div className="rounded-xl bg-[var(--surface-container-high)] px-3 py-2 text-sm text-[var(--text-secondary)]">
                  <p>
                    Preview status: <span className="font-medium text-[var(--text-primary)]">{previewStatusLabel}</span>
                  </p>
                  {avatarProfilePayload?.readiness?.missing_requirements?.length > 0 && (
                    <p className="mt-1 text-xs">
                      Missing requirements: {avatarProfilePayload.readiness.missing_requirements.join(', ')}
                    </p>
                  )}
                </div>

                {previewVideoUrl && (
                  <video
                    src={previewVideoUrl}
                    controls
                    className="max-h-[22rem] w-full rounded-2xl token-surface object-contain"
                  />
                )}
              </CollapsibleSection>
            </div>

            {teacherMessage && (
              <p className="rounded-xl bg-[color:color-mix(in_srgb,var(--surface-muted),transparent_6%)] px-3 py-2 text-sm text-[var(--text-secondary)]">
                {teacherMessage}
              </p>
            )}
          </SettingsSection>
        )}
      </section>
    </div>
  );
}
