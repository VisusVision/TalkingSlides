import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  fetchSharedLesson: vi.fn(),
}));

vi.mock('../api', () => ({
  fetchSharedLesson: mocks.fetchSharedLesson,
}));

vi.mock('../components/player/playerMode', () => ({
  PLAYER_MODES: {
    PUBLIC_MP4: 'public_mp4',
    SECURE_HLS: 'secure_hls',
    UNAVAILABLE: 'unavailable',
  },
  resolvePlayerMode: () => ({
    mode: 'public_mp4',
    fallbackUrl: 'https://media.example.test/shared.mp4',
    manifestUrl: '',
  }),
}));

vi.mock('../components/player/VideoStage', () => ({
  default: ({ lesson }) => (
    <div data-testid="shared-video-stage">
      {lesson?.title}
      <span>{lesson?.avatar_overlay?.stream_url}</span>
      <span>{lesson?.subtitle_vtt_url}</span>
    </div>
  ),
}));

vi.mock('../components/player/UnavailableStage', () => ({
  default: ({ message }) => <div>{message}</div>,
}));

vi.mock('../components/ui/SurfaceCard', () => ({
  default: ({ children, ...props }) => <section {...props}>{children}</section>,
}));

import SharedWatch from './SharedWatch';

async function renderShared(initialEntry = '/share/share-token') {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/share/:token" element={<SharedWatch />} />
        </Routes>
      </MemoryRouter>,
    );
  });

  return { host, root };
}

describe('SharedWatch', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    vi.clearAllMocks();
  });

  it('loads shared playback metadata without authentication UI', async () => {
    mocks.fetchSharedLesson.mockResolvedValue({
      id: 42,
      title: 'Shared lesson',
      stream_url: 'https://media.example.test/shared.mp4',
      subtitle_vtt_url: 'https://media.example.test/shared.vtt',
      avatar_overlay: {
        enabled: true,
        stream_url: 'https://media.example.test/avatar.mp4',
      },
      share: {
        expires_at: '2026-06-26T12:00:00Z',
      },
    });

    const { host, root } = await renderShared();

    await vi.waitFor(() => {
      expect(host.querySelector('[data-testid="shared-video-stage"]')).not.toBeNull();
    });

    expect(mocks.fetchSharedLesson).toHaveBeenCalledWith('share-token');
    expect(host).toHaveTextContent('Shared lesson');
    expect(host).toHaveTextContent('https://media.example.test/avatar.mp4');
    expect(host).toHaveTextContent('https://media.example.test/shared.vtt');
    expect(host).toHaveTextContent('No sign-in required');

    await act(async () => root.unmount());
    host.remove();
  });

  it('shows an expired message for expired links', async () => {
    const error = new Error('Share link has expired.');
    error.reason = 'expired';
    mocks.fetchSharedLesson.mockRejectedValue(error);

    const { host, root } = await renderShared('/share/expired-token');

    await vi.waitFor(() => {
      expect(host).toHaveTextContent('This share link has expired.');
    });

    await act(async () => root.unmount());
    host.remove();
  });
});
