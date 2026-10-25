# Revised P1 implementation and engineering evidence

Scope: inactive production-provenance foundation only. Production dependencies
remain unavailable and deployment readiness remains false. This report is not a
producer enrollment, audio qualification, deployment certificate or rollout grant.

**P1_ENGINEERING_ACCEPTED** for the inactive revised P1 foundation. All six hosted
CI jobs passed for final implementation commit
`e04c7f9ec0fa2ed3d14f785a428701e7390e0b75`. This follow-up report changes no runtime
source. Production authentication, enrollment and availability remain deferred.

## Starting state and preserved design

On 25 October 2026, local HEAD, remote-tracking ref and a live GitHub ref read
matched `ea04701d9495da290c173a3bc3101a9a5dd6a1ec` on
`feature/ai-visual-designer`. There were no divergent local commits or unrelated
source modifications. The integration review and revised scope were untracked;
the production design had approved uncommitted revisions. All three were read
and preserved byte for byte, and are included in this implementation commit:

| Approved input | Starting SHA-256 |
|---|---|
| [Integration review](AI_VISUAL_DESIGNER_PARTIAL_RERENDER_INTEGRATION_REVIEW.md) | `2db7d173a587d8504e460eb34c8a9df383e8828415ee9ce6e14e22c37277ce40` |
| [Revised production design](AI_VISUAL_DESIGNER_PRODUCTION_PROVENANCE_DESIGN.md) | `a51c2be63389c84337433cb5477a270d26a744a1a12ca06251f8e6b8f3747abc` |
| [Controlling revised P1 scope](AI_VISUAL_DESIGNER_PROVENANCE_P1_REVISED_SCOPE.md) | `fd2723262cb34ccf8c747b8f8e0f462e047758cfc3a907618ee21c8cd69b9d20` |

No reset, clean, merge, rebase, ordinary runtime rewrite or historical audio
backfill was performed. Locally generated Playwright reports are excluded from
the commit. This implementation is based on the approved local documents, not the
older design available at the starting remote revision.

## Implementation file ledger

| File | Change |
|---|---|
| `services/api/core/visual_design/producer_contracts.py` | Pure versioned structures, hashes, strict Ed25519 verification and replay equality |
| `services/api/core/audio_producer_models.py` | Exactly six additive inactive ledger models |
| `services/api/core/models.py` | Explicit registration import of the six models |
| `services/api/core/migrations/0045_inactive_audio_producer_ledger.py` | Six-table schema, protected internal references and constraints |
| `services/api/core/migrations/0046_audio_producer_guards.py` | PostgreSQL immutable guards, control routines, commit checks and ordered reader locks |
| `infra/sql/audio_producer_roles.sql` | Explicit operator-only object ownership and group grants, with unsafe-role checks |
| `services/api/requirements.txt` | Direct maintained `cryptography>=50.0.0,<51` dependency; no key material |
| `tests/test_audio_producer_contracts.py` | Ephemeral-key pure contract/identity/closed-gate tests |
| `tests/integration/test_audio_producer_schema.py` | Six empty tables and additive relationship/ORM checks |
| `tests/integration/test_audio_producer_postgres_roles.py` | Separate real LOGIN role, SQL bypass, CAS, receipt and lock tests |
| `.github/workflows/ci.yml` | Required real-role step in existing PostgreSQL job (`P1_REQUIRE_POSTGRES=1`), plus pure contracts on the built worker's Python runtime |
| Three approved inputs above and this report | Preserved specifications and engineering record |

## Final schema boundary

| Table | Role and immutable contents |
|---|---|
| `AudioProducerAuthority` / `audio_producer_authority` | Immutable operator enrollment, public key/purpose/environment, approved image/profile and validity; no private keys |
| `AudioProducerExecution` / `audio_producer_execution` | Immutable original invocation capture, planned speech, historical owner/project/Job/page tuple and publisher-context issuer |
| `AudioProducerAttempt` / `audio_producer_attempt` | Append-only resolved observation, issued event, nonce/generation/context and actual outcome; never a blank mutable success row |
| `AudioProducerReceipt` / `audio_producer_receipt` | Immutable original receipt, original attempt/execution and unchanged nested `SegmentAudioProof`; one per execution and attempt |
| `AudioProducerControl` / `audio_producer_control` | Only operational projection: exactly one typed target, epoch/head/status, current generation/nonce/deadline and accepted receipt |
| `AudioProducerControlEvent` / `audio_producer_event` | Append-only sequence/previous hash, typed issuance fields and exact authorized instruction |

