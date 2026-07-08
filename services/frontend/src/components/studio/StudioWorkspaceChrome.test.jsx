import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StudioRenderStatus, StudioSlideRail } from './StudioWorkspaceChrome';
import { studioWorkspaceCopy, studioWorkspaceLocale } from './studioWorkspaceCopy';

describe('Studio workspace chrome', () => {
  let host;
  let root;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    document.documentElement.lang = '';
  });

  it('falls back expanded and unsupported locales safely', () => {
    expect(studioWorkspaceLocale('tr-TR')).toBe('tr');
    expect(studioWorkspaceLocale('de-DE')).toBe('en');
    expect(studioWorkspaceCopy('tr-TR').slides).toBe('Slaytlar');
  });

  it('renders the slide rail and preserves selection callbacks', async () => {
    const onSelect = vi.fn();
    const onMove = vi.fn();
    const onDelete = vi.fn();
    await act(async () => {
      root.render(
        <StudioSlideRail
          scenes={[
            { key: 'one', label: 'Slide 1', text: 'User-authored title', status: 'draft' },
            { key: 'two', label: 'Slide 2', text: 'Second title', status: 'ready' },
          ]}
          selectedSceneKey="one"
          onSelect={onSelect}
          onMove={onMove}
          onDelete={onDelete}
        />,
      );
    });

    expect(host.querySelector('[data-testid="studio-slide-rail"]')).toBeTruthy();
    const buttons = host.querySelectorAll('button[aria-label^="Select slide"]');
    expect(buttons[0]).toHaveAttribute('aria-current', 'true');
    await act(async () => buttons[1].click());
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ key: 'two' }), 1);

    await act(async () => {
      buttons[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    });
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ key: 'two' }), 1);

    await act(async () => {
      buttons[0].dispatchEvent(new MouseEvent('contextmenu', {
        bubbles: true,
        cancelable: true,
        clientX: 24,
        clientY: 32,
      }));
    });
    expect(document.body.textContent).toContain('Delete');
    const deleteItem = Array.from(document.body.querySelectorAll('[role="menuitem"]'))
      .find((item) => item.textContent.includes('Delete'));
    await act(async () => deleteItem.click());
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ key: 'one' }), 0);
  });

  it('shows localized loading, empty, and render queue states', async () => {
    document.documentElement.lang = 'tr-TR';
    await act(async () => root.render(<StudioSlideRail loading />));
    expect(host.textContent).toContain('Slaytlar yükleniyor');

    await act(async () => root.render(<StudioSlideRail scenes={[]} />));
    expect(host.textContent).toContain('Henüz slayt yok');

    await act(async () => root.render(<StudioRenderStatus renderStatus={{ status: 'processing' }} />));
    expect(host.textContent).toContain('Render durumu: İşleniyor');
  });
});
