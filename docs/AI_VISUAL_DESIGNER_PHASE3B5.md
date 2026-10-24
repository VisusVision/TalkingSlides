# Phase 3B-5 — Owner-private API, controlled dispatch and media delivery

Verified baseline: `17d8a2f4b2855d131ae1cbfc7a86d34ac6319317` on
`feature/ai-visual-designer`, initially clean. This stage exposes the existing
private one-page segment machinery. No Studio, normal Save & Rerender, ordinary
render/finalizer, TTS, moderation, playback or publication execution changes.
Phase 3A snapshot and 3B-1 request/audio/receipt schemas and hashes are unchanged.

## Availability and trust

Production remains **disabled**. `visual_segment_runs._producer_dependencies()`
still returns `None`; ordinary reservation returns HTTP 503 with
`audio_provenance_unavailable`, without creating a Job. No legacy audio receipt is
invented. Separately, `visual_segment_dispatch._deployment_ready()` returns
`False`, without an environment/settings/API override. Even a future producer
integration cannot execute until the separately authorized deployment stage
implements a verified server readiness boundary. Tests replace these private
dependencies only in their own processes; they do not enable deployed behavior.

API, dispatch, worker and delivery all enforce these gates. Existing configured
private storage, supported native runtime, current producer authority and pinned
bytes are still validated by the established Run/media services. Gate failure
does not authorize synthesis, another provider, an original-scene substitution
or public playback.

## Components

| File | Responsibility |
|---|---|
| `core/visual_segment_views.py` | Owner-only JSON reservation, bounded list/detail, cancellation and fixed media roles |
| `core/visual_segment_dispatch.py` | Persist send intent, post-commit broker send, conditional acknowledgement and bounded reconciliation |
| `core/management/commands/reconcile_visual_segments.py` | Explicit operator replay; no scheduler |
| `worker/visual_segment_celery.py` | Identity-only Celery adapter, atomic server claim, existing private media executor |
| `core/visual_segment_delivery.py` | Verified private descriptors, ranges, bounded streaming authorization |
| Migration `0044_private_segment_dispatch` | Four operational dispatch fields and consistency/budget constraints |

`VisualSegmentRun` gains `dispatch_generation`, `dispatch_attempts`,
`dispatch_next_at` and `dispatch_error`. They are controlled operational fields;
immutable requests, execution identities and accepted receipts cannot be changed
through these updates. Constraints bound sends to three per generation, require
a consistent persisted intent, and prohibit a future dispatch generation. The
existing generic private Job `celery_task_id`, `result_url` and `srt_url` stay
blank. Its distinct persisted `Run.dispatch_task_id` is the broker identity.

## Owner API

Routes are below `/api/v1/projects/{project_id}/visual-segments/`:

| Method and suffix | Behavior |
|---|---|
| `GET /` | Owner's Runs; optional `page_key`, `limit` (1–20, default 10), `before` (owner-scoped Run UUID cursor) |
| `POST /` | Reserve the exact saved snapshot/page using the existing reservation service; register send after transaction commit |
| `GET /{run_id}/` | Validated owner metadata, current/historical status and gated availability |
| `POST /{run_id}/cancel/` | Existing transactional cancellation; body contains only explicit `reason` |
| `GET`, `HEAD /{run_id}/media/{role}/` | Authorized accepted result, where role is exactly `mp4`, `srt` or `vtt` |

Reservation accepts exactly six string fields:

```json
{
  "snapshot_id": "<UUID>",
  "expected_snapshot_hash": "sha256:<64 lowercase hex>",
  "expected_revision_hash": "sha256:<64 lowercase hex>",
  "page_key": "<stable page key>",
  "producer_receipt_id": "<UUID>"
}
```

Unknown, missing or incorrectly typed writable fields are rejected. No browser
SVG, audio/path, request JSON, trust digest, claim token, generation, synthesis
settings, receipt or publication flag is accepted. Creation returns 201; an exact
idempotent reservation returns 200. Source/integrity conflicts return 409,
unavailable production prerequisites 503, revoked/cancelled access 403 and
foreign project/Run lookups 404. Authentication uses existing DRF conventions;
staff receives no private-owner override.

`visual-segment-status.v1` exposes IDs, page/snapshot/revision/execution hashes,
status, current/historical indicators, machine reasons, gated result availability,
supported roles and `publishing_allowed=false`. No request payload, receipt body,
private path, pin locator, producer credentials or claim token is serialized.
Listing uses a stable creation-time/UUID cursor and a 20-row maximum. The API
uses the existing JSON-only authenticated view/security headers and a 20/minute
per-user throttle. Availability is observational; it is not a lease or authority
to execute or deliver without subsequent checks.

## Dispatch protocol and recovery

1. Existing reservation verifies server-owned snapshot, page, selection,
   approval, independent producer proof, runtime and pins. It deduplicates by
   `(project, owner, execution_hash)` and persists one private Run/Job/task UUID.
