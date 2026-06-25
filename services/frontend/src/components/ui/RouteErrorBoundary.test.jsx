import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import RouteErrorBoundary from './RouteErrorBoundary';

vi.mock('./SurfaceCard', () => ({
  default: ({ children, ...props }) => <section {...props}>{children}</section>,
}));

vi.mock('./Button', () => ({
  default: ({ children, ...props }) => <button {...props}>{children}</button>,
}));

function SafePage() {
  return <div>Safe route body</div>;
}

describe('RouteErrorBoundary', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  });

  it('shows a route fallback instead of leaving the root blank', async () => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const root = createRoot(host);
    let boundaryRef = null;

    await act(async () => {
      root.render(
        <RouteErrorBoundary ref={(node) => { boundaryRef = node; }} resetKey="/studio">
          <SafePage />
        </RouteErrorBoundary>,
      );
    });
    await act(async () => {
      boundaryRef.setState({ error: new Error('route failed') });
    });

    expect(host.textContent).toContain('This page could not render');
    expect(host.textContent).toContain('Reload');

    await act(async () => root.unmount());
    host.remove();
  });

  it('clears the fallback when the route reset key changes', async () => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const root = createRoot(host);
    let boundaryRef = null;

    await act(async () => {
      root.render(
        <RouteErrorBoundary ref={(node) => { boundaryRef = node; }} resetKey="/studio">
          <SafePage />
        </RouteErrorBoundary>,
      );
    });
    await act(async () => {
      boundaryRef.setState({ error: new Error('route failed') });
    });
    expect(host.textContent).toContain('This page could not render');

    await act(async () => {
      root.render(
        <RouteErrorBoundary resetKey="/browse">
          <SafePage />
        </RouteErrorBoundary>,
      );
    });

    expect(host.textContent).toContain('Safe route body');
    expect(host.textContent).not.toContain('This page could not render');

    await act(async () => root.unmount());
    host.remove();
  });
});
