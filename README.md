# VISUS VidLab

VISUS VidLab, still referenced in parts of the repository as `AI_ACADEMY`, turns lesson sources into narrated, secure, AI-assisted video lessons. Teachers and publishers can upload documents or slide decks, edit transcript pages in Studio, render playable lessons, publish them to learners, and optionally attach talking-avatar overlays.

The active integration branch for contribution work is `developer`.

## What It Does

- Converts `.pptx`, `.pdf`, `.docx`, `.txt`, and image-based lesson sources into video lessons.
- Builds transcript pages, narration audio, subtitles, playback sidecars, and publishable lesson records.
- Provides Studio for upload/edit/rerender workflows and Watch for secure lesson playback.
- Supports Turkish-aware TTS preprocessing, optional subtitle translation, and original-language subtitle tracks.
- Provides moderation gates for source text, visual assets, OCR/frame checks, and publish safety.
- Offers creator analytics and optional local intelligence reports.
- Runs optional talking-avatar generation as a separate non-blocking GPU worker path.

## Current Runtime Status

| Area | Status |
| --- | --- |
| API/frontend/core data services | Docker Compose local development is available. The Windows start script defaults to API, frontend, Postgres, Redis, and MinIO. |
| TTS | Implemented as a FastAPI service with XTTS support and development fallbacks. Start it only when needed for local work. |
| Intelligence | Heuristic lesson and analytics intelligence are implemented. Local Ollama enhancement is optional and currently host-side unless a future Compose service is added. |
| Translation | Subtitle translation has an optional provider chain. Compose includes `libretranslate` behind the `translation` profile. |
| Avatar | Optional and non-blocking. `worker-avatar` is behind the Compose `avatar` profile. LivePortrait/MuseTalk/OpenMMLab dependencies belong in the Docker worker image, not in Windows Python. First heavy avatar image builds can be large. |
| CI Docker smoke | CI validates Docker build contracts but intentionally skips full heavy avatar runtime dependencies. |

## Quick Start

From the repository root:

```powershell
Copy-Item .\infra\.env.example .\infra\.env
.\scripts\windows-preflight.ps1
.\scripts\windows-dev-setup.ps1 -CheckOnly
.\scripts\windows-runtime.ps1 -Profile core
.\scripts\windows-runtime-health.ps1
```

Then open:

- Frontend: `http://localhost:3000`
- API readiness: `http://localhost:8000/api/v1/ready/`
- MinIO console: `http://localhost:9001`

The default Windows start script launches the core developer stack: `postgres`, `redis`, `minio`, `api`, and `frontend`.

Add services only when they are needed:

```powershell
.\scripts\windows-runtime.ps1 -Profile worker
.\scripts\windows-runtime.ps1 -Profile tts
.\scripts\windows-runtime.ps1 -Profile avatar
.\scripts\windows-runtime.ps1 -Profile translation
.\scripts\windows-runtime.ps1 -Profile full
```

`-Profile avatar` implies TTS and worker services and passes `--profile avatar` to Compose. Use it only on a validated GPU host with avatar models and Docker GPU support ready.

## Runtime Profiles

The planned installer/runtime profiles are documented in [docs/FULL_STACK_LOCAL_RUNTIME.md](docs/FULL_STACK_LOCAL_RUNTIME.md). Today they map to Windows script service selection plus explicit Compose profiles for optional services:

- `core`: API, frontend, Postgres, Redis, MinIO.
- `tts`: adds the TTS service.
- `intelligence`: enables backend intelligence; Ollama currently runs on the host.
- `avatar-gpu`: adds the GPU avatar worker path through the Compose `avatar` profile.
- `translation`: enables the Compose `translation` profile for LibreTranslate.
- `full-stack`: planned combination after preflight checks pass.

Use the read-only Windows checks before and after startup:

```powershell
.\scripts\windows-preflight.ps1
.\scripts\windows-preflight.ps1 -Json
.\scripts\windows-runtime-health.ps1
.\scripts\windows-runtime-health.ps1 -Json
.\scripts\windows-runtime.ps1 -Status
```

`PASS` means the check is ready, `WARN` means optional or degraded behavior needs attention, and `FAIL` means a core blocker must be fixed before the core app should be considered ready.
`windows-runtime-health.ps1` may exit with code `1` when the core stack is stopped because it only checks already-running services.
`windows-runtime.ps1 -Stop` uses `docker compose stop` and preserves volumes, images, and runtime data.

## Full Local AI Runtime

The local AI path is intentionally profile-driven. Heavy dependencies should stay containerized where possible:

- Do not install `mmcv`, `mmpose`, LivePortrait, or MuseTalk into the Windows virtual environment.
- Use Docker image build arguments or a prebuilt avatar worker image for avatar dependencies.
- A stale/light `ai_academy_worker:local` image built with `INSTALL_OPENMMLAB_DEPS=0` can still fail inside `worker-avatar` with missing `mmcv`; fix the Docker image, not the host `.venv`.
- Provision the MuseTalk/LivePortrait model bundle before starting `worker-avatar`.
- Use `.\scripts\windows-avatar-runtime.ps1` to print the avatar image build plan, local wheel/prebuilt image options, expected model bundle paths, and future smoke commands without building, pulling, starting services, or downloading models.
- Use host-side Ollama for now with `http://host.docker.internal:11434` from containers.
- Keep Docker smoke CI separate from hardware/model-dependent avatar validation.

See:

- [Windows install guide](docs/INSTALL_WINDOWS.md)
- [Full stack local runtime](docs/FULL_STACK_LOCAL_RUNTIME.md)
- [Installer roadmap](docs/INSTALLER_ROADMAP.md)
- [Avatar pipeline](docs/AVATAR_PIPELINE.md)
- [Intelligence providers](docs/INTELLIGENCE_PROVIDERS.md)

## Documentation

Start with [docs/README.md](docs/README.md). Key docs:

- [Windows installation](docs/INSTALL_WINDOWS.md)
- [Local development](docs/LOCAL_DEVELOPMENT.md)
- [Local development quickstart](docs/LOCAL_DEVELOPMENT_QUICKSTART.md)
- [Environment variables](docs/ENVIRONMENT_VARIABLES.md)
- [Deployment profiles](docs/DEPLOYMENT_PROFILES.md)
- [Operations runbook](docs/OPERATIONS_RUNBOOK.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)

Roadmaps:

- [Installer roadmap](docs/INSTALLER_ROADMAP.md)
- [Full stack local runtime](docs/FULL_STACK_LOCAL_RUNTIME.md)
- [Partial rendering roadmap](docs/PARTIAL_RENDERING_ROADMAP.md)
- [Avatar slide defaults roadmap](docs/AVATAR_SLIDE_DEFAULTS_ROADMAP.md)
- [I18N roadmap](docs/I18N_ROADMAP.md)

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
| `docs` | Architecture, install, runtime, operations, roadmap, and audit docs |
| `tests` | API, worker, storage, playback, moderation, TTS, and avatar regression tests |

## Testing

Backend and integration tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Frontend tests:

```powershell
cd services\frontend
npm test
```

Frontend build:

```powershell
cd services\frontend
npm run build
```

Compose validation:

```powershell
docker compose -f infra\docker-compose.yml config --quiet
```

## Production Notes

Production deployments must not use committed env files, development secrets, wildcard hosts, SQLite fallback, CORS allow-all, or unvalidated storage mounts. Start with [docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md), [docs/DEPLOYMENT_PROFILES.md](docs/DEPLOYMENT_PROFILES.md), and [docs/ENVIRONMENT_VARIABLES.md](docs/ENVIRONMENT_VARIABLES.md).

## License

License terms are not finalized yet. Until a license is published, all rights are reserved by the project owner.