2. `transaction.on_commit` calls the controlled dispatcher. A short existing
   owner → Project → source/control → Job → Run transaction rechecks execution
   authority and stores generation, send count, cooldown and `send_unconfirmed`.
3. Outside database locks, the existing API Celery send-only client sends to the
   existing render queue, with bounded transport connection timeouts and no
   broker retry loop. Payload contains only:

   ```json
   {"run_id":"<UUID>","execution_hash":"sha256:<digest>","attempt_generation":0}
   ```

4. Conditional acknowledgement updates only the same still-pending generation.
   A successful send does **not** clear pending dispatch: only a database claim
   does. Commit-before-send, send-before-ack and duplicate-send failures retain
   replayable intent and the same task identity. Broker errors expose a machine
   reason, not transport exception text/credentials.
5. `worker.visual_segments.generate_private_segment` loads the exact persisted
   Run, validates purpose/hash/task identity/send intent and gates, then atomically
   claims the expected generation. The server creates the token; the task invokes
   the unchanged Phase 3B-4 executor with that token and its new generation.
   No latest-Job fallback, browser token or broker content becomes render input.

The task uses `acks_late`, worker-loss rejection, no Celery automatic retries,
240/260-second soft/hard limits and a 300-second bounded claim. Active duplicates
cannot obtain another token. Completed duplicates recheck authorization and
return only small non-publishing metadata. Old generation/hash/task identities,
cancelled/revoked Runs and missing provenance cannot execute. The existing
independent native validator, completion-race repair, sealed witness and atomic
receipt/Job commit remain authoritative.

Operator recovery (disabled by the same live readiness/provenance gates):

```sh
python services/api/manage.py reconcile_visual_segments --limit 20
python services/api/manage.py reconcile_visual_segments --run-id <UUID>
python services/api/manage.py reconcile_visual_segments --run-id <UUID> --retry-claimed
```

Default reconciliation sends only never-claimed pending Runs, at most 50 rows
per call, three sends per generation, separated by 30 seconds. It cannot change
inputs or resurrect terminal/cancelled/revoked Runs. A previously claimed,
released transient attempt or an expired worker requires the explicit
`--retry-claimed --run-id` operator action. An active lease cannot be stolen.
Expired takeover uses the existing fence before dispatching the new expected
generation. The existing maximum of three claims remains enforced. Exhausted
send budgets remain visible for diagnosis; there is no unbounded resend/reset,
periodic scheduler or automatic media retry service. A process restart alone
does not redefine an immutable request.

## Private delivery and revocation

Delivery never uses public storage, playback/share/preview tokens, generic Job
URLs, CDN links or caller filenames. Before returning a descriptor it validates
the completed receipt, snapshot/request/source revision, owner/purpose, current
approval and producer authority, supported runtime, exact input/output pins,
digests, sizes and confinement through the existing access policy. It rechecks
database state on both sides of preparation, immediately before opening, after
opening and before the first yielded bytes. All input/output descriptors and
original source-asset identities are guarded during the stream. Filesystem
hashing/native work occurs outside the short control transactions.

Responses use `Cache-Control: private, no-store`, `Pragma: no-cache`,
`Vary: Authorization, Cookie`, `nosniff`, same-origin resource policy and a
restrictive sandbox CSP. MP4 is `video/mp4`; SRT is UTF-8 `text/plain`; WebVTT is
UTF-8 `text/vtt`. GET supports single/open/suffix MP4 byte ranges, accurate
206/Content-Range/Content-Length, and 416 for invalid, multiple or sidecar ranges.
HEAD authorizes identically, ignores Range per HTTP semantics, returns the full
length and sends no body. If-Range without a supported validator returns the
full authorized response. No cacheable public validator is issued.

Streaming uses 64 KiB reads, with file identity/confinement/source-asset checks
before and after every read. Live owner/source/cancellation/revocation/producer/
readiness checks occur at the first iteration and before the next chunk after
at most 256 KiB or one second. Slow consumers are rechecked when delivery
resumes; there is no database transaction held across a stream. A failed check
or database error closes descriptors and truncates delivery. Already transmitted
or buffered bytes cannot be recalled, and sent HTTP headers cannot be changed
midstream. This bounded detection policy is not instantaneous retroactive
revocation. Deployment must avoid proxy buffering that defeats these bounds.

The strict current-revision policy is unchanged: source edits retain historical
metadata/immutable receipts but deny new execution and media delivery. Late
cancellation or revocation preserves historical completed identity while denying
access. An accepted receipt cannot be replaced, posted through the API or used
to publish a lesson.

## Verification

Reproducible focused commands:

