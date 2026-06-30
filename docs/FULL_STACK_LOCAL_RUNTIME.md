# Full Stack Local Runtime

This document describes the local runtime profiles VISUS VidLab should expose through scripts and the future installer. These are product/runtime profiles, not all current Docker Compose profiles.

Current reality:

- `scripts/windows-runtime.ps1` is the profile selector/start wrapper that a future EXE/MSI can wrap.
- `scripts/windows-dev-start.ps1` starts selected Compose services by name.
- `scripts/windows-preflight.ps1` checks host prerequisites and profile readiness without installing, building, or starting services.
- `scripts/windows-runtime-health.ps1` summarizes already-running services and HTTP endpoints without starting, rebuilding, or pulling anything.
- `infra/docker-compose.yml` currently has explicit Compose profiles for optional services: `translation` for `libretranslate` and `avatar` for `worker-avatar`.
- Ollama currently runs on the host unless a future Compose service is added.
- Avatar worker dependencies belong in the Docker worker image, not the Windows virtual environment.
- Docker smoke CI intentionally avoids the full heavy avatar runtime.

## Profile Summary

| Profile | Purpose | Current entry point |
| --- | --- | --- |
| `core` | API, frontend, database, Redis, and local object-store helper. | `.\scripts\windows-runtime.ps1 -Profile core` |
| `worker` | Add the render worker to the core stack. | `.\scripts\windows-runtime.ps1 -Profile worker` |
| `tts` | Add TTS service for render and preview work. | `.\scripts\windows-runtime.ps1 -Profile tts` |
| `intelligence` | Enable heuristic intelligence and optional host-side Ollama enhancement. | Env configuration plus core/worker services |
| `avatar` | Add GPU avatar worker for LivePortrait/MuseTalk work. | `.\scripts\windows-runtime.ps1 -Profile avatar` |
| `translation` | Add local LibreTranslate service for translation fallback checks. | `.\scripts\windows-runtime.ps1 -Profile translation` |
| `full` | Combine core, worker, TTS, avatar worker, and LibreTranslate after preflight passes. | `.\scripts\windows-runtime.ps1 -Profile full` |

## Core

Purpose:

- Daily API/frontend development.
- Local database-backed Studio/Watch flows.
- Storage and readiness checks without heavy model startup.

Services:

- `postgres`
- `redis`
- `minio`
- `api`
- `frontend`

Host requirements:

- Windows 11 or a working Linux/WSL development environment.
- Docker Desktop with WSL2 integration on Windows.
- Git, PowerShell, Python 3.10+, Node.js 20+, and npm for local tool checks.
- Ports `3000`, `8000`, `5432`, `6379`, `9000`, and `9001` available.

Expected health checks:

```powershell
.\scripts\windows-preflight.ps1
.\scripts\windows-runtime.ps1 -Profile core
docker compose -f infra\docker-compose.yml ps
Invoke-WebRequest http://localhost:8000/api/v1/ready/
Invoke-WebRequest http://localhost:3000/
.\scripts\windows-runtime-health.ps1
```

Known gaps:

- API readiness is intentionally lightweight and does not prove TTS, GPU, Redis, or Postgres behavior in depth.
- Runtime media still uses filesystem storage under repo-root `storage_local/` by default.

## TTS

Purpose:

- Local narration generation and TTS preview work.
- Render worker support for jobs that synthesize audio.

Services:

- Core services.
- `tts_service`.
- Usually `worker` when rendering end-to-end.

Host requirements:

- Core requirements.
- Sufficient CPU/RAM for model startup.
- Optional GPU for XTTS acceleration.
- Model/cache storage under `storage_local/` or another configured cache path.

Expected health checks:

```powershell
.\scripts\windows-runtime.ps1 -Profile tts
Invoke-WebRequest http://localhost:8001/ready
docker compose -f infra\docker-compose.yml logs -f tts_service
.\scripts\windows-runtime-health.ps1
```

Known gaps:

- The current Compose TTS service is oriented toward XTTS-capable startup.
- Lightweight host-side TTS can use `XTTS_ENABLED=0`; a future installer should expose that as a clear profile choice.
- gTTS fallback may need internet access; silent fallback keeps jobs moving but is not production-quality narration.

## Intelligence / Ollama

Purpose:

- Lesson Intelligence and Analytics Intelligence.
- Heuristic report generation with optional local Ollama enhancement.

Services:

- API.
- Worker on the render queue for local background enhancement unless a dedicated intelligence worker is configured.
- Host-side Ollama when local LLM enhancement is enabled.

Host requirements:

- Core requirements.
- Optional Ollama installed on the host.
- Local model pulled on the host, such as the model configured by `OLLAMA_LESSON_INTELLIGENCE_MODEL` or `OLLAMA_ANALYTICS_INTELLIGENCE_MODEL`.

Expected health checks:

```powershell
ollama list
Invoke-WebRequest http://localhost:11434/api/tags
.\scripts\windows-preflight.ps1
```

From containers, Docker Desktop should use:

