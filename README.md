# TalkingSlides

TalkingSlides converts presentation and document sources into narrated lesson videos, then serves them through a web learning platform with catalog, studio, player, settings, and analytics surfaces.

This repository currently includes a Django REST API, FastAPI TTS microservice, Celery worker pipeline, Vite React frontend, avatar runtime integration (LivePortrait and MuseTalk), Docker-based local infrastructure, and an extensive backend integration test suite.

[![Python](https://img.shields.io/badge/Python-3.10-blue.svg)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-5.x-092E20.svg)](https://www.djangoproject.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.11x-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18.x-61DAFB.svg)](https://react.dev/)
[![License](https://img.shields.io/badge/License-TBD-lightgrey.svg)](#license)

---

## Overview

TalkingSlides is an end-to-end lesson production system.

- Instructors upload PPTX, PDF, DOCX, or TXT lesson sources.
- The worker extracts pages/slides and narration text.
- Text is segmented and synthesized into speech with XTTS v2 (when available), then gTTS fallback, then silent fallback.
- Slide visuals and audio are rendered into MP4 with subtitles.
- Generated assets are stored under `STORAGE_ROOT` and exposed through API playback endpoints.
- Students consume lessons in the React player and browse discovery/catalog pages.

The codebase also includes advanced playback protection, avatar preparation and preview flows, and optional pronunciation-suggestion tooling, with some of these features currently backend-complete but frontend-partial.

## Key Features

- Multi-format lesson upload: PPTX, PDF, DOCX, TXT, and optional cover.
- Transcript extraction and page/chunk segmentation for long-form narration.
- TTS pipeline with XTTS voice cloning plus robust fallback chain.
- Deterministic Turkish and English pronunciation normalization.
- Studio-side pronunciation preview without synthesis (fail-open).
- Project-level TTS settings persistence and rerender integration.
- Optional LLM-based pronunciation suggestions (disabled by default).
- Background rendering with Celery + Redis task orchestration.
- Subtitle (SRT) generation and output media packaging.
- Avatar upload, validation, prepare, preview, and worker execution paths.
- Tokenized playback endpoints and DRM/HLS contract scaffolding.
- React surfaces for Home, Browse, Watch, Studio, Settings, Library, Analytics.

## How It Works

```text
Lesson Source Upload (PPTX/PDF/DOCX/TXT)
  -> API Project + Job creation
  -> Celery worker pipeline start
  -> Slide/Page extraction + note/text extraction
  -> Transcript paging + TTS chunking
  -> TTS synthesis (XTTS -> gTTS -> silent fallback)
  -> Per-page video render + concat + subtitle generation
  -> Playback metadata + storage persistence
  -> Catalog/watch endpoints + React player consumption
```

Primary execution path:

1. Upload request creates `Project` and `Job` in Django.
2. API enqueues `worker.tasks.process_pptx_to_video`.
3. Worker uses `services/scripts/pptx_extract.py` for source extraction.
4. Worker segments text via `services/scripts/text_segmentation.py`.
5. Worker calls TTS via `services/scripts/tts_client.py` and `services/tts_service/main.py`.
6. FFmpeg helpers render page videos and final output.
7. Worker writes `playback_assets.json`, subtitles, and job status.

## Architecture

| Layer | Current Role | Main Paths |
|---|---|---|
| Frontend | React app for catalog/player/studio/settings/analytics | `services/frontend/src` |
| API | Django REST for auth, projects, jobs, transcript, playback, avatar, social | `services/api/core` |
| TTS | FastAPI synthesis and preview service | `services/tts_service` |
| Worker | Celery render and orchestration pipeline | `services/worker` |
| Avatar | Canonical adapters and preview/runtime handling | `services/avatar` |
| Shared scripts | Extraction, segmentation, ffmpeg, tts client helpers | `services/scripts` |
| Infra | Docker Compose, Dockerfiles, k8s/grafana/prometheus assets | `infra` |
| Tests | Integration coverage for API/worker/TTS/playback/avatar | `tests/integration` |

## Tech Stack

| Area | Technology |
|---|---|
| Backend API | Django REST Framework |
| TTS Service | FastAPI |
| Worker Queue | Celery + Redis |
| Frontend | Vite + React |
| Media Tools | FFmpeg, python-pptx, Pillow |
| Voice Synthesis | XTTS v2, gTTS |
| Avatar Runtime | LivePortrait, MuseTalk (runtime dependent) |
| Storage | Local filesystem (`STORAGE_ROOT`) |
| Infra | Docker Compose, optional Postgres/MinIO services |

## Repository Layout

| Path | Purpose |
|---|---|
| `services/api/` | Django API, models, serializers, views, migrations |
| `services/frontend/` | Active React frontend |
| `services/worker/` | Celery tasks and rendering pipeline |
| `services/tts_service/` | FastAPI TTS service + preprocessing |
| `services/avatar/` | Avatar canonical pipeline and adapters |
| `services/scripts/` | Shared extraction/render helper scripts |
| `infra/` | Compose, Dockerfiles, env, monitoring assets |
| `tests/integration/` | Integration tests |
| `storage_local/` | Local runtime-generated artifacts |

## Local Setup

For quick local onboarding, also see `docs/LOCAL_DEVELOPMENT_QUICKSTART.md`.

### 1) Docker Compose

```powershell
Copy-Item infra/.env.example infra/.env
docker compose -f infra/docker-compose.yml up --build
```

Default local endpoints:

- API: `http://localhost:8000`
- TTS: `http://localhost:8001`
- Frontend: `http://localhost:3000`
- Redis: `localhost:6379`
- Postgres: `localhost:5432`
- MinIO: `localhost:9000` and `localhost:9001`

### 2) API (standalone)

```powershell
cd services/api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DJANGO_SETTINGS_MODULE = "config.settings"
python manage.py migrate
python manage.py runserver 8000
```

### 3) TTS Service (standalone)

```powershell
cd services/tts_service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:STORAGE_ROOT = "..\..\storage_local"
uvicorn main:app --host 0.0.0.0 --port 8001
```

### 4) Worker (standalone)

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.settings"
$env:PYTHONPATH = "$PWD\services\api;$PWD\services;$PWD\services\scripts;$PWD\services\tts_service"
$env:STORAGE_ROOT = "$PWD\storage_local"
$env:TTS_SERVICE_URL = "http://localhost:8001"
celery -A worker worker --loglevel=info --pool=solo
```

### 5) Frontend (standalone)

```powershell
cd services/frontend
npm install
npm run dev -- --host 0.0.0.0 --port 3000
```

## Environment Variables

Use `infra/.env.example` as baseline. Major groups:

- Django/app: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DJANGO_SETTINGS_MODULE`
- DB: `POSTGRES_*`, `SQLITE_TEST_DATABASE_PATH`
- Redis/Celery: `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`
- Storage/playback: `STORAGE_ROOT`, `MEDIA_TOKEN_SECRET`, `MEDIA_TOKEN_TTL_SECONDS`
- TTS: `TTS_SERVICE_URL`, `XTTS_*`, `TTS_PREPROCESSING_ENABLED`, `TTS_GLOSSARY_PATH`
- Optional suggestion flow: `TTS_LLM_SUGGESTIONS_ENABLED`, `TTS_LLM_PROVIDER`, `OLLAMA_*`
- Avatar: `AVATAR_*`, `MUSETALK_*`
- Protection/DRM: `DRM_*`, `LESSON_PROTECTION_*`, `LECTURE_*`
- Frontend: `VITE_API_BASE_URL`

## TTS Pipeline Details

- Preview endpoint supports normalization checks without audio synthesis.
- Override precedence is deterministic and request-safe.
- Project-level `tts_settings` is persisted and passed into render/rerender.
- `provider_preference` is advisory and does not hard-fail generation.
- Runtime overrides affect spoken audio text only; transcript/SRT stay source-aligned.

## Avatar and Playback Protection Status

Current state:

- Avatar profile/upload/prepare/preview APIs are present.
- Worker-side LivePortrait and MuseTalk paths are present but runtime-dependent.
- Tokenized playback and protection payload contracts exist in backend.
- Frontend player currently uses basic video element and does not yet fully initialize advanced HLS/DRM/watermark/heartbeat flows.

## Testing

Run backend integration suite:

```powershell
pip install -r requirements-test.txt
pytest tests/integration -q -rs
```

Some tests need additional service dependencies:

```powershell
pip install -r services/api/requirements.txt -r services/worker/requirements.txt -r services/tts_service/requirements.txt
```

## Development Notes

- Active frontend is `services/frontend`.
- Runtime-generated media should not be committed.
- Local default runtime storage is repository root `storage_local/`.
- MinIO/S3 variables and services are configured in infra, but local filesystem is the active storage path in current app flow.

## Roadmap (Current Practical Priorities)

- Complete frontend wiring for secure playback contract (HLS/DRM/watermark/heartbeat).
- Complete avatar overlay playback integration in player UI.
- Expand frontend unit tests and CI checks.
- Harden production storage adapter path (S3/MinIO integration).
- Finalize deployment profiles and environment hardening.

## License

License terms are not finalized yet. Until then, all rights reserved by the project owner.

## Contact

VisusVision - Visus Artificial Vision and Automation Systems

For issues, collaboration, or commercial inquiries, open an issue in this repository.