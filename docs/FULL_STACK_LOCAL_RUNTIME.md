# Full Stack Local Runtime

This document describes the local runtime profiles VISUS VidLab should expose through scripts and the future installer. These are product/runtime profiles, not all current Docker Compose profiles.

Current reality:

- `scripts/windows-dev-start.ps1` starts selected Compose services by name.
- `infra/docker-compose.yml` currently has one explicit Compose profile: `translation` for `libretranslate`.
- Ollama currently runs on the host unless a future Compose service is added.
- Avatar worker dependencies belong in the Docker worker image, not the Windows virtual environment.
- Docker smoke CI intentionally avoids the full heavy avatar runtime.

## Profile Summary

| Profile | Purpose | Current entry point |
| --- | --- | --- |
| `core` | API, frontend, database, Redis, and local object-store helper. | `.\scripts\windows-dev-start.ps1` |
| `tts` | Add TTS service for render and preview work. | `.\scripts\windows-dev-start.ps1 -WithTts` |
| `intelligence` | Enable heuristic intelligence and optional host-side Ollama enhancement. | Env configuration plus core/worker services |
| `avatar-gpu` | Add GPU avatar worker for LivePortrait/MuseTalk work. | `.\scripts\windows-dev-start.ps1 -WithAvatar` |
| `translation` | Add local LibreTranslate service for translation fallback checks. | `docker compose -f infra\docker-compose.yml --profile translation up -d libretranslate` |
| `full-stack` | Planned installer profile combining selected AI services after preflight passes. | Roadmap |

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
docker compose -f infra\docker-compose.yml ps
Invoke-WebRequest http://localhost:8000/api/v1/ready/
Invoke-WebRequest http://localhost:3000/
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
Invoke-WebRequest http://localhost:8001/ready
docker compose -f infra\docker-compose.yml logs -f tts_service
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
- `worker-avatar`.

Host requirements:

- Core and TTS requirements.
- NVIDIA GPU and current NVIDIA driver.
- Docker Desktop GPU support.
- Valid model paths and avatar source assets.
- Enough disk space for CUDA, PyTorch, OpenMMLab, LivePortrait, MuseTalk, and model caches.

Expected health checks:

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
docker compose -f infra\docker-compose.yml logs -f worker-avatar
```

When validating a built heavy worker image, import checks should run inside the container, not the Windows virtual environment.

Known gaps:

- First avatar image build may be heavy.
- `mmcv`/OpenMMLab dependencies must be installed in the Docker image or provided through a compatible local wheel/prebuilt image.
- Installing `mmcv`, `mmpose`, LivePortrait, or MuseTalk into Windows Python does not fix `worker-avatar`.
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
docker compose -f infra\docker-compose.yml --profile translation up -d libretranslate
Invoke-WebRequest http://localhost:5000/languages
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
- Optional `worker-avatar`.
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

Known gaps:

- There is no one-click installer yet.
- There is no packaged EXE/MSI wrapper yet.
- Ollama is not Compose-managed yet.
- Full avatar runtime is not a normal CI path and should remain a hardware smoke path.
