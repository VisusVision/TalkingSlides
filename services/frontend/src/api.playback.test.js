import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  fetchPlaybackToken,
  fetchLesson,
  fetchStudioPreviewToken,
  heartbeatPlaybackSession,
  setToken,
} from './api';

describe('playback session API credentials', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
    setToken(null);
    global.fetch = vi.fn();
  });

  it('includes browser credentials when requesting a playback token', async () => {
    setToken('token-123');
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ video_url: '/api/v1/stream/video-token/' }),
    });

    await fetchPlaybackToken(42);

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/projects/42/playback-token/'),
      expect.objectContaining({
        credentials: 'include',
        headers: expect.objectContaining({ Authorization: 'Token token-123' }),
      }),
    );
  });

  it('includes browser credentials when fetching a lesson because it carries playback tokens', async () => {
    setToken('token-123');
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ id: 42, stream_url: '/api/v1/stream/video-token/' }),
    });

    await fetchLesson(42);

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/catalog/42/'),
      expect.objectContaining({
        credentials: 'include',
        headers: expect.objectContaining({ Authorization: 'Token token-123' }),
      }),
    );
  });

  it('includes browser credentials when requesting a studio preview token', async () => {
    setToken('token-123');
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ video_url: '/api/v1/stream/preview-token/' }),
    });

    await fetchStudioPreviewToken(42);

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/projects/42/studio-preview-token/'),
      expect.objectContaining({
        credentials: 'include',
        headers: expect.objectContaining({ Authorization: 'Token token-123' }),
      }),
    );
  });

  it('includes browser credentials when sending playback heartbeat', async () => {
    setToken('token-123');
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ active: true }),
    });

    await heartbeatPlaybackSession(42, 'visible');

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/projects/42/playback-session/heartbeat/'),
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        headers: expect.objectContaining({
          Authorization: 'Token token-123',
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({ visibility: 'visible' }),
      }),
    );
  });

  it('deduplicates concurrent lesson fetches for the same authenticated session', async () => {
    setToken('token-123');
    let resolveFetch;
    global.fetch.mockImplementation(
      () => new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );

    const first = fetchLesson(42);
    const second = fetchLesson(42);

    expect(global.fetch).toHaveBeenCalledTimes(1);

    resolveFetch({
      ok: true,
      json: async () => ({ id: 42, stream_url: '/api/v1/stream/video-token/' }),
    });

    const [firstResult, secondResult] = await Promise.all([first, second]);
    expect(firstResult.stream_url).toContain('/api/v1/stream/video-token/');
    expect(secondResult).toEqual(firstResult);
  });

  it('reuses a just-resolved lesson response across immediate remount fetches', async () => {
    setToken('token-123');
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        id: 42,
        stream_url: '/api/v1/stream/video-token-first/',
      }),
    });

    const first = await fetchLesson(42);
    const second = await fetchLesson(42);

    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(second).toEqual(first);
    expect(second.stream_url).toContain('/api/v1/stream/video-token-first/');
  });

  it('does not reuse a lesson response across authenticated users', async () => {
    setToken('token-one');
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 42, stream_url: '/api/v1/stream/user-one/' }),
    });

    const first = await fetchLesson(42);

    setToken('token-two');
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 42, stream_url: '/api/v1/stream/user-two/' }),
    });

    const second = await fetchLesson(42);

    expect(global.fetch).toHaveBeenCalledTimes(2);
    expect(first.stream_url).toContain('/api/v1/stream/user-one/');
    expect(second.stream_url).toContain('/api/v1/stream/user-two/');
  });

  it('does not cache failed lesson responses', async () => {
    setToken('token-123');
    global.fetch
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        json: async () => ({ error: 'temporary failure' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: 42, stream_url: '/api/v1/stream/recovered/' }),
      });

    await expect(fetchLesson(42)).rejects.toThrow('temporary failure');
    const recovered = await fetchLesson(42);

    expect(global.fetch).toHaveBeenCalledTimes(2);
    expect(recovered.stream_url).toContain('/api/v1/stream/recovered/');
  });

  it('deduplicates concurrent playback-token fetches for the same authenticated session', async () => {
    setToken('token-123');
    let resolveFetch;
    global.fetch.mockImplementation(
      () => new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );

    const first = fetchPlaybackToken(42);
    const second = fetchPlaybackToken(42);

    expect(global.fetch).toHaveBeenCalledTimes(1);

    resolveFetch({
      ok: true,
      json: async () => ({ video_url: '/api/v1/stream/video-token/' }),
    });

    const [firstResult, secondResult] = await Promise.all([first, second]);
    expect(firstResult.video_url).toContain('/api/v1/stream/video-token/');
    expect(secondResult).toEqual(firstResult);
  });
});
