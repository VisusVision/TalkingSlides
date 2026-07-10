import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import StudioWorkflowGuide, { studioWorkflowState } from './StudioWorkflowGuide';
import { SUPPORTED_APP_LOCALES } from '../../i18n/locale';
import { translateAppMessage } from '../../i18n/messages';

describe('StudioWorkflowGuide', () => {
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

  it('maps each workflow step state without mutating project data', () => {
    expect(studioWorkflowState({ hasChanges: true }).activeStep).toBe('edit');
    expect(studioWorkflowState({ hasChanges: false, hasNarration: false }).activeStep).toBe('review');
    expect(studioWorkflowState({ renderReady: false }).activeStep).toBe('render');
    expect(studioWorkflowState({ renderReady: true, canPublish: false }).activeStep).toBe('publish');
    expect(studioWorkflowState({ renderReady: true, canPublish: true, published: true }).activeStep).toBe('watch');
  });

  it('uses Save changes as the next best action for unsaved edits', async () => {
    const onAction = vi.fn();
    await act(async () => {
      root.render(<StudioWorkflowGuide hasChanges onAction={onAction} />);
    });

    expect(host.querySelector('[aria-current="step"]')).toHaveTextContent('Edit');
    expect(host.textContent).toContain('Unsaved changes must be saved first.');
    const button = Array.from(host.querySelectorAll('button')).find((item) => item.textContent.includes('Save changes'));
    await act(async () => button.click());
    expect(onAction).toHaveBeenCalledWith('save_changes');
  });

  it('shows render running progress without inventing an ETA', async () => {
    const state = studioWorkflowState({
      renderStatus: { status: 'running', progress_pct: 42, current_stage: 'Compositing' },
    });

    expect(state.activeStep).toBe('render');
    expect(state.action.id).toBe('view_progress');
    await act(async () => {
      root.render(<StudioWorkflowGuide state={state} />);
    });

    expect(host.textContent).toContain('Compositing');
    expect(host.textContent).toContain('42%');
    expect(host.textContent).toContain('A render is already queued or running.');
  });

  it('surfaces render failure with a retry action', async () => {
    const onAction = vi.fn();
    const state = studioWorkflowState({
      renderStatus: { status: 'failed', error_message: 'Worker exited' },
      canRetryRender: true,
    });

    expect(state.errorStep).toBe('render');
    expect(state.action.id).toBe('retry_render');
    await act(async () => {
      root.render(<StudioWorkflowGuide state={state} onAction={onAction} />);
    });

    expect(host.textContent).toContain('Worker exited');
    const retry = Array.from(host.querySelectorAll('button')).find((item) => item.textContent.includes('Retry render'));
    await act(async () => retry.click());
    expect(onAction).toHaveBeenCalledWith('retry_render');
  });

  it('maps blocked publishing to issue resolution', () => {
    const state = studioWorkflowState({
      renderReady: true,
      canPublish: false,
      moderationMessage: 'Moderation required',
    });

    expect(state.activeStep).toBe('publish');
    expect(state.errorStep).toBe('publish');
    expect(state.blockedSteps).toContain('watch');
    expect(state.action.id).toBe('resolve_publishing_issues');
    expect(state.blockers).toContain('workflowBlockerModerationRequired');
  });

  it('makes Watch the next action once published', async () => {
    const state = studioWorkflowState({
      renderReady: true,
      canPublish: true,
      published: true,
    });

    expect(state.activeStep).toBe('watch');
    expect(state.action.id).toBe('watch_lesson');
    await act(async () => {
      root.render(<StudioWorkflowGuide state={state} />);
    });

    expect(host.querySelector('[aria-current="step"]')).toHaveTextContent('Watch');
    expect(host.textContent).toContain('Published. Watch is the next step.');
  });

  it('renders responsive stepper structure with keyboard-focusable steps', async () => {
    window.innerWidth = 390;
    await act(async () => {
      root.render(<StudioWorkflowGuide renderReady published />);
    });

    const guide = host.querySelector('[data-testid="studio-workflow-guide"]');
    expect(guide.className).toContain('lg:grid-cols');
    const steps = host.querySelectorAll('ol button');
    expect(steps).toHaveLength(5);
    expect(Array.from(steps).every((step) => step.className.includes('focus-ring'))).toBe(true);
  });

  it('has localized workflow keys for every supported locale', () => {
    for (const { code } of SUPPORTED_APP_LOCALES) {
      expect(translateAppMessage(code, 'studioWorkspaceWorkflowLabel')).toBeTruthy();
      expect(translateAppMessage(code, 'studioWorkspaceWorkflowActionWatchLesson')).toBeTruthy();
      expect(translateAppMessage(code, 'studioWorkspaceWorkflowBlockerMissingNarration')).toBeTruthy();
    }
  });
});
