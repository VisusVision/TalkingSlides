# Local Development

Use this for daily development after the first Windows setup. The shorter coworker path remains in [LOCAL_DEVELOPMENT_QUICKSTART.md](LOCAL_DEVELOPMENT_QUICKSTART.md).

## Start and Stop

Default core stack:

```powershell
.\scripts\windows-dev-start.ps1
```

With TTS and render worker:

```powershell
.\scripts\windows-dev-start.ps1 -WithTts -WithWorker
```

With GPU avatar worker:

```powershell
.\scripts\windows-dev-start.ps1 -WithTts -WithWorker -WithAvatar
```

Stop:

```powershell
.\scripts\windows-dev-stop.ps1
```

Remove volumes only when you intentionally want to delete local Compose data:

```powershell
.\scripts\windows-dev-stop.ps1 -RemoveVolumes
```

## Backend Checks

Use the repo virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_ready_endpoint.py -q
cd services\api
..\..\.venv\Scripts\python.exe manage.py check
..\..\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
cd ..\..
```

If `python -m pytest` fails with `ModuleNotFoundError: django`, you are using the system Python instead of the project venv.

## Frontend

```powershell
cd services\frontend
npm ci
npm run build
npm run dev -- --host 0.0.0.0 --port 3000
cd ..\..
```

The frontend reads `VITE_API_BASE_URL`, defaulting to `http://localhost:8000/api/v1`.

## Docker Compose Logs

```powershell
docker compose -f infra\docker-compose.yml ps
docker compose -f infra\docker-compose.yml logs -f api
docker compose -f infra\docker-compose.yml logs -f frontend
docker compose -f infra\docker-compose.yml logs -f worker
docker compose -f infra\docker-compose.yml logs -f tts_service
```

## Celery and Worker Notes

- `worker` consumes the render queue for upload, extraction, TTS orchestration, and base video work.
- `worker-avatar` consumes the avatar queue and should be treated as GPU-bound.
- Keep avatar worker concurrency at `1` per GPU until the target host is benchmarked.
- Redis is the broker and result backend through `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND`.

Local manual worker command:

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.settings"
$env:PYTHONPATH = "$PWD\services\api;$PWD\services;$PWD\services\scripts;$PWD\services\tts_service"
$env:STORAGE_ROOT = "$PWD\storage_local"
$env:TTS_SERVICE_URL = "http://localhost:8001"
.\.venv\Scripts\celery.exe -A worker worker --loglevel=info --pool=solo
```

On WSL/Linux, use `:` instead of `;` in `PYTHONPATH`.

## TTS Readiness

The API and worker expect the TTS service at `TTS_SERVICE_URL`. For lightweight development, set `XTTS_ENABLED=0` to avoid large model startup and rely on fallback behavior. Real XTTS voice cloning needs model downloads/cache and more startup time.

## Avatar Worker Notes

Avatar generation is optional for normal API/frontend work. Real generation needs GPU runtime, model assets, LivePortrait paths, MuseTalk paths, and storage mounted consistently. The current stable behavior is non-blocking: base videos publish and play before the avatar overlay finishes.

## Local Storage

Runtime files live under repo-root `storage_local/` and are mounted into containers as `/app/storage_local`. Do not commit:

- `storage_local/`
- generated media
- `services/api/db.sqlite3`
- `infra/.env`
- `node_modules/`
- `services/frontend/dist/`
- local scratch scripts or smoke artifacts

## Useful Smoke Commands

```powershell
.\scripts\check_repo_setup.ps1
.\scripts\windows-dev-setup.ps1 -CheckOnly
Invoke-WebRequest http://localhost:8000/api/v1/ready/
Invoke-WebRequest http://localhost:8001/ready
```
