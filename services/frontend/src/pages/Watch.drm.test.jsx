import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  addComment: vi.fn(),
  fetchCatalog: vi.fn(),
  fetchComments: vi.fn(),
  fetchLesson: vi.fn(),
  fetchPlaybackToken: vi.fn(),
  fetchProjectTranscript: vi.fn(),
  fetchSubtitleTrackBundle: vi.fn(),
  fetchSubtitleTracks: vi.fn(),
  generateSubtitleTrack: vi.fn(),
  getPlaylistContext: vi.fn(),
  saveProgress: vi.fn(),
  setSearchParams: vi.fn(),
  navigate: vi.fn(),
  toggleFollowPublisher: vi.fn(),
  toggleLike: vi.fn(),
}));

vi.mock('react-router-dom', () => ({
  Link: ({ to, children, ...props }) => <a href={String(to || '#')} {...props}>{children}</a>,
  useNavigate: () => mocks.navigate,
  useSearchParams: () => [new URLSearchParams('lesson=101'), mocks.setSearchParams],
}));

vi.mock('../api', () => ({
  addComment: mocks.addComment,
  fetchCatalog: mocks.fetchCatalog,
  fetchComments: mocks.fetchComments,
  fetchLesson: mocks.fetchLesson,
  fetchPlaybackToken: mocks.fetchPlaybackToken,
  fetchProjectTranscript: mocks.fetchProjectTranscript,
  fetchSubtitleTrackBundle: mocks.fetchSubtitleTrackBundle,
  fetchSubtitleTracks: mocks.fetchSubtitleTracks,
  generateSubtitleTrack: mocks.generateSubtitleTrack,
  getPlaylistContext: mocks.getPlaylistContext,
  saveProgress: mocks.saveProgress,
  toggleFollowPublisher: mocks.toggleFollowPublisher,
  toggleLike: mocks.toggleLike,
}));

vi.mock('../lib/capabilities', () => ({
  featureEnabled: () => false,
  useCapabilities: () => ({ capabilities: { features: { avatar: false } } }),
}));

vi.mock('../components/player/DrmShakaPlayer', () => ({
  default: ({ manifestUrl, drmSystems }) => (
    <div data-testid="watch-drm-player">
      DRM routed: {manifestUrl} / {drmSystems?.[0]?.keySystem}
    </div>
  ),
}));

vi.mock('../components/player/HlsPlayer', () => ({
  default: () => <div data-testid="watch-hls-player" />,
}));

vi.mock('../components/player/VideoStage', () => ({
  default: () => <div data-testid="watch-mp4-player" />,
}));

vi.mock('../components/player/AvatarOverlayLayer', () => ({
  default: () => null,
}));

vi.mock('../components/player/ChapterList', () => ({
  default: () => null,
}));

vi.mock('../components/player/TranscriptPanel', () => ({
  default: () => null,
}));

vi.mock('../components/player/NotesPanel', () => ({
  default: () => null,
}));

vi.mock('../components/player/RelatedLessonsRow', () => ({
  default: () => null,
}));

vi.mock('../components/moderation/LessonActionButton', () => ({
  default: () => null,
}));

vi.mock('../components/ui/Button', () => ({
  default: ({ children, ...props }) => <button type={props.type || 'button'} {...props}>{children}</button>,
}));

vi.mock('../components/ui/SurfaceCard', () => ({
  default: ({ children, ...props }) => <div {...props}>{children}</div>,
}));

import Watch from './Watch';

const lessonPayload = {
  id: 101,
  title: 'DRM Lesson',
  description: 'A protected playback lesson.',
  status: 'done',
  is_published: true,
  protection_mode: 'drm_protected',
  stream_url: '',
  video_url: '',
  duration_minutes: 4,
  category_name: 'Security',
};

const drmPlaybackPayload = {
  video_url: '',
  protection_mode: 'drm_protected',
  allow_mp4_fallback: false,
  playback_status: { protection_mode: 'drm_protected' },
  protection: { visibility_lock: true },
  streaming: {
    hls: {
      manifest_url: 'https://api.example.test/api/v1/stream/hls-token/',
    },
    fallback: null,
  },
  drm: {
    enabled: true,
    ready: true,
    manifest_url: 'https://api.example.test/api/v1/stream/hls-token/',
    systems: {
      widevine: {
        enabled: true,
        ready: true,
        key_system: 'com.widevine.alpha',
        license_url: 'https://drm.example.test/widevine/license',
        certificate_url: '',
        content_type: 'application/vnd.apple.mpegurl',
      },
    },
  },
};

async function renderWatch() {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);

  await act(async () => {
    root.render(<Watch searchQuery="" user={null} onLoginRequest={vi.fn()} />);
  });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await new Promise((resolve) => {
      window.setTimeout(resolve, 0);
    });
  });
  await act(async () => {
    await Promise.resolve();
  });

  return { host, root };
}

describe('Watch DRM routing', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    vi.clearAllMocks();
    import.meta.env.VITE_PLAYER_ENABLE_DRM_SHAKA = 'true';
    Object.defineProperty(window.navigator, 'requestMediaKeySystemAccess', {
      value: vi.fn(() => Promise.resolve({})),
      configurable: true,
    });
    mocks.fetchCatalog.mockResolvedValue([lessonPayload]);
    mocks.fetchLesson.mockResolvedValue(lessonPayload);
    mocks.fetchPlaybackToken.mockResolvedValue(drmPlaybackPayload);
    mocks.fetchProjectTranscript.mockResolvedValue({ pages: [] });
    mocks.fetchSubtitleTrackBundle.mockResolvedValue({ tracks: [], requestableLanguages: [] });
    mocks.fetchComments.mockResolvedValue([]);
    mocks.getPlaylistContext.mockResolvedValue({ mode: 'publisher', items: [] });
  });

  it('routes valid DRM metadata to the lazy Shaka player when the feature flag is enabled', async () => {
    const { root, host } = await renderWatch();

    expect(host.querySelector('[data-testid="watch-drm-player"]')).not.toBeNull();
    expect(host.textContent).toContain('https://api.example.test/api/v1/stream/hls-token/');
    expect(host.textContent).toContain('com.widevine.alpha');
    expect(host.querySelector('[data-testid="watch-mp4-player"]')).toBeNull();

    await act(async () => root.unmount());
    host.remove();
  });

  it('keeps DRM unavailable when the Shaka feature flag is disabled', async () => {
    import.meta.env.VITE_PLAYER_ENABLE_DRM_SHAKA = 'false';

    const { root, host } = await renderWatch();

    expect(host.querySelector('[data-testid="watch-drm-player"]')).toBeNull();
    expect(host.textContent).toContain('This lesson requires protected playback');
    expect(host.textContent).toContain('Reason: drm shaka disabled');
    expect(host.querySelector('[data-testid="watch-mp4-player"]')).toBeNull();

    await act(async () => root.unmount());
    host.remove();
  });
});
