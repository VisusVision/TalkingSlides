# Phase 3B-3 — Immutable private Runs and live control gates

Starting branch: `feature/ai-visual-designer`; clean local and remote HEAD verified
at `7e0bce3eeb88d8c2bc332b8d22dbf675ae970d17`. This implements persistence and
control-plane engineering only. No Celery task, broker call, HTTP Run route,
producer service, media compositor, subtitle writer or Studio control was added.

## Records and isolation

Migration `0042_private_visual_segment_controls` adds:

* Job purpose **`visual_segment_preview`**. The ordinary Job status remains the
  only pending/running/failed lifecycle; Run does not duplicate it. Database checks
  forbid private public-result URLs and `done` in this stage. Generic creation,
  repurposing and updates cannot write private Jobs; the transactional controller
  uses a module-private capability. Celery task IDs on Jobs stay blank.
* **VisualSegmentRun**: UUID; owner/project; immutable snapshot/hash/revision;
  stable page; candidate/proposal/confirmation FKs; complete immutable one-page
  request/execution hash; exact private input pins and native audio verification;
  one-to-one Job; unique `(project, owner, execution_hash)`; persistent future
  dispatch UUID and pending intent; monotonic generation, token, lease, cancellation
  and failure metadata. Receipt fields are reserved and database-constrained empty.
* **VisualEvidenceRevocation**: append-only, one per original review; exact
  source/revision/proposal/confirmation identity, owner/project, reason and time.
  Its BigAutoField is a monotonic control identity. The original confirmation is
  retained unchanged. Restrictive foreign keys prevent deleting a confirmation or
  proposal to cascade away its revocation (owner/project lifecycle cleanup is
  separate). Retrying confirmation cannot remove a revocation.

Run/revocation normal saves, QuerySet updates, bulk updates, bulk creates and direct
history deletion are rejected. Controlled updates allow operational fields only;
request/snapshot/owner/page/design/pin identities cannot be overwritten. Model
validation and every read/claim boundary recheck scope/hashes. Raw SQL/database
administrator forgery of all records is outside this application trust boundary;
negative tests deliberately corrupt individual SQL fields to prove detection.
Project/owner lifecycle deletion cleans up private companion Jobs, without deleting
ordinary export Jobs through the private controller.

`JobViewSet`, project Job status and public media Job lookup exclude the private
purpose, including for staff. The generic Job serializer refuses it. Subtitle
translation excludes private Jobs. Standard notifications and render-follow-up
claims refuse private Job identities. Existing playable/latest/active render,
recovery, observability and catalog selectors already restrict to `video_export`.
Storage inventory/retention sees no private media locator in a Job: its generic
result fields remain empty; private pins are not public storage references.

## Trusted construction and reservation

`core/visual_segment_runs.py::reserve_visual_segment` accepts only an owned
snapshot ID, expected snapshot/revision hashes, stable page key and producer receipt
UUID. It never accepts caller-authored request JSON, SVG, narration, asset paths,
runtime settings or a trust digest. Pure Phase 3A/3B-1 contracts remain unchanged.

**Production producer dependencies are unconfigured.** `_producer_dependencies`
returns `None`, so ordinary reservation fails as `audio_provenance_unavailable`
without creating any Job. There is no environment switch, public argument or route
that enables synthetic attestations. The strict production proof/pin functions
never opt in to synthetic receipts. A production receipt integration requires
separate authorization and independently authenticated records.

Preparation outside row locks:

1. Read the owned snapshot through the existing historical integrity boundary.
2. Resolve an independently authenticated proof from server-controlled producer
   dependencies; bind verbatim narration, saved voice/TTS and source/split mapping.
3. Construct the unchanged SegmentRequest from that snapshot, proof and an
   independently supported server recipe/runtime. Initial hold is a fixed 200 ms.
4. Require saved, exact millisecond baseline caption cues whose hash is attested
   by the proof. Ordinary Studio drafts often omit timing; these return
   `captions_unavailable`, never borrowing incompatible active timing. No allocator
   or subtitle-file generation is introduced in this control stage.
5. Use Phase 3B-2's real byte/native validation and private pinning, preserving
   exact audio. Independently rehash required visual/font/audio/supporting pins.
