import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AUTOPLAY_NEXT_KEY } from '../utils/playbackPreferences';

const apiMocks = vi.hoisted(() => ({
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
  addComment: apiMocks.addComment,
  fetchCatalog: apiMocks.fetchCatalog,
  fetchComments: apiMocks.fetchComments,
  fetchLesson: apiMocks.fetchLesson,
  fetchPlaybackToken: apiMocks.fetchPlaybackToken,
  fetchProjectTranscript: apiMocks.fetchProjectTranscript,
  fetchSubtitleTrackBundle: apiMocks.fetchSubtitleTrackBundle,
  fetchSubtitleTracks: apiMocks.fetchSubtitleTracks,
  generateSubtitleTrack: apiMocks.generateSubtitleTrack,
  getPlaylistContext: apiMocks.getPlaylistContext,
  saveProgress: apiMocks.saveProgress,
  toggleFollowPublisher: apiMocks.toggleFollowPublisher,
  toggleLike: apiMocks.toggleLike,
}));

vi.mock('../components/player/VideoStage', () => ({
  default: (props) => (
    <div data-testid="watch-video-stage">
      <button type="button" data-testid="mock-video-ended" onClick={() => props.onPlaybackEnded?.()}>
        End playback
      </button>
      <div data-testid="player-fullscreen-shell">
        {props.continueNextPrompt?.lesson ? (
          <div data-testid="watch-autoplay-next">
            <span>Next: {props.continueNextPrompt.lesson.title}</span>
            <span>Continuing in {props.continueNextPrompt.secondsRemaining} seconds.</span>
            <button type="button" onClick={() => props.onContinueNext?.()}>Continue now</button>
            <button type="button" onClick={() => props.onCancelContinueNext?.()}>Stay here</button>
          </div>
        ) : null}
      </div>
      <span>Video player</span>
    </div>
  ),
}));

vi.mock('../components/player/HlsPlayer', () => ({
  default: (props) => (
    <div data-testid="watch-hls-stage">
      <button type="button" data-testid="mock-hls-ended" onClick={() => props.onPlaybackEnded?.()}>
        End secure playback
      </button>
      <div data-testid="player-fullscreen-shell">
        {props.continueNextPrompt?.lesson ? (
          <div data-testid="watch-autoplay-next">
            <span>Next: {props.continueNextPrompt.lesson.title}</span>
            <button type="button" onClick={() => props.onContinueNext?.()}>Continue now</button>
            <button type="button" onClick={() => props.onCancelContinueNext?.()}>Stay here</button>
          </div>
        ) : null}
      </div>
      <span>Secure player</span>
    </div>
  ),
}));

vi.mock('../components/moderation/LessonActionButton', () => ({
  default: () => null,
}));

vi.mock('../components/ui/PageLoading', () => ({
  usePageLoading: () => {},
}));

vi.mock('../hooks/usePlaybackHeartbeat', () => ({
  default: () => ({ error: '' }),
}));

vi.mock('../lib/capabilities', async () => {
  const actual = await vi.importActual('../lib/capabilities');
  return {
    ...actual,
    useCapabilities: () => ({
      capabilities: {
        features: {
          avatar: { enabled: false },
          visual_moderation: { enabled: true },
        },
      },
    }),
  };
});

import Watch, { WatchContextPanel } from './Watch';

const user = {
  id: 42,
  username: 'viewer',
  profile: { role: 'student' },
};

function lesson(id, overrides = {}) {
  return {
    id,
    title: `Watch lesson ${id}`,
    description: `Description for lesson ${id}`,
    teacher_name: 'VISUS Publisher',
    teacher_id: 7,
    publisher_id: 7,
    category_name: 'Design',
    duration_minutes: 8,
    stream_url: '/media/watch-test.mp4',
    video_url: '/media/watch-test.mp4',
    protection_mode: 'public',
    is_published: true,
    ...overrides,
  };
}

function publisherContext(count = 6) {
  return {
    mode: 'publisher',
    items: Array.from({ length: count }, (_, index) => ({
      is_current: index === 0,
      project: lesson(index + 1, {
        title: index === 0 ? 'Current watch lesson' : `Publisher lesson ${index + 1}`,
      }),
    })),
  };
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

function renderNode(node) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => {
    root.render(node);
  });
  return { host, root };
}