```sh
VISUAL_MEDIA_REQUIRE=1 pytest -q tests/integration/test_visual_segment_api.py
VISUAL_MEDIA_REQUIRE=1 pytest -q tests/integration/test_visual_segment_runs.py tests/integration/test_visual_segment_media.py tests/integration/test_visual_segment_artifacts.py tests/test_visual_segment_contracts.py tests/integration/test_visual_render_snapshots.py tests/integration/test_visual_render_planning.py
pytest -q tests/integration/test_visual_segment_api.py --ds=config.settings_test_postgres
python services/api/manage.py check --settings=config.settings_test
python services/api/manage.py makemigrations --check --dry-run --settings=config.settings_test
```

Observed locally during implementation:

* Combined segment/snapshot/planning regression run: **461 passed, 15 skipped**.
  Final delivery guard checks: **8 passed**; added input-pin/time-bound checks:
  **2 passed**. The final complete API run: **48 passed, 2 skipped** (50 cases,
  including two genuine PostgreSQL-only cases). PostgreSQL is unavailable locally; those cases must run
  in the hosted gate, not be relabeled as SQLite concurrency acceptance.
* Full local backend: **2,781 passed, 26 skipped, 63 subtests passed, 1 failed**.
  The failure is the unchanged
  `test_musetalk_warmup_cleanup.py::test_orphan_check_detects_and_kills_marked_warmup_process`.
  It was not patched, excluded or suppressed. This full run preceded the last
  two added streaming cases; final hosted CI runs the complete committed tree.
* Django check: no issues. Migration consistency: no changes detected.
* `npm ci`, frontend **350 tests / 49 files**, and production build pass.
  Both `npm audit --audit-level=high` and
  `npm audit --omit=dev --audit-level=moderate`: **0 vulnerabilities**.
  Python dependency audit: **no known vulnerabilities**.
* Local `npm run e2e` cannot launch any of the 36 Chromium cases because its
  Chromium executable is missing. This is not browser acceptance. Hosted
  Chromium is required. Docker/PostgreSQL executables are unavailable locally;
  hosted Docker image build smoke is not deployed shared-volume acceptance.

The existing CI workflow retains full backend/native media, migration, Python
audit, frontend/build/npm audits, Chromium and Docker gates. Its PostgreSQL job
now also runs this API suite. Final committed-tree results are recorded in the
feature branch's [GitHub Actions history](https://github.com/VisusVision/TalkingSlides/actions?query=branch%3Afeature%2Fai-visual-designer).

Tests exercise unknown-field/foreign-owner rejection, source/split conflicts,
production gates, immutable metadata, broker failure/crash windows, replay/send
budgets, explicit expired-claim recovery, real media execution, private ranges/
sidecars, corrupt/symlink/foreign artifacts, cancellation/revocation/source/
owner/producer/readiness changes during streams, input-pin corruption and timed
rechecks. Existing generic token/playback denial and real five-scene AAC-copy
acceptance remain in the retained earlier suites.

Streaming HTTP cases use `django_db(transaction=True)` so response closure and
Django request-finished connection cleanup occur outside an artificial outer
pytest transaction, matching actual request lifetimes. The first hosted run
exposed eight PostgreSQL closed-connection failures under that outer wrapper;
cleanup was retained, and these cases now use committed transactions rather than
suppressing cleanup or mocking database access.

The PostgreSQL suite includes concurrent reconciliation/claim and a real Celery
consumer over Kombu's in-process **memory broker**, with the actual task
implementation, committed PostgreSQL state, real FFmpeg execution, accepted
receipt, authenticated range retrieval and duplicate broker delivery. This is
separate from mocked send-failure tests. It does not establish external Redis,
multi-process worker-kill recovery, container networking or private mount/UID
acceptance. No engineering attestations are enrolled for runtime use.

## Phase 3B-6 prerequisites and limits

Before any live execution, separately authorize and verify:

1. An independently authenticated real producer receipt source, current
   authorization/revocation lookup and audio/payload identity support. No old
   lesson audio can be certified by merely hashing it.
2. Persistent private API/worker shared mounts, correct UID/GID/permissions,
   no public-root overlap, content-addressed read/write confinement and restart
   behavior in the actual built containers.
3. Exact supported FFmpeg/ffprobe/codec/font/rasterizer identities in both images
   matching frozen requests; native media acceptance on that deployment.
4. External broker/real worker process failure and operator reconciliation
   acceptance; task route consumption and the closed readiness boundary must be
   verified, not replaced with a user/configuration Boolean bypass.
5. Streaming server/proxy behavior, connection/descriptor/concurrency budgets,
   request deadlines, private storage quotas and controlled reference-aware
   retention. The current adapter keeps bounded guarded descriptors per stream;
   it is not a deployed capacity or garbage-collection solution.

No owner-facing Studio video controls, ordinary render integration, full-lesson
assembly, active-release pointer, public download, HLS or publication pathway is
implemented. Stop at Phase 3B-5.
