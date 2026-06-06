# Studio Phase 5C Page Controls Plan

## Executive Summary

Phase 5C should add explicit transcript page mutation controls for split, merge, reorder, delete, and restore. The safest backend approach is a new action endpoint:

`POST /api/v1/projects/<project_id>/transcript/actions/`

Do not extend the current transcript PATCH endpoint for structural mutations. The existing PATCH endpoint is now a partial text-edit endpoint keyed by existing page `id`. Split, merge, reorder, delete, and restore are command-style operations that touch multiple rows, need atomic validation, and may affect rerender targeting. Keeping them separate lowers the risk of data loss and keeps save/rerender text edits stable.

Phase 5C should be backend-first:

1. Add a small soft-delete migration for `TranscriptPage`.
2. Add the action endpoint with atomic validation and integration tests.
3. Wire frontend controls after the backend contract is proven.
4. Polish rerender behavior for structural page changes.

## Current Backend Capabilities

### TranscriptPage Model

`TranscriptPage` currently stores:

| Field | Purpose |
| --- | --- |
| `project` | Owning `Project`, related as `project.transcript_pages`. |
| `order` | Display/render ordering. Current timeline sorts by `order`, then `id`. |
| `source_slide_index` | Zero-based source slide index. |
| `split_index` | Split segment index within a source slide. |
| `page_key` | Stable page identity string, unique per project. |
| `original_text` | Extracted source/reference text. Should remain read-only in Studio. |
| `narration_text` | User-editable spoken/caption text. |
| `rich_text_html` | Rich display representation of narration. |
| `editor_document` | Structured editor representation. |
| `subtitle_chunks` | Caption/subtitle chunks derived from narration. |
| `chunk_timeline` | Timeline metadata. |
| `whiteboard_mode` | Per-page render flag. |
| `start_seconds`, `end_seconds`, `duration_seconds` | Timing metadata. |

Current constraints:

- `unique_together = ("project", "page_key")`
- No `is_active`, `deleted_at`, or soft-delete field exists.
- No explicit parent/source relationship field exists for split/merge history.

### Page Identity

The backend identifies persisted transcript pages by:

- Primary identity for PATCH: `id`
- Stable render/rerender identity: `page_key`
- Ordering: `order`, then `id`
- Source grouping: `source_slide_index` plus `split_index`

The frontend currently preserves `id`, `page_key`, `order`, `source_slide_index`, and `split_index` in save payloads.

### Current GET/PATCH Semantics

`GET /api/v1/projects/<id>/transcript/` returns:

```json
{
  "project_id": 123,
  "pages": [...]
}
```

`pages` is serialized from `project.transcript_pages.all().order_by("order", "id")`.

`PATCH /api/v1/projects/<id>/transcript/` currently expects:

```json
{
  "pages": [
    {
      "id": 1,
      "narration_text": "Edited narration",
      "rich_text_html": "Edited narration",
      "editor_document": {},
      "whiteboard_mode": false
    }
  ],
  "trigger_rerender": false,
  "pause_sec": 2.2,
  "lang_hint": "auto"
}
```

Important current behavior:

- PATCH is partial. It only updates supplied page IDs.
- Unknown or missing page IDs are ignored.
- It writes only `narration_text`, `rich_text_html`, `editor_document`, `whiteboard_mode`, and derived `subtitle_chunks`.
- It does not update `original_text`.
- It currently calls `_split_pages_on_double_newline(project)` after every PATCH, which can create additional pages as a side effect.
- If `trigger_rerender` is true, it passes only changed `page_key` values as `rerender_page_keys`.

### Current Rerender Page Key Behavior

Transcript-triggered rerender passes `rerender_page_keys` to `worker.tasks.process_pptx_to_video`.

The worker:

- Re-syncs transcript pages from fresh export using `page_key`.
- Preserves existing `original_text`, edited `narration_text`, subtitle chunks, and missing pages after the Phase 5 hotfix.
- Builds `rerender_set` from `rerender_page_keys`.
- Renders only slides whose `page_key` is in `rerender_set`.
- Falls back to all slides if the target list is empty.
- Uses `merge_and_finalize_segments` for targeted rerender and `concat_and_finalize` for full render.

This is good enough for text edits. Structural actions need more careful rerender rules because delete and reorder can affect final sequence, not only page content.

## Recommended API Contract

### Recommendation

Use a new explicit action endpoint:

```text
POST /api/v1/projects/<project_id>/transcript/actions/
```