External ordinary IDs are captured scalar references as explicitly required by
the revised scope. They do not authenticate live ownership. Internal FKs are
protected; ordinary Job/page deletion cannot cascade or rewrite signed history.
All tables start empty. There is no use, presentation, publication, cache, lease
or generic artifact table. No data migration creates authority or audio records.

Observation UUID, execution/generation, nonce, issued-context digest and issued
event are unique; attempts are bounded to generations 1–3. Equal text/audio hashes
are not unique. Receipt insertion is accepted only with the execution acceptance
event/pointer and its own active control committed atomically. Control head and
epoch completeness is checked at commit; failed transactions leave no event or
projection advance. Direct immutable UPDATE/DELETE is denied by database triggers.

## Signed formats and identity separation

The canonical envelope has the eight approved fields. Ed25519 is the only
algorithm; there is no algorithm negotiation, JWK/URL lookup or signer enrollment
from the envelope. The fixed prefix is
`talkingslides-producer-signature.v1\n`; the signed body includes purpose,
environment, audience, issuer/key identity and exact payload. Verification uses
an independently supplied registry; there is no default registry or settings hook.
Producer, publisher and operator key families cannot share a public key. A
publisher key may have several separately granted publisher purposes.

| Schema | Purpose |
|---|---|
| `production-producer-enrollment.v1` | `operator.enrollment.v1` |
| `production-producer-context.v1` | `publisher.context.v1` |
| `production-tts-observation.v1` | `tts.original-observation.v1` |
| `production-audio-receipt.v1` | `publisher.original-receipt.v1` |
| `production-audio-reuse.v1` | `publisher.reuse.v1` |
| `production-producer-control.v1` | Disjoint `operator.control.v1` / `publisher.attempt.v1` actions |

Unsigned nested/hash structures are execution, speech, scoped speech and page
presentation under their approved new domains. Two concrete wire details resolve
the revised scope's distinction without additional tables: the pure
`production-consumer-capture.v1` contains consuming Job/manifest/source/speech
inputs **without** an original execution, synthesis generation or nonce; the new
outer original receipt includes explicit `speech_hash` and `proof_hash` to bind
the full original speech projection and independently expected nested v1 proof.
This does not modify `SegmentAudioProof` or any previous golden hash.

Consumer capture and the signed reuse payload explicitly bind `job_type` /
`consumer_job_type`. Initial P1 structures permit the approved ordinary
`video_export` consuming capture only; they do not grant a new private Job or
publication purpose. The future P4 adapter must preserve the existing baseline
Job and separate private Run semantics. Unsupported consuming purposes fail
rather than being inferred from a mutable Job, path or status.

`signed_envelope_hash` derives separate `*-envelope.v1` identities over the exact
canonical signed envelope, including signer/signature. Those hashes are not
authentication; full independent signature/parent verification is still required.
The six-table ledger retains exact immutable signed bytes and their issuer FKs.
Control events use `production-producer-control-event.v1`; their hashes include
control ID, sequence, previous hash, kind and exact instruction. The PostgreSQL
integer/string speech serializer is limited to the new speech projection. It
does not replace or reinterpret the existing proof serializer/numeric contracts.

Parsing rejects duplicate keys, invalid UTF-8, nonfinite/unknown/noncanonical
payloads, unsupported fields/versions and bounded-resource violations. Context,
observation ingress, enrollment and reuse/control windows are checked against
independent caller time. Full observation/receipt/reuse verification requires
independently validated parents; a signature or self-calculated hash alone cannot
substitute. Authentication returns a validated statement, never execution access.

