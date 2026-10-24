# Phase 3B-6 — Deployment packaging and isolated security evidence

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

| Gate | Evidence scope / unresolved boundary |
|---|---|
| D1 | Built cross-process private writes/reads, numeric IDs, root privilege drop and restart checks in suite; no local container acceptance |
| D2 | Built normal API observations and exact-worker-derived API comparison; real deployment image enrollment remains closed |
| D3 | Actual external Redis and separate prefork worker tests; not memory/eager execution |
| D4 | Real fixture AAC-copy MP4/SRT/VTT, independent media verification and immutable receipt in suite |
| D5 | Enumerated real process faults/replay; exhaustive production topology/host/storage failure matrix not established |
| D6 | Enumerated owner/cancel/revoke/source controls across processes; production slow-stream/proxy acceptance outstanding |
| D7 | Tested isolated HTTP routes and mount isolation; production public server/CDN configuration unverified |
| D8 | Read-only inventory and reference retention; destructive GC disabled, no claim of interrupted-delete acceptance |
| D9 | Explicit closed production gates, actual bounded tmpfs failure probes and broker failure; live expiring attestation still missing |
| D10 | Existing regression gates retained; no live historical lesson replacement, real TTS/GPU/avatar deployment acceptance or Phase 3C |

Observed local checks at initial commit preparation:

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