Do not overload `PATCH /api/v1/projects/<id>/transcript/`.

Reasons:

- PATCH should remain a predictable text-edit endpoint.
- Page structure actions are commands with multi-row side effects.
- Split, merge, reorder, delete, and restore need all-or-nothing validation.
- The endpoint can return `changed_page_keys` and optional `rerender_job` consistently.
- The endpoint can reject invalid actions instead of silently ignoring them.
- It creates a clear frontend/backend contract for Phase 5C controls.

### Shared Request Fields

Every action request should include:

```json
{
  "action": "split_page",
  "trigger_rerender": false,
  "pause_sec": 2.2,
  "lang_hint": "auto"
}
```

Action-specific fields are described below.

### Shared Response Shape

Every successful action should return:

```json
{
  "project_id": 123,
  "action": "split_page",
  "pages": [],
  "changed_page_keys": ["s1-p1"],
  "deleted_pages": [],
  "rerender_job": null
}
```

Rules:

- `pages` should be the active transcript timeline in current display order.
- `deleted_pages` should be included only when useful for restore UI.
- `changed_page_keys` should be stable strings, not database IDs.
- `rerender_job` should match the existing JobSerializer shape when a job is queued.

### Shared Permission Behavior

Use the same project ownership/staff behavior as `ProjectTranscriptView`:

- `404` if the project does not exist.
- `403` if the authenticated user cannot manage the project.
- `401` through DRF auth when unauthenticated.

### Shared Atomicity Rules

Wrap each action in `transaction.atomic()`.

Validate everything before mutating rows:

- Project exists and user can manage it.
- Action name is allowed.
- Page IDs belong to the project.
- Page state allows the requested action.
- New page keys will be unique.
- Reorder payload is complete and duplicate-free.

If validation fails, return `400` and leave all transcript rows unchanged.

### Action: split_page

Recommended payload:

```json
{
  "action": "split_page",
  "page_id": 10,
  "parts": [
    {
      "narration_text": "First segment."
    },
    {
      "narration_text": "Second segment."
    }
  ],
  "trigger_rerender": false
}
```

Frontend may produce `parts` from:

- Cursor split in the selected textarea.
- Double blank line split.

Backend validation:

- `page_id` is required.
- Page must belong to the project and be active.
- `parts` must be a list with at least two items.
- At least one part must contain non-whitespace text.
- Reject more than a conservative maximum number of parts, for example 20.
- Do not allow page key collisions.

Effects:

- First part updates the existing page.
- Additional parts create new `TranscriptPage` rows inserted after the original page.
- Existing page keeps its `id` and `page_key`.
- New pages receive stable unique keys derived from the base key, for example `s1-p1-x1`, `s1-p1-x2`.
- Recompute `order` sequentially for active pages.
- Preserve `source_slide_index` from the original page.
- Set `split_index` deterministically after the original page.
- Recompute `subtitle_chunks`, `rich_text_html`, and `editor_document` from each part.
- Carry `whiteboard_mode` from the original page unless the payload explicitly overrides it.

`original_text` rule:

- Existing page keeps its current `original_text`.
- New split pages should not invent source text. If the split is clearly derived from current `original_text`, the backend may assign matching fragments. Otherwise new pages should use an empty `original_text` and rely on `narration_text` as the editable/render text.
- Do not overwrite the original page's `original_text`.

Rerender keys:

- `changed_page_keys` should include the original page key and all new page keys.
- If `trigger_rerender` is true, initial implementation can rerender affected keys. If sequence finalization proves fragile, Phase 5C3 should switch split actions to a full same-project rerender.

### Action: merge_with_next

Recommended payload:

```json
{
  "action": "merge_with_next",
  "page_id": 10,
  "separator": "\n\n",
  "trigger_rerender": false
}
```

Backend validation:

- `page_id` is required.
- Page must belong to the project and be active.
- There must be an active next page by current order.
- `separator` should be optional and restricted to safe whitespace, defaulting to `"\n\n"`.

Effects:

- The current page survives.
- The next page is soft-deleted.
- Surviving `narration_text` becomes current narration plus separator plus next narration.
- Surviving `rich_text_html`, `editor_document`, and `subtitle_chunks` are recomputed.
- Surviving `page_key` remains unchanged.
- Active page order is normalized.

`original_text` rule:

- Preserve source references by combining current and next `original_text` with the same separator when either value exists.
- Because the next page is soft-deleted, restore can recover the original row if the merge needs to be undone later.
- Do not silently discard the next page's source/reference text.