Speech identity includes exact protected/effective text, actual ordered engine
chunks/pauses, concrete provider/voice, neutral supported effective controls and
loaded model/config/preparation/pronunciation/segmentation/sample identities.
It excludes Job, scene, order and subtitle grouping. Speaking an expanded order
number changes the text and therefore speech identity. Source zero and exact
source/split/page tuples remain explicit; position is never a fallback identity.

Reuse references a validated original receipt/observation and final eligible audio
plus an independently captured consumer and presentation/current policy. The
same owner/project/stable tuple is mandatory. It retains the actual original
provider, creates no new synthesis event and cannot certify legacy MP3 paths.
Caption grouping/cues are distinct from engine chunking. Frozen-baseline claims
retain the original cue hash; changed presentation still needs future independent
P3/P4 acceptance and all existing private request checks.

## PostgreSQL authority and locking

| Principal | Capabilities after explicit provisioning |
|---|---|
| Trusted migration administrator | Schema installation, narrowly scoped provisioning and administrative recovery |
| `ts_audio_owner` NOLOGIN | Owns six tables and new functions; never granted to runtime principals |
| `ts_audio_operator` NOLOGIN group | SELECT, INSERT immutable enrollment, issuer control, non-escalating revoke |
| `ts_audio_publisher` NOLOGIN group | SELECT, INSERT execution/observation/original receipt, bounded execution/receipt transitions |
| `ts_audio_reader` NOLOGIN group | SELECT plus ordered lock/check routine; no direct UPDATE permission |
| Ordinary API/worker | No ledger writes, DDL, ownership, privileged membership or function EXECUTE grant |
| TTS service | No platform DB identity or credentials provisioned |

Run `psql -X -v ON_ERROR_STOP=1 -f infra/sql/audio_producer_roles.sql` only as the
trusted migration operator after migrations. It creates NOLOGIN groups and moves
only new object ownership. It installs no LOGIN credentials, production issuers,
startup hooks or runtime memberships. Deployment operators must separately ensure
ordinary runtime principals are not database/schema owners, superusers, role
administrators or members of privileged groups. Broad existing application-owner
credentials are not a production-ready isolated deployment. Key custody, real
issuer registry loading and independently operated publisher identities remain
later operator decisions.

Provisioning checks existing group attributes/memberships and unexpected object
ACLs, including inherited default grants to ordinary roles. It fails rather than
silently accepting them or changing unrelated default privileges. Only the nine
exact new function signatures and six tables are transferred; unexpected same-name
overloads are rejected before ownership changes. No wildcard schema grant or
ownership transfer is used. Operators must fix a reported ACL/overload conflict before
retrying. This is setup evidence, never a production readiness switch.

Definer routines use schema-qualified tables and fixed `pg_catalog,pg_temp`
search paths, PUBLIC EXECUTE revoked, explicit group EXECUTE grants and
`session_user` purpose checks. No caller-selected SQL/table/function is executed.
The narrow reader holds ordered shared locks without a broad UPDATE grant.
Writer/reader definer routines bound lock waits to three seconds; production
connection statement/transaction/idle timeouts remain an operator prerequisite.
The new-control subsequence is authority controls sorted by target ID → parent
receipt controls → execution control. Scope/epoch/window/status are rechecked.
Global issuer revoke takes only the issuer control; it never subsequently locks
a Project. These primitives are not wired into existing private Run transactions.

Administrators/object owners remain the trusted computing base: no SQL design
can protect a table against its superuser disabling/dropping guards. The immutable
TRUNCATE trigger has an explicit **session-superuser administrative exception**
for disposable Django test DB flush/recovery; runtime and publisher/operator
roles have neither TRUNCATE privilege nor that exception. Never use an admin
connection as evidence of runtime isolation. The role suite uses separate
non-superuser LOGIN connections; admin is used only for provisioning and teardown.

The database verifies indexed payload/hash/reference consistency and controls,
not Ed25519 or the honesty of a privileged publisher. The future publisher is an
explicit trust boundary and must perform complete pure signature/parent validation
before INSERT. SQL fixtures test privileges/relationships, not authentic synthesis.

## Verification record

Local environment: Python 3.12, temporary virtualenv, SQLite for ordinary tests;
Node 24.22.1; installed host FFmpeg. No Docker daemon/client. PostgreSQL 16 binaries
could be obtained but this environment maps only UID 0; PostgreSQL refuses root
and a non-root user namespace was unavailable. No root-check bypass was attempted.

