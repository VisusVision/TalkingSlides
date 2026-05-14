# VISUS VidLab / AI_ACADEMY

VISUS VidLab, also known in the repository as AI_ACADEMY, turns source lesson material into narrated video lessons with secure playback and optional avatar overlay support. It combines a Django API, Celery workers, a FastAPI TTS service, and a Vite React frontend for Studio, Watch, Catalog, and settings workflows.

## Core Features

- Upload PPTX, PDF, DOCX, TXT, and image-based lesson sources.
- Extract lesson text, speaker notes, pages, and slide images for render jobs.
- Generate narration through XTTS, with local fallback behavior for development.
- Render MP4 lessons and original subtitle tracks.
- Support tokenized playback, secure stream mode, HLS packaging, and a DRM-ready playback contract.
- Run a non-blocking avatar overlay pipeline using LivePortrait, MuseTalk, and optional restoration.
- Provide teacher Studio, student Watch, public Catalog, library, settings, and analytics screens.

## Quick Start

- Windows setup: [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md)
- Daily local workflow: [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md)


```powershell
.\scripts\windows-dev-setup.ps1
```

To start the default local stack:

```powershell
.\scripts\windows-dev-start.ps1
```

## Documentation Map

- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Environment variables: [docs/ENVIRONMENT_VARIABLES.md](docs/ENVIRONMENT_VARIABLES.md)
- Production deployment: [docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md)
- Secure playback and DRM: [docs/SECURE_PLAYBACK_DRM.md](docs/SECURE_PLAYBACK_DRM.md)
- Avatar pipeline: [docs/AVATAR_PIPELINE.md](docs/AVATAR_PIPELINE.md)
- Troubleshooting: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

Internal roadmaps and technical planning documents live on the `developer` branch.


## Local URLs

When using the Docker Compose development stack:

- Frontend: `http://localhost:3000`
- API: `http://localhost:8000`
- API readiness: `http://localhost:8000/api/v1/ready/`
- TTS service: `http://localhost:8001`
- MinIO console: `http://localhost:9001`

## Production Notes

Production must not use committed env files, SQLite fallback, development secrets, wildcard hosts, or CORS allow-all. Start with [docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md) and [docs/ENVIRONMENT_VARIABLES.md](docs/ENVIRONMENT_VARIABLES.md), then configure secrets through your deployment platform or secret manager.

The stable production shape is API, frontend, render worker, TTS service, Redis, Postgres, storage, and a separately sized GPU avatar worker when avatar generation is enabled.