Rerender keys:

- `changed_page_keys` should include the surviving page key.
- For a same-project final video update, deletion of the next page may require full finalization. Phase 5C1 can return changed keys; Phase 5C3 should verify whether targeted merge is enough or should force full rerender.

### Action: merge_with_previous

Recommended payload:

```json
{
  "action": "merge_with_previous",
  "page_id": 11,
  "separator": "\n\n",
  "trigger_rerender": false
}
```

Rules mirror `merge_with_next`, except:

- The previous page survives.
- The selected page is soft-deleted.
- `changed_page_keys` contains the previous page key.

This is useful in the UI because users often notice that the current selected page should be part of the prior scene.

### Action: reorder_pages

Recommended payload:

```json
{
  "action": "reorder_pages",
  "page_ids": [10, 12, 11, 13],
  "trigger_rerender": false
}
```

Backend validation:

- `page_ids` must contain every active page for the project exactly once.
- No duplicates.
- No inactive/deleted pages.
- No pages from another project.
- Reject empty lists.

Effects:

- Set `order` to the index in `page_ids`.
- Do not change `id`, `page_key`, `source_slide_index`, `split_index`, `original_text`, `narration_text`, subtitles, timing, or editor fields.

Rerender keys:

- Reorder changes sequence, not page content.
- Default should be no rerender.
- If `trigger_rerender` is requested, Phase 5C3 should decide whether to queue full same-project rerender rather than targeted keys, because final concat order is sequence-wide.

### Action: delete_page

Recommended payload:

```json
{
  "action": "delete_page",
  "page_id": 10,
  "trigger_rerender": false
}
```

Backend validation:

- `page_id` is required.
- Page must belong to the project and be active.
- Reject deleting the last active page unless the product explicitly supports empty transcripts.

Effects:

- Soft-delete the page.
- Preserve `original_text`, `narration_text`, `page_key`, subtitle chunks, timing, and editor fields.
- Normalize order for active pages.

Rerender keys:

- Deletion removes a scene from the final sequence. Targeted rerender may not be enough if final media has to be re-concatenated.
- Phase 5C1 should return the deleted page key in `changed_page_keys`.
- Phase 5C3 should verify full same-project rerender behavior for delete.

### Action: restore_page

Recommended payload:

```json
{
  "action": "restore_page",
  "page_id": 10,
  "position": "end",
  "after_page_id": null,
  "trigger_rerender": false
}
```

Backend validation:

- `page_id` must belong to the project and be inactive/deleted.
- `position` can be `end`, `start`, or `after`.
- `after_page_id` is required only when `position` is `after`.
- `after_page_id` must belong to the project and be active.

Effects:

- Mark the page active.
- Clear deleted timestamp.
- Insert into requested position.
- Preserve all previous text, source, timing, and page identity fields.
- Normalize active order.

Rerender keys:

- `changed_page_keys` should include the restored page key.
- Full rerender may be safer if restoring changes final sequence.

## Data Rules

1. `original_text` is source/reference text. It must not be overwritten by normal editing or action payloads.
2. `narration_text` is editable and controls TTS, captions, and rerender narration.
3. Captions/subtitles should be derived from `narration_text`, not TTS pronunciation-normalized `spoken_text`.
4. Delete/restore should use soft delete. Do not hard-delete rows in Phase 5C.
5. `page_key` should remain stable for existing rows.
6. Newly split pages need unique stable `page_key` values.
7. Reorder must be deterministic. Normalize active page `order` values after structural actions.
8. Merge must not lose the merged page's source or narration text.
9. All actions must be atomic and must never create a new project.
10. Action endpoints should reject invalid payloads with `400`; do not silently ignore invalid page IDs like the current PATCH endpoint does.
11. Existing text PATCH should remain backward compatible for Phase 5A/5B UI.
12. Once explicit split exists, stop using implicit double-blank auto-split for normal text PATCH, or gate it carefully. Surprise page creation from ordinary save is too risky for Phase 5C UX.

## Migration Risk

Phase 5C should include a small migration.

Recommended migration:

```python
TranscriptPage.is_active = models.BooleanField(default=True, db_index=True)
TranscriptPage.deleted_at = models.DateTimeField(null=True, blank=True)
```

Why migration is needed:

- Delete/restore cannot be implemented safely with hard deletes.
- Restore requires preserving `id`, `page_key`, `original_text`, narration, timing, and editor metadata.
- Keeping inactive rows avoids data loss when a user deletes the wrong scene.

