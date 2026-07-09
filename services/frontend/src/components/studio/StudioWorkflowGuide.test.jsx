import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import StudioWorkflowGuide, { studioWorkflowState } from './StudioWorkflowGuide';

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
  });

  it('maps the lightweight edit-to-watch progression without changing project state', async () => {
    expect(studioWorkflowState({ hasChanges: true }).activeStep).toBe('Edit');
    expect(studioWorkflowState({ renderReady: false }).activeStep).toBe('Render');
    expect(studioWorkflowState({ renderReady: true, published: false }).activeStep).toBe('Publish');
    expect(studioWorkflowState({ renderReady: true, published: true }).activeStep).toBe('Watch');

    await act(async () => {
      root.render(<StudioWorkflowGuide renderReady published />);
    });

    expect(host.textContent).toContain('Edit→Render→Publish→Watch');
    expect(host.querySelector('[aria-current="step"]')).toHaveTextContent('Watch');
    expect(host.textContent).toContain('Open Watch to verify the learner experience');
  });
});