6. Capture the current saved revision with the existing snapshot adapter and
   require exact snapshot equality. Bound/hash relevant database state before and
   after capture. Hold verified descriptors and observe source-file identity.

A short transaction then takes the shared lock order:

**owner User (PostgreSQL NO KEY UPDATE) → Project → pages → selections → voice /
profile → proposal/review → Jobs → Runs.**

The owner lock also serializes cross-project quotas. NO KEY UPDATE allows ordinary
foreign-key key-share checks, avoiding a cycle with existing Project-first writers.
Rows lock in primary-key order. Source/control sets have a 1,024-row bound and saved
state an 8 MiB bound. Database state, persisted snapshot, selection/approval,
revocation and producer control identity are rechecked. Open private descriptors
and fresh no-follow namespace resolution must still identify the same files; source
asset resolution/stat identity is rechecked too. No native command or full media
scan runs while those locks are held.

Only then create the Job and Run atomically, or retrieve the exact unique existing
execution. Same-hash content disagreement fails rather than overwriting it. A
rollback leaves neither record. Exact retries do not get new random execution
identities. Cancelled, revoked or failed historical Runs cannot be resurrected.
Limits: four pending/running private reservations per project, eight per owner,
and one live private claim per owner across projects. Normal video Jobs are not
included or changed.

Filesystem mutation and database commit are not a distributed transaction.
Descriptor/inode/stat/hash checks detect observed replacement during the operation;
pinned bytes are the eventual inputs. Same-UID/administrator tampering remains
possible, so later execution must verify bytes again. No exact-current-revision
publication guarantee or audit of every legacy source writer is claimed.

## Claims, fencing and cancellation

`check_run_executable`, `claim_attempt`, `release_claim`, `expire_claim`,
`cancel_run` and `record_terminal_failure` use the same locks and purpose checks.
Claims revalidate the saved revision, immutable request, independent producer,
approval/revocation and private pins before granting a 30–300 second lease.
Takeover requires the expected current generation and an expired lease. Every
new claim increases generation and receives a new token; release/expiry,
cancellation, revocation and terminal failure also fence prior generations.
No process-local mutex is used for database concurrency.

SQLite does not implement PostgreSQL row locks. Sequential SQLite tests verify
validation/rollback/uniqueness, but do not establish concurrent claim fencing. Its
write-contention errors require an explicit retry; real simultaneous reservation,
source/restore, claim/takeover, cancellation and revocation proofs run on PostgreSQL.
CI requires native media tooling for that job, so those races cannot silently skip
because input verification tooling is missing.

