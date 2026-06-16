# VISUS VidLab / AI_ACADEMY

Turns lesson sources into narrated, secure, AI-assisted video lessons with optional talking-avatar overlays. The platform combines source extraction, Turkish-aware TTS preprocessing, XTTS narration, video rendering, subtitle generation, moderation, analytics, and a React learning experience for teachers and students.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-4.2-092E20.svg)](https://www.djangoproject.com/)
[![React](https://img.shields.io/badge/React-18-61DAFB.svg)](https://react.dev/)
[![Vite](https://img.shields.io/badge/Vite-6-646CFF.svg)](https://vitejs.dev/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://docs.docker.com/compose/)
[![License](https://img.shields.io/badge/License-TBD-lightgrey.svg)](#license)

---

## Windows Format Oncesi Backup / Restore

Bu repo kokunde iki PowerShell scripti vardir:

- `backup-before-format.ps1`
- `restore-after-format.ps1`

Backup almak icin PowerShell'i bu repo/proje klasorunde acin ve calistirin:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\backup-before-format.ps1
```

Script `C:\docker-backup` klasorunu olusturur; Docker volume arsivlerini `.tar.gz` olarak, VS Code `User` ayarlarini, `$HOME\.ssh`, Git config dosyalarini ve mevcut klasor altindaki `docker-compose.yml`, `compose.yml`, `.env`, `.env.local`, `.env.production` dosyalarini yedekler. Eksik Docker volume varsa sadece uyari verir ve devam eder.

Format atmadan once `C:\docker-backup` klasorunu mutlaka format atilmayacak harici diske veya buluta kopyalayin. Bu klasor SSH anahtarlari, `.env` dosyalari ve Git credential bilgileri icerebilir; guvenli saklayin.

Format sonrasi once Docker Desktop ve VS Code kurun, `C:\docker-backup` klasorunu yeni sisteme geri kopyalayin, sonra PowerShell'de:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\restore-after-format.ps1
```

Restore scripti `C:\docker-backup\volumes` altindaki `.tar.gz` yedeklerini bulur, eksik Docker volume'lari olusturur ve icerigi geri yukler. VS Code ayarlari, SSH klasoru ve Git config dosyalari icin onay ister; mevcut dosyalarin ustune yazmadan once ayrica sorar.

Restore bittikten sonra ilgili proje klasorlerinde servisleri baslatmak icin:

```powershell
docker compose up -d
```

## Overview

**VISUS VidLab**, also referenced in the repository as **AI_ACADEMY**, converts uploaded learning material into playable video lessons. Teachers can upload `.pptx`, `.pdf`, `.docx`, `.txt`, or image-based sources; the system extracts lesson content, prepares transcript pages, synthesizes narration, renders video, creates subtitle assets, and publishes lessons through a web learning platform.

The product includes a full creator and learner workflow: Studio for upload and editing, Watch for secure playback, Catalog/Browse for discovery, Library and History for learners, Settings and profiles for publishers, and Analytics for lesson performance.

For richer lessons, the render pipeline can generate a non-blocking avatar overlay using LivePortrait and MuseTalk. If avatar rendering is unavailable, queued, or failed, the base lesson video remains playable and publishable according to the configured policy.

## Key Features

- **Source-to-Video Pipeline** - Converts documents, slides, notes, pages, and images into narrated MP4 lessons.
- **TTS Narration** - Uses a FastAPI TTS service with XTTS support and development fallbacks.
- **Turkish-Aware Text Processing** - Normalizes technical terms, acronyms, glossary entries, and text chunks before synthesis.
- **Transcript and Subtitle Workflow** - Builds transcript pages, original subtitle tracks, and optional translated subtitle sidecars.
- **Avatar Overlay Pipeline** - Runs LivePortrait and MuseTalk avatar jobs on a separate GPU worker queue.
- **Secure Playback** - Supports tokenized media URLs, secure stream mode, HLS packaging, watermark policy, and a DRM-ready contract.
- **Moderation and Safety Gates** - Provides text, visual, OCR, frame, and admin moderation flows before render or publish.
- **Creator Platform** - Includes Studio drafts, rerender flows, upload composer, playlist management, publisher profiles, and analytics.
- **Observability Foundation** - Exposes Prometheus metrics, Grafana dashboard config, render recovery reports, and storage snapshots.

## How It Works

```text
source upload
  -> project and job records
  -> content extraction
  -> transcript pages and TTS chunks
  -> TTS synthesis
  -> slide/page video render
  -> MP4 composition
  -> subtitles and playback sidecars
  -> moderation and publish state
  -> Watch player and catalog delivery
```

Optional avatar flow:

```text
base lesson video
  -> avatar handoff manifest
  -> avatar queue
  -> LivePortrait motion pass
  -> MuseTalk lip sync
  -> optional restoration
  -> avatar track
  -> Watch overlay payload
```

1. **Upload** - A teacher uploads lesson material and creates a project.
2. **Extract** - The worker extracts text, pages, slide images, notes, and renderable assets.
3. **Prepare Speech** - Text is normalized, segmented, and sent to the TTS service.
4. **Render** - The worker builds lesson video segments and composes the final MP4.
5. **Package** - Subtitles, playback metadata, optional HLS assets, and secure media tokens are prepared.
6. **Moderate** - Text, visual, OCR, frame, and admin checks can gate render or publish behavior.
7. **Publish** - Lessons appear in Watch, Catalog/Browse, Library, playlists, profiles, and analytics views.
8. **Overlay Avatar** - When enabled, avatar generation runs separately and attaches to the Watch player when ready.

## Tech Stack

| Layer | Technology |
| --- | --- |
| API | Django 4.2, Django REST Framework, Gunicorn |
| Frontend | React 18, Vite 6, React Router, Tailwind CSS, hls.js |
| Workers | Celery, Redis, FFmpeg, Pillow, OpenCV |
| TTS | FastAPI, Uvicorn, Coqui XTTS, gTTS fallback, custom preprocessing |
| Avatar | LivePortrait, MuseTalk, optional restoration path |
| Database | PostgreSQL 16 locally through Docker Compose; SQLite fallback for lightweight local API runs |
| Storage | Filesystem-backed runtime storage under `storage_local/`; S3 adapter foundation for staged readiness checks |
| Infrastructure | Docker Compose, MinIO, Prometheus, Grafana, Kubernetes examples |
| Testing | pytest, Vitest, Playwright |

## Repository Layout

| Path | Role |
| --- | --- |
| `services/api` | Django API, models, auth, catalog, playback, moderation, analytics, admin endpoints |
| `services/worker` | Celery render and avatar task orchestration |
| `services/tts_service` | FastAPI TTS service and text normalization pipeline |
| `services/frontend` | React app for Studio, Watch, Browse, Library, Settings, Analytics, and moderation screens |
| `services/scripts` | PPTX extraction, FFmpeg helpers, TTS clients, smoke tools, avatar runners |
| `services/avatar` | Avatar preprocessing, validation, resource management, and engine adapters |
| `infra` | Docker Compose, Dockerfiles, env templates, Prometheus, Grafana, Kubernetes examples |
| `docs` | Architecture, deployment, operations, storage, avatar, playback, and development docs |
| `tests` | API, worker, storage, playback, moderation, TTS, and avatar regression tests |

## Installation

### Prerequisites

- Windows 11 or a Linux/WSL development environment.
- Docker Desktop with WSL 2 integration for the Compose stack.
- Python 3.10+.
- Node.js 20+ and npm.
- Optional: NVIDIA GPU runtime for XTTS and avatar generation.
- Optional: FFmpeg for local render checks outside Docker.

### Environment

Copy the local environment template before starting services:

```powershell
Copy-Item .\infra\.env.example .\infra\.env
```

On Git Bash, WSL, or Linux:

```bash
cp infra/.env.example infra/.env
```

Keep runtime media in the repository root `storage_local/` directory for local development. Docker mounts this folder into API, worker, avatar worker, and TTS containers as `/app/storage_local`.

## Usage

### Start the full local stack

```powershell
docker compose -f infra/docker-compose.yml up --build
```

This starts:

| Service | URL |
| --- | --- |
| Frontend | `http://localhost:3000` |
| API | `http://localhost:8000` |
| API readiness | `http://localhost:8000/api/v1/ready/` |
| TTS service | `http://localhost:8001` |
| MinIO console | `http://localhost:9001` |
| Redis | `localhost:6379` |
| PostgreSQL | `localhost:5432` |

### Guided Windows setup

```powershell
.\scripts\windows-dev-setup.ps1
.\scripts\windows-dev-start.ps1
```

### Run the API locally

```powershell
cd services/api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DJANGO_SETTINGS_MODULE = "config.settings"
$env:STORAGE_ROOT = "..\..\storage_local"
python manage.py migrate
python manage.py runserver 8000
```

### Run the frontend locally

```powershell
cd services/frontend
npm install
npm run dev -- --host 0.0.0.0 --port 3000
```

The frontend reads `VITE_API_BASE_URL` and defaults to `http://localhost:8000/api/v1`.

### Run lightweight TTS locally

```powershell
cd services/tts_service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:STORAGE_ROOT = "..\..\storage_local"
$env:XTTS_ENABLED = "0"
uvicorn main:app --host 0.0.0.0 --port 8001
```

## Testing

Run backend and integration tests:

```powershell
pytest
```

Run frontend unit tests:

```powershell
cd services/frontend
npm test
```

Run frontend end-to-end tests:

```powershell
cd services/frontend
npm run e2e
```

## Documentation

- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Local development: [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md)
- Quickstart: [docs/LOCAL_DEVELOPMENT_QUICKSTART.md](docs/LOCAL_DEVELOPMENT_QUICKSTART.md)
- Windows install: [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md)
- Environment variables: [docs/ENVIRONMENT_VARIABLES.md](docs/ENVIRONMENT_VARIABLES.md)
- Production deployment: [docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md)
- Storage readiness: [docs/STORAGE_PRODUCTION_READINESS.md](docs/STORAGE_PRODUCTION_READINESS.md)
- Secure playback and DRM: [docs/SECURE_PLAYBACK_DRM.md](docs/SECURE_PLAYBACK_DRM.md)
- Avatar pipeline: [docs/AVATAR_PIPELINE.md](docs/AVATAR_PIPELINE.md)
- Moderation operations: [docs/MODERATION_OPERATIONS.md](docs/MODERATION_OPERATIONS.md)
- Operations runbook: [docs/OPERATIONS_RUNBOOK.md](docs/OPERATIONS_RUNBOOK.md)
- Troubleshooting: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- Release process: [docs/RELEASE_PROCESS.md](docs/RELEASE_PROCESS.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)

## Roadmap

- [ ] Harden production storage migration beyond local filesystem runtime storage.
- [ ] Expand S3/MinIO media serving behind the existing relative-path contract.
- [ ] Complete production avatar worker rollout with validated GPU sizing.
- [ ] Add provider-backed DRM integration on top of the current playback contract.
- [ ] Improve creator analytics and lesson intelligence reports.
- [ ] Broaden subtitle translation provider coverage and public request controls.

## Production Notes

Production deployments must not use committed env files, development secrets, wildcard hosts, SQLite fallback, CORS allow-all, or unvalidated storage mounts. Start with [docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md), [docs/ENVIRONMENT_VARIABLES.md](docs/ENVIRONMENT_VARIABLES.md), and [docs/STORAGE_PRODUCTION_READINESS.md](docs/STORAGE_PRODUCTION_READINESS.md).

The stable production shape is API, frontend, render worker, TTS service, Redis, PostgreSQL, durable storage, and a separately sized GPU avatar worker when avatar generation is enabled.

## License

License terms are not finalized yet. Until a license is published, all rights are reserved by the project owner.

## Contact

**VISUS Vision** - Artificial Vision and Automation Systems

For questions, partnership, or commercial inquiries, use the organization contact channel or open an issue in the repository.
