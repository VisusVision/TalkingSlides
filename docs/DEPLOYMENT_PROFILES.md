# Deployment Profiles

These profiles are practical starting points before choosing the final hosting platform. They describe environment posture and service split only. They do not require application code changes.

## Profile Summary

| Profile | Purpose | Playback | Data services | Avatar |
| --- | --- | --- | --- | --- |
| `local-dev` | Developer laptop and Docker Compose | Public by default, secure stream optional | Local Compose Postgres/Redis or local debug fallback | Optional GPU |
| `staging` | Production-like validation | `secure_stream` | Postgres, Redis, durable storage | Optional separate worker |
| `production-secure_stream` | First production target | `secure_stream` | Managed Postgres/Redis, durable storage, HTTPS | Separate GPU node if enabled |
| `production-drm_protected-later` | Later DRM launch | `drm_protected` | Same as production plus DRM vendor/key operations | Separate GPU node if enabled |

Use [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) before moving between profiles.

## A. local-dev

Use for local development and onboarding.

Expected shape:

- `DEBUG=True`
- local Docker Compose through `infra/docker-compose.yml`
- local filesystem storage at repo-root `storage_local/`, mounted as `/app/storage_local`
- local placeholder secrets only
- DRM disabled
- `secure_stream` optional, but local profile usually uses public playback
- avatar GPU optional; do not start `worker-avatar` unless GPU/runtime assets are ready

Recommended example:

- `infra/env.local.example`
- `infra/.env.example` remains the broad local template

Typical start:

```powershell
Copy-Item infra\env.local.example infra\.env
.\scripts\windows-dev-start.ps1
```

Use `.\scripts\windows-dev-start.ps1 -WithTts -WithWorker -WithAvatar` only after GPU support is validated.

## B. staging

Use for production-like validation before a production deploy.

Required posture:

- `DEBUG=False`
- Postgres, not SQLite
- Redis for Celery/cache
- HTTPS API and frontend domains
- existing, readable, writable, durable `STORAGE_ROOT`
- explicit `ALLOWED_HOSTS`
- explicit `CORS_ALLOWED_ORIGINS`
- explicit `CSRF_TRUSTED_ORIGINS`
- secure stream enabled
- DRM disabled unless real provider credentials and packaging are configured
- avatar worker optional and separate from render worker
- production fail-fast settings must pass

Recommended example:

- `infra/env.staging.example`

Validate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check-production-env.ps1 -EnvFile infra\env.staging.example -Profile staging
```

Staging should run the full release checklist, including upload, render, playback, HLS, and optional avatar smoke.

## C. production-secure_stream

Use as the first production target.

Required posture:

- `DEBUG=False`
- managed or production-grade Postgres
- managed or production-grade Redis
- HTTPS only
- tokenized playback
- HLS packaging enabled where supported by the deployment
- watermark and heartbeat enabled
- secure cookies and HSTS enabled by Django production defaults
- secrets provided by a secret manager or deployment platform
- durable shared storage visible to API, render worker, TTS, and avatar worker
- storage smoke check passes with `python manage.py storage_smoke_check`
- avatar worker on a separate GPU node if avatar generation is enabled

Recommended example:

- `infra/env.production-secure-stream.example`

Validate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check-production-env.ps1 -EnvFile infra\env.production-secure-stream.example -Profile secure_stream
```

Do not use wildcard hosts or CORS allow-all. Do not use SQLite.

## D. production-drm_protected-later

Use only after `production-secure_stream` is stable and a DRM vendor has been selected.

Includes everything from `production-secure_stream`, plus:

- `LESSON_PROTECTION_DEFAULT_MODE=drm_protected`
- `DRM_ENABLED=1`
- DRM vendor required
- license URLs and certificate URLs configured by backend environment
- Shaka/EME player behavior validated in browser matrix
- encrypted packaging and key management handled by provider or production packaging process
- no frontend DRM secrets
- no Clear Key or raw-key shortcut for production
- no license credentials in Git or frontend bundles

Recommended example:

- `infra/env.production-drm-protected.example`

Validate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check-production-env.ps1 -EnvFile infra\env.production-drm-protected.example -Profile drm_protected
```

DRM is an operational and vendor integration project, not only an environment toggle.

## Profile Decision Notes

- Choose `production-secure_stream` first unless a commercial DRM requirement is already funded and staffed.
- Keep `local-dev` permissive, but do not copy local secrets or CORS posture to staging/production.
- Keep staging as close to production as cost allows.
- Keep avatar GPU capacity separate so avatar work cannot starve base lesson rendering.
- Keep deployment secrets outside the repository for all non-local profiles.
