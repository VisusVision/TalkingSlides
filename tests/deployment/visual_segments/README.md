# Isolated private segment deployment evidence

This harness is not a production configuration or producer service. Its test
settings, fault pauses and committed synthetic receipt anchors are mounted
read-only only into disposable containers. Application images contain none of
these settings or fixture enrollment code. Production `_producer_dependencies()`
and `_deployment_ready()` remain closed.

Requirements: Docker Engine/Compose v2, an isolated host with sufficient space for
the actual CUDA worker image, Python 3.11+ on the controller, Internet access for
image builds, and no GPU/model download. Do not point this Compose project at
existing databases/volumes/brokers. It uses an internal network, generated project
name, disposable PostgreSQL/Redis volumes, no production `.env`, no exposed broker
or database ports, and a loopback-only ephemeral Gunicorn port.
Only the API joins an additional HTTP bridge for that loopback publication;
the worker, broker and database remain exclusively on the internal network.

```sh
docker build --build-arg INSTALL_TEST_DEPS=1 --build-arg INSTALL_AVATAR_RUNTIME_DEPS=0 --build-arg INSTALL_OPENMMLAB_DEPS=0 --build-arg DOWNLOAD_LIVEPORTRAIT_WEIGHTS=0 -f infra/dockerfiles/Dockerfile.worker -t vs-worker:acceptance .
docker build -f infra/dockerfiles/Dockerfile.api -t vs-default-api:acceptance .
docker build --build-arg PRIVATE_WORKER_IMAGE=vs-worker:acceptance -f infra/dockerfiles/Dockerfile.private-api -t vs-private-api:acceptance .
python tests/deployment/visual_segments/run.py --report-dir /tmp/visual-segment-deployment-evidence
```

The private API variant inherits the **same built worker image**, with no package
installation between their native-runtime layers. Normal slim API packaging is
also observed and compared; it is not silently admitted if incompatible. For a
real deployment, use a reviewed immutable worker image digest/ID, not a moving
local tag. Normal API/worker/GPU defaults are not replaced by the harness.

The controller first observes and records the actual worker FFmpeg, FFprobe,
complete linked-runtime hash and fonts, then freezes that engineering support
record in a read-only mount **before** reserving anything. Both processes must
match it. This test support record is not production readiness attestation or
producer authentication. The original producer proof digests remain independently
anchored in the existing reviewed fixtures. No current audio file is certified by
hashing it, no new speech is synthesized, and no expected request identity is
rewritten by a worker.

The suite uses real external Redis, real Gunicorn HTTP and a separately running
Celery process on `render`, not eager execution or memory transport. Test-only
barriers stop exact boundaries; the controller actually kills/restarts containers.
The native pause stops the real FFmpeg child with SIGSTOP before controller
cancellation/revocation/source-edit or worker termination, then resumes it where
appropriate. Pauses have 45-second deadlines. Expiry/cooldown test-clock updates
change only operational fields in the isolated test database, never immutable
request content. Retrying an expired claim uses the real explicit reconciliation
and generation fence. No automatic retry service or new queue is introduced.

`acceptance.json` records image IDs, actual UID/GID, native builds, media digests,
executed scenarios and separate gate states. `containers.log` contains disposable
engineering logs. These files do not contain auth tokens or production credentials.
No generated MP4 is committed. Missing Docker is a failing execution, recorded as
NOT_EXECUTED for deployment gates; unit tests are never substituted for deployment
acceptance.

The current reporter intentionally cannot classify production deployment as
accepted: production readiness enrollment/expiry/mount-and-worker heartbeat
controls, a real producer source and the production reverse proxy remain outside
its evidence. `PHASE3B_DEPLOYMENT_BLOCKED` is explicit even if the enumerated
engineering scenarios pass. A successful CI evidence job means its tests ran,
not that all D1–D10 or a production rollout was accepted. Unimplemented/partial
gates are preserved in the report. No Phase 3C publication is authorized.