Commands are from repository root unless a directory is specified:

| Command | Observed result |
|---|---|
| `PYTHONPATH=services/api:services /tmp/talkingslides-p1-venv/bin/pytest -q tests/test_audio_producer_contracts.py tests/integration/test_audio_producer_schema.py` | 68 passed, 2 warnings after expanded assertions |
| `PYTHONPATH=services/api:services /tmp/talkingslides-p1-venv/bin/pytest -q tests --junitxml=/tmp/p1-frozen-backend.xml` | 2,859 passed, 44 skipped, 1 known MuseTalk process-group sandbox failure at `3ec2fac`; subsequent consumer-purpose assertion covered by focused suite; final hosted full suite passed below |
| `PYTHONPATH=services/api:services /tmp/talkingslides-p1-venv/bin/pytest -q tests/integration/test_render_planner_foundation.py tests/integration/test_partial_render_manifest.py tests/integration/test_partial_rerender.py tests/integration/test_visual_render_planning.py` | 228 passed, 2 warnings; existing tests unchanged |
| `PYTHONPATH=services/api:services DJANGO_SETTINGS_MODULE=config.settings_test /tmp/talkingslides-p1-venv/bin/python services/api/manage.py check` and same `makemigrations --check --dry-run` | System check: no issues; no model changes detected |
| Frontend `npm run test:ci` | 350 passed, 49 files |
| Frontend `npm run build` | Passed |
| Frontend `npm run e2e -- --project=chromium` | Blocked: all 36 fail browser launch, missing Chromium executable; bounded installer retries downloaded invalid/truncated archives |
| Frontend `npm audit --audit-level=low` and `npm audit --omit=dev --audit-level=low` | 0 vulnerabilities |
| `pip-audit -r services/api/requirements.txt` | No known vulnerabilities with cryptography 50.0.2; initial 46.x candidate failed audit and was replaced, not suppressed |
| Real-role PostgreSQL and concurrency | Local infrastructure blocked (never acceptance). final CI 831 passed all 19 real-role/concurrency cases with no skips |
| Docker builds / isolated Redis-container acceptance | Local Docker unavailable; final hosted builds/startup and real Redis/container harness passed |

Mandatory P1 gates are N01–N11 and PG01–PG08 in the controlling scope; proposed
T01–T22/A01–A32 cases assigned to later phases remain deferred. Existing full backend
includes partial-render planner/manifest/rerender and Phase 3A/3B suites; no previous
phase's historical result is claimed as proof of this tree. Final exact-source
hosted evidence below independently verifies these gates.

### Final hosted evidence

