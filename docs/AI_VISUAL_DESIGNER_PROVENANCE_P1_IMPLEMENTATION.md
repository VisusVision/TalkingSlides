# Revised P1 implementation and engineering evidence

Scope: inactive production-provenance foundation only. Production dependencies
remain unavailable and deployment readiness remains false. This report is not a
producer enrollment, audio qualification, deployment certificate or rollout grant.

Current classification: **P1_IMPLEMENTATION_BLOCKED** pending the mandatory real
PostgreSQL authority/lock job and final-tree verification. This is an intermediate
implementation checkpoint; the evidence below must be updated after hosted CI.

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
the commit. This checkpoint is based on the approved local documents, not the
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
explicit new functions and six tables are transferred; no wildcard schema grant
or ownership transfer is used. Operators must fix a reported ACL conflict before
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

## Verification record — checkpoint

Local environment: Python 3.12, temporary virtualenv, SQLite for ordinary tests;
Node 24.22.1; installed host FFmpeg. No Docker daemon/client. PostgreSQL 16 binaries
could be obtained but this environment maps only UID 0; PostgreSQL refuses root
and a non-root user namespace was unavailable. No root-check bypass was attempted.

Commands are from repository root unless a directory is specified:

| Command | Observed result |
|---|---|
| `PYTHONPATH=services/api:services /tmp/talkingslides-p1-venv/bin/pytest -q tests/test_audio_producer_contracts.py tests/integration/test_audio_producer_schema.py` | 68 passed, 2 warnings after expanded assertions |
| `PYTHONPATH=services/api:services /tmp/talkingslides-p1-venv/bin/pytest -q tests --junitxml=/tmp/p1-frozen-backend.xml` | 2,859 passed, 44 skipped, 1 known MuseTalk process-group sandbox failure at `3ec2fac`; subsequent consumer-purpose assertion covered by focused suite, hosted final full suite required |
| `PYTHONPATH=services/api:services /tmp/talkingslides-p1-venv/bin/pytest -q tests/integration/test_render_planner_foundation.py tests/integration/test_partial_render_manifest.py tests/integration/test_partial_rerender.py tests/integration/test_visual_render_planning.py` | 228 passed, 2 warnings; existing tests unchanged |
| `PYTHONPATH=services/api:services DJANGO_SETTINGS_MODULE=config.settings_test /tmp/talkingslides-p1-venv/bin/python services/api/manage.py check` and same `makemigrations --check --dry-run` | System check: no issues; no model changes detected |
| Frontend `npm run test:ci` | 350 passed, 49 files |
| Frontend `npm run build` | Passed |
| Frontend `npm run e2e -- --project=chromium` | Blocked: all 36 fail browser launch, missing Chromium executable; bounded installer retries downloaded invalid/truncated archives |
| Frontend `npm audit --audit-level=low` and `npm audit --omit=dev --audit-level=low` | 0 vulnerabilities |
| `pip-audit -r services/api/requirements.txt` | No known vulnerabilities with cryptography 50.0.2; initial 46.x candidate failed audit and was replaced, not suppressed |
| Real-role PostgreSQL and concurrency | Local infrastructure blocked (never acceptance). CI 829 on `3ec2fac` passed all 18 real-role/concurrency cases with no skips |
| Docker builds / isolated Redis-container acceptance | Local Docker unavailable; existing hosted jobs pending |

Mandatory P1 gates are N01–N11 and PG01–PG08 in the controlling scope; proposed
T01–T22/A01–A32 cases assigned to later phases remain deferred. Existing full backend
includes partial-render planner/manifest/rerender and Phase 3A/3B suites; no previous
phase's historical result is claimed as proof of this tree. Hosted CI must verify
the exact final commit and real isolated-role tests before acceptance.

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

Checkpoint commit: `4b0e63d3f3ce9f169a1814392581294793ac89e7`,
[CI 826](https://github.com/VisusVision/TalkingSlides/actions/runs/40152818473).
The first hosted PostgreSQL migration attempt failed with a PL/pgSQL CASE
expression syntax error before role assertions ran; it was corrected with explicit
expression parentheses. A local libpg_query PL/pgSQL parser then accepted both SQL
batches. That parser check is not a substitute for the mandatory PostgreSQL run.
Frontend CI passed at that checkpoint. Final-tree hosted results remain pending.

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
denial; their 18-case PostgreSQL suite must run independently before acceptance.

Authority checkpoint `3ec2facc1c6c4c5f4bc75dd272151d32ce641664`,
[CI 829](https://github.com/VisusVision/TalkingSlides/actions/runs/40154138696):
PostgreSQL legacy/API/media **246 passed** and authority/concurrency **18 passed**,
no skips. Frontend and Docker smoke passed. The final source correction is the
explicit supported consumer Job-purpose binding in the new pure contracts;
the six-table SQL/role implementation is unchanged from this verified checkpoint.
