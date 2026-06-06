# Repo Health Check

Run these from a fresh clone after copying `infra/.env.example` to `infra/.env` and activating the Python environment you want to test.

## Basic Checks

```powershell
python services/api/manage.py check
```

Requires Django/API dependencies. Set `DJANGO_SETTINGS_MODULE=config.settings` if your shell does not already have it. Without `POSTGRES_HOST`, the API uses local SQLite.

```powershell
python -m pytest tests/test_storage_service.py tests/test_media_api_access.py -q
```

Requires `requirements-test.txt` and `services/api/requirements.txt`.

```powershell
python -m pytest tests/integration/test_secure_playback.py tests/integration/test_transcript_editor_pipeline.py -q
```

Requires API and test dependencies. These are focused integration tests and should not require GPU/avatar models.

```powershell
cd services/frontend
npm install
npm run build
```

Requires Node.js and npm. Run `npm install` again if `node_modules/` is missing or stale.

```powershell
docker compose -f infra/docker-compose.yml config
```

Requires Docker Desktop. This validates Compose syntax and environment interpolation; copy `infra/.env.example` to `infra/.env` first.

## Optional One-Command Smoke Check

From the repo root on Windows PowerShell:

```powershell
.\scripts\check_repo_setup.ps1
```

The script reports Python, Node, env-template presence, Docker Compose config parsing when Docker is available, and Django `manage.py check` when Django is installed. Missing GPU tools, ffmpeg, or avatar models are not treated as required for basic setup.
