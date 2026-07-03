import { describe, expect, it } from 'vitest';
import {
  buildPayloadPage,
  editableSignature,
  pageWithTranscriptAvatarLayout,
} from './TranscriptEditorPanel';


function page(overrides = {}) {
  return {
    id: 1,
    page_key: 's1-p1',
    order: 0,
    original_text: 'Display',
    narration_text: 'Narration',
    rich_text_html: '<p>Display</p>',
    editor_document: {
      version: 1,
      scene: {
        background_mode: 'custom',
        unrelated_scene_value: { keep: true },
      },
    },
    ...overrides,
  };
}


describe('TranscriptEditorPanel avatar layout drafts', () => {
  it('updates one page, preserves unrelated scene fields, and keeps explicit hidden', () => {
    const pages = [page(), page({ id: 2, page_key: 's2-p1' })];
    const nextPages = pages.map((item, index) => (
      index === 0
        ? pageWithTranscriptAvatarLayout(item, { position: 'bottom-left', size: 'large', visible: false })
        : item
    ));

    expect(nextPages[0].editor_document.scene).toEqual({
      background_mode: 'custom',
      unrelated_scene_value: { keep: true },
      avatar_layout: { position: 'bottom-left', size: 'large', visible: false },
    });
    expect(nextPages[1]).toBe(pages[1]);
    expect(editableSignature(nextPages[0])).not.toBe(editableSignature(pages[0]));

    const payload = buildPayloadPage(nextPages[0]);
    expect(payload.editor_document.scene.avatar_layout).toEqual({
      position: 'bottom-left',
      size: 'large',
      visible: false,
    });
    expect(payload.editor_document.scene.unrelated_scene_value).toEqual({ keep: true });

    const layoutOnlyPayload = buildPayloadPage(nextPages[0], { avatarLayoutOnly: true });
    expect(layoutOnlyPayload).toEqual({
      id: 1,
      page_key: 's1-p1',
      order: 0,
      editor_document: {
        scene: {
          avatar_layout: { position: 'bottom-left', size: 'large', visible: false },
        },
      },
    });
  });

  it('removes an empty override and serializes inherit distinctly from hidden', () => {
    const saved = pageWithTranscriptAvatarLayout(page(), { visible: false });
    const reset = pageWithTranscriptAvatarLayout(saved, {});

    expect(saved.editor_document.scene.avatar_layout).toEqual({ visible: false });
    expect(reset.editor_document.scene).not.toHaveProperty('avatar_layout');
    expect(buildPayloadPage(reset).editor_document.scene.avatar_layout).toBeNull();
    expect(editableSignature(reset)).toBe(editableSignature(page()));
  });

  it('drops malformed values instead of adding them to the draft payload', () => {
    const next = pageWithTranscriptAvatarLayout(page(), {
      position: 'center',
      size: 'giant',
      visible: 'false',
    });

    expect(next.editor_document.scene).not.toHaveProperty('avatar_layout');
    expect(buildPayloadPage(next).editor_document.scene.avatar_layout).toBeNull();
  });
});