A claim's future handoff contains only `{run_id, execution_hash,
attempt_generation}`. It does not dispatch anything. A future worker must retrieve
the database request, never trust broker-supplied narration/paths/recipe overrides.
The dispatch UUID is durable; no reconciliation, broker retry or on-commit send is
implemented. A pending reservation is not an active renderer. Claim tests simulate
work; they do not run a worker task. Every public decision/claim keeps
`execution_enabled=false`.

A strict current-revision policy is chosen for this stage: ordinary source or
selection edits leave A's immutable historical records readable, but prevent a new
claim or acceptance precondition against stale A. No input can be replaced by B.
This is deliberately more conservative than the design's optional historical
completion policy. Cancellation remains available even if media is missing.
Ownership loss/inactive owner/project deletion denies further operations.

Cancellation records its first timestamp/reason, fences work, clears readiness and
marks only the private Job failed. Exact repeated cancellation is idempotent.
Terminal failure requires a current unexpired generation/token. Neither operation
can approve a result or alter existing playback.

## Revocation and future acceptance

`revoke_confirmation` checks an owned confirmed review and expected confirmation
hash, records a permanent revocation, and fences/cancels affected Runs under the
same Project/control lock. Unrelated deterministic source-backed Runs remain
eligible. Reservation, claim, acceptance preconditions and the reusable private
access policy consult current revocation. Historical frozen approvals do not
restore permission. Two revocations converge on one record.

`prepare_receipt_acceptance` verifies current claim/control/input preconditions,
then returns **`media_validation_unavailable`**. It accepts no caller receipt,
"trusted" flag or synthetic completion bypass. No successful receipt can be
persisted; database checks also prevent it. Tests race this actual precondition
gate against cancellation/revocation, not an unverified successful media callback.
`private_result_access_policy` always denies delivery: terminal/revoked/cancelled
reasons first, otherwise `completed_media_unavailable`. Future output validation,
receipt persistence and private serving must extend these boundaries under separate
authorization, not treat a positive control eligibility report as media acceptance.

## Engineering fixtures

Tests reuse the existing intelligible `narration.m4a`/WAV bytes and Phase 3B-1
producer/page mappings `[0,2,0]` / `[1,0,0]`. Original receipts, anchors and contract
golden hashes are unchanged. `control_trust_anchors.json` explicitly documents an
additional **synthetic control-plane test association** with the canonical neutral
saved settings emitted by real snapshot capture (rather than the pure fixture's
empty settings object). Its expected proof hashes are committed constants,
independent of supplied requests. This is an engineering assumption using known
speech, not historical/production producer certification or another speech fixture.
Only test-local monkeypatches enable it; actual Phase 3B-2 native validation/pinning
still runs. Production rejects these attestations.

## Executed verification

* Final Run + secure-playback regression: **100 passed, 9 skipped** (35.12 s).
  The skips are PostgreSQL-only races, not ignored failures.
* Standalone Phase 3B-1 pure contracts: **216 passed** (1.18 s), without Django,
  database or native tooling. Existing golden hashes remain unchanged.
* Final full local backend: **2,692 passed, 1 failed, 21 skipped, 63 subtests passed**
  (138.82 s). Sole failure:
  `test_musetalk_warmup_cleanup.py::test_orphan_check_detects_and_kills_marked_warmup_process`.
  The unchanged test observes process-group ID zero in this sandbox and cannot
  kill its marked process group. It is reported, not patched or suppressed.
* Django check: no issues. Migration consistency dry run: no changes detected.
* `npm ci`, all **350 frontend tests** (49 files), and `npm run build`: passed.
  The existing HLS chunk-size warning remains; no dependency files changed.
* `npm audit --audit-level=high` and
  `npm audit --omit=dev --audit-level=moderate`: **0 vulnerabilities**.
* `pip-audit -r services/api/requirements.txt`: **No known vulnerabilities found**.

Final focused Run/artifact/snapshot/contract/private-draft/evidence/promotion/
scoped-render/follow-up suite: **483 passed, 11 skipped** (47.77 s). These include
nine new PostgreSQL races plus the two existing PostgreSQL-only source/selection
checks, all scheduled in the hosted PostgreSQL gate.

Hosted full backend, real PostgreSQL races, Chromium and Docker results for the
committed revision are reported in the completion message and
[feature-branch CI](https://github.com/VisusVision/TalkingSlides/actions?query=branch%3Afeature%2Fai-visual-designer).
Local PostgreSQL/Docker are unavailable. Native fixture audio is validated, but no
new narrated MP4, subtitle output, producer service or media-execution acceptance
is claimed. Unrelated MuseTalk process-control failures are never patched/skipped.

## Prerequisites for separately authorized 3B-4 and later

* A real independently authenticated producer source before real-lesson use;
  fixture availability does not enable production reservation.
* Provision and test persistent private roots, image imports, same UID/permissions,
  cross-container mount visibility and retention/quota handling. Docker build smoke
  is not private-volume deployment acceptance.
* Reuse the established scene/audio/caption helpers only in the separately
  authorized media stage. Validate current runtime, pins, output streams, durations
  and packet integrity; no TTS fallback. Add trusted output-validation witnesses
  before relaxing the intentionally closed receipt/Job constraints.
* Preserve the same lock/fence/revocation checks through output acceptance and
  every future private GET/HEAD/Range. A future completion transaction must enforce
  generation/token/lease/owner/purpose and live controls again.
* Later dispatch/reconciliation, retry policy, Run endpoints and Studio controls
  require separate stages. Legacy index-zero fixes, full-lesson assembly,
  Save & Rerender and authoritative publication remain outside this implementation.

Stop after 3B-3. No production execution path is activated.
