import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
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
  toggleFollowPublisher: vi.fn(),
  toggleLike: vi.fn(),
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
  useCapabilities: () => ({ capabilities: { features: {} } }),
}));

vi.mock('../hooks/usePlaybackHeartbeat', () => ({
  default: () => ({ error: '', enabled: false, visibility: 'visible' }),
}));

vi.mock('../components/player/playerMode', () => ({
  PLAYER_MODES: {
    PUBLIC_MP4: 'public_mp4',
    SECURE_HLS: 'secure_hls',
    UNAVAILABLE: 'unavailable',
  },
  resolvePlayerMode: () => ({
    mode: 'public_mp4',
    fallbackUrl: 'https://media.example.test/lesson.mp4',
    manifestUrl: '',
  }),
}));

vi.mock('../components/player/VideoStage', () => ({
  default: ({ lesson }) => <div data-testid="video-stage">{lesson?.title}</div>,
}));

vi.mock('../components/player/AvatarOverlayLayer', () => ({
  default: () => null,
}));

vi.mock('../components/player/UnavailableStage', () => ({
  default: ({ message }) => <div>{message}</div>,
}));

vi.mock('../components/player/ChapterList', () => ({
  default: ({ chapters }) => (
    <div data-testid="chapters">
      {chapters.map((chapter) => <span key={chapter.id}>{chapter.title}</span>)}
    </div>
  ),
}));

vi.mock('../components/player/TranscriptPanel', () => ({
  default: ({ lines }) => (
    <div data-testid="transcript">
      {lines.map((line) => <span key={line.id}>{line.text}</span>)}
    </div>
  ),
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
  default: ({ children, ...props }) => <button {...props}>{children}</button>,
}));

vi.mock('../components/ui/SurfaceCard', () => ({
  default: ({ children, ...props }) => <section {...props}>{children}</section>,
}));

import Watch from './Watch';

const catalogLesson = {
  id: 42,
  title: 'Public lesson',
  description: 'Catalog detail fallback text',
  publisher_id: 7,
  publisher_display_name: 'Other Publisher',
  duration_minutes: 4,
  stream_url: 'https://media.example.test/lesson.mp4',
  protection_mode: 'public_mp4',
  transcript_pages: [
    {
      id: 101,
      narration_text: 'Transcript supplied by the public catalog detail.',
      start_seconds: 0,
      duration_seconds: 8,
    },
  ],
};

async function renderWatch(user = null) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/watch?lesson=42']}>
        <Watch searchQuery="" user={user} onLoginRequest={vi.fn()} />
      </MemoryRouter>,
    );
  });

  await vi.waitFor(() => {
    expect(host.querySelector('[data-testid="transcript"]')).not.toBeNull();
  });

  return { host, root };
}

describe('public Watch transcript data flow', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    vi.clearAllMocks();
    window.localStorage.clear();

    mocks.fetchCatalog.mockResolvedValue([catalogLesson]);
    mocks.fetchLesson.mockResolvedValue(catalogLesson);
    mocks.fetchPlaybackToken.mockResolvedValue(null);
    mocks.fetchSubtitleTrackBundle.mockResolvedValue({ tracks: [], requestableLanguages: [] });
    mocks.fetchComments.mockResolvedValue([
      { id: 5, display_name: 'Viewer', text: 'Public comment' },
    ]);
    mocks.getPlaylistContext.mockResolvedValue(null);
  });

  it('renders transcript and comments from public data without requesting the project transcript', async () => {
    mocks.fetchProjectTranscript.mockRejectedValue(new Error('Forbidden'));

    const { host, root } = await renderWatch();

    expect(host.querySelector('[data-testid="transcript"]')).toHaveTextContent(
      'Transcript supplied by the public catalog detail.',
    );
    expect(host).toHaveTextContent('Public comment');
    expect(host.querySelector('[data-testid="video-stage"]')).toHaveTextContent('Public lesson');
    expect(mocks.fetchProjectTranscript).not.toHaveBeenCalled();

    await act(async () => root.unmount());
    host.remove();
  });

  it('does not use the owner-only transcript endpoint when staff views the public Watch route', async () => {
    const { host, root } = await renderWatch({ id: 1, is_staff: true });

    expect(host.querySelector('[data-testid="transcript"]')).toHaveTextContent(
      'Transcript supplied by the public catalog detail.',
    );
    expect(mocks.fetchProjectTranscript).not.toHaveBeenCalled();

    await act(async () => root.unmount());
    host.remove();
  });
});
