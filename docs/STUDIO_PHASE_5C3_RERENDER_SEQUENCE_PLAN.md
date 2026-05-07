# Studio Phase 5C3 Rerender Sequence Plan

## Summary

Phase 5C3 polishes the rerender contract for structural transcript actions. Split, merge, reorder, delete, and restore can change sequence membership or order, so they must continue to use full same-project rerender when rerender is requested.

The key invariant is:

- Structural actions never pass `changed_page_keys` as targeted render input.
- Structural action rerenders pass `rerender_page_keys=[]`.
- `changed_page_keys` remains UI/status metadata only.
- The worker render sequence is rebuilt from active `TranscriptPage` rows ordered by `order, id`.
- Inactive or deleted transcript pages are skipped.

## Current Behavior

The transcript action endpoint supports `trigger_rerender` for all structural actions. When requested, it queues `worker.tasks.process_pptx_to_video` for the same existing project through `_queue_transcript_rerender(..., full_rerender=True)`.

With `full_rerender=True`, the backend sends an empty `rerender_page_keys` list. The worker treats an empty target set as full render and uses `concat_and_finalize`, not targeted segment replacement.

The worker sync path already rebuilds render payloads from persisted transcript rows after refreshing source export metadata. This is the correct basis for structural rerender because the database transcript timeline, not raw export order, is the source of truth after page actions.

## Risks Addressed

- Reorder could leave old media sequence in place if treated as targeted rerender.
- Delete could leak a stale segment into final output if inactive pages are not filtered.
- Restore could be omitted if the render sequence only followed fresh export rows.
- Split pages can have new page keys that are not present in raw export data.
- Merge soft-deletes one row and must not keep rendering the merged-away page.
- Job status can report completion while final output is sequence-stale if final concat does not use active transcript order.

## Required Implementation

1. Add additive `rerender_strategy` response metadata to transcript action responses:
   - `"full"` when a structural action queues a rerender job.
   - `"none"` when no rerender job is queued.
2. Preserve full-rerender behavior for all structural actions.
3. Keep `changed_page_keys` as metadata only for structural actions.
4. Document worker sequencing near `_sync_transcript_pages_from_export`.
5. Add integration tests proving action-triggered rerenders use the same project, pass saved TTS settings, pass `rerender_page_keys=[]`, and build render sequences from active transcript order.

## Test Plan

Backend tests should cover:

- Structural action with rerender returns `rerender_strategy="full"` and queues a job.
- Structural action without rerender returns `rerender_strategy="none"` and no job.
- Action rerender uses the same project and creates no duplicate project.
- Action rerender passes saved canonical project TTS settings unchanged.
- Celery args for action rerender include `rerender_page_keys == []`.
- Worker sync respects reordered active transcript order.
- Worker sync excludes inactive/deleted pages.
- Worker sync includes split-created pages using source slide templates where needed.
- Worker sync excludes merged-away soft-deleted pages.
- Worker sync includes restored pages at the expected active position.

## Manual QA

After implementation, manually verify in Studio:

- Split a page and rerender; final video includes both active split pages in order.
- Merge pages and rerender; final video excludes the soft-deleted merged page.
- Delete a page and rerender; final video excludes the deleted scene.
- Restore a page and rerender; final video includes it at the restored position.
- Move a page up/down and rerender; video, transcript, and timeline order match.

## Future Prompt

```text
Implement Phase 5C3 only: structural transcript action rerender sequencing polish.

Add rerender_strategy metadata to transcript action responses, preserve full same-project rerender for structural actions, keep changed_page_keys as UI metadata only, document worker active-order sequencing, and add integration tests for split, merge, delete, restore, and reorder rerender behavior.

Do not change frontend controls, TTS, storage, Docker, avatar, DRM, HLS, auth, dictionary/LLM logic, multilingual subtitles, or drag-and-drop.
```
