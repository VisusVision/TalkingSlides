# RFC: RenderFollowUpIntent

## Status

Draft for review. This document is intentionally documentation-only and should not be treated as approval to implement model, migration, task, view, frontend, or Celery behavior changes.

## Problem

Transcript edits and transcript-driven actions can arrive while a project already has an active render job. Today, that creates an ambiguous ownership boundary between the active render and the newly requested work:

- A targeted rerender intent can conflict with a later full rerender intent.
- Multiple targeted requests can create duplicate jobs instead of one unioned follow-up.
- Finalize logic can become stale if an older job completes after newer transcript state exists.
- Snapshot consistency can be weakened if the render job and follow-up request do not agree on the transcript version or affected page set.

The desired behavior is not to interrupt the active render. Instead, the system should remember that more work is required and drain that intent after the active job reaches a terminal finalize path.

## Current State

Recent worker reliability work reduced several adjacent failure modes but does not fully model follow-up render intent as first-class state:

- #57 worker reliability improved render worker behavior and reduced operational fragility.
- #58 added a job-scoped finalize guard so stale jobs cannot finalize unrelated active work.
- #59 added manual active render dedupe to avoid obvious duplicate render dispatch.
- #60 improved caption snapshot consistency so render inputs are bound to a coherent transcript snapshot.

These changes form the foundation for follow-up intent handling. The missing piece is durable, mergeable intent that survives concurrent edits, active render completion, and worker failure.

## Goals

- Do not lose new transcript changes while an active job continues.
- Avoid unnecessary duplicate render jobs.
- Allow full rerender intent to override targeted rerender intent.
- Merge targeted intents by unioning their `page_keys`.
- Keep draft render intent separate from active render intent.
- Preserve snapshot consistency by dispatching follow-up work from a clear intent state.

## Non-Goals

- Do not implement the model in this RFC.
- Do not create a migration in this RFC.
- Do not change render task, view, frontend, or Celery behavior in this RFC.
- Do not decide UX copy or frontend state presentation in this RFC.

## Proposed Model

Introduce a durable `RenderFollowUpIntent` model in a later implementation PR.

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

The model should be constrained so each project has at most one active follow-up intent per render context. If draft rendering and active rendering share the same table, the context must be explicit so draft render intent cannot merge with active render intent.

## Merge Rules

Follow-up intent merge must be deterministic and idempotent:

- `targeted + targeted = targeted` with `page_keys` as the set union.
- `full + targeted = full`; the existing full intent is unchanged except metadata may record the extra request.
- `targeted + full = full`; the targeted page set is discarded because full rerender covers it.
- Structural actions always create or upgrade to `full`.
- Draft render intent must not merge with active render intent.

Merge should happen transactionally. If two transcript edits arrive concurrently, both must either produce a single unioned pending intent or upgrade that intent to full.

## Drain Flow

When an active render reaches finalize:

1. The job-scoped finalize guard verifies that the finalizing job still owns the active render state.
2. Finalize transitions the active render to its terminal state.
3. The drain step checks for a `pending` follow-up intent for the same project and render context.
4. If one exists, the drain step claims it by moving `pending -> claimed` and setting `claimed_at`.
5. The drain step dispatches a new render job from the claimed intent.
6. After dispatch succeeds, the intent moves `claimed -> dispatched`.
7. Once the new job is accepted as the active render, the intent can move to `cleared` or be deleted, depending on retention needs.

The `claimed` and `dispatched` states prevent multiple finalize paths or workers from dispatching duplicate jobs from the same pending intent.

## Failure Policy

If the active job fails, the follow-up intent should be preserved and still be eligible for dispatch. A failed active render does not mean the transcript edits are obsolete.

Recommended policy:

- Preserve the pending follow-up intent when the active job fails.
- Attempt to dispatch the follow-up after the failed job reaches a terminal state.
- Record failure and retry metadata on the intent or associated render job.
- Add a poison-loop guard so a permanently failing project cannot dispatch infinite follow-up renders.

The poison-loop guard can be based on consecutive follow-up dispatch failures, recent dispatch count within a time window, or repeated failure signatures in `metadata`.

## Race Condition Scenarios

### Two Concurrent Transcript Edits

Both edits attempt to create or merge a pending targeted intent. The expected result is one pending targeted intent whose `page_keys` contain the union of both edits.

### Finalize and Edit at the Same Time

Finalize may claim an existing pending intent while a new edit arrives. The edit must not be lost. If the claimed intent can no longer be merged safely, the edit should create a new pending intent for the next drain cycle.

### Full and Targeted Collision

If a full intent and targeted intent race, the final durable state must be full. Targeted page keys become advisory metadata at most.

### Worker Crash

If a worker crashes after `claimed` but before dispatch, the intent needs a recovery path. A stale `claimed_at` timeout can return the intent to `pending` or allow a repair job to redispatch it.

### Stale Finalize

A stale finalize must not drain or clear follow-up intent for a newer active job. The existing job-scoped finalize guard from #58 should remain the authority for whether a finalize path may mutate active render state.

## PR Breakdown

### PR-1: Model, Migration, and Unit Tests

- Add `RenderFollowUpIntent`.
- Add constraints for one active intent per project and render context.
- Add status and mode choices.
- Add tests for model validation and constraints.

### PR-2: API Intent Merge Helper

- Add a service/helper that creates or merges follow-up intent when edits arrive during an active render.
- Implement targeted union and full override semantics.
- Keep draft render intent separate from active render intent.

### PR-3: Finalize Drain

- Add drain logic after successful job-scoped finalize.
- Claim pending intent before dispatch.
- Move intent to dispatched or cleared after successful handoff.

### PR-4: Race Condition Tests

- Cover concurrent targeted edits.
- Cover finalize racing with edit.
- Cover full and targeted collision.
- Cover worker crash recovery behavior.
- Cover stale finalize behavior.

### PR-5: Observability and Documentation

- Add structured logging for intent create, merge, claim, dispatch, clear, and recovery.
- Add metrics for pending intent count, claim age, dispatch count, and poison-loop guard trips.
- Update operations docs with recovery and cleanup guidance.

## Risks

- Migration risk: constraints and backfill behavior need careful rollout.
- Render storm risk: poor merge or drain behavior could dispatch too many jobs.
- Infinite follow-up loop risk: repeated failures could keep creating new work.
- Stale intent cleanup: old claimed or dispatched intents need a repair or cleanup policy.
- UX expectation risk: users may expect edits during active render to appear immediately, while this design queues them for follow-up render.

## Test Plan

- Targeted union: two targeted edits create one intent with unioned `page_keys`.
- Full override: full intent overrides existing targeted intent.
- Active render while edit: edit during active render creates pending follow-up intent and does not dispatch duplicate active work.
- Failed active job: pending follow-up intent is preserved and dispatches after terminal failure handling.
- Finalize drain: active job finalize claims pending intent and dispatches one new job.
- Concurrent edits: simultaneous edits do not drop page keys or create duplicate pending intents.
- Structural action full intent: structural transcript action creates or upgrades to full intent.

## Decision

Do not implement this change directly from the current task. The next step is RFC review. Implementation should proceed only after the model, merge semantics, drain flow, and failure policy are accepted.