Avoid in first implementation:

- New parent/source metadata fields.
- Complex version history.
- Audit-log table.

Existing `source_slide_index`, `split_index`, and `page_key` are enough for Phase 5C1. Parent/source metadata can be added later only if restore history or source provenance becomes insufficient.

Migration follow-up requirements:

- Update `_project_transcript_timeline(project)` to return active pages by default.
- Add an optional internal helper for deleted pages when the restore UI needs it.
- Update worker transcript sync to avoid reactivating soft-deleted rows unintentionally.
- Update serializers to expose `is_active` and `deleted_at` only where appropriate for Studio management.

## Frontend UX Plan

Add controls in `TranscriptEditorPanel`, because it already owns transcript draft state, selected page focus, save/rerender controls, and dirty labels.

### Split Controls

Add:

- `Split at Cursor`
- `Split by Blank Lines`

Rules:

- Enable only when a page is selected and the narration can be split into at least two parts.
- For cursor split, use textarea cursor position to build `parts`.
- For blank-line split, split on double blank lines and show a confirmation preview.
- Do not call split automatically on save.

### Merge Controls

Add:

- `Merge with Previous`
- `Merge with Next`

Rules:

- Disable previous on first active page.
- Disable next on last active page.
- Confirm before merge and show which page will survive.
- After success, keep selection on the surviving page.

### Reorder Controls

Add minimal controls first:

- `Move Up`
- `Move Down`

Rules:

- Disable at list bounds.
- Implement by sending the full active `page_ids` array in desired order.
- Do not add drag and drop in Phase 5C1/5C2 unless the action endpoint is already stable.
- After success, keep selected page selected.

### Delete/Restore Controls

Add:

- `Delete Page`
- `Show Deleted Pages`
- `Restore`

Rules:

- Confirm deletion.
- Prevent deleting the last active page.
- Deleted pages should be hidden from the main scene rail/timeline.
- Restore should allow placing at end first. `after` placement can come after the base restore flow is stable.

### Rerender UX

Keep structure changes separate from rerender by default:

- After a structural action, show "Page structure updated. Rerender this project to update video output."
- Offer `Rerender affected pages` only when the backend marks it safe.
- For delete/reorder/restore, prefer a full same-project rerender until targeted finalization is proven correct.

### Scene Rail And Timeline Updates

After every action success:

- Replace transcript pages from response `pages`.
- Keep selected project unchanged.
- Keep selected page when it still exists.
- If selected page was deleted, select the next active page, then previous, then first.
- Update scene rail and timeline from active pages only.

## Tests

Add backend integration tests in `tests/integration/test_transcript_editor_pipeline.py` or a new focused file such as `tests/integration/test_transcript_page_actions.py`.

Recommended tests:

1. `test_transcript_action_split_creates_two_pages`
   - Split one page into two parts.
   - Existing page keeps `id` and `page_key`.
   - New page has unique `page_key`.
   - Both pages have expected `narration_text`.
   - Original page `original_text` is not overwritten.

2. `test_transcript_action_split_is_atomic_on_invalid_payload`
   - Send invalid parts.
   - Assert `400`.
   - Assert page count and text are unchanged.

3. `test_transcript_action_merge_with_next_preserves_source_references`
   - Merge page one with page two.
   - Surviving page contains both narration values.
   - Source/reference text from both pages is preserved.
   - Merged-away page is inactive, not hard-deleted.

4. `test_transcript_action_reorder_changes_order_without_losing_ids`
   - Reorder three pages.
   - Assert same IDs/page keys remain.
   - Assert order is deterministic.

5. `test_transcript_action_delete_soft_deletes_page`
   - Delete one page.
   - Assert it is inactive/deleted.
   - Assert other pages remain active.
   - Assert active timeline omits deleted page.

6. `test_transcript_action_restore_page`
   - Delete and restore a page.
   - Assert original ID/page key/text values survive.
   - Assert active order is normalized.

7. `test_transcript_actions_are_permission_protected`
   - Non-owner cannot mutate project page structure.

8. `test_transcript_action_rerender_uses_same_project`
   - Trigger rerender through action endpoint.
   - Assert job belongs to same project.
   - Assert no duplicate project is created.

9. `test_transcript_action_changed_page_keys`
   - Split/merge/delete return expected changed page keys.

10. `test_transcript_patch_does_not_auto_split_after_action_endpoint_exists`
   - Once explicit split is implemented, verify ordinary text save with double blank lines does not surprise-create pages unless intentionally preserved for backwards compatibility.

