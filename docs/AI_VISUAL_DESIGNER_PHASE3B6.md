# Phase 3B-6 — Deployment packaging and isolated security evidence

## Interrupted-session recovery (24 October 2026)

The continuation recovered the original checkout at
`9d731938b49d9a818bdd5a71015dfd23f01f10e0`, on the requested feature branch.
Three Phase 3B-6 commits were already present and pushed: `5010536` (packaging,
volume provisioner, retention, container suite and CI), `0a72948` (private
permission preservation and stable native comparison), and `9d73193` (API CUDA
entrypoint isolation and container result parsing). Two unstaged test changes
in `run.py` and `vs_acceptance/ops.py` added a client-reset/descriptor probe.
There were no staged or untracked repository files and no unrelated user changes.
All prior implementation and those two pending changes were preserved.

Recovered [CI run 40081320496](https://github.com/VisusVision/TalkingSlides/actions/runs/40081320496)
passed backend/migrations/Python audit, PostgreSQL, frontend/Chromium/npm audits,
Docker builds and k6. Its deployment job built all three images, verified shared
volume identity and native compatibility, then failed on missing API host port
publication. Gunicorn had started successfully in the archived container log;
the real media/recovery scenarios had not run. Earlier two Phase 3B-6 runs were
cancelled, not accepted. This failure was a deployment-harness configuration
failure, not evidence of another network download failure.

The continuation adds a separate loopback-facing API bridge, keeping the broker,
database and worker on the internal network only, and waits boundedly for port
publication plus HTTP health at each API restart. It completes the pending
client-disconnect probe and requires actual Gunicorn process inspection rather
than silently treating unavailable descriptor inspection as success. Final-tree
test results and remaining gates are recorded below.

The first continuation run, [40121841441](https://github.com/VisusVision/TalkingSlides/actions/runs/40121841441)
at `af3ba87cf657f01b8195c5f920447ccf5b07b0bb`, passed actual private generation,
HTTP GET/HEAD/Range/sidecars, cross-owner/public-route denial and descriptor cleanup.
It stopped during restart health verification because the controller reused an
ephemeral port from before container restart. The API itself restarted normally.
The controller now resolves the loopback port after every API start/restart.
Additional continuation coverage waits for executor exit after revocation,
tests abrupt Redis/AOF recovery without republishing, and checks producer denial
at reservation, worker claim, completion and delivery across processes. Built
fault probes also reject an unmounted private directory and an unexpected FFmpeg
binary hash. Production trust and readiness functions are unchanged.

[Run 40122312795](https://github.com/VisusVision/TalkingSlides/actions/runs/40122312795)
at `67710e05da2450605aca6495746ac06d9ae7fa25` passed private media/restart,
all API/worker/Redis interruption scenarios, AOF recovery without republishing,
and cancellation/revocation/source/producer loss during FFmpeg. Its artifact
records 17 completed scenarios before a harness assertion rejected the existing
producer-unavailable HTTP response. The corrected assertion requires exactly
`503` and `audio_provenance_unavailable`, plus an unchanged accepted receipt;
application behavior was not modified. Commit `346cc60db0797bd91f90b0a2a4502f78c46efa79`
also records each linked native library hash and verifies that the complete map
matches the unchanged frozen aggregate runtime contract. Earlier partial runs
are preserved as recovery history, not substituted for final acceptance.

Verified starting HEAD: `ed49b3e0ffc26167e29d94d49c7d9cca80148d10`, clean
`feature/ai-visual-designer`. Main/developer are not modified or merged.

**Classification: PHASE3B_DEPLOYMENT_BLOCKED.** This work provides packaging,
strict provisioning, bounded inventory and an isolated container evidence suite.
It does not enroll production deployment attestations, implement producer receipts
or enable real lessons. A successful evidence job is not a production-readiness
grant or a claim that every required deployment matrix case has passed.

## Packaging and numeric identity

`infra/docker-compose.yml` declares a dedicated `visual_private` named volume at
`/var/lib/talkingslides-private`, mounted only in the API, ordinary render worker
and an explicit provisioning service in the operator overlay. Both application services receive the same
`VISUAL_RENDER_PRIVATE_ROOT`. Frontend, TTS, avatar worker, MinIO, public/static
handlers and model caches receive no mount. Existing public storage paths and
legacy public chmod behavior remain separate and unchanged.

API and worker Dockerfiles explicitly create appuser as **1000:1000**. Names alone
are not accepted as numeric identity. The deployment suite checks both real
Python process IDs and root-entrypoint privilege-drop behavior in built images.
The shared entrypoint rejects private/public root overlap before public chmod,
requires the expected numeric application identity when private storage is
configured, and refuses a root fallback if privilege drop cannot occur. Its
private checks do not recursively modify any private asset permissions.

`core/visual_private_volume.py` is a standalone, no-database provisioner. It uses
no-follow directory descriptors. Only an **empty, explicitly selected, root-owned
volume** can be initialized as UID/GID 1000 and mode 0700; a populated root-owned,
wrong-owner or permissive volume fails instead of being recursively repaired.
Inspection never changes a directory. Application CAS pins remain 0600 and private
directories 0700. Ordinary lesson startup is not made dependent on provisioning an
unused private volume; private operations already fail closed without it.

For the existing development Compose, run the explicit operator initialization
before attempting any private snapshot/media operation:

```sh
docker compose -f infra/docker-compose.yml -f infra/docker-compose.visual-private.yml --profile private-visuals run --rm private-volume-init
```

This provisions storage only. It grants neither producer trust nor readiness.
Never point `STORAGE_ROOT`, `MEDIA_ROOT`, `STATIC_ROOT` or a public object mount at
this volume. Do not use chmod 777 or recursive ownership repair to recover it.
Back up/inspect an incorrectly owned populated volume as an operator instead.

## Native runtime compatibility

The default API uses Python 3.11 slim; the CUDA worker uses Ubuntu 22.04/Python
3.10. Matching native identities cannot be assumed across those distributions.
`Dockerfile.api` now packages the existing `scripts` formatters needed by strict
private caption validation, without importing a worker planner into API code.

A concrete Python compatibility defect was found: private native hashing called
`hashlib.file_digest`, introduced in Python 3.11. The common bounded `_hash_stream`
implementation now works on the worker's Python 3.10 too. It produces exactly the
same SHA-256 digests; snapshot/request/receipt schemas, canonicalization, accepted
hashes, native allowlists and validation semantics are unchanged. A regression
removes the newer API and exercises this path.

`infra/dockerfiles/Dockerfile.private-api` is an **optional API variant derived
from an exact worker image**, adding no apt/pip/native packages. It inherits the
same FFmpeg/FFprobe, linked libraries, fonts and rasterizer. The isolated suite
builds and observes both the default slim API and this compatible variant. An
incompatible default API is recorded, not admitted by replacing expected hashes
at execution time. The controller freezes its built-image support evidence before
any reservation; execution still compares to that frozen identity.

The candidate API variant is tested with packaged `/app/api` source and a real
Gunicorn process. Do not simply swap it into the development Compose API's `/app`
source bind mounts: those mounts target the slim image's different layout. A real
rollout needs reviewed immutable image digests, compatible packaged-source layout
and fresh evidence. Existing API/worker/GPU defaults are not replaced by this
engineering suite. No GPU/avatar native packages or provider behavior is changed.

## Isolated external Redis and HTTP harness

See [deployment README](../tests/deployment/visual_segments/README.md) for complete
build/run commands. `tests/deployment/visual_segments/compose.yml` contains an
isolated PostgreSQL 16, Redis 7 with AOF, actual prefork Celery render worker,
Gunicorn API, private/source/public test volumes and an internal network. Only an
ephemeral loopback HTTP port is exposed. No production `.env` or credentials,
frontend, TTS, MinIO, public bucket, GPU/model download or new queue is used.

Application source comes from built images. The test-only package and existing
fixtures are mounted read-only, never copied into application images. Dedicated
settings refuse normal database/producer/broker/root configurations and require
a read-only controller marker. The original reviewed synthetic proof digests and
speech bytes are reused. Only that isolated package injects engineering producer
and readiness dependencies; normal runtime code still rejects synthetic proofs.
There is no browser/API argument or production environment Boolean bypass.

The host controller records image IDs, actual UID/GID, FFmpeg/FFprobe hashes,
version, linked runtime hash/libraries, font hashes, accepted media digests and
scenario results in `acceptance.json`. Auth tokens are not included. Generated
MP4s are not committed. Missing Docker is a failed execution with NOT_EXECUTED
gates, not a passing unit-test substitute.

Enumerated process scenarios include committed dispatch before API kill, actual
Redis send before API acknowledgement loss, worker restart before/after claim,
real FFmpeg child interruption, interruption after first output pin and after
receipt acceptance before task acknowledgement. Expired claims use explicit
bounded reconciliation and higher generations. Tests also cover Redis unavailable
before send, reconnect/restart, old-generation replay, duplicate delivery,
cancellation/revocation/source edits during real composition, and post-receipt
owner/revocation/source access denial. Exact test outcomes must come from the
committed-tree CI report; enumerating a test is not evidence it passed.

Fault pauses are only test wrappers around existing operations. The native pause
stops the real FFmpeg process with SIGSTOP, and the controller actually kills or
restarts containers. Test-clock expiry/cooldown updates affect only operational
fields in the disposable database. They never rewrite request/receipt identities,
forge a successful witness, synthesize audio or enroll a real producer.

Real Gunicorn requests verify owner GET/HEAD, full/open/suffix/single ranges,
invalid/multiple ranges, SRT/VTT, correct lengths/types, private/no-store headers,
foreign-owner rejection and guessed public routes. There is **no production
reverse proxy** in this isolated network. Its buffering/disconnect behavior,
large/slow-stream revocation bounds and capacity remain unaccepted deployment
prerequisites. Bytes already sent/buffered cannot be revoked retroactively.

## Readiness remains closed

`_producer_dependencies()` still returns `None` in production and
`_deployment_ready()` still returns `False`. Real receipts require separate
implementation authorization. An engineering support JSON file, image observation
or successful suite does not authenticate production deployment safety.

A safe live readiness implementation still needs independently controlled,
expiring deployment enrollment and live proof of private mount identity/owner/
permissions, compatible supported runtime, worker consumption of the intended
queue, broker health and persistence/operator checks. It must revoke readiness
on mount/binary drift, expired/untrusted evidence or unavailable worker. This
stage does not replace those controls with a self-asserted JSON document or an
environment flag. Until implemented and verified, the gate stays closed even
when isolated container scenarios succeed.

## Retention and resources

The existing per-attempt 64 MiB, audio 16 MiB, MP4 32 MiB and caption 128 KiB
budgets are unchanged. Disposable tmpfs probes exercise actual disk and inode
exhaustion, wrong UID/root owner, world-readable/readonly private roots and symlink
failure; the existing budget enforces aggregate quota failure. These are bounded
pin/storage tests, not general filesystem capacity claims.

`core/visual_segment_retention.py` and `visual_segment_inventory` provide a
**report-only** bounded, no-follow inventory. Reference collection includes all
immutable snapshots, all Run inputs and all accepted receipts, including revoked
and cancelled history. Reference rows/JSON, directory depth and filesystem entry
counts are bounded. Symlink, unsafe permission, special-file and partial-budget
scans fail. No data is deleted, no deletion permission is returned, and an
`unreferenced_at_scan` object is not called safe to delete: pinning may be in
progress outside a database transaction.

```sh
python services/api/manage.py visual_segment_inventory --limit 1000
```

Current retention is indefinite for referenced assets and conservative for failed
attempts/orphans. There is no scheduled GC, destructive cleanup switch or automatic
age-based expiry. Safe deletion needs a separate reference/lease/pin coordination
proof and grace/backup/recovery policy. Interrupted inventory cannot corrupt
media because it performs no writes. Interrupted destructive cleanup is therefore
not an executed acceptance case. Operators must provision capacity and deny new
work when space/inodes are unavailable, not delete live inputs to make room.

## Gate accounting and verification

Local Docker/Redis/containers are unavailable. Local deployment attempt explicitly
recorded all D1–D10 as NOT_EXECUTED. Hosted evidence is uploaded by the dedicated
`private-segment-deployment-evidence` job; the normal full backend, PostgreSQL,
frontend/Chromium, Docker smoke and security-audit gates remain enabled.

| Gate | Final engineering evidence / remaining boundary |
|---|---|
| D1 | PASS in built isolated containers: shared persistent private mount, UID/GID 1000:1000, 0700/0600, cross-container writes/reads and restart persistence; production mount enrollment remains unavailable |
| D2 | PASS for exact-worker-derived API: complete native/font identities match; normal slim API is incompatible and remains ineligible |
| D3 | PASS with actual Redis/AOF and a separate prefork worker: persisted intent, render routing, claims, duplicate/stale delivery and reconciliation |
| D4 | PASS: real MP4/SRT/VTT and independent receipt validation; original AAC packet payload preserved |
| D5 | PASS for enumerated API/worker/broker interruptions, pin/receipt/ack windows, restart and stale replay; broader host/storage failure matrices are not certified |
| D6 | PASS for tested ownership/cancel/source/producer/evidence boundaries; production proxy and large/slow-stream revocation bounds remain blocked |
| D7 | PASS for tested isolated private/public/Job/token/hash routes; production public proxy/CDN configuration remains unverified |
| D8 | REPORT_ONLY: historical references retained, no deletion authorized; coordinated destructive GC is disabled |
| D9 | PARTIAL: closed production gates and nine built negative probes pass; independent expiring deployment enrollment is unavailable |
| D10 | PASS for full committed-tree regression, PostgreSQL, frontend/Chromium, builds, migrations and audits; live GPU/provider deployment quality is outside this evidence |


Historical local checks at initial commit preparation (before interruption):

* Pure segment and packaging/guard suite: **221 passed**.
* Real artifact/media/retention suite: **94 passed, 3 PostgreSQL-only skipped**.
* Django check: no issues. Migration consistency: no changes detected.
* Frontend: **350 tests**, build and both npm audits pass; **0 vulnerabilities**.
* Python dependency audit: **no known vulnerabilities**.
* Full local backend and final hosted suites are reported with the final commit;
  the unchanged local MuseTalk process-group limitation is not suppressed or
  patched. Local Chromium remains unavailable; hosted browser results are separate.

Exact final image IDs, hashes, executed gate states, failed/blocked cases and
hosted CI links belong to the final committed-tree evidence and completion report.
Do not borrow Phase 3B-5 results or label an image build as deployed acceptance.

Stop after Phase 3B-6. No producer service, fresh synthesis, Studio controls,
ordinary Save & Rerender, normal task/finalizer semantics, active playback, HLS,
public download, release promotion, branch merge or Phase 3C is implemented.

## Continuation local verification

The recovered virtual environment was reused; no application dependencies were
downloaded again. The full local command was:

```sh
VISUAL_MEDIA_REQUIRE=1 /workspace/scratch/b391289f356e/segment-venv/bin/python -m pytest -q tests --junitxml=/workspace/scratch/dda2975a8dc2/phase3b6-local-backend.xml
```

Result: **2,793 passed, 26 skipped, 63 subtests passed, 1 failed** in 353.91 s.
The sole failure is the unchanged
`tests/integration/test_musetalk_warmup_cleanup.py::test_orphan_check_detects_and_kills_marked_warmup_process`.
It was not patched, suppressed or excluded. This run includes the Phase 3B-1
through 3B-5 and legacy regressions; PostgreSQL-only skips are not race acceptance.
The subsequent changes are isolated harness/verification documentation only.

Additional executed commands, using that same Python environment:

```sh
python -m pytest -q tests/deployment/visual_segments/test_packaging.py
python services/api/manage.py check --settings=config.settings_test
python services/api/manage.py makemigrations --check --dry-run --settings=config.settings_test
PYTHONPATH=tests/deployment/visual_segments:services/api:services python -m vs_acceptance.faults missing_mount
PYTHONPATH=tests/deployment/visual_segments:services/api:services python -m vs_acceptance.faults runtime_drift
python tests/deployment/visual_segments/run.py --report-dir /workspace/scratch/dda2975a8dc2/local-container-evidence
git diff --check
```

Packaging: **8 passed**. Django check: no issues. Migrations: no changes detected;
`0044_private_segment_dispatch` remains the latest private-segment migration.
Both narrow fault probes rejected their invalid condition. Diff checks passed.
The local container command exited nonzero with **Docker unavailable**, explicitly
recording D1–D10 as NOT_EXECUTED. Docker, PostgreSQL and Redis containers were not
available locally. Hosted evidence below is separate from this local limitation.
Direct Git push lacked local credentials; the connected GitHub writer committed
the exact inspected trees using non-forced, expected-parent updates of the feature
branch. No merge, rebase, reset, clean or unrelated branch update was performed.

## Complete Phase 3B-6 file ledger

Paths changed from the confirmed Phase 3B-5 baseline, including recovered work:

- `.github/workflows/ci.yml`
- `docs/AI_VISUAL_DESIGNER_PHASE3B.md`
- `docs/AI_VISUAL_DESIGNER_PHASE3B6.md`
- `docs/visual_design/phase3b6/deployment-evidence.json`
- `infra/docker-compose.visual-private.yml`
- `infra/docker-compose.yml`
- `infra/dockerfiles/Dockerfile.api`
- `infra/dockerfiles/Dockerfile.private-api`
- `infra/dockerfiles/Dockerfile.worker`
- `infra/dockerfiles/local-storage-entrypoint.sh`
- `services/api/core/management/commands/visual_segment_inventory.py`
- `services/api/core/visual_private_volume.py`
- `services/api/core/visual_segment_artifacts.py`
- `services/api/core/visual_segment_dispatch.py`
- `services/api/core/visual_segment_retention.py`
- `services/api/core/visual_segment_validation.py`
- `tests/deployment/visual_segments/README.md`
- `tests/deployment/visual_segments/compose.yml`
- `tests/deployment/visual_segments/run.py`
- `tests/deployment/visual_segments/test_packaging.py`
- `tests/deployment/visual_segments/vs_acceptance/__init__.py`
- `tests/deployment/visual_segments/vs_acceptance/apps.py`
- `tests/deployment/visual_segments/vs_acceptance/dependencies.py`
- `tests/deployment/visual_segments/vs_acceptance/faults.py`
- `tests/deployment/visual_segments/vs_acceptance/ops.py`
- `tests/deployment/visual_segments/vs_acceptance/settings.py`
- `tests/integration/test_visual_segment_retention.py`

No dependency lockfiles, frontend code, ordinary rendering/TTS/avatar tasks,
publication handlers or migration files changed in Phase 3B-6.

## Completed built-container evidence

Engineering source commit: `346cc60db0797bd91f90b0a2a4502f78c46efa79`.
[CI run 40123137215](https://github.com/VisusVision/TalkingSlides/actions/runs/40123137215),
[artifact 12572462490](https://github.com/VisusVision/TalkingSlides/actions/runs/40123137215/artifacts/12572462490).
The committed [observation record](visual_design/phase3b6/deployment-evidence.json)
retains complete per-library hashes, image/process/mount identities, receipt/output
hashes, 20 scenario results and nine negative probes. This file is engineering
evidence only; it cannot enroll production readiness or authenticate a producer.

The actual external Redis/prefork worker/Gunicorn suite completed successfully.
Both serving processes and the shared private mount used UID/GID **1000:1000**;
API and worker saw the same device/inode (**2049 / 8945091**) and **0700** root.
Cross-container writes/reads and media access after API/worker restart passed.
Private media files remained 0600. Root-entrypoint privilege drop passed in both
built default API and worker images. Wrong-owner, permissive, readonly, symlink,
space/inode/quota failures were rejected; an unmounted directory was rejected by
the mount inspector and a wrong FFmpeg digest by the native verifier.

The compatible API and worker matched all **205** linked library hashes, FFmpeg,
FFprobe, fontconfig and both DejaVu fonts. The normal slim API did **not** match;
it was recorded as incompatible rather than admitted by changing the contract.
Observed FFmpeg: **4.4.2-0ubuntu0.22.04.1**. Supported recipe/runtime remains
`private-segment-linux.v1`, `librsvg.v1`, the existing CRF-23/medium H.264 recipe
and immutable AAC-copy semantics.

| Built native component | SHA-256 |
|---|---|
| FFmpeg | `sha256:36d94a605d612e4090d1b8aec889d0c0801c6eafb1593c90f5c0dfd2e2966a45` |
| FFprobe | `sha256:d4f3ef9c12be756793cad83dd2004d89f49c1c4094053bfbbe7e28925c8fa4fd` |
| libx264.so.163 | `sha256:74263a09bc162e1ef7268e3d93b0b7839977292f5d702104830ca0998d85b27c` |
| librsvg-2.so.2 | `sha256:de487005050dc31281932e2de24d8307167b4c1cd9c042521b34602fc93c866f` |
| libfontconfig.so.1 | `sha256:7d688909aab1c566b60832045fef012ea118ff7ee189df87608fbff999d3bd0d` |
| DejaVu Sans regular | `sha256:690243adfefe0ce154b547db6205794bd30ac4277275179517a90994f4980648` |
| DejaVu Sans bold | `sha256:d1c3ff99f1e1ce1827a33efd4dad81f40babda06bff9e43bd7591c86662a287b` |
| Complete linked native runtime | `sha256:67cf7c0cbfe497555d865b6d0310fa6f3948e7f7a29b36e12a92f741d35cf8b7` |

| Built image | Observed image ID |
|---|---|
| `vs-worker:acceptance` | `sha256:3814eda6ae903a1b0cfcf0b840a44247fbcaceebd31051ca733658210d82b0c5` |
| `vs-private-api:acceptance` | `sha256:ddf8d012e2f66895b0562453d1b6943e22ed2e4cfcf6a5384b6a9632ffebf67e` |
| `vs-default-api:acceptance` | `sha256:54b99a65fdb710c5a6fedc42c4c9f18ad5f071a7dde34efaa0b899168c8850a4` |

Actual output was a **38,179-byte 1920×1080 H.264/yuv420p/25-fps MP4**, 63 frames
and 2,520 ms, with AAC-LC mono 44.1 kHz audio lasting 2,335 ms. SRT/VTT were
69/77 bytes; exact caption text and timing were validated. AAC packet payload
remained `sha256:05c9c0e0b1cfe5b55f5c72a697f62a74aa900da42ed94b1ea9d86195ddc44d91`.
No speech was generated. Full/open/suffix ranges, HEAD/lengths/types, invalid
ranges, owner boundaries, guessed public hashes and ordinary Job/token denial
passed. Three client resets returned private descriptor count to **0**; this is
fixture-sized direct-Gunicorn evidence, not proxy/slow-stream capacity acceptance.

All **20** recorded recovery/authorization scenarios passed: committed intent
before/after send and API kill; worker kill after claim, during FFmpeg, after
output pinning, and after receipt acceptance before acknowledgement; restart
before claim; Redis unavailable/restart/reconnect; abrupt Redis kill with a queued
AOF-persisted message recovered without republishing; stale delivery/receipt
preservation; cancellation, evidence revocation, source edits and producer loss
during rendering; owner/source/revocation/producer denial after acceptance; and
producer denial before reservation/dispatch and before real worker claim. Each
render-time denial waits for executor exit before asserting no accepted receipt.

The post-cancellation inventory retained referenced historical objects, reported
17 files / 1,731,337 bytes and authorized **no deletion**. No destructive cleanup,
normal lesson rendering, active media replacement or publication occurred.

## Final implementation CI and production prerequisites

Final engineering implementation SHA:
**`346cc60db0797bd91f90b0a2a4502f78c46efa79`**.
All six jobs in [run 40123137215](https://github.com/VisusVision/TalkingSlides/actions/runs/40123137215)
passed on that exact commit. The report/evidence correction that follows changes
only these two Markdown reports and the observation JSON. Its final SHA and its
independently completed CI run are supplied in the completion response; resolve
the report commit locally with `git log -1 --format=%H -- docs/AI_VISUAL_DESIGNER_PHASE3B6.md`.
No self-referential commit hash is embedded into its own contents.

| Check | Command / CI step | Observed implementation result |
|---|---|---|
| Full backend | `VISUAL_MEDIA_REQUIRE=1 pytest -q tests --junitxml=pytest-report.xml` with `DJANGO_SETTINGS_MODULE=config.settings`, `USE_SQLITE_FOR_TESTS=1`, `INTELLIGENCE_OLLAMA_CALIBRATION_ENABLED=false` | 2,794 passed, 26 skipped, 63 subtests passed; 874.82 s; unchanged MuseTalk case passed hosted |
| PostgreSQL races/API/media | `pytest -q tests/integration/test_visual_drafts.py tests/integration/test_visual_evidence.py tests/integration/test_visual_render_snapshots.py tests/integration/test_visual_segment_runs.py tests/integration/test_visual_segment_media.py tests/integration/test_visual_segment_api.py --ds=config.settings_test_postgres` with mandatory native media | 246 passed, no skips; 292.61 s |
| Actual container deployment | README's three exact image-build commands, then `python tests/deployment/visual_segments/run.py --report-dir "$RUNNER_TEMP/visual-segment-deployment"` | Passed: 20 process/recovery/authorization scenarios and nine negative probes; artifact linked above |
| Frontend | `npm ci`, `npm run test:ci`, `npm run build` | Passed; 350 tests / 49 files |
| Chromium | `npx playwright install --with-deps chromium`, `npm run e2e` | 36 passed |
| npm audits | `npm audit --audit-level=high`; `npm audit --omit=dev --audit-level=moderate` | Both clean: zero vulnerabilities |
| Python audit | `pip-audit -r services/api/requirements.txt` | No known vulnerabilities |
| Migration consistency | `python manage.py makemigrations --check --dry-run` under the workflow's SQLite test configuration | Passed; no new migration |
| Default Docker builds | Existing `docker buildx bake` API/worker/TTS smoke step in `.github/workflows/ci.yml`, unchanged arguments plus existing CI cache settings | Passed |
| k6 | `k6 inspect infra/loadtest/k6-production-sprint.js` | Passed |

The source pipeline, normal rerender/finalizer/provider modules, active lesson
state and public playback integration were not changed during this continuation.
The complete 27-file stage ledger above includes the recovered Phase 3B-6 work.
Generated media, host logs, temporary databases and test credentials are not
committed. Native/image observations are evidence of the named engineering run,
not universal cross-build goldens or deployable production attestations.

Remaining prerequisites keep **PHASE3B_DEPLOYMENT_BLOCKED**:

1. A real independently authenticated producer-receipt source and current
   producer authorization/revocation lookup. This service is explicitly excluded
   from this phase; historical audio is not retroactively certified.
2. Independently controlled, expiring deployment enrollment and live revocation
   on mount/UID/permission/runtime/queue/broker/worker drift. A Boolean or the
   observation JSON cannot provide readiness. `_deployment_ready()` stays false.
3. Reviewed immutable production image/storage selection and compatible API source
   layout. The ordinary slim API is not admitted as a matching private runtime.
4. Actual production proxy/CDN isolation, buffering/disconnect/large-stream bounds,
   capacity/deadline/quota and appropriate real deployment/GPU variant acceptance.
5. A separately justified pin/lease/reference-coordinated retention grace/backup
   policy before destructive cleanup. Current inventory cannot delete anything.

No normal traffic was enabled. Stop at Phase 3B-6; no merge or Phase 3C work.