describe('Watch notes-first layout', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    vi.clearAllMocks();
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it('renders publisher recommendations collapsed by default and toggles them', async () => {
    const { host, root } = renderNode(
      <WatchContextPanel
        context={publisherContext(10)}
        currentLessonId={1}
        onOpenLesson={vi.fn()}
      />,
    );

    expect(host.textContent).toContain('More from this publisher');
    expect(host.textContent).toContain('9 more lessons');
    expect(host.querySelector('[data-testid="watch-context-list"]')).toBeNull();

    const showButton = [...host.querySelectorAll('button')].find((button) => button.textContent.includes('Show'));
    expect(showButton).toBeTruthy();
    await act(async () => {
      showButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const expandedPanel = host.querySelector('[data-testid="watch-context-panel"]');
    const list = host.querySelector('[data-testid="watch-context-list"]');
    expect(expandedPanel.className).toContain('max-h-[28rem]');
    expect(expandedPanel.className).toContain('overflow-hidden');
    expect(list).toBeTruthy();
    expect(list.className).toContain('min-h-0');
    expect(list.className).toContain('flex-1');
    expect(list.className).toContain('overflow-y-auto');
    expect(list.querySelectorAll('button')).toHaveLength(10);

    const hideButton = [...host.querySelectorAll('button')].find((button) => button.textContent.includes('Hide'));
    expect(hideButton).toBeTruthy();
    await act(async () => {
      hideButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(host.querySelector('[data-testid="watch-context-list"]')).toBeNull();

    await act(async () => root.unmount());
    host.remove();
  });

  it('places notes in the desktop side column before publisher recommendations', async () => {
    apiMocks.fetchCatalog.mockResolvedValue([lesson(376), lesson(377)]);
    apiMocks.fetchLesson.mockResolvedValue(lesson(376, { title: 'Current watch lesson' }));
    apiMocks.fetchPlaybackToken.mockResolvedValue({ video_url: '/media/watch-test.mp4', protection_mode: 'public' });
    apiMocks.fetchProjectTranscript.mockResolvedValue({ pages: [] });
    apiMocks.fetchSubtitleTrackBundle.mockResolvedValue({ tracks: [], requestableLanguages: [] });
    apiMocks.fetchComments.mockResolvedValue([]);
    apiMocks.getPlaylistContext.mockResolvedValue(publisherContext(5));

    const { host, root } = renderNode(
      <MemoryRouter initialEntries={['/watch?lesson=376']}>
        <Watch user={user} />
      </MemoryRouter>,
    );

    await flush();
    await flush();
    await flush();

    const layout = host.querySelector('[data-testid="watch-learning-layout"]');
    const videoColumn = host.querySelector('[data-testid="watch-video-column"]');
    const notesColumn = host.querySelector('[data-testid="watch-notes-column"]');
    const contextPanel = host.querySelector('[data-testid="watch-context-panel"]');

    expect(layout.className).toContain('layout-grid-12');
    expect(videoColumn.className).toContain('lg:col-span-8');
    expect(notesColumn.className).toContain('lg:col-span-4');
    expect(host.textContent).toContain('Personal Notebook');
    expect(contextPanel.textContent).toContain('More from this publisher');
    expect(contextPanel.querySelector('[data-testid="watch-context-list"]')).toBeNull();

    const sideChildren = [...notesColumn.children];
    expect(sideChildren[0].textContent).toContain('Personal Notebook');
    expect(sideChildren[1].textContent).toContain('More from this publisher');

    await act(async () => root.unmount());
    host.remove();
  });

  it('shows the continue-next prompt inside the player shell when playback ends', async () => {
    apiMocks.fetchCatalog.mockResolvedValue([lesson(376), lesson(377, { title: 'Next lesson in line' })]);
    apiMocks.fetchLesson.mockResolvedValue(lesson(376, { title: 'Current watch lesson' }));
    apiMocks.fetchPlaybackToken.mockResolvedValue({ video_url: '/media/watch-test.mp4', protection_mode: 'public' });
    apiMocks.fetchProjectTranscript.mockResolvedValue({ pages: [] });
    apiMocks.fetchSubtitleTrackBundle.mockResolvedValue({ tracks: [], requestableLanguages: [] });
    apiMocks.fetchComments.mockResolvedValue([]);
    apiMocks.getPlaylistContext.mockResolvedValue(publisherContext(2));

    const { host, root } = renderNode(
      <MemoryRouter initialEntries={['/watch?lesson=376']}>
        <Watch user={user} />
      </MemoryRouter>,
    );

    await flush();
    await flush();
    await flush();

    const endButton = host.querySelector('[data-testid="mock-video-ended"]');
    expect(endButton).toBeTruthy();
    await act(async () => {
      endButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const shell = host.querySelector('[data-testid="player-fullscreen-shell"]');
    const prompt = host.querySelector('[data-testid="watch-autoplay-next"]');
    expect(prompt).toBeTruthy();
    expect(shell.contains(prompt)).toBe(true);
    expect(prompt.textContent).toContain('Next: Publisher lesson 2');
    expect(prompt.textContent).toContain('Continuing in 5 seconds');

    const stayButton = [...prompt.querySelectorAll('button')].find((button) => button.textContent.includes('Stay here'));
    await act(async () => {
      stayButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(host.querySelector('[data-testid="watch-autoplay-next"]')).toBeNull();

    await act(async () => root.unmount());
    host.remove();
  });

  it('does not show the continue-next prompt when the playback setting is off', async () => {
    window.localStorage.setItem(AUTOPLAY_NEXT_KEY, '0');
    apiMocks.fetchCatalog.mockResolvedValue([lesson(376), lesson(377)]);
    apiMocks.fetchLesson.mockResolvedValue(lesson(376, { title: 'Current watch lesson' }));
    apiMocks.fetchPlaybackToken.mockResolvedValue({ video_url: '/media/watch-test.mp4', protection_mode: 'public' });
    apiMocks.fetchProjectTranscript.mockResolvedValue({ pages: [] });
    apiMocks.fetchSubtitleTrackBundle.mockResolvedValue({ tracks: [], requestableLanguages: [] });
    apiMocks.fetchComments.mockResolvedValue([]);
    apiMocks.getPlaylistContext.mockResolvedValue(publisherContext(2));

    const { host, root } = renderNode(
      <MemoryRouter initialEntries={['/watch?lesson=376']}>
        <Watch user={user} />
      </MemoryRouter>,
    );

    await flush();
    await flush();
    await flush();

    const endButton = host.querySelector('[data-testid="mock-video-ended"]');
    expect(endButton).toBeTruthy();
    await act(async () => {
      endButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(host.querySelector('[data-testid="watch-autoplay-next"]')).toBeNull();

    await act(async () => root.unmount());
    host.remove();
  });
});
