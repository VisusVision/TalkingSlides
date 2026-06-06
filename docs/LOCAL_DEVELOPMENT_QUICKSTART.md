# Local Development Quickstart

This is the basic local setup path for coworkers who need the API, frontend, storage, and lightweight TTS pieces to start. The full avatar/GPU path is optional and heavier; it is not required for basic API/frontend development.

## Prerequisites

- Windows 11.
- Docker Desktop with WSL 2 integration enabled.
- Git for Windows, Git Bash, or WSL for commands like `cp`.
- Python 3.10+ available as `python`.
- Node.js 20+ and npm for the frontend.
- Optional: ffmpeg for local video rendering checks.

## 1. Copy Environment

From the repo root:

```bash
cp infra/.env.example infra/.env
```

The default local storage path is repo-root `storage_local/`. Docker mounts that folder into API, worker, and TTS containers as `/app/storage_local`. Do not use `infra/storage_local/` for local runtime media.

Keep `STORAGE_BACKEND=filesystem` for normal coworker setup. Existing local env files that still use `STORAGE_BACKEND=local` remain compatible, but `filesystem` is the canonical value. Compose MinIO is available for optional S3 adapter readiness checks only; it does not serve runtime media by default.

## 2. Start Docker Services

From the repo root:

```bash
docker compose -f infra/docker-compose.yml up --build
```

This starts Postgres, Redis, MinIO, API, TTS, worker, avatar worker, and frontend. The avatar worker needs GPU/runtime assets for the full avatar path; if you only need API/frontend work, use the local API/frontend commands below and treat avatar startup as optional.

Useful local URLs:

- API: `http://localhost:8000`
- TTS service: `http://localhost:8001`
- Frontend: `http://localhost:3000`
- MinIO console: `http://localhost:9001`

## 3. Local API Setup

Use this when developing the Django API outside Docker:

```powershell
cd services/api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DJANGO_SETTINGS_MODULE = "config.settings"
$env:STORAGE_ROOT = "..\..\storage_local"
python manage.py migrate
python manage.py check
python manage.py runserver 8000
```

Without `POSTGRES_HOST`, Django uses SQLite at `services/api/db.sqlite3`. With Docker Postgres, keep the database variables from `infra/.env`.

## 4. Local Frontend Setup

```powershell
cd services/frontend
npm install
npm run dev -- --host 0.0.0.0 --port 3000
```

The frontend reads `VITE_API_BASE_URL` and defaults to `http://localhost:8000/api/v1`.

## 5. Local TTS Lightweight Mode

For basic development, skip XTTS model loading and rely on gTTS/silent fallback:

```powershell
cd services/tts_service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:STORAGE_ROOT = "..\..\storage_local"
$env:XTTS_ENABLED = "0"
uvicorn main:app --host 0.0.0.0 --port 8001
```

XTTS, voice cloning, model caches, and GPU acceleration are not required for a lightweight API/frontend startup.

## 6. Local Worker Note

The worker needs the API code on `PYTHONPATH`, Redis, the TTS service URL, and `STORAGE_ROOT` pointing at repo-root `storage_local/`.

From the repo root:

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.settings"
$env:PYTHONPATH = "$PWD\services\api;$PWD\services;$PWD\services\scripts;$PWD\services\tts_service"
$env:STORAGE_ROOT = "$PWD\storage_local"
$env:TTS_SERVICE_URL = "http://localhost:8001"
celery -A worker worker --loglevel=info --pool=solo
```

On Linux/macOS/WSL, use `:` instead of `;` in `PYTHONPATH`.

## Common Problems

- Missing `infra/.env`: run `cp infra/.env.example infra/.env` before Docker Compose.
- Wrong `STORAGE_ROOT`: local runtime media should live in repo-root `storage_local/`; Docker containers should use `/app/storage_local`.
- Ports already in use: stop the process using `3000`, `8000`, `8001`, `5432`, `6379`, `9000`, or `9001`, or change the Compose port mapping for local testing.
- Migrations not run: run `python services/api/manage.py migrate` locally or `docker compose -f infra/docker-compose.yml exec api python manage.py migrate`.
- Frontend dependencies missing: run `cd services/frontend && npm install`.
- GPU/avatar runtime unavailable: MuseTalk/LivePortrait/avatar preview is optional and heavy. Basic API, frontend, storage, and lightweight TTS startup do not require avatar models or GPU setup.
