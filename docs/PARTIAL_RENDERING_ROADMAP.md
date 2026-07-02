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
- Finalized `final_segments` entries carry `page_key` so targeted rerenders can match prior duration and pause metadata to stable pages.
- A pure report-only classifier can compare an old manifest with a newly built expected manifest and label dependency changes without changing render behavior.
- `playback_assets.json` now surfaces report-only `partial_render_analysis` metadata from the old sidecar manifest and the newly finalized manifest for debugging and future optimization.
- `partial_render_analysis.plan` now maps classifier output to report-only future planning actions without changing render behavior.
- Targeted rerenders can now use a narrow worker-only visual recomposition optimization when the runtime-recomputed plan says every target page is visual-only.
- Avatar-disabled targeted rerenders can now use a narrow worker-only narration recomposition optimization when runtime classification says selected targets only need narration/TTS regeneration and every non-target page is unchanged.
- Project detail exposes sanitized last completed render analysis as `latest_render_analysis`, and Studio shows a diagnostic-only "Last render analysis" panel for the selected lesson.
- Studio can request a read-only predicted rerender impact preview before Save & Rerender. The preview compares the latest saved `partial_render_manifest` with the current editor request payload, saved dirty draft, or active project state and returns sanitized prediction-only plan data.
- `RenderFollowUpIntent` supports targeted and full follow-up requests while another render is active.

Current safety rule:

- Structural page edits still use full same-project rerender.
- Split, merge, reorder, delete, and restore should not be treated as targeted render inputs.
- For structural actions, `changed_page_keys` is UI/status metadata and `rerender_page_keys=[]` means full rerender.

## Current Limitations

- Targeted rerender is page-key based, not dependency-hash based.
- Stored `partial_render_analysis` is visibility metadata only; it is not trusted as command state for render decisions.
- Studio/API visibility shows the last completed render analysis plus a prediction-only pre-rerender preview. The preview is diagnostic; actual rendering may safely fall back.
- Unsaved transcript editor changes are included when Studio sends the current request payload. Otherwise the preview uses the saved dirty draft if one exists, then the active project state.
- Visual-only targeted recomposition recomputes the expected manifest, classifier, and plan at worker runtime before it can run.
- Visual-only optimization is all-or-nothing for the targeted page set. If any target page is not eligible, all targets use the existing slide render path.
- Narration-only targeted recomposition is avatar-disabled only, all-or-nothing, and requires an old manifest plus required old visual/segment/audio artifacts. Any uncertainty falls back to the existing slide render path.
- Legacy sidecars without segment page keys use ordered timing fallback only when manifest order and segment/page counts match exactly; ambiguous sidecars still fall back conservatively.
- Avatar-enabled narration/TTS changes are still deferred and fall back to the existing render behavior.
- The final lesson asset is still finalized after targeted work; the system does not yet publish independent immutable slide packages.
- Final MP4, HLS sidecar, SRT, VTT, playback sidecar, manifest, and analysis are still regenerated because narration duration, timeline, and subtitles can shift.
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
- Surface classifier visibility/reporting in `playback_assets.json` as `partial_render_analysis`. Implemented in report-only mode.
- Add report-only future planning from classifier output. Implemented as `partial_render_analysis.plan`.
- Expose sanitized last completed render analysis in project detail and Studio. Implemented as diagnostic-only `latest_render_analysis`; it does not expose artifact paths.
- Expose sanitized predicted rerender impact in Studio before Save & Rerender. Implemented as a read-only API preview and diagnostic-only Studio panel; it creates no jobs or follow-up intents and does not mutate draft, project, moderation, publish, or render state.
- Compare manifest decisions against current rerender behavior without changing output.

### Phase 2: Visual-only Targeting

- Support display/background/layout changes that do not alter narration, TTS, avatar inputs, or timeline. Implemented for targeted rerenders through a conservative worker-only visual segment recomposition path.
- Reuse existing TTS audio and existing avatar clip metadata only when required artifacts exist, dependency hashes remain unchanged, and avatar-enabled lessons have a reusable prior avatar overlay track.
- Keep fallback to the existing targeted slide render path when manifest data is missing, stale, mixed, or non-visual.
- Limitations: targeted rerenders only, all-or-nothing gate, visual-only classifications only, avatar-enabled narration/TTS optimization deferred.

### Phase 3: Audio-aware Targeting

- Support avatar-disabled narration/TTS-only changes per targeted slide. Implemented through conservative worker-only narration recomposition when runtime planning proves only requested targets need TTS/audio regeneration and cached visuals/segments are available.
- Rebuild subtitle cues and duration metadata from the changed slide set. Implemented as part of finalization for optimized targeted narration rerenders.
- Avatar-enabled narration/TTS changes remain future/deferred and use the existing render path.

### Phase 4: Avatar-aware Targeting

- Track avatar source/settings hashes separately from display defaults.
- Regenerate only affected avatar segments where possible.

### Phase 5: Structural Targeting Review

- Revisit split/merge/reorder/delete/restore only after sequence manifests, subtitle ordering, and finalization rules have strong tests.
- Keep full rerender as the safe default for structural edits until then.
