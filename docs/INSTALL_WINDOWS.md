# Windows 11 Installation

This guide sets up VISUS VidLab / AI_ACADEMY for local development on Windows. It favors Docker Compose for shared services and keeps avatar/GPU work optional.

## Prerequisites

- Git for Windows.
- Docker Desktop with WSL2 integration enabled.
- WSL2 recommended for Docker performance and Linux-compatible troubleshooting.
- Python 3.10 or newer on PATH.
- Node.js 20 or newer with npm.
- PowerShell 5.1+ or PowerShell 7.
- Optional: NVIDIA GPU, current NVIDIA driver, CUDA-capable Docker runtime, and validated LivePortrait/MuseTalk model paths for real avatar generation.

Check tools:

```powershell
git --version
docker --version
docker compose version
python --version
node --version
npm --version
```

## Clone

```powershell
git clone <repo-url> AI_ACADEMY
cd AI_ACADEMY
```

## Environment File

Create a local env file from the template:

```powershell
Copy-Item infra\.env.example infra\.env
```

Never commit `infra/.env`. The template contains placeholders only. For local development, keep `DEBUG=True` and use local placeholder values. Production requirements are documented in [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) and [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md).

## Run Setup Script

From the repository root:

```powershell
.\scripts\windows-dev-setup.ps1
```

For a read-only check:

```powershell
.\scripts\windows-dev-setup.ps1 -CheckOnly
```

The setup script checks Git, Docker, Node, npm, and Python. It creates `.venv` if missing, installs the obvious Python dev/test requirements, and runs `npm ci` in `services/frontend` when `node_modules` is missing. It does not write secrets and does not overwrite `infra/.env`.

Double-click users can run:

```text
install-dev.bat
```

## Start Services

Default local stack:

```powershell
.\scripts\windows-dev-start.ps1
```

This starts the lightweight core services: Postgres, Redis, MinIO, API, and frontend. It does not start the GPU avatar worker by default.

For render/TTS work:

```powershell
.\scripts\windows-dev-start.ps1 -WithWorker -WithTts
```

For avatar work on a validated GPU host:

```powershell
.\scripts\windows-dev-start.ps1 -WithWorker -WithTts -WithAvatar
```

## Access

- Frontend: `http://localhost:3000`
- API: `http://localhost:8000`
- Readiness: `http://localhost:8000/api/v1/ready/`
- TTS service, when started: `http://localhost:8001`
- MinIO console: `http://localhost:9001`

## Stop Services

```powershell
.\scripts\windows-dev-stop.ps1
```

To remove Compose volumes, use the explicit destructive flag:

```powershell
.\scripts\windows-dev-stop.ps1 -RemoveVolumes
```

## Common First-run Problems

- Docker is installed but not running: start Docker Desktop and wait until it reports ready.
- `infra/.env` is missing: copy `infra/.env.example` to `infra/.env`.
- PowerShell script execution is blocked: run with `-ExecutionPolicy Bypass` for this invocation only.
- Wrong Python is used: use `.\.venv\Scripts\python.exe` for project commands.
- Frontend dependencies are missing: run `npm ci` in `services/frontend`.
- Ports are busy: stop anything using `3000`, `8000`, `8001`, `5432`, `6379`, `9000`, or `9001`.
- Avatar startup fails without GPU: start without `-WithAvatar`, or validate NVIDIA Docker GPU support first.
