import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  jsonResponse,
  mockCommonAppChromeApi,
  seedAuthenticatedSession,
} from './support/apiMocks.js';

const AUTH_USER = {
  id: 42,
  username: 'avatar.teacher',
  display_name: 'Avatar Teacher',
  first_name: 'Avatar',
  last_name: 'Teacher',
  role: 'teacher',
  auth_provider: 'password',
  profile: {
    role: 'teacher',
    display_name: 'Avatar Teacher',
    bio: 'Mock teacher profile for settings avatar smoke coverage.',
    is_public_profile: true,
  },
};

const CAPABILITIES_PAYLOAD = {
  features: {
    avatar: { enabled: true, status: 'enabled' },
    google_auth: { enabled: false },
    moderation: { enabled: false },
    tts_preview: { enabled: false },
    visual_moderation: { enabled: false },
  },
};

const PROFILE_PAYLOAD = {
  first_name: 'Avatar',
  last_name: 'Teacher',
  display_name: 'Avatar Teacher',
  bio: 'Mock teacher profile for settings avatar smoke coverage.',
  website_url: '',
  contact_email: 'avatar.teacher@example.test',
  social_links: {},
  is_public_profile: true,
  banner_url: '',
  logo_url: '',
  banner_moderation_status: '',
  banner_moderation_summary: {},
  logo_moderation_status: '',
  logo_moderation_summary: {},
};

const AVATAR_PROFILE_PAYLOAD = {
  profile: {
    avatar_enabled: true,
    avatar_consent_confirmed: true,
    avatar_motion_preset: 'natural',
    avatar_lipsync_engine: 'liveportrait+musetalk',
    avatar_quality_preset: 'high',
    avatar_overlay_visible: true,
    avatar_overlay_default_position: 'top-right',
    avatar_overlay_size: 'medium',
    avatar_image_original: '/media/avatar/originals/teacher-42.png',
  },
  avatar_setup_status: {
    state: 'missing_voice',
    action_required: 'upload_voice_sample',
    primary_action_label: 'Upload voice sample',
    message: 'Upload a voice sample.',
    checklist: {
      portrait_uploaded: true,
      voice_uploaded: false,
      consent_confirmed: true,
      avatar_generation_enabled: true,
      avatar_prepared: false,
    },
    can_prepare: false,
    can_generate_preview: false,
    needs_prepare: false,
    preview_ready: false,
  },
  readiness: {
    ready: false,
    missing_requirements: ['missing_voice'],
    checks: {
      avatar_image_original: true,
      avatar_consent_confirmed: true,
      avatar_enabled: true,
      voice_id_exists: false,
    },
  },
  avatar_summary: {
    last_preview_path: '',
  },
};

test.use({
  permissions: ['microphone'],
  launchOptions: {
    args: [
      '--use-fake-device-for-media-stream',
      '--use-fake-ui-for-media-stream',
    ],
  },
});

async function mockSettingsAvatarApi(page, { allowVoiceUpload = false, onVoiceUpload = null } = {}) {
  await mockCommonAppChromeApi(page, {
    user: AUTH_USER,
    capabilities: CAPABILITIES_PAYLOAD,
    categories: [
      { id: 1, name: 'Avatar QA', slug: 'avatar-qa' },
    ],
    unreadCount: 0,
  });

  await page.route('**/api/v1/me/profile/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse(PROFILE_PAYLOAD));
  });

  await page.route('**/api/v1/users/42/avatar/**', (route) => {
    expect(route.request().method()).toBe('GET');
    return route.fulfill(jsonResponse(AVATAR_PROFILE_PAYLOAD));
  });

  await page.route('**/api/v1/users/42/voice/**', (route) => {
    if (allowVoiceUpload) {
      onVoiceUpload?.(route.request());
      return route.fulfill(jsonResponse({
        status: 'ready',
        voice_id: 'voice_recorded_smoke',
        audio: {
          format: 'wav',
          codec: 'pcm_s16le',
          sample_rate: 24000,
          channels: 1,
          duration_seconds: 11,
        },
      }));
    }
    throw new Error(`Unexpected voice sample request: ${route.request().method()} ${route.request().url()}`);
  });
}

