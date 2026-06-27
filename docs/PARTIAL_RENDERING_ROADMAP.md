# Partial Rendering Roadmap

This document describes current targeted rerender support and the future plan for per-slide partial rendering.

## Current State

Implemented today:

- Transcript PATCH can update selected existing pages.
- Changed transcript pages can produce `changed_page_keys`.
- `process_pptx_to_video` accepts `rerender_page_keys`.
- When `rerender_page_keys` is non-empty, the worker renders only target slides and calls `merge_and_finalize_segments`.
- `merge_and_finalize_segments` merges newly rendered slide outputs with existing unchanged artifacts, then finalizes the full lesson output.
- `playback_assets.json` stores per-slide and final playback sidecar data used by Watch/API payloads.
- `playback_assets.json` now includes report-only `partial_render_manifest` metadata with deterministic per-page dependency hashes.
- A pure report-only classifier can compare an old manifest with a newly built expected manifest and label dependency changes without changing render behavior.
- `RenderFollowUpIntent` supports targeted and full follow-up requests while another render is active.

Current safety rule:

- Structural page edits still use full same-project rerender.
- Split, merge, reorder, delete, and restore should not be treated as targeted render inputs.
- For structural actions, `changed_page_keys` is UI/status metadata and `rerender_page_keys=[]` means full rerender.

## Current Limitations

- Targeted rerender is page-key based, not dependency-hash based.
- Visual scene changes, background changes, layout changes, TTS settings changes, and avatar setting changes are represented for reporting only; they do not yet drive render decisions.
- The final lesson asset is still finalized after targeted work; the system does not yet publish independent immutable slide packages.
- Structural timeline changes are intentionally conservative and still full rerender.

## Future Content-hash Manifest

Add a per-slide render manifest keyed by stable `page_key`:

```json
{
  "version": 1,
  "project_id": 123,
  "slides": [
    {
      "page_key": "s1-p1",
      "sequence_index": 0,
      "display_hash": "sha256:...",
      "narration_hash": "sha256:...",
      "tts_settings_hash": "sha256:...",
      "avatar_hash": "sha256:...",
      "background_hash": "sha256:...",
      "layout_hash": "sha256:...",
      "intelligence_hash": "sha256:...",
      "artifacts": {
        "visual": "123/images/s1-p1.png",
        "audio": "123/audio/s1-p1.wav",
        "base_segment": "123/parts/s1-p1.mp4",
        "avatar_segment": "123/avatar_segments/avatar_001.mp4"
      }
    }
  ]
}
```

The manifest should let the worker decide whether a slide needs:

- no work,
- visual recomposition only,
- TTS regeneration,
- avatar regeneration,
- segment recomposition,
- full finalization,
- full rerender.

## Dependency Matrix

| Change | Affects | Expected future work |
| --- | --- | --- |
| Display text | Slide visual, captions if displayed text feeds captions | Recompose visual and affected segment only |
| Narration text | TTS audio, subtitles, avatar lip sync, segment duration | Regenerate TTS, avatar if enabled, segment, and subtitle timing for that slide |
| TTS settings | TTS audio, subtitle timing, avatar lip sync, segment duration | Regenerate audio, avatar if enabled, segment, and timing for affected slides |
| Avatar source/settings | Avatar segment/overlay metadata | Regenerate avatar only when source or render-affecting avatar settings change |
| Avatar display default | Playback metadata only | Update sidecar/default metadata; no video rerender |
| Background | Slide visual and segment composition | Recompose visual and affected segment only |
| Layout | Slide visual and segment composition | Recompose visual and affected segment only |
| Intelligence suggestion accepted into narration | Narration text path | Same as narration changed |
| Intelligence suggestion accepted into display-only note | Display/layout path if visible | Recompose visual only when it changes visible output |
| Reorder/delete/split/merge | Timeline, sequence, durations, subtitle ordering | Full rerender until manifest sequencing is proven safe |

## Examples

Visible text only:

- Recompute display hash.
- Recompose the slide visual.
- Recompose only that slide segment.
- Finalize playback sidecar and final lesson output.
- Do not regenerate TTS or avatar if narration/audio is unchanged.

Narration changed:

- Recompute narration hash.
- Regenerate TTS for that slide.
- Regenerate avatar lip sync for that slide if avatar is enabled and render-affecting.
- Recompose the segment.
- Update subtitle timing for that slide.

Background changed:

- Recompute background hash.
- Recompose visual and affected segment only.
- Do not regenerate TTS or avatar if audio and avatar inputs are unchanged.

Avatar display default changed:

- Update `playback_assets.json` or a successor playback manifest.
- Do not render video.
- Do not regenerate TTS or avatar.

Structural reorder/delete/split/merge:

- Treat sequence as changed.
- Continue using full same-project rerender until a manifest proves segment order, subtitles, timing, and deleted/restored page handling are correct.

## Implementation Phases

### Phase 1: Manifest Readiness

- Document existing `playback_assets.json` fields and all artifact dependencies.
- Add manifest generation in report-only mode. Implemented for `playback_assets.json`.
- Add report-only manifest change classification. Implemented as pure helper logic only.
- Compare manifest decisions against current rerender behavior without changing output.

### Phase 2: Visual-only Targeting

- Support display/background/layout changes that do not alter narration or timeline.
- Keep fallback to full rerender when manifest data is missing or stale.

### Phase 3: Audio-aware Targeting

- Support narration and TTS settings changes per slide.
- Rebuild subtitle cues and duration metadata from the changed slide set.

### Phase 4: Avatar-aware Targeting

- Track avatar source/settings hashes separately from display defaults.
- Regenerate only affected avatar segments where possible.

### Phase 5: Structural Targeting Review

- Revisit split/merge/reorder/delete/restore only after sequence manifests, subtitle ordering, and finalization rules have strong tests.
- Keep full rerender as the safe default for structural edits until then.
