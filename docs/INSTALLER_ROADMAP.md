# Installer Roadmap

This roadmap describes the planned Windows installer architecture for VISUS VidLab. It is design guidance, not a claim that an EXE, MSI, or one-click installer already exists.

## Goals

- Make local setup simple for a teacher, publisher, evaluator, or developer.
- Keep heavy dependencies containerized where possible.
- Avoid installing `mmcv`, `mmpose`, LivePortrait, MuseTalk, or other avatar runtime packages into Windows Python.
- Guide users through prerequisite installation without silently making major system changes.
- Let users choose a runtime profile before heavy downloads or builds start.
- Produce a clear health summary at the end.

## Non-goals

- No silent Docker Desktop, WSL, driver, CUDA, model, or system-wide package changes.
- No destructive cleanup without explicit confirmation.
- No private repository workflow assumptions.
- No hidden dependency on a GPU unless the user selects an avatar GPU profile.
- No claim that full avatar runtime is CI-built.

## Release Package Contents

A future release package should contain:

- A small Windows launcher, initially a signed PowerShell entry point or batch wrapper.
- Profile-aware scripts for preflight, start, stop, update, and health summary.
- A versioned `.env` generator based on `infra/.env.example`.
- Documentation links for install, troubleshooting, runtime profiles, and operations.
- Optional checksums for downloaded models or external artifacts.
- A manifest describing supported service images, model versions, required ports, and profile dependencies.

Future packaging may wrap the scripts in an EXE/MSI, but the script contract should remain testable without the wrapper.

## Installer Flow

1. Show selected install mode and profile choices.
2. Run preflight checks.
3. Ask for consent before opening or downloading prerequisite installers.
4. Generate or update local `.env` from safe prompts.
5. Pull or build required Docker images for the selected profiles.
6. Download or verify model artifacts when the selected profile needs them.
7. Start selected services.
8. Run health checks.
9. Print a health summary with next actions and troubleshooting links.

## Preflight Checker

The current script entry point is:

```powershell
.\scripts\windows-preflight.ps1
.\scripts\windows-preflight.ps1 -Json
```

The preflight checker should report pass/warn/fail status for:

- Windows version.
- PowerShell version.
- WSL2 availability and default distro state.
- Docker Desktop installed.
- Docker daemon reachable.
- Docker Compose v2 available.
- Disk space for selected profiles.
- Required ports available.
- NVIDIA GPU presence when avatar GPU mode is selected.
- NVIDIA driver version and `docker run --gpus all ... nvidia-smi` when avatar GPU mode is selected.
- Git, Python, Node.js, and npm availability for developer mode.
- Existing `infra/.env` state and missing required placeholders.

The checker may offer links or open official installers, but should not silently install WSL, Docker Desktop, GPU drivers, or system packages.

Current Phase B behavior is intentionally read-only: it prints clear next steps for missing external prerequisites and exits with code `1` only when core blockers are present.

## Profile Selector

The profile selector should expose:

- `core`: API, frontend, Postgres, Redis, MinIO.
- `tts`: TTS service and any selected model/cache path.
- `intelligence`: heuristic intelligence plus optional host-side or future Compose Ollama.
- `translation`: optional LibreTranslate service.
- `avatar-gpu`: GPU avatar worker, model checks, Docker GPU validation.
- `full-stack`: all selected AI paths after preflight passes.

The selector should explain disk, network, time, and GPU implications before building or downloading heavy assets.

## Environment Generator

The installer should generate `infra/.env` from safe defaults and user input:

- Preserve existing values unless the user approves replacement.
- Never print secrets after entry.
- Keep local placeholder secrets distinct from production secrets.
- Keep `STORAGE_BACKEND=filesystem` unless the user is explicitly running storage adapter readiness checks.
- Use `OLLAMA_BASE_URL=http://host.docker.internal:11434` for Docker Desktop host-side Ollama.
- Keep avatar heavy dependency settings in Docker build args or image metadata, not Windows Python.

