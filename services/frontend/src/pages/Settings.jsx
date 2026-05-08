import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ChevronDown,
  CircleHelp,
  MoonStar,
  Save,
  Sparkles,
  Sun,
  Trash2,
  Upload,
} from 'lucide-react';
import Button from '../components/ui/Button';
import SurfaceCard from '../components/ui/SurfaceCard';
import { useTheme } from '../components/ui/ThemeProvider';
import {
  API_BASE_URL,
  deleteAvatarPreview,
  fetchAvatarPreviewStatus,
  fetchAvatarProfile,
  prepareAvatarProfile,
  regenerateAvatarPreview,
  updateAvatarProfile,
  uploadAvatarImage,
  uploadAvatarVideo,
  uploadVoiceSample,
} from '../api';
import { canAccessStudio } from '../lib/auth';

const REDUCED_MOTION_KEY = 'visus-reduced-motion';
const API_ORIGIN = API_BASE_URL.replace(/\/api\/v1\/?$/, '');

const THEME_OPTIONS = [
  {
    id: 'light',
    title: 'Light',
    caption: 'Editorial day mode with bright surfaces.',
    icon: Sun,
  },
  {
    id: 'dark',
    title: 'Dark',
    caption: 'Cinematic night mode for focus sessions.',
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

export default function Settings({ user, onUserRefresh }) {
  const { resolvedTheme, setMode } = useTheme();
  const teacherMode = canAccessStudio(user);
  const [reducedMotion, setReducedMotion] = useState(
    () => window.localStorage.getItem(REDUCED_MOTION_KEY) === 'true',
  );
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

  useEffect(() => {
    window.localStorage.setItem(REDUCED_MOTION_KEY, String(reducedMotion));
    document.documentElement.classList.toggle('reduced-motion', reducedMotion);
  }, [reducedMotion]);

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
      ? 'Dark theme active: low-glare cinematic surfaces.'
      : 'Light theme active: editorial clarity and high readability.';
  }, [resolvedTheme]);

  const activeTheme = resolvedTheme === 'dark' ? 'dark' : 'light';

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
    <div className="space-y-6">
      <section className="layout-grid-12">
        <SurfaceCard elevated className="lg:col-span-8">
          <p className="label-sm">Settings</p>
          <h1 className="display-lg mt-2 text-[var(--text-primary)]">Personalize VidLab</h1>
          <p className="body-md mt-3 max-w-2xl">
            Control theme behavior, motion preference, and local study data.
          </p>
        </SurfaceCard>

        <SurfaceCard className="lg:col-span-4">
          <p className="label-sm">Current Theme</p>
          <div className="mt-3 inline-flex items-center gap-2 rounded-full token-surface px-3 py-1.5 text-sm text-[var(--text-primary)]">
            {resolvedTheme === 'dark' ? <MoonStar size={14} /> : <Sun size={14} />}
            <span>{toneDescription}</span>
          </div>
        </SurfaceCard>
      </section>

      <SurfaceCard className="space-y-4">
        <div>
          <p className="label-sm">Theme Controls</p>
          <h2 className="headline-md mt-1 text-[var(--text-primary)]">Theme Mode</h2>
        </div>

        <div className="inline-flex rounded-full token-surface p-1">
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

        <p className="text-sm text-[var(--text-secondary)]">
          Theme choice is persisted to local storage and applied through the global theme provider.
        </p>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {THEME_OPTIONS.map((option) => (
            <div key={option.id} className="rounded-2xl token-surface px-4 py-3">
              <p className="title-lg text-[var(--text-primary)]">{option.title}</p>
              <p className="mt-1 text-sm text-[var(--text-secondary)]">{option.caption}</p>
            </div>
          ))}
        </div>
      </SurfaceCard>

      <SurfaceCard className="space-y-3">
        <div>
          <p className="label-sm">Accessibility</p>
          <h2 className="headline-md mt-1 text-[var(--text-primary)]">Motion Preferences</h2>
        </div>

        <label className="inline-flex items-center gap-2 rounded-2xl token-surface px-3 py-2 text-sm text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={reducedMotion}
            onChange={(event) => setReducedMotion(event.target.checked)}
          />
          <span>Reduce motion and transitions</span>
        </label>
      </SurfaceCard>

      <SurfaceCard className="space-y-3">
        <div>
          <p className="label-sm">Data</p>
          <h2 className="headline-md mt-1 text-[var(--text-primary)]">Local Storage</h2>
          <p className="body-md mt-2">
            This removes saved watch notes from this browser only.
          </p>
        </div>

        <Button variant="secondary" onClick={clearLocalNotes}>
          <Trash2 size={15} />
          <span>Clear Local Notes</span>
        </Button>
      </SurfaceCard>

      <SurfaceCard>
        <p className="label-sm">Session</p>
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          {user ? `Signed in as ${user.username}.` : 'Browsing as guest.'}
        </p>
      </SurfaceCard>

      <SurfaceCard id="help" className="space-y-2">
        <div className="inline-flex items-center gap-2">
          <CircleHelp size={15} className="text-[var(--text-secondary)]" />
          <p className="label-sm">Help</p>
        </div>
        <p className="title-lg text-[var(--text-primary)]">Need Assistance?</p>
        <p className="body-md">
          Use Studio as a teacher account for publishing access, and use Watch for transcript-first study with local note drafts.
        </p>
      </SurfaceCard>

      {teacherMode && (
        <SurfaceCard className="space-y-4">
          <div>
            <p className="label-sm">Teacher Setup</p>
            <h2 className="headline-md mt-1 text-[var(--text-primary)]">Avatar And Voice Samples</h2>
            <p className="body-md mt-2">
              Upload voice and visual references, then regenerate preview clips from one minimal settings workspace.
            </p>
          </div>

          <CollapsibleSection
            title="Voice Sample"
            caption="Upload one audio sample and preview it locally before saving."
            defaultOpen
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

            <div className="flex flex-wrap gap-2">
              <Button onClick={handleUploadVoice} disabled={teacherBusy || !voiceFile}>
                <Upload size={15} />
                <span>{teacherBusy ? 'Uploading...' : 'Upload Voice Sample'}</span>
              </Button>
            </div>
          </CollapsibleSection>

          <CollapsibleSection
            title="Picture Or Video Sample"
            caption="Upload an image or short video source and preview before submit."
          >
            <div className="grid gap-3 md:grid-cols-2">
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

            <div className="flex flex-wrap gap-2">
              <Button onClick={handleUploadVisualSample} disabled={teacherBusy || (!imageFile && !videoFile)}>
                <Upload size={15} />
                <span>{teacherBusy ? 'Uploading...' : 'Upload Visual Sample'}</span>
              </Button>
            </div>
          </CollapsibleSection>

          <CollapsibleSection
            title="Avatar Preview"
            caption="Prepare profile, queue preview, and monitor render state."
          >
            <div className="grid gap-3 md:grid-cols-2">
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

            <label className="inline-flex items-center gap-2 rounded-xl token-surface px-3 py-2 text-sm text-[var(--text-secondary)]">
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

            <div className="rounded-xl token-surface px-3 py-2 text-sm text-[var(--text-secondary)]">
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

          {teacherMessage && (
            <p className="rounded-xl bg-[color:color-mix(in_srgb,var(--surface-muted),transparent_6%)] px-3 py-2 text-sm text-[var(--text-secondary)]">
              {teacherMessage}
            </p>
          )}
        </SurfaceCard>
      )}
    </div>
  );
}
