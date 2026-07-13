import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  StudioInspectorSection,
  StudioRenderStatus,
  StudioSaveStatus,
  StudioSlideRail,
} from './StudioWorkspaceChrome';
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
    expect(host.querySelector('[data-selected="true"]').className).toContain('motion-studio-selection');
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
    expect(document.body.querySelector('[role="menu"]').className).toContain('motion-popover-in');
    const deleteItem = Array.from(document.body.querySelectorAll('[role="menuitem"]'))
      .find((item) => item.textContent.includes('Delete'));
    expect(deleteItem.className).toContain('motion-interactive');
    await act(async () => deleteItem.click());
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ key: 'one' }), 0);
  });

  it('shows localized loading, empty, and render queue states', async () => {
    document.documentElement.lang = 'tr-TR';
    await act(async () => root.render(<StudioSlideRail loading />));
    expect(host.textContent).toContain('Slaytlar yükleniyor');

    await act(async () => root.render(<StudioSlideRail scenes={[]} />));
    expect(host.textContent).toContain('Henüz slayt yok');

    await act(async () => root.render(<StudioRenderStatus renderStatus={{ status: 'processing', progress: 64 }} />));
    expect(host.textContent).toContain('Render durumu: İşleniyor');
    const renderStatus = host.querySelector('[data-testid="studio-render-status"]');
    expect(renderStatus).toHaveAttribute('data-state', 'processing');
    expect(renderStatus).toHaveAttribute('data-render-state', 'active');
    expect(renderStatus.className).toContain('motion-studio-status');
    expect(renderStatus.className).toContain('motion-task-active');
    expect(host.querySelector('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '64');

    await act(async () => root.render(<StudioRenderStatus renderStatus={{ status: 'pending', progress: 0 }} />));
    expect(host.querySelector('[data-testid="studio-render-status"]')).toHaveAttribute('data-state', 'queued');
    expect(host.querySelector('[role="progressbar"]')).toBeNull();
    expect(host.querySelector('.motion-task-progress')).not.toBeNull();

    await act(async () => root.render(<StudioRenderStatus renderStatus={{ status: 'running', progress: 100 }} />));
    expect(host.textContent).toContain('99%');
    expect(host.querySelector('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '99');

    await act(async () => root.render(<StudioRenderStatus renderStatus={{ status: 'done', progress: 100 }} />));
    expect(host.textContent).toContain('100%');
    expect(host.querySelector('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '100');
  });

  it('keeps save and disclosure state visible while adding Studio motion hooks', async () => {
    await act(async () => {
      root.render(
        <>
          <StudioSaveStatus saving lastSavedAt="10:30 AM" />
          <StudioInspectorSection title="Scene background" summary="Selected slide controls">
            <button type="button">Focusable control</button>
          </StudioInspectorSection>
        </>,
      );
    });

    const saveStatus = host.querySelector('[data-state="saving"]');
    expect(saveStatus.textContent).toContain('Saving');
    expect(saveStatus.className).toContain('motion-studio-status');

    const details = host.querySelector('details');
    expect(details.open).toBe(true);
    expect(details.className).toContain('motion-studio-status');
    expect(host.querySelector('summary').className).toContain('motion-interactive');
    expect(host.querySelector('.motion-studio-panel')).not.toBeNull();
    expect(host.querySelector('button').textContent).toContain('Focusable control');
  });
});
