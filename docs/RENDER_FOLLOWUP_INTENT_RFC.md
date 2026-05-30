# RFC: RenderFollowUpIntent

## Status

Accepted and implemented through #61-#65. This document records the intended behavior and the policy choices in the merged implementation.

## Problem

Transcript edits and transcript-driven actions can arrive while a project already has an active render job. Today, that creates an ambiguous ownership boundary between the active render and the newly requested work:

- A targeted rerender intent can conflict with a later full rerender intent.
- Multiple targeted requests can create duplicate jobs instead of one unioned follow-up.
- Finalize logic can become stale if an older job completes after newer transcript state exists.
- Snapshot consistency can be weakened if the render job and follow-up request do not agree on the transcript version or affected page set.

The desired behavior is not to interrupt the active render. Instead, the system should remember that more work is required and drain that intent after the active job reaches a terminal finalize path.

## Current State

Recent worker reliability work reduced several adjacent failure modes and now models follow-up render intent as first-class state:

- #61 documented the RenderFollowUpIntent design.
- #62 added the `RenderFollowUpIntent` model, constraint, and model tests.
- #63 added the merge helper and merge semantics.
- #64 records follow-up intent from transcript endpoints when a render is already active.
- #65 drains pending intent after successful current finalize and dispatches the follow-up render job.
- #57 worker reliability improved render worker behavior and reduced operational fragility.
- #58 added a job-scoped finalize guard so stale jobs cannot finalize unrelated active work.
- #59 added manual active render dedupe to avoid obvious duplicate render dispatch.
- #60 improved caption snapshot consistency so render inputs are bound to a coherent transcript snapshot.

Together these changes provide durable, mergeable intent that survives concurrent edits and is drained after a successful current render finalize.

## Goals

- Do not lose new transcript changes while an active job continues.
- Avoid unnecessary duplicate render jobs.
- Allow full rerender intent to override targeted rerender intent.
- Merge targeted intents by unioning their `page_keys`.
- Keep draft render intent separate from active render intent.
- Preserve snapshot consistency by dispatching follow-up work from a clear intent state.

## Non-Goals

- Do not interrupt or cancel an active render when follow-up intent is recorded.
- Do not dispatch follow-up renders after failed or stale finalize paths.
- Do not change frontend presentation in this policy update.

## Proposed Model

The merged implementation introduces a durable `RenderFollowUpIntent` model.

Fields:

- `project`: the project that owns the follow-up render intent.
- `mode`: either `targeted` or `full`.
- `page_keys`: the affected page keys for targeted rerenders. Empty or null when `mode = full`.
- `status`: one of `pending`, `claimed`, `dispatched`, or `cleared`.
- `reason`: short machine-readable reason for the intent.
- `requested_by`: nullable user or system actor that requested the intent.
- `created_at`: creation timestamp.
- `updated_at`: last mutation timestamp.
- `claimed_at`: timestamp set when a drain attempt claims the intent.
- `metadata`: JSON payload for diagnostics, source event details, counters, or future compatibility.

The model is constrained so each project has at most one active follow-up intent. Active statuses are `pending`, `claimed`, and `dispatched`. Terminal statuses are `cleared` and `cancelled`.

## Merge Rules

Follow-up intent merge must be deterministic and idempotent:

- `targeted + targeted = targeted` with `page_keys` as the set union.
- `full + targeted = full`; the existing full intent is unchanged except metadata may record the extra request.
- `targeted + full = full`; the targeted page set is discarded because full rerender covers it.
- Structural actions always create or upgrade to `full`.
- Draft render intent must not merge with active render intent.

Merge should happen transactionally. If two transcript edits arrive concurrently, both must either produce a single unioned pending intent or upgrade that intent to full.

## Drain Flow

When an active render successfully reaches current finalize:

1. The job-scoped finalize guard verifies that the finalizing job still owns the active render state.
2. Finalize transitions the active render job to `done`.
3. The drain step checks for a `pending` follow-up intent for the same project and render context.
4. If one exists, the drain step claims it by moving `pending -> claimed` and setting `claimed_at`.
5. The drain step creates a new `video_export` job in the same transaction.
6. Celery dispatch runs in a transaction `on_commit` callback and passes explicit `job_id` to the worker task.
7. After dispatch succeeds, dispatch metadata is recorded and the intent moves to `cleared`.

The `claimed` and `dispatched` states prevent multiple finalize paths or workers from dispatching duplicate jobs from the same pending intent.

## Failure Policy

The merged implementation uses success-only drain. Follow-up dispatch is attempted only after a successful current finalize.