Final implementation/source SHA:
[`e04c7f9ec0fa2ed3d14f785a428701e7390e0b75`](https://github.com/VisusVision/TalkingSlides/commit/e04c7f9ec0fa2ed3d14f785a428701e7390e0b75).
[CI 831](https://github.com/VisusVision/TalkingSlides/actions/runs/40154980824)
completed **successfully, all six jobs**. Tests ran on Ubuntu 24.04, Python
3.11.17, actual PostgreSQL 16 and isolated role logins; the built CUDA-base worker
variant independently ran its Python 3.10 pure contracts without a GPU requirement.
The following report-only commit preserves this exact source tree. Its separate
CI result is available in the branch Actions history and final engineering handoff.

| Exact CI command / job | Result at final implementation SHA |
|---|---|
| `pytest -q tests --junitxml=pytest-report.xml` | **2,862 passed, 45 skipped, 63 subtests passed**; the local MuseTalk failure passed on the hosted runner without modification/suppression |
| `pytest -q tests/integration/test_audio_producer_postgres_roles.py --ds=config.settings_test_postgres`, `P1_REQUIRE_POSTGRES=1` | **19 passed, no skips**; [real-role job](https://github.com/VisusVision/TalkingSlides/actions/runs/40154980824/job/121054378765) |
| `pytest -q tests/integration/test_visual_drafts.py tests/integration/test_visual_evidence.py tests/integration/test_visual_render_snapshots.py tests/integration/test_visual_segment_runs.py tests/integration/test_visual_segment_media.py tests/integration/test_visual_segment_api.py --ds=config.settings_test_postgres` | **246 passed, no skips**, including existing PostgreSQL races, private media and GET/HEAD/Range/revocation boundaries |
| `python manage.py makemigrations --check --dry-run` in `services/api` with SQLite test settings | **No changes detected** |
| Built worker: `docker run --rm --entrypoint python -e DJANGO_SETTINGS_MODULE=config.settings_test -v "$PWD:/verify:ro" -w /verify vs-worker:acceptance -m pytest -q -p no:cacheprovider tests/test_audio_producer_contracts.py` | **65 passed**, actual worker Python/runtime; [container job](https://github.com/VisusVision/TalkingSlides/actions/runs/40154980824/job/121054378895) |
| Frontend `npm run test:ci`, `npm run build`, `npm run e2e` | **350 passed**, build passed, **36 Chromium E2E passed** |
| Frontend `npm audit --audit-level=high`, `npm audit --omit=dev --audit-level=moderate` | **0 vulnerabilities**, both checks |
| `pip-audit -r services/api/requirements.txt` | **No known vulnerabilities** |
| `docker buildx bake --allow=fs.read=.. --file docker-compose.yml --progress=plain` with the workflow cache/smoke overrides and targets `api worker tts_service` | **Passed** for the three image build surfaces; [Docker job](https://github.com/VisusVision/TalkingSlides/actions/runs/40154980824/job/121054378880) |
| `python tests/deployment/visual_segments/run.py --report-dir "${{ runner.temp }}/visual-segment-deployment"` after building the actual default API, worker and compatible private-API images | **Passed**, actual Redis/Gunicorn/separate Celery worker, persistent volume, native media and restart scenarios |
| `k6 inspect infra/loadtest/k6-production-sprint.js` | **Passed** syntax inspection; no production load/capacity claim |

The 45 full-backend skips consist of the 19 real-role cases intentionally not
run against SQLite plus 26 existing skips. Those same 19 cases passed independently
against PostgreSQL; no mandatory P1 gate is accepted as a skip. The local browser
installation failure and MuseTalk sandbox failure remain recorded above; neither
was hidden or substituted for hosted verification.

The final SQL suite proves runtime denial codes for raw INSERT/UPDATE/DELETE/
TRUNCATE/COPY, ownership/trigger/function changes and privileged SET ROLE; separate
operator/publisher purposes; immutable observations/receipts; exact signature
ownership; ACL drift denial; protected scope/issuer/digest relationships; nonce/
generation CAS; one winner for conflicting receipts; insertion/acceptance and
control/event rollback; reader/revoker ordering; receipt revocation; shared issuer
retries across two owners/projects; and canonical speech bytes. Its test
administrator only provisions/tears down principals and performs explicitly
identified pure-codec/setup checks. Runtime isolation assertions use the separate
non-superuser LOGIN connections.

### Actual container regression evidence

[Deployment artifact 12583364840](https://github.com/VisusVision/TalkingSlides/actions/runs/40154980824/artifacts/12583364840)
contains `acceptance.json` and container logs. It is engineering evidence using
the existing independently anchored synthetic fixture, not P1 producer authority.
The artifact remains honestly classified `PHASE3B_DEPLOYMENT_BLOCKED` with
`production_enabled=false`. Its D1–D7 enumerated checks passed; D8 is report-only,
D9 retains the closed production prerequisite, and D10 refers to the separately
completed hosted regressions above. No report result activates production.

API and worker both ran as **1000:1000**, saw the same mounted device/inode
`66305/9700696`, and verified root mode **0700**, file mode **0600**, cross-process
visibility and restart persistence. Nine storage/runtime fault cases rejected
unsafe execution. Twenty enumerated process/revocation scenarios covered API
send/sent interruption, worker claim/native/pin/accepted interruption, pending
Redis delivery and AOF/reconnect recovery, cancellation, source/evidence/owner/
producer loss, and authorization after accepted receipts. A duplicate/stale
attempt could not replace the accepted result. Three direct-Gunicorn client
disconnects left private descriptors at **0 → 0**; no production-proxy or arbitrary
slow-stream claim is made.

| Actual built image | Image identity |
|---|---|
| Worker | `sha256:7713d33524520c730e4dd3d189b951a3aaf73bca97a300c054a44110143a86dc` |
| Compatible private API | `sha256:4cfeb9da904ae22157ac6b2f8092a5e1a0b7ce37cf3467e6a2b5660bc8a5ebe5` |
| Default slim API | `sha256:488b560d5b50e5637258f254772f3d3516a0871959a74cf6aecede5b6d5b85c6` |

The compatible private-API/worker identities matched. The default slim API did
**not** match the worker native profile; it remained unsupported for that recipe.
This existing strict contract was preserved.

| Native component | Matched-variant evidence |
|---|---|
| FFmpeg binary, version 4.4.2-0ubuntu0.22.04.1 | `sha256:36d94a605d612e4090d1b8aec889d0c0801c6eafb1593c90f5c0dfd2e2966a45` |
| FFprobe binary | `sha256:d4f3ef9c12be756793cad83dd2004d89f49c1c4094053bfbbe7e28925c8fa4fd` |
| Linked x264 | `sha256:74263a09bc162e1ef7268e3d93b0b7839977292f5d702104830ca0998d85b27c` |
| Linked librsvg | `sha256:de487005050dc31281932e2de24d8307167b4c1cd9c042521b34602fc93c866f` |
| Fontconfig | `sha256:7d688909aab1c566b60832045fef012ea118ff7ee189df87608fbff999d3bd0d` |
| DejaVu regular / bold | `sha256:690243adfefe0ce154b547db6205794bd30ac4277275179517a90994f4980648` / `sha256:d1c3ff99f1e1ce1827a33efd4dad81f40babda06bff9e43bd7591c86662a287b` |

All linked-library identities and renderer recipe observations are retained in
the artifact. The example output was real 1920×1080 H.264/AAC with valid SRT/VTT;
the original AAC packet-payload identity remained
`sha256:05c9c0e0b1cfe5b55f5c72a697f62a74aa900da42ed94b1ea9d86195ddc44d91`.
The MP4 content hash was
`sha256:4f76b622b4d8f302919b7538bfc56381fc7ff7c77b02a35c8e2fd575dca84b69`.
These fixture artifacts are never accepted by a production producer source.

### Scope-case traceability

| Revised gate | Concrete P1 coverage |
|---|---|
| N01 | Canonical/signature/registry, duplicate/nonfinite/Unicode budgets, signed malformed payloads, independent enrollment root, cross-environment/audience/key-family rejection |
| N02 | Speech versus order/subtitle presentation; actual spoken slide context and loaded dependency/chunk/pause changes |
| N03 | Distinct original observation, new actual attempt and reuse; original provider/root retained |
| N04 | Same-page consumer Job/manifest binding, source zero, splits, consistent cross-owner/project/source changes denied |
| N05 | Cue/text/order/window checks, independently supplied presentation/audio parent, changed cues cannot claim frozen baseline |
| N06 | Planned versus actual resources, samples, voice, controls, fallback/coverage/output failure, failed history ineligible |
| N07 | Metadata/path cannot substitute for a validated original parent; pure module does no media I/O |
| N08 | Empty six-table schema/internal protected FKs, SQL indexed capture/hash/issuer/parent guards and ordinary Job deletion history |
| N09 | Pure replay equality/conflict, DB unique issuance/nonces, current generation CAS and conflicting receipts |
| N10 | Independent current policy epochs, revoked/expired issuer tests, reader/revoker locks and rollback; immutable history unchanged |
| N11 | Actual closed seams and six models, unchanged legacy source/hashes, existing full/backend/partial suites |
| PG01/PG02 | Separate actual non-superuser LOGIN connections, denial SQLSTATE checks, real COPY protocol, forbidden function/ownership/issuer mutation and cross-purpose controls |
| PG03/PG04 | Event rollback/hash chain, ordinary Job deletion, concurrent generation CAS, atomic receipt insertion/acceptance and two-session conflicting receipt race |
| PG05/PG06 | Reader holds shared controls against revoker, stale epoch denial, temporary-schema shadow and PUBLIC EXECUTE denial |
| PG07 | Unissued/replayed/expired context, missing controls, immutable expired grants, cross-environment/profile observations, commit-completeness triggers |
| PG08 | Global issuer revoke/retry and shared issuer across two owners/projects, no deadlock/lock-timeout fallback; receipt revocation checked against full parent controls |

The additional codec test uses the test administrator only to compare pure SQL
speech bytes with Python canonical bytes. It is not runtime-role isolation
evidence. Setup ACL-conflict testing likewise uses admin only to simulate a bad
deployment grant; all authorization assertions use the independent runtime logins.

## Exclusions and remaining risks

No TTS calls/observation ingress, production publisher, audio copy/AAC publication,
durable chunk cache, render scheduler change, producer-source adapter, private
reservation/streaming change, deployment enrollment, new endpoint, private preview
activation, publication, GC or Phase 3C. Existing runtime source outside explicit
model registration/dependency declaration is unchanged. Test keys are ephemeral
in-memory only and engineering issuers are rejected in production verification.

Operator key custody, image/resource enrollment, sample consent/revocation,
actual loaded-resource observation, WAV/AAC processing policy, full-render
qualification, presentation/caption acceptance, deployment isolation and proxy/
retention/backup acceptance remain later prerequisites. P1 cannot establish real
producer authenticity without those future independently operated components.

## Checkpoint history

Checkpoint commit: `4b0e63d3f3ce9f169a1814392581294793ac89e7`,
[CI 826](https://github.com/VisusVision/TalkingSlides/actions/runs/40152818473).
The first hosted PostgreSQL migration attempt failed with a PL/pgSQL CASE
expression syntax error before role assertions ran; it was corrected with explicit
expression parentheses. A local libpg_query PL/pgSQL parser then accepted both SQL
batches. That parser check is not a substitute for the mandatory PostgreSQL run.
Frontend CI passed at that checkpoint; final evidence below supersedes this partial checkpoint.

Correction checkpoint `11060f4cc122e663204716ec52c384dc73241df6`,
[CI 827](https://github.com/VisusVision/TalkingSlides/actions/runs/40153053810):
PostgreSQL legacy/API/media regression **246 passed, no skips**; independent
LOGIN role/lock suite **9 passed, no skips**. Frontend and Docker smoke passed.
The next verification adds stricter signed-envelope/column binding, full parent
control checks, explicit timeout bounds, expanded negative/race cases and worker
Python contract execution; previous results are not substituted for those checks.

Hardening checkpoint `f9d13b04eed9435da134d630fcc71e83649a664e`,
[CI 828](https://github.com/VisusVision/TalkingSlides/actions/runs/40153644133):
PostgreSQL legacy/API/media **246 passed** and expanded real-role/concurrency
**15 passed**, no skips. Frontend **350 passed**, build passed, Chromium **36
passed**, both npm audits clean, Docker smoke passed. Built-worker pure-contract
step passed (the following container acceptance step was reached). Final checks
add explicit enrollment target/key-family validation and provisioning ACL drift
denial; the subsequent 18-case PostgreSQL suite passed independently.

Authority checkpoint `3ec2facc1c6c4c5f4bc75dd272151d32ce641664`,
[CI 829](https://github.com/VisusVision/TalkingSlides/actions/runs/40154138696):
PostgreSQL legacy/API/media **246 passed** and authority/concurrency **18 passed**,
no skips. Frontend and Docker smoke passed. The final source correction is the
explicit supported consumer Job-purpose binding in the new pure contracts;
the six-table migration implementation is unchanged from this verified checkpoint;
the later role script additionally rejects unexpected function overloads.

Final provisioning review added a nineteenth real-role case: an API-owned
SECURITY DEFINER overload must never be adopted by the operator script. Ownership
selection now uses exact signatures and fails on unexpected overloads. The
consumer-purpose checkpoint is `87040be849c23afa52410f389c278476057ba5e4`;
CI 831 independently verified this final operator-script correction and all
other final implementation files. Earlier interrupted/cancelled workflow runs
are used only for the completed individual job results explicitly listed above.