Frontend manual QA for Phase 5C2:

- Split selected page at cursor.
- Split selected page by blank line.
- Merge with next and previous.
- Move page up and down.
- Delete page and restore it.
- Confirm scene rail/timeline update immediately.
- Confirm selected project stays selected.
- Confirm no duplicate lesson appears.
- Confirm save after action still persists.
- Confirm rerender after action uses same project.

## Implementation Phases

### Phase 5C1: Backend Action Endpoint And Tests

Scope:

- Add `TranscriptPage.is_active` and `deleted_at` migration.
- Add helper functions for active/deleted transcript timelines.
- Add `ProjectTranscriptActionView`.
- Add URL path `projects/<int:project_id>/transcript/actions/`.
- Implement split, merge, reorder, delete, and restore actions.
- Wrap mutations in `transaction.atomic()`.
- Keep existing PATCH behavior stable except for a deliberate decision on implicit double-blank splitting.
- Add integration tests.

Success criteria:

- All action tests pass.
- Existing transcript edit/rerender tests pass.
- No new project is created by any action.
- `original_text` remains protected.

### Phase 5C2: Frontend Controls

Scope:

- Add action API helper in `services/frontend/src/api.js`.
- Add controls to `TranscriptEditorPanel`.
- Update scene rail/timeline from action responses.
- Add restore panel for inactive pages.
- Keep current Save and Save + Rerender behavior unchanged.

Success criteria:

- Controls are functional and never local-only.
- Invalid actions show backend errors.
- Scene rail/timeline and selected page state stay stable.

### Phase 5C3: Rerender Integration Polish

Scope:

- Decide action-specific rerender strategy:
  - Text-only split may use affected keys.
  - Merge/delete/reorder/restore likely need full same-project finalization.
- Ensure worker respects active pages and does not reactivate deleted rows.
- Verify final output sequence after reorder/delete/restore.

Success criteria:

- Rerender output matches active transcript page order.
- Deleted pages do not render.
- Restored pages render in their restored position.

### Phase 5C4: Docs And Manual QA

Scope:

- Update `docs/UNFINISHED_WORK.md`.
- Add README or Studio workflow notes if needed.
- Record manual QA checklist and known limitations.

Success criteria:

- Phase 5C status is documented.
- Remaining richer visual scene editor and multilingual subtitles stay future work.

## Final Implementation Prompt For Phase 5C1

Use this prompt when ready to implement backend-first Phase 5C1:

```text
You are working in my AI_ACADEMY repo.

Task:
Implement Phase 5C1 only: backend transcript page action endpoint and tests.

Use docs/STUDIO_PHASE_5C_PAGE_CONTROLS_PLAN.md as the source of truth.

Do not implement frontend controls yet.
Do not touch TTS, storage, Docker, avatar, DRM, HLS, auth, dictionary/LLM, or multilingual subtitles.
Do not create duplicate projects during any action.

Implement:
1. Add a small TranscriptPage soft-delete migration with is_active and deleted_at.
2. Add POST /api/v1/projects/<project_id>/transcript/actions/.
3. Support actions: split_page, merge_with_next, merge_with_previous, reorder_pages, delete_page, restore_page.
4. Wrap each action in transaction.atomic().
5. Preserve original_text unless deliberately combining source references during merge.
6. Keep narration_text as the render/caption source.
7. Keep page_key stable for existing rows and generate unique stable keys for split rows.
8. Return project_id, action, pages, deleted_pages when useful, changed_page_keys, and optional rerender_job.
9. Keep existing transcript PATCH text-edit behavior stable for Phase 5A/5B.

Tests:
Add/extend integration tests covering split, merge, reorder, delete, restore, permissions, atomic failure, rerender same-project behavior, changed_page_keys, and no duplicate project creation.

Run:
.\.venv\Scripts\python.exe -m pytest tests/integration/test_transcript_editor_pipeline.py -q
.\.venv\Scripts\python.exe -m py_compile services/api/core/views.py services/api/core/models.py services/api/core/serializers.py

Commit:
git add services/api/core/models.py services/api/core/views.py services/api/core/serializers.py services/api/core/urls.py services/api/core/migrations tests/integration/test_transcript_editor_pipeline.py
git commit -m "Add transcript page action endpoint"

Final response:
- files changed
- migration created
- actions implemented
- tests run/results
- whether Phase 5C2 frontend controls can begin
```
