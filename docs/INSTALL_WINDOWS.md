# Windows Installation

This guide sets up VISUS VidLab for local development on Windows. The current path uses PowerShell scripts and Docker Compose. A future one-click installer is planned and documented in [INSTALLER_ROADMAP.md](INSTALLER_ROADMAP.md).

## Current Developer Setup

Use this flow for normal local development:

```powershell
git clone <repo-url> AI_ACADEMY
cd AI_ACADEMY
Copy-Item infra\.env.example infra\.env
.\scripts\windows-dev-setup.ps1 -CheckOnly
.\scripts\windows-dev-start.ps1
```

The default start script launches the core stack:

- `postgres`
- `redis`
- `minio`
- `api`
- `frontend`

Add heavier services only when needed:

```powershell
.\scripts\windows-dev-start.ps1 -WithTts
.\scripts\windows-dev-start.ps1 -WithWorker
.\scripts\windows-dev-start.ps1 -WithAvatar
```

`-WithAvatar` also starts TTS and the render worker. Use it only on a validated GPU host.

## Prerequisite Checklist

Required for the current developer path:

- Windows 11.
- Git for Windows.
- PowerShell 5.1+ or PowerShell 7.
- Docker Desktop with WSL2 integration enabled.
- Docker daemon running.
- Docker Compose v2 available through `docker compose`.
- Python 3.10+ on PATH.
- Node.js 20+ and npm.
- Free ports for the selected profile.
- Enough disk space for Docker images, node modules, Python wheels, and `storage_local/`.

Optional:

- NVIDIA GPU and current NVIDIA driver for avatar GPU mode.
- Docker GPU support for avatar GPU mode.
- Host-side Ollama for optional local LLM intelligence enhancement.
- Local model/cache storage for XTTS, Ollama, LivePortrait, MuseTalk, and OpenMMLab.

Check tools:

```powershell
git --version
docker --version
docker compose version
python --version
node --version
npm --version
```

Run the read-only setup check:

```powershell
.\scripts\windows-dev-setup.ps1 -CheckOnly
```

## Docker Desktop and WSL2 Notes

Docker Desktop should be installed and running before starting services. On Windows, enable WSL2 integration in Docker Desktop settings.

The current scripts do not install Docker Desktop, WSL2, GPU drivers, or system packages. A future installer may guide users to prerequisite installers, but it should not silently make major system changes.

## NVIDIA / Avatar Notes

Avatar generation is optional and GPU-bound.

Before using `-WithAvatar`, validate Docker GPU support:

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

Keep avatar runtime dependencies in Docker:

- Do not install `mmcv`, `mmpose`, LivePortrait, or MuseTalk into the Windows virtual environment.
- Use the Docker worker image, a compatible local `MMCV_LOCAL_WHEEL`, `MMCV_WHEEL_URL`, or a prebuilt heavy avatar worker image.
- Expect the first avatar-capable worker image build to be heavy.

## Environment File

Create a local env file from the template:

```powershell
Copy-Item infra\.env.example infra\.env
```

Never commit `infra/.env`. The template contains placeholders only. For local development, keep local placeholder values and review [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md) before enabling optional providers.

## Core Stack Start

Start the core stack:

```powershell
.\scripts\windows-dev-start.ps1
```

Access:

- Frontend: `http://localhost:3000`
- API: `http://localhost:8000`
- API readiness: `http://localhost:8000/api/v1/ready/`
- MinIO console: `http://localhost:9001`

Stop:

```powershell
.\scripts\windows-dev-stop.ps1
```

Remove Compose volumes only with the explicit destructive flag:

```powershell
.\scripts\windows-dev-stop.ps1 -RemoveVolumes
```

## TTS and Worker Start

For render/TTS work:

```powershell
.\scripts\windows-dev-start.ps1 -WithTts -WithWorker
```

Access:

- TTS readiness: `http://localhost:8001/ready`
- Worker logs: `docker compose -f infra\docker-compose.yml logs -f worker`

## Translation Profile

Compose includes LibreTranslate behind the `translation` profile:

```powershell
docker compose -f infra\docker-compose.yml --profile translation up -d libretranslate
```

Check:

```powershell
Invoke-WebRequest http://localhost:5000/languages
```

## Full AI Stack Planned Path

The planned full local AI runtime is profile-driven:

- Core app services.
- TTS service.
- Render worker.
- Optional host-side Ollama enhancement.
- Optional LibreTranslate profile.
- Optional GPU avatar worker.

See [FULL_STACK_LOCAL_RUNTIME.md](FULL_STACK_LOCAL_RUNTIME.md) for profile details and known gaps.

Important current reality:

- Ollama currently runs host-side unless a future Compose service is added.
- Avatar heavy dependencies belong in the Docker worker image.
- Full avatar runtime is not a normal CI path.
- The one-click EXE/MSI installer is planned, not present.

## Future One-click Installer Goal

The future installer should:

- Run prerequisite checks for Windows, WSL2, Docker Desktop, Docker daemon, ports, disk space, and optional GPU mode.
- Let the user select runtime profiles before heavy downloads or builds.
- Generate `.env` safely without exposing secrets.
- Pull or build Docker images for selected profiles.
- Download or verify model artifacts with consent.
- Produce a clear health summary.
- Provide start, stop, update, and troubleshooting actions.
- Avoid silent destructive actions.

See [INSTALLER_ROADMAP.md](INSTALLER_ROADMAP.md).

## Troubleshooting Links

- [Troubleshooting](TROUBLESHOOTING.md)
- [Local development](LOCAL_DEVELOPMENT.md)
- [Local development quickstart](LOCAL_DEVELOPMENT_QUICKSTART.md)
- [Full stack local runtime](FULL_STACK_LOCAL_RUNTIME.md)
- [Environment variables](ENVIRONMENT_VARIABLES.md)
- [Avatar pipeline](AVATAR_PIPELINE.md)
- [Avatar model provisioning](AVATAR_MODEL_PROVISIONING.md)