## Docker Image Pull / Build

The installer should prefer prebuilt release images where available. If local build is required:

- Build only images needed by the selected profiles.
- Explain that first avatar builds can be large.
- Support `MMCV_LOCAL_WHEEL` or `MMCV_WHEEL_URL` for controlled OpenMMLab dependency sources.
- Do not fall back to compiling `mmcv` from source.
- Keep Docker smoke CI separate from full avatar hardware validation.

## Model Downloads and Checks

Model handling should be explicit:

- Show model names, size estimates, license notes, and destination paths before download.
- Verify checksums where available.
- Keep model files under ignored local storage/cache paths.
- Support offline or predownloaded model bundles for avatar worker and TTS profiles.
- Report missing model paths as a profile health failure, not as a Python package suggestion.

## Health Summary

The current script entry point is:

```powershell
.\scripts\windows-runtime-health.ps1
.\scripts\windows-runtime-health.ps1 -Json
```

The final summary should include:

- Selected profiles.
- Service status.
- URLs for frontend, API readiness, TTS readiness, MinIO console, and LibreTranslate when enabled.
- Docker GPU result when avatar mode is selected.
- Ollama reachability and model list when intelligence enhancement is selected.
- Missing or degraded capabilities.
- Exact next commands for logs, stop, update, and troubleshooting.

The current health script checks already-running services only. It must not start services, rebuild images, pull models, or install optional providers.
It may exit with code `1` when core API/frontend services are stopped, which is the expected health result for a stopped core stack.

## Start / Stop / Update Scripts

Initial script contract:

- `windows-preflight.ps1`: read-only host prerequisite and profile-readiness check with optional JSON output.
- `windows-dev-setup.ps1`: prerequisite and local dependency checks.
- `windows-dev-start.ps1`: profile-aware service startup.
- `windows-dev-stop.ps1`: stop selected Compose services.
- `windows-runtime-health.ps1`: profile-aware summary for already-running services, with optional JSON output.
- Future `windows-dev-update.ps1`: image pull/build, migrations, and dependency refresh with consent.

Destructive actions, such as volume removal, must require explicit flags and visible warnings.

## Failure Recovery

The installer should handle:

- Docker daemon unavailable.
- Ports already in use.
- Missing WSL2 integration.
- GPU selected but unavailable in Docker.
- Failed image pull/build.
- Missing model files.
- Unhealthy TTS/avatar/translation services.
- Existing `.env` values that conflict with the selected profile.

Recovery should prefer clear instructions, rerunnable commands, and narrow retries. It should not delete volumes, wipe storage, reset git state, or edit unrelated system state.

## Implementation Plan

### Phase A: Documentation

- Publish the Windows install guide.
- Publish runtime profile docs.
- Publish this installer roadmap.
- Mark EXE/MSI and one-click installer as planned only.

### Phase B: Preflight Check Script

- Add a read-only profile-aware preflight command. Initial script exists as `scripts/windows-preflight.ps1`.
- Report host prerequisites, ports, Docker state, disk space, GPU state, and env-file status.
- Keep major prerequisite installation user-driven.

### Phase C: Runtime Profile Health Summary

- Add a profile-aware health command. Initial script exists as `scripts/windows-runtime-health.ps1`.
- Summarize selected service status, endpoints, Docker service state, Ollama reachability, and missing capabilities.
- Keep health checks non-mutating.

### Phase D: Full-stack Compose / Ollama Strategy

- Decide whether Ollama remains host-side or gains a Compose-managed optional service.
- Document model storage and resource expectations.
- Add Compose/service wiring only after the resource and update model is clear.

### Phase E: Packaged EXE/MSI Wrapper

- Wrap the script contract in a signed launcher or installer.
- Preserve transparent commands and logs.
- Keep consent gates for prerequisites, models, heavy builds, updates, and destructive actions.