```text
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

Known gaps:

- Ollama is not currently a Compose-managed service.
- Paid/external intelligence providers are not implemented in this branch.
- Production should use dedicated low-priority intelligence workers if local LLM work can compete with render jobs.

## Avatar GPU

Purpose:

- Real LivePortrait/MuseTalk avatar preview and lesson overlay generation.
- GPU-bound avatar queue processing separate from base render work.

Services:

- Core services.
- `tts_service`.
- `worker`.
- `worker-avatar` behind the Compose `avatar` profile.

Host requirements:

- Core and TTS requirements.
- NVIDIA GPU and current NVIDIA driver.
- Docker Desktop GPU support.
- Valid model paths and avatar source assets.
- Enough disk space for CUDA, PyTorch, OpenMMLab, LivePortrait, MuseTalk, and model caches.

Expected health checks:

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
.\scripts\windows-avatar-runtime.ps1 -Action Check
.\scripts\windows-avatar-runtime.ps1 -Action PrintBuildCommand
.\scripts\windows-avatar-runtime.ps1 -Action PrintSmokeCommand
.\scripts\windows-preflight.ps1 -Profile avatar
.\scripts\windows-runtime.ps1 -Profile avatar
docker compose -f infra\docker-compose.yml --profile avatar logs -f worker-avatar
```

When validating a built heavy worker image, import checks should run inside the container, not the Windows virtual environment.
The avatar helper prints the online OpenMMLab build, local wheel/cache build, `MMCV_WHEEL_URL` build, prebuilt image tag path, model bundle paths under `storage_local\models`, and throwaway-queue smoke commands without executing any of them.
`worker-avatar` keeps persistent model paths under `storage_local\models` and `/opt/liveportrait/pretrained_weights`, but routes volatile parser/runtime caches such as YAPF, Matplotlib, and Numba to `/tmp/visus-cache` so Windows bind-mounted cache files cannot block bootstrap.

Known gaps:

- First avatar image build may be heavy.
- `mmcv`/OpenMMLab dependencies must be installed in the Docker image or provided through a compatible local wheel/prebuilt image.
- Installing `mmcv`, `mmpose`, LivePortrait, or MuseTalk into Windows Python does not fix `worker-avatar`.
- A stale/light `ai_academy_worker:local` image built with `INSTALL_OPENMMLAB_DEPS=0` or `DOWNLOAD_LIVEPORTRAIT_WEIGHTS=0` can still fail avatar bootstrap until a heavy avatar image is built or retagged.
- `worker-avatar` should not consume the real `avatar` queue until model bundle checks and a throwaway-queue smoke path have passed.
- Docker smoke CI intentionally sets heavy avatar dependency build args to skip full avatar runtime.

## Translation

Purpose:

- Local subtitle translation fallback checks.
- LibreTranslate validation where a self-hosted service is useful.

Services:

- Optional `libretranslate` Compose service behind the `translation` profile.
- Worker for async subtitle generation.

Host requirements:

- Core requirements.
- Port `5000` available if exposing LibreTranslate locally.

Expected health checks:

```powershell
.\scripts\windows-runtime.ps1 -Profile translation
Invoke-WebRequest http://localhost:5000/languages
.\scripts\windows-runtime-health.ps1
```

Known gaps:

- Subtitle translation has provider fallback behavior, but production provider validation remains deployment-specific.
- Content language and future UI language are separate concerns.

## Full Stack

Purpose:

- One local developer path for API, frontend, TTS, worker, intelligence, translation, and optional avatar GPU.
- Future installer profile selector.

Services:

- Core services.
- Optional `tts_service`.
- Optional `worker`.
- Optional `worker-avatar` through the Compose `avatar` profile.
- Optional `libretranslate`.
- Host-side or future Compose-managed Ollama.

Host requirements:

- Every selected profile requirement.
- Preflight confirmation for GPU mode, disk space, ports, Docker daemon state, and model locations.

Expected health checks:

- Compose config parses.
- Selected containers are running.
- API readiness responds.
- TTS readiness responds when TTS is selected.
- Ollama model list is reachable when local LLM enhancement is selected.
- LibreTranslate languages endpoint responds when translation profile is selected.
- GPU smoke passes before avatar worker is started.

The read-only summary commands are:

```powershell
.\scripts\windows-preflight.ps1
.\scripts\windows-preflight.ps1 -Json
.\scripts\windows-runtime-health.ps1
.\scripts\windows-runtime-health.ps1 -Json
.\scripts\windows-runtime.ps1 -PreflightOnly
.\scripts\windows-runtime.ps1 -HealthOnly
.\scripts\windows-runtime.ps1 -Status
```

`PASS` means the capability is ready, `WARN` means an optional or degraded path needs attention, and `FAIL` means the core path is blocked.
`windows-runtime-health.ps1` may exit with code `1` when the core stack is stopped because it checks running services only.
`windows-runtime.ps1 -Stop` uses `docker compose stop` and preserves volumes, images, and runtime data.

Known gaps:

- There is no one-click installer yet.
- There is no packaged EXE/MSI wrapper yet.
- Ollama is not Compose-managed yet.
- `windows-runtime.ps1` does not install or start Ollama and does not pull Ollama models.
- Full avatar runtime is not a normal CI path and should remain a hardware smoke path.