async function setupAuthenticatedSettingsSmoke(page, apiOptions = {}) {
  const expectNoBrowserErrors = collectBrowserErrors(page);

  await mockSettingsAvatarApi(page, apiOptions);
  await seedAuthenticatedSession(page, {
    token: 'settings-avatar-token',
    user: AUTH_USER,
  });

  return expectNoBrowserErrors;
}

test('authenticated Settings renders mocked avatar status and voice modal', async ({ page }) => {
  const expectNoBrowserErrors = await setupAuthenticatedSettingsSmoke(page);

  await page.goto('/settings');

  await expect(page.getByRole('heading', { name: 'Workspace preferences' })).toBeVisible();
  const avatarPreferencesToggle = page.getByRole('button', { name: /Voice and avatar samples/ });
  await expect(avatarPreferencesToggle).toBeVisible();
  await avatarPreferencesToggle.click();

  await expect(page.getByRole('button', { name: /Voice Sample/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Picture Or Video Sample/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Avatar Preview/ })).toBeVisible();

  const checklist = page.getByRole('list').filter({ hasText: 'Portrait uploaded' });
  await expect(checklist.getByText('Portrait uploaded')).toBeVisible();
  await expect(checklist.getByText('Voice uploaded')).toBeVisible();
  await expect(checklist.getByText('Consent confirmed')).toBeVisible();
  await expect(checklist.getByText('Avatar generation enabled')).toBeVisible();
  await expect(checklist.getByText('Avatar prepared')).toBeVisible();
  await expect(page.getByText('Upload a voice sample.')).toBeVisible();

  await page.getByRole('button', { name: /Voice Sample/ }).click();
  await expect(page.getByRole('heading', { name: 'Voice sample' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Upload Voice Sample' })).toBeVisible();

  expectNoBrowserErrors();
});

test.describe('microphone voice sample recording', () => {
  test('records, previews, uses, and uploads a browser microphone sample', async ({ page }) => {
    let voiceUploadSeen = false;
    const expectNoBrowserErrors = await setupAuthenticatedSettingsSmoke(page, {
      allowVoiceUpload: true,
      onVoiceUpload: (request) => {
        expect(request.method()).toBe('POST');
        expect(request.headers()['content-type']).toContain('multipart/form-data');
        expect(request.postData() || '').toContain('voice-sample-recording.webm');
        voiceUploadSeen = true;
      },
    });

    await page.addInitScript(() => {
      navigator.mediaDevices.getUserMedia = async () => {
        const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
        const audioContext = new AudioContextCtor();
        const oscillator = audioContext.createOscillator();
        const destination = audioContext.createMediaStreamDestination();
        oscillator.connect(destination);
        oscillator.start();
        return destination.stream;
      };
    });

    await page.goto('/settings');
    await page.getByRole('button', { name: /Voice and avatar samples/ }).click();
    await page.getByRole('button', { name: /Voice Sample/ }).click();

    await expect(page.getByText('Record from microphone')).toBeVisible();
    await page.getByRole('button', { name: 'Start recording' }).click();
    await expect(page.getByText(/Status: recording/)).toBeVisible();
    await page.waitForTimeout(1200);
    await page.getByRole('button', { name: 'Stop recording' }).click();
    await expect(page.getByText(/Status: recorded/)).toBeVisible();

    await page.getByRole('button', { name: 'Play preview' }).click();
    await page.getByRole('button', { name: 'Use recording' }).click();
    await page.getByRole('button', { name: 'Upload Voice Sample' }).click();

    await expect.poll(() => voiceUploadSeen).toBe(true);
    await expect(page.getByText('Voice sample uploaded.')).toBeVisible();
    expectNoBrowserErrors();
  });
});
