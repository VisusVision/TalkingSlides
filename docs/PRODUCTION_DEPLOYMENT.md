# Production Deployment

This document describes the intended production shape. It is not a replacement for environment-specific infrastructure-as-code, secret management, backup policy, or incident response.

## Deployment Architecture

Run the system as separate services:

- Frontend: static Vite build served by CDN or web server.
- API: Django/Gunicorn service.
- Render worker: Celery worker for upload, extraction, TTS orchestration, and base video rendering.
- TTS service: FastAPI service for narration generation.
- Avatar worker: separate GPU Celery worker for LivePortrait, MuseTalk, and restoration work.
- Postgres: primary relational database.
- Redis: Celery broker/result backend and cache.
- Storage: durable shared storage for uploaded and generated media.

Do not run avatar/GPU work inside the API process.

## Required Secrets and Configuration

Production must set:

- `DEBUG=False`
- `SECRET_KEY`
- `MEDIA_TOKEN_SECRET`
- `POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- `REDIS_URL` or explicit Celery broker/result URLs
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `CORS_ALLOWED_ORIGINS`
- `API_PUBLIC_BASE_URL`
- `VITE_API_BASE_URL`

Use a secret manager or deployment platform secrets. Never bake real secrets into images, `.env.example`, docs, or Git.

## Fail-fast Settings Guard

When `DEBUG=False`, the Django settings guard rejects:

- default or missing `SECRET_KEY`
- missing `POSTGRES_HOST`
- default or missing `MEDIA_TOKEN_SECRET`
- missing `ALLOWED_HOSTS`
- wildcard `ALLOWED_HOSTS` unless explicitly allowed
- `CORS_ALLOW_ALL_ORIGINS=true`
- missing `CORS_ALLOWED_ORIGINS`

This is intentional. Fix the environment rather than bypassing the guard.

## Database and Redis

Use managed Postgres and Redis where possible. SQLite is local-only and must not be used in production. Run migrations before serving traffic:

```powershell
cd services\api
..\..\.venv\Scripts\python.exe manage.py migrate
```

In container deployments, run migrations as a release step or one-off job before new API/worker tasks process traffic.

## Storage

The current app reads and writes through `STORAGE_ROOT`. Production needs durable shared storage visible to API, render worker, TTS service, and avatar worker. Use an external volume or implement object storage before running horizontally scaled workers. MinIO variables exist for local/future S3-compatible storage, but the active application paths are filesystem-based.

When `DEBUG=False`, `STORAGE_ROOT` must be explicitly configured as an existing absolute directory that is readable and writable by the app process. Missing or read-only mounts fail during settings startup instead of later during uploads or renders.

Run a storage smoke check before deploys and after storage mount changes:

```powershell
cd services\api
python manage.py storage_smoke_check
```

Define backup, retention, quota, and cleanup policies before broad production use. See [Storage production readiness](STORAGE_PRODUCTION_READINESS.md).

## HTTPS, Proxy, and Secure Flags

Terminate HTTPS at a trusted load balancer, ingress, or reverse proxy. With `DEBUG=False`, Django defaults to:

- `SECURE_SSL_REDIRECT=True`
- `SESSION_COOKIE_SECURE=True`
- `CSRF_COOKIE_SECURE=True`
- `SECURE_HSTS_SECONDS=31536000`
- `SECURE_HSTS_INCLUDE_SUBDOMAINS=True`
- `SECURE_HSTS_PRELOAD=True`
- `SECURE_REFERRER_POLICY=same-origin`
- `X_FRAME_OPTIONS=DENY`

Optional first CSP rollout:

- `CSP_REPORT_ONLY_ENABLED=true`

This adds telemetry-only `Content-Security-Policy-Report-Only` and leaves enforcing CSP disabled. See [CSP Report-Only foundation](CSP_REPORT_ONLY.md) for the initial policy, report endpoint, and triage workflow.

Only disable SSL redirect for a known reverse-proxy edge case, and document the reason in deployment config.

## CORS and CSRF

Production CORS must be explicit:

```text
CORS_ALLOWED_ORIGINS=https://app.example.com
CORS_ALLOW_ALL_ORIGINS=False
CSRF_TRUSTED_ORIGINS=https://api.example.com,https://app.example.com
```

Do not use wildcard CORS in production.

## Playback Modes

For production, use `secure_stream` unless intentionally running a public-only deployment:

```text
LESSON_PROTECTION_DEFAULT_MODE=secure_stream
LESSON_PROTECTION_ALLOW_MP4_FALLBACK=1
LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION=1
```

`drm_protected` mode requires a real DRM vendor, license URLs, player integration, packaged/encrypted assets, and operational key management. Keep DRM credentials in secret infrastructure/provider configuration.

See [SECURE_PLAYBACK_DRM.md](SECURE_PLAYBACK_DRM.md).

## Health and Readiness

- Lightweight health: `/health/`
- API readiness: `/api/v1/ready/`

The readiness endpoint intentionally returns only `{"status":"ok"}` and does not depend on Redis, GPU, or TTS. Use deeper smoke tests for dependency validation.

## Avatar GPU Sizing

Avatar rendering is non-blocking and should run on a separate queue. Start with one avatar worker process per GPU. Benchmark before increasing concurrency. Watch:

- GPU memory
- average avatar job duration
- queue depth
- failed restoration or MuseTalk stages
- storage throughput

Base video rendering and publication should continue even when avatar rendering is queued, slow, or failed.

## Rollback Basics

- Keep database migrations backward-compatible when possible.
- Deploy API and workers from the same image revision.
- Stop or drain workers before rolling back task schema changes.
- Keep media storage and database backups independent of app releases.
- Keep old images available until post-deploy smoke checks pass.

## Post-deploy Smoke

Minimum smoke:

```powershell
Invoke-WebRequest https://api.example.com/api/v1/ready/
Invoke-WebRequest https://app.example.com/
```

Then verify login, upload, render job completion, catalog visibility, playback-token issuance, and one Watch playback session.
