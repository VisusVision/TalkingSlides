# Avatar Cloud Deployment

This runbook covers Docker/GPU deployment for `worker-avatar` across local
Windows Docker Desktop, local Linux, cloud Linux GPU VMs, single GPU, and
multi-GPU hosts.

The existing local workflow stays on `infra/docker-compose.yml`. Cloud and
production-like hosts add `infra/docker-compose.avatar.cloud.yml`, which uses a
prebuilt worker image and persistent model/media mounts instead of source bind
mounts.

## Local Mode

Windows Docker Desktop and local Linux development can keep using the existing
compose file:

```bash
docker compose -f infra/docker-compose.yml config
docker compose -f infra/docker-compose.yml --profile avatar up -d worker-avatar
```

This mode keeps the current local bind mounts and `ai_academy_worker:local`
image behavior.

## Cloud Mode

Build or publish a worker image that contains the application source and avatar
runtime dependencies, then point the override at it:

```bash
export AVATAR_IMAGE=registry.example.com/talkingslides/worker-avatar:2026-07-07
export AVATAR_MEDIA_VOLUME=/mnt/talkingslides/storage
export AVATAR_MODEL_VOLUME=/mnt/talkingslides/models
export MUSETALK_MODEL_PATH=/app/storage_local/models
export STORAGE_ROOT=/app/storage_local

docker compose -f infra/docker-compose.yml -f infra/docker-compose.avatar.cloud.yml config
```

The cloud override mounts:

- media/storage at `/app/storage_local`
- models at `/app/storage_local/models`

Do not use source bind mounts in this mode. The image must already contain
`/app/api`, `/app/worker`, `/app/scripts`, `/app/avatar`, and the avatar runtime.

## Model Provisioning

Provision the MuseTalk model bundle into the host path or named volume mounted
at `/app/storage_local/models`. The required layout is documented in
[AVATAR_MODEL_PROVISIONING.md](AVATAR_MODEL_PROVISIONING.md).

Validate without starting a worker:

```bash
python scripts/check_avatar_models.py /mnt/talkingslides/models --json
```

Inside the worker image the same checker is available at:

```bash
python /app/scripts/check_avatar_models.py /app/storage_local/models --json
```

## Persistent Storage

Use durable storage for both mounts:

```bash
export AVATAR_MEDIA_VOLUME=/mnt/talkingslides/storage
export AVATAR_MODEL_VOLUME=/mnt/talkingslides/models
```

If those variables are not set, the cloud override uses named Docker volumes
`talkingslides_avatar_media` and `talkingslides_avatar_models`.

`STORAGE_ROOT` must remain `/app/storage_local` inside containers.
`MUSETALK_MODEL_PATH` should remain `/app/storage_local/models`.

## Single GPU

All GPUs visible to one worker:

```bash
export AVATAR_GPU_DEVICE=all
export NVIDIA_VISIBLE_DEVICES=all
export NVIDIA_DRIVER_CAPABILITIES=compute,utility
export CELERY_AVATAR_QUEUE=avatar
export CELERY_WORKER_QUEUES=avatar
export CELERY_AVATAR_WORKER_CONCURRENCY=1
export AVATAR_GPU_SERIAL_LOCK_PATH=/app/storage_local/locks/avatar_gpu_all.lock

docker compose -f infra/docker-compose.yml -f infra/docker-compose.avatar.cloud.yml --profile avatar up -d worker-avatar
```

One specific GPU:

```bash
export AVATAR_GPU_DEVICE=device=0
export NVIDIA_VISIBLE_DEVICES=0
export AVATAR_GPU_SERIAL_LOCK_PATH=/app/storage_local/locks/avatar_gpu_0.lock

docker compose -f infra/docker-compose.yml -f infra/docker-compose.avatar.cloud.yml --profile avatar up -d worker-avatar
```

For the single `worker-avatar` service, `NVIDIA_VISIBLE_DEVICES` is the
enforced visibility control. `AVATAR_GPU_DEVICE` is also used by the preflight
Docker GPU check. For strict one-worker-per-GPU placement, prefer the
`worker-avatar-gpuN` services below; they use Compose `device_ids`.

Keep `CELERY_AVATAR_WORKER_CONCURRENCY=1`.

## Multi GPU

Run one worker service per GPU:

