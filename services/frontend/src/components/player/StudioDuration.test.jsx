import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('./AvatarOverlayLayer', () => ({
  AVATAR_OVERLAY_Z_INDEX: {
    captions: 20,
    videoControls: 30,
  },
  default: () => null,
}));

vi.mock('./WatermarkOverlay', () => ({
  default: () => null,
}));

vi.mock('../ui/SurfaceCard', () => ({
  default: ({ children }) => <div>{children}</div>,
}));

import VideoStage from './VideoStage';

const mountedRoots = [];

async function renderVideoStage(lesson) {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const root = createRoot(host);
  mountedRoots.push({ host, root });

  await act(async () => {
    root.render(
      <VideoStage
        lesson={lesson}
        asSurface={false}
        showSubtitleControls={false}
        avatarOverlayMode="disabled"
      />,
    );
  });

  return host;
}

afterEach(async () => {
  while (mountedRoots.length > 0) {
    const { host, root } = mountedRoots.pop();
    await act(async () => {
      root.unmount();
    });
    host.remove();
  }
});

describe('Studio embedded lesson preview duration', () => {
  it('displays the canonical duration_seconds value instead of a stale minute field', async () => {
    const host = await renderVideoStage({
      title: 'Neural Network Optimization',
      duration_seconds: 521,
      duration_minutes: 8,
    });

    expect(host).toHaveTextContent('9m');
    expect(host).not.toHaveTextContent('8m');
  });

  it('uses the canonical Studio preview transcript timeline when direct duration is absent', async () => {
    const host = await renderVideoStage({
      title: 'Neural Network Optimization',
      transcript_pages: [
        { start_seconds: 0, end_seconds: 240 },
        { start_seconds: 240, end_seconds: 521 },
      ],
    });

    expect(host).toHaveTextContent('9m');
    expect(host).not.toHaveTextContent('8m');
  });

  it('renders a safe unknown state when draft duration metadata is missing', async () => {
    const host = await renderVideoStage({
      title: 'Incomplete Draft',
    });

    expect(host).toHaveTextContent('Duration unavailable');
  });
});
