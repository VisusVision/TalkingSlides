import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PageLoadingProvider, usePageLoading } from './PageLoading';

function LoadingSource({ active }) {
  usePageLoading(active, 'test-source');
  return <div>Route body</div>;
}

describe('PageLoadingProvider', () => {
  let host;
  let root;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers();
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    host.remove();
    vi.useRealTimers();
  });

  it('shows the progress indicator only after the loading delay', async () => {
    await act(async () => {
      root.render(
        <PageLoadingProvider>
          <LoadingSource active />
        </PageLoadingProvider>,
      );
    });

    expect(host.querySelector('.visus-page-progress')).toBeNull();

    await act(async () => {
      vi.advanceTimersByTime(179);
    });

    expect(host.querySelector('.visus-page-progress')).toBeNull();

    await act(async () => {
      vi.advanceTimersByTime(1);
    });

    expect(host.querySelector('.visus-page-progress')).toBeTruthy();

    await act(async () => {
      root.render(
        <PageLoadingProvider>
          <LoadingSource active={false} />
        </PageLoadingProvider>,
      );
    });

    expect(host.querySelector('.visus-page-progress')).toBeNull();
  });
});