```bash
export CELERY_AVATAR_QUEUE=avatar
export CELERY_WORKER_QUEUES=avatar
export CELERY_AVATAR_WORKER_CONCURRENCY=1
export AVATAR_GPU0_DEVICE=0
export AVATAR_GPU1_DEVICE=1
export AVATAR_GPU0_LOCK_PATH=/app/storage_local/locks/avatar_gpu_0.lock
export AVATAR_GPU1_LOCK_PATH=/app/storage_local/locks/avatar_gpu_1.lock

docker compose -f infra/docker-compose.yml -f infra/docker-compose.avatar.cloud.yml --profile avatar-multigpu up -d worker-avatar-gpu0 worker-avatar-gpu1
```

The override includes `worker-avatar-gpu0` through `worker-avatar-gpu3`.
Each service has its own `NVIDIA_VISIBLE_DEVICES` value and lock path. Do not
point multiple GPU workers at the same `AVATAR_GPU_SERIAL_LOCK_PATH` unless you
intentionally want all avatar jobs serialized across those GPUs.

## Isolated Smoke

Smoke must not consume the real `avatar` queue. Use the `avatar-smoke` queue:

```bash
export CELERY_AVATAR_QUEUE=avatar-smoke
export CELERY_WORKER_QUEUES=avatar-smoke
export AVATAR_GPU_SERIAL_LOCK_PATH=/app/storage_local/locks/avatar_smoke.lock

docker compose -f infra/docker-compose.yml -f infra/docker-compose.avatar.cloud.yml --profile avatar-smoke up -d worker-avatar-smoke
```

Health check only:

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.avatar.cloud.yml ps worker-avatar-smoke
docker compose -f infra/docker-compose.yml -f infra/docker-compose.avatar.cloud.yml exec worker-avatar-smoke python /app/scripts/avatar_worker_health.py
```

Stop smoke before production:

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.avatar.cloud.yml stop worker-avatar-smoke
```

## Production Queue Start

After model checks and smoke pass:

```bash
export CELERY_AVATAR_QUEUE=avatar
export CELERY_WORKER_QUEUES=avatar
export CELERY_AVATAR_WORKER_CONCURRENCY=1

docker compose -f infra/docker-compose.yml -f infra/docker-compose.avatar.cloud.yml --profile avatar up -d worker-avatar
```

## Preflight

Before worker start:

```bash
python scripts/avatar_cloud_preflight.py \
  --model-path /mnt/talkingslides/models \
  --storage-root /mnt/talkingslides/storage \
  --redis-url redis://localhost:6379/0 \
  --skip-worker-health
```

After worker start:

```bash
python scripts/avatar_cloud_preflight.py \
  --model-path /mnt/talkingslides/models \
  --storage-root /mnt/talkingslides/storage \
  --redis-url redis://localhost:6379/0 \
  --worker-health-url http://127.0.0.1:17860/health
```

## Validation Commands

```bash
docker compose -f infra/docker-compose.yml config
docker compose -f infra/docker-compose.yml -f infra/docker-compose.avatar.cloud.yml config
python scripts/check_avatar_models.py --json
```

If Docker GPU is available locally:

```bash
docker compose -f infra/docker-compose.yml --profile avatar ps
```

Do not start smoke workers against `CELERY_AVATAR_QUEUE=avatar`.

## Troubleshooting

CUDA unavailable:
Check `nvidia-smi`, NVIDIA Container Toolkit, `AVATAR_GPU_DEVICE`, and
`NVIDIA_VISIBLE_DEVICES`. For a specific GPU use `AVATAR_GPU_DEVICE=device=0`
and `NVIDIA_VISIBLE_DEVICES=0`.

Model files missing:
Run `python scripts/check_avatar_models.py <model-root> --json`. The mounted
container path must be `/app/storage_local/models`.

Wrong GPU device:
Inspect `docker compose ... config` and the worker health output. The service
should show the expected `NVIDIA_VISIBLE_DEVICES` and the MuseTalk health
payload should report CUDA available.

Lock path shared incorrectly:
Each GPU worker should use a GPU-specific path such as
`/app/storage_local/locks/avatar_gpu_0.lock`. A shared lock path serializes
all workers that use it.

Redis not reachable:
Verify `REDIS_URL`/`CELERY_BROKER_URL`, container networking, and
`redis-cli PING`. The preflight script opens a TCP connection and sends PING.

Storage not writable:
Verify the host mount ownership and that the container can write to
`/app/storage_local`. The storage entrypoint can create the top-level directory,
but production hosts should provision ownership intentionally.
