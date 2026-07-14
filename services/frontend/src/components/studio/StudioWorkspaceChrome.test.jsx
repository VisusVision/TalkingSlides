import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  StudioCreatorHeader,
  StudioInspectorSection,
  StudioRenderStatus,
  StudioSaveStatus,
  StudioSlideRail,
  StudioWorkflowStrip,
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

  it('renders the Studio workflow as an accessible production sequence', async () => {
    await act(async () => {
      root.render(
        <StudioWorkflowStrip
          steps={[
            { key: 'edit', label: 'Edit', status: 'complete', detail: 'Saved' },
            { key: 'review', label: 'Review', status: 'active', detail: 'Approved' },
            { key: 'render', label: 'Render', status: 'pending', detail: 'Not queued' },
          ]}
        />,
      );
    });

    const workflow = host.querySelector('[data-testid="studio-workflow-strip"]');
    expect(workflow).toBeTruthy();
    expect(workflow).toHaveAttribute('aria-label', 'Studio workflow');
    expect(host.textContent).toContain('AI-assisted studio flow');
    expect(host.textContent).toContain('Edit');
    expect(host.querySelector('[aria-current="step"]').textContent).toContain('Review');
  });

  it('renders the AI creator header with metadata, chips, CTA, and render status', async () => {
    const onRender = vi.fn();
    await act(async () => {
      root.render(
        <StudioCreatorHeader
          title="Creator Header Lesson"
          description="A concise lesson summary."
          metadata={[
            { key: 'avatar', label: 'Avatar', value: 'Avatar ready' },
            { key: 'voice', label: 'Voice', value: 'XTTS v2' },
            { key: 'duration', label: 'Duration', value: '2:41' },
          ]}
          chips={[
            { key: 'ready', label: 'Ready', variant: 'success' },
            { key: 'publish-ready', label: 'Publish Ready', variant: 'success' },
          ]}
          nextActionTitle="Render the updated video"
          nextActionDetail="The transcript changes require a fresh render."
          primaryAction={{ label: 'Render', onClick: onRender }}
          renderStatus={{ status: 'ready', progress: 100 }}
        />,
      );
    });

    const header = host.querySelector('[data-testid="studio-creator-header"]');
    expect(header).toBeTruthy();
    expect(header).toHaveAttribute('aria-labelledby', 'studio-creator-header-title');
    expect(host.textContent).toContain('Creator Header Lesson');
    expect(host.textContent).toContain('Avatar ready');
    expect(host.textContent).toContain('Publish Ready');
    expect(host.textContent).toContain('Render status: Ready');
    expect(host.textContent).toContain('Next best action');

    const button = Array.from(host.querySelectorAll('button')).find((item) => item.textContent.includes('Render'));
    await act(async () => button.click());
    expect(onRender).toHaveBeenCalledTimes(1);
  });
});