Current policy:

- Successful current finalize drains and dispatches pending follow-up intent.
- Failed finalize does not dispatch follow-up intent.
- Stale finalize does not dispatch follow-up intent.
- Dispatch failure marks the created follow-up job failed and cancels the claimed intent with failure metadata.

This policy reduces render storm risk and avoids automatic loops when a project has a transient or non-transient render failure. Manual retry or a later user edit can create or merge fresh intent and is safer than automatically dispatching more render work from a failed active job.

## Race Condition Scenarios

### Two Concurrent Transcript Edits

Both edits attempt to create or merge a pending targeted intent. The expected result is one pending targeted intent whose `page_keys` contain the union of both edits.

### Finalize and Edit at the Same Time

Finalize may claim an existing pending intent while a new edit arrives. The merged implementation clears successfully dispatched intent quickly, allowing a later edit during the follow-up render to create a new pending intent for the next drain cycle. There is still a theoretical short `claimed -> cleared` window where the unique active-intent constraint prevents a second pending intent from being created. Fully closing that window would require a constraint or status-model migration and is out of scope for #61-#65.

### Full and Targeted Collision

If a full intent and targeted intent race, the final durable state must be full. Targeted page keys become advisory metadata at most.

### Worker Crash

The worker registers dispatch in a transaction `on_commit` callback after the claim and job creation commit. If dispatch fails, the follow-up job is marked failed and the intent is cancelled with failure metadata. A process crash immediately after commit but before the callback executes remains a theoretical orphan risk; a future recovery job could scan stale `claimed_at` rows and pending follow-up jobs.

### Stale Finalize

A stale finalize must not drain or clear follow-up intent for a newer active job. The existing job-scoped finalize guard remains the authority for whether a finalize path may mutate active render state.

## PR Breakdown

### PR-1: Model, Migration, and Unit Tests (#62)

- Add `RenderFollowUpIntent`.
- Add constraints for one active intent per project and render context.
- Add status and mode choices.
- Add tests for model validation and constraints.

### PR-2: API Intent Merge Helper (#63)

- Add a service/helper that creates or merges follow-up intent when edits arrive during an active render.
- Implement targeted union and full override semantics.
- Keep draft render intent separate from active render intent.

### PR-3: Transcript Intent Write (#64)

- Record follow-up intent when transcript endpoints request render work while a render is active.
- Store active job and render request metadata for later drain.
- Do not dispatch duplicate render work from the API path.

### PR-4: Finalize Drain (#65)

- Add drain logic after successful job-scoped finalize.
- Claim pending intent before dispatch.
- Dispatch with explicit `job_id` after transaction commit.
- Move intent to `cleared` after successful handoff.
- Do not dispatch after failed or stale finalize paths.

### PR-5: Race Condition Tests (#64-#65)

- Cover concurrent targeted edits.
- Cover finalize racing with edit.
- Cover full and targeted collision.
- Cover dispatch failure handling.
- Cover stale finalize behavior.

### Future: Observability, Recovery, and Documentation

- Add structured logging for intent create, merge, claim, dispatch, clear, and recovery.
- Add metrics for pending intent count, claim age, dispatch count, and poison-loop guard trips.
- Update operations docs with recovery and cleanup guidance.

## Risks

- Migration risk: constraints and backfill behavior need careful rollout.
- Render storm risk: poor merge or drain behavior could dispatch too many jobs.
- Infinite follow-up loop risk: repeated failures could keep creating new work.
- Stale intent cleanup: old claimed intents and pending follow-up jobs need a repair or cleanup policy.
- Short claim window: `claimed -> cleared` is brief but not fully eliminated without changing the active-status constraint.
- UX expectation risk: users may expect edits during active render to appear immediately, while this design queues them for follow-up render.

## Test Plan

- Targeted union: two targeted edits create one intent with unioned `page_keys`.
- Full override: full intent overrides existing targeted intent.
- Active render while edit: edit during active render creates pending follow-up intent and does not dispatch duplicate active work.
- Failed active job: failed finalize does not dispatch follow-up intent automatically.
- Stale finalize: stale finalize does not dispatch follow-up intent.
- Finalize drain: active job finalize claims pending intent and dispatches one new job.
- Concurrent edits: simultaneous edits do not drop page keys or create duplicate pending intents.
- Structural action full intent: structural transcript action creates or upgrades to full intent.

## Decision

Implemented in #61-#65 with success-only drain. Future work should focus on observability, stale-claim recovery, and any migration needed to fully remove the short `claimed -> cleared` merge window.
