# Windows Installation

This guide sets up VISUS VidLab for local development on Windows. The current path uses PowerShell scripts and Docker Compose. A future one-click installer is planned and documented in [INSTALLER_ROADMAP.md](INSTALLER_ROADMAP.md).

## Current Developer Setup

Use this flow for normal local development:

```powershell
VISUS-VidLab.bat
```

You can also run the same launcher directly:

```powershell
.\scripts\visus-launcher.ps1
```

The launcher is a convenience wrapper, not an EXE/MSI installer. It provides menu options for Doctor, core runtime start, avatar runtime start, health checks, quick tests, safe stop, local URLs, and saving Doctor reports. Doctor remains read-only. Runtime start and stop actions delegate to `scripts/windows-runtime.ps1`, preserving the existing no-build/no-pull start behavior and safe stop behavior. Start avatar runtime only when you intentionally want avatar work to be processed.

Manual script flow:

```powershell
git clone <repo-url> AI_ACADEMY
cd AI_ACADEMY
.\scripts\windows-doctor.ps1
.\scripts\windows-preflight.ps1
Copy-Item infra\.env.example infra\.env
.\scripts\windows-dev-setup.ps1 -CheckOnly
.\scripts\windows-runtime.ps1 -Profile core
.\scripts\windows-runtime-health.ps1
```

The default start script launches the core stack:

- `postgres`
- `redis`
- `minio`
- `api`
- `frontend`

Add heavier services only when needed:

```powershell
.\scripts\windows-runtime.ps1 -Profile worker
.\scripts\windows-runtime.ps1 -Profile tts
.\scripts\windows-runtime.ps1 -Profile avatar
.\scripts\windows-runtime.ps1 -Profile translation
.\scripts\windows-runtime.ps1 -Profile full
```

`-Profile avatar` also starts TTS and the render worker, and it opts into the Compose `avatar` profile for `worker-avatar`. Use it only on a validated GPU host.

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

Run the read-only preflight and setup checks:

```powershell
.\scripts\windows-doctor.ps1
.\scripts\windows-doctor.ps1 -Json
.\scripts\windows-doctor.ps1 -OutputPath scratch\doctor-report.json
.\scripts\windows-preflight.ps1
.\scripts\windows-preflight.ps1 -Json
.\scripts\windows-dev-setup.ps1 -CheckOnly
```

`windows-doctor.ps1` is the first one-command readiness report for a new Windows developer or company checkout. It is read-only by default: it does not build images, pull images, start services, install packages, download models, delete Docker volumes/images, or run avatar jobs. It checks host prerequisites, Docker/Compose shape, `infra\.env` presence, required core variable names, optional provider groups, and model/cache paths. Secret-like values are never printed; the report shows only variable names as present, missing, blank, or placeholder-like.

Optional providers are warnings, not core startup failures, unless the selected env state makes them required. Missing Google OAuth, SMTP/Brevo/Mailjet, OpenAI, Ollama, LibreTranslate, and avatar credentials/assets are reported as `WARN` with the disabled capability explained.

`windows-preflight.ps1` does not install Docker, WSL2, Ollama, GPU drivers, models, or Python packages. It reports host prerequisites, ports, disk space, `infra\.env` state, Compose config, optional GPU hints, and optional Ollama reachability. When run for `-Profile avatar` or `-Profile full`, it also reports read-only avatar image/model warnings without starting containers.

Status meanings:

- `PASS`: ready for the selected path.
- `WARN`: optional, degraded, already-running, or follow-up-needed state.
- `FAIL`: core blocker. The script exits with code `1` when core blockers are present.

## Docker Desktop and WSL2 Notes

Docker Desktop should be installed and running before starting services. On Windows, enable WSL2 integration in Docker Desktop settings.

The current scripts do not install Docker Desktop, WSL2, GPU drivers, or system packages. A future installer may guide users to prerequisite installers, but it should not silently make major system changes.

## NVIDIA / Avatar Notes

Avatar generation is optional and GPU-bound.

Before using `-Profile avatar`, validate Docker GPU support:

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

Keep avatar runtime dependencies in Docker:

- Do not install `mmcv`, `mmpose`, LivePortrait, or MuseTalk into the Windows virtual environment.
- Use the Docker worker image, a compatible local `MMCV_LOCAL_WHEEL`, `MMCV_WHEEL_URL`, or a prebuilt heavy avatar worker image.
- If `ai_academy_worker:local` was built as a smoke/light image with `INSTALL_OPENMMLAB_DEPS=0`, `worker-avatar` can fail with missing `mmcv` even if Windows Python has `mmcv` installed.
- Provision the MuseTalk model bundle under `storage_local\models` before starting `worker-avatar`.
- Expect the first avatar-capable worker image build to be heavy.

Print the avatar image and smoke strategy without running it:

```powershell
.\scripts\windows-avatar-runtime.ps1
.\scripts\windows-avatar-runtime.ps1 -Action Check
.\scripts\windows-avatar-runtime.ps1 -Action PrintBuildCommand
.\scripts\windows-avatar-runtime.ps1 -Action PrintSmokeCommand
```

