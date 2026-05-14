# Troubleshooting

## Docker Build Is Slow

CUDA, PyTorch, TTS, and avatar dependencies can be large. First builds can take a long time. Use the lightweight default start script when you only need API/frontend work:

```powershell
.\scripts\windows-dev-start.ps1
```

Start TTS, worker, and avatar services only when needed:

```powershell
.\scripts\windows-dev-start.ps1 -WithTts -WithWorker -WithAvatar
```

## Docker Desktop Not Ready

If Compose fails immediately, verify Docker Desktop is running:

```powershell
docker version
docker compose version
docker compose -f infra\docker-compose.yml config
```

## Vite or npm Missing

Install Node.js 20+, then:

```powershell
cd services\frontend
npm ci
npm run build
```

If `node_modules` is missing, `windows-dev-setup.ps1` can install it.

## Django Is Not Installed

If tests fail with `ModuleNotFoundError: django`, the command is using system Python. Use the repo venv:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_ready_endpoint.py -q
```

Create/install the venv:

```powershell
.\scripts\windows-dev-setup.ps1
```

## Redis or Celery Problems

Check services:

```powershell
docker compose -f infra\docker-compose.yml ps
docker compose -f infra\docker-compose.yml logs -f redis
docker compose -f infra\docker-compose.yml logs -f worker
```

Common causes:

- Redis container is not running.
- Worker is consuming the wrong queue.
- `CELERY_BROKER_URL` or `CELERY_RESULT_BACKEND` does not match Redis.
- Avatar jobs are sent to `avatar` but no avatar worker is running.

## TTS Service Not Ready

Check:

```powershell
Invoke-WebRequest http://localhost:8001/ready
docker compose -f infra\docker-compose.yml logs -f tts_service
```

XTTS startup can be slow due to model loading. For lightweight development, set `XTTS_ENABLED=0` or avoid starting TTS until needed.

## Avatar GPU Unavailable

Start without avatar first:

```powershell
.\scripts\windows-dev-start.ps1
```

Then validate GPU support:

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

If that fails, fix NVIDIA drivers, WSL2 GPU support, or Docker Desktop GPU integration before debugging application code.

## Playback Token Errors

Check:

- Lesson has a completed `video_export` job.
- Lesson is published if accessed anonymously.
- `MEDIA_TOKEN_SECRET` is set consistently across API instances.
- `LESSON_PROTECTION_DEFAULT_MODE` is appropriate for local/prod.
- The requested media file exists under `STORAGE_ROOT`.

## 409 Playback Session Lock

A `409` can mean the lesson is already active in another browser session or tab. Close the other session or refresh after the lock expires. For local testing, verify heartbeat settings:

```text
LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION=1
VITE_PLAYER_HEARTBEAT_ENABLED=true
```

## HLS Missing Manifest

If secure playback expects HLS but no manifest exists:

- Confirm the worker wrote `playback_assets.json`.
- Confirm HLS packaging was enabled for the render path.
- Use MP4 fallback only when policy allows it.
- For `drm_protected`, missing HLS should block playback by design.

## Local Storage Confusion

Use repo-root `storage_local/`, mounted as `/app/storage_local` in containers. Do not use `infra/storage_local/` as the active runtime path.

## Env File Problems

If production settings fail fast, read the error. Do not bypass it. Common missing production values are `SECRET_KEY`, `POSTGRES_HOST`, `MEDIA_TOKEN_SECRET`, `ALLOWED_HOSTS`, and `CORS_ALLOWED_ORIGINS`.