`windows-avatar-runtime.ps1` is a dry-run helper. It does not build or pull images, start containers, install Python packages, download model files, delete Docker volumes/images, or run avatar jobs. Use it to choose between an online OpenMMLab build, a local `local_wheels/` `MMCV_LOCAL_WHEEL` build, a pinned `MMCV_WHEEL_URL` build, or a prebuilt image tagged to `ai_academy_worker:local`.

## Environment File

Create a local env file from the template:

```powershell
Copy-Item infra\.env.example infra\.env
```

Never commit `infra/.env`. The template contains placeholders only. For local development, keep local placeholder values and review [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md) before enabling optional providers.

## Core Stack Start

Start the core stack:

```powershell
.\scripts\windows-runtime.ps1 -Profile core
```

Check already-running services without starting, rebuilding, or pulling anything:

```powershell
.\scripts\windows-runtime-health.ps1
.\scripts\windows-runtime-health.ps1 -Json
.\scripts\windows-runtime.ps1 -HealthOnly
.\scripts\windows-runtime.ps1 -Status
```

The runtime health script may exit with code `1` when the core stack is stopped. That is expected: it reports stopped or missing core API/frontend services as `FAIL` and does not start them.

Access:

- Frontend: `http://localhost:3000`
- API: `http://localhost:8000`
- API readiness: `http://localhost:8000/api/v1/ready/`
- MinIO console: `http://localhost:9001`

Stop:

```powershell
.\scripts\windows-runtime.ps1 -Stop
```

`windows-runtime.ps1 -Stop` uses `docker compose stop`, not `down`, so Compose volumes, images, and runtime data are preserved.

The older development stop script still has an explicit destructive volume-removal flag. Use it only when you intentionally want to remove Compose volumes:

```powershell
.\scripts\windows-dev-stop.ps1 -RemoveVolumes
```

## Runtime Wrapper

`scripts/windows-runtime.ps1` is the current profile selector/start wrapper. It is still a PowerShell script, not an EXE/MSI installer.
`VISUS-VidLab.bat` and `scripts/visus-launcher.ps1` are the installer-like console menu on top of this wrapper. Use them when you want one place to run Doctor, start or stop a selected runtime profile, check health, open local URLs, or save a timestamped Doctor report under `scratch\doctor-reports\`.

Common commands:

```powershell
.\scripts\windows-runtime.ps1 -PreflightOnly
.\scripts\windows-runtime.ps1 -HealthOnly
.\scripts\windows-runtime.ps1 -Status
.\scripts\windows-runtime.ps1 -Stop
.\scripts\windows-runtime.ps1 -Profile core
.\scripts\windows-runtime.ps1 -Profile worker
.\scripts\windows-runtime.ps1 -Profile tts
.\scripts\windows-runtime.ps1 -Profile avatar
.\scripts\windows-runtime.ps1 -Profile translation
.\scripts\windows-runtime.ps1 -Profile full
```

The wrapper runs preflight before start unless `-SkipPreflight` is supplied. It runs the health summary after start unless `-NoHealth` is supplied. Starts use Compose with no build and no pull behavior; missing images fail instead of being built or downloaded by the wrapper. `avatar` and `full` pass `--profile avatar`; `core`, `worker`, `tts`, and `translation` do not include `worker-avatar`.

## TTS and Worker Start

For render/TTS work:

```powershell
.\scripts\windows-runtime.ps1 -Profile tts
```

For backend-only API smoke tests that do not need the Vite frontend, exclude it explicitly:

```powershell
.\scripts\windows-runtime.ps1 -Profile tts -NoFrontend
.\scripts\windows-runtime-health.ps1 -Profile tts -NoFrontend
.\scripts\windows-runtime.ps1 -Profile tts -NoFrontend -Stop
```

`-NoFrontend` skips frontend startup, frontend health expectations, and frontend port checks. This avoids the Compose frontend command path that runs `npm install && npm run dev`; API, worker, TTS, database, Redis, and MinIO remain selected. It does not opt into `worker-avatar`.

Access:

- TTS readiness: `http://localhost:8001/ready`
- Worker logs: `docker compose -f infra\docker-compose.yml logs -f worker`

## Translation Profile

Compose includes LibreTranslate behind the `translation` profile:

```powershell
.\scripts\windows-runtime.ps1 -Profile translation
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
- `windows-runtime.ps1 -Profile full` reports Ollama as host-side and does not install, start, or pull Ollama models.
- `worker-avatar` is behind the Compose `avatar` profile; the core runtime does not start it.
- Avatar heavy dependencies belong in the Docker worker image.
- Full avatar runtime is not a normal CI path.
- The one-click EXE/MSI installer is planned, not present.
- The current runtime, preflight, and health scripts are installer-friendly building blocks; they are not an EXE/MSI package.

## Future One-click Installer Goal

The future installer should:

- Run prerequisite checks for Windows, WSL2, Docker Desktop, Docker daemon, ports, disk space, and optional GPU mode.
- Wrap the current runtime, preflight, and runtime health script contracts.
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
