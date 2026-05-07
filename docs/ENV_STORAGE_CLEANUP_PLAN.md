# Env And Storage Cleanup Plan

## Executive Summary

This began as a planning document. A follow-up storage mount fix now makes repo-root `storage_local/` the canonical local Docker host storage directory while preserving `/app/storage_local` as the container `STORAGE_ROOT`.

The least risky env strategy for this repo is to keep `infra/.env.example` as the single canonical Docker env template because it is the only env example currently present and `infra/docker-compose.yml` reads `infra/.env` through `env_file: .env`. The template has been updated for the active Django settings module, active TTS glossary path, Vite API URL variable, XTTS startup flags, and safe Google OAuth placeholders. MinIO/S3 variables remain configured but are not wired to application storage, and media token settings still need clearer production documentation.

The local Docker storage strategy is now repo-root `storage_local/` mounted into containers as `/app/storage_local`. `infra/storage_local` was the old accidental location caused by compose-relative `./storage_local` mounts and should not be used for new local runtime media. This still does not reorganize subfolders or migrate any leftover old files.

## Current Env Sources

| Source | Status | Evidence | Notes |
| --- | --- | --- | --- |
| `infra/.env.example` | Only env template found | `rg --files` found only `infra/.env.example`. | Should remain canonical for Docker env until there is a strong reason to add a root template. |
| `infra/.env` | Local ignored runtime env | Present locally and ignored by `.gitignore` via `.env*`. | Docker Compose reads this file when run from `infra/docker-compose.yml`. Do not commit it. |
| Root `.env.example` | Missing | No root env example exists. | Do not add one in Phase B1; adding a second template increases confusion. |
| `services/*/.env.example` | Missing | No service-level env examples found. | Not needed yet. |
| README env docs | Present but mixed | README tells users to copy `infra/.env.example` to `infra/.env`, and also gives shell env examples for local API/TTS runs. | Keep Docker template canonical; document shell overrides separately. |
| `infra/docker-compose.yml` | Reads `infra/.env` | `x-python-common` uses `env_file: .env`; path is relative to the compose file directory. | Compose also overrides some critical env values per service. |

### Env Conflicts And Gaps

| Variable / Group | Current State | Recommended Future Cleanup |
| --- | --- | --- |
| `DJANGO_SETTINGS_MODULE` | Template now uses `config.settings`, matching the active Django package and compose overrides. | Keep README aligned. |
| `TTS_GLOSSARY_PATH` | Template now uses `/app/tts_preprocess/glossary.json`, matching the active package and TTS README. | Document that local Python can leave it unset to use package default. |
| `TTS_SERVICE_URL` | Template uses `http://tts_service:8001`; compose uses the same for worker/API service-to-service calls. Local shell examples use `http://localhost:8001`. | Keep Docker value in `infra/.env.example`. Document local shell override separately. |
| `VITE_API_BASE_URL` | Template defines `VITE_API_BASE_URL=http://localhost:8000/api/v1`, and `services/frontend/src/api.js` reads it with the same local fallback. | Keep Docker/frontend docs aligned. |
| `STORAGE_ROOT` | Compose sets `/app/storage_local` for API/TTS/worker. API default is repo-root `storage_local`; worker/TTS defaults are relative `storage_local`. Docker now mounts repo-root `storage_local/` to that container path. | Keep `/app/storage_local` inside containers. Document that repo-root `storage_local/` is canonical for local Docker development. |
| `LOCAL_STORAGE_PERMISSIVE_CHMOD` / `LOCAL_STORAGE_DIR_MODE` | Local compose uses these to make the top-level `/app/storage_local` bind mount writable by non-root `appuser` containers. Defaults are dev-oriented: enabled with mode `0777`. | Keep scoped to local Docker runtime storage. Do not recursively chmod media. Production deployments should use a volume with deliberate ownership/mode instead. |
| `XTTS_ENABLED`, `XTTS_PRELOAD_ON_STARTUP`, `XTTS_WARMUP_BLOCKING` | Template includes the Docker defaults and compose applies them to TTS service. | Include lightweight dev notes for `XTTS_ENABLED=0` where needed. |
| MinIO/S3 | Compose starts MinIO and template defines `MINIO_*` / `AWS_*`, but code inspection found local `STORAGE_ROOT` reads/writes and no active boto3/django-storages adapter. | Mark MinIO/S3 as optional future storage, not active app storage. Do not remove compose service until storage direction is decided. |
| Google OAuth | Template now uses safe placeholders and `GOOGLE_AUTH_ENABLED=0` by default. | Rotate credentials if the previously committed values were real. |
| `MEDIA_TOKEN_SECRET` / playback protection | Settings have defaults for `MEDIA_TOKEN_SECRET` and `MEDIA_TOKEN_TTL_SECONDS`; template does not clearly include them. | Add placeholders and production warning. Keep protection defaults aligned with `services/api/config/settings.py`. |
| `DATABASE_URL` | Template constructs `DATABASE_URL` with shell-style interpolation, but settings use individual `POSTGRES_*` vars because Docker env files do not expand references. | Remove or comment as informational only. Prefer `POSTGRES_*` as canonical. |

## Recommended Canonical Env Strategy

Choose option A: keep only `infra/.env.example` as the canonical Docker env template.

Reasons:

- It is the only env example currently present.
- `infra/docker-compose.yml` already reads `infra/.env` by design.
- README already directs users to copy `infra/.env.example` to `infra/.env`.
- Adding a root `.env.example` now would create two sources of truth before env semantics are stable.

Policy:

- Docker env: `infra/.env.example` -> `infra/.env`.
- Local shell runs: README may show explicit `$env:...` examples, but those are not a second template.
- Frontend Vite env: document `VITE_API_BASE_URL` in `infra/.env.example` for Docker/dev consistency, then wire it in code in Phase B2.
- Secrets: env examples must use placeholders only.

## Frontend API URL Status

Current state:

- `services/frontend/src/api.js` reads `import.meta.env.VITE_API_BASE_URL`.
- The fallback remains `http://localhost:8000/api/v1` for local Docker/browser development.
- `API_ORIGIN = API_BASE_URL.replace(/\/api\/v1\/?$/, "")` still preserves existing media URL behavior.
- `infra/.env.example` now documents `VITE_API_BASE_URL=http://localhost:8000/api/v1`.

Remaining work:

1. Keep README/deployment docs aligned with the chosen API origin.
2. Validate frontend builds when environment-specific API origins are introduced.

## Current Storage Usage

| Area | Current Path Pattern | Owner / Evidence | Notes |
| --- | --- | --- | --- |
| API storage root | `settings.STORAGE_ROOT` default repo-root `storage_local`; Docker sets `/app/storage_local`. | `services/api/config/settings.py` | Used by API upload, media streaming, cover, voice, and avatar endpoints. |
| Worker storage root | `STORAGE_ROOT` env default `storage_local`; Docker sets `/app/storage_local`. | `services/worker/tasks.py` | Worker writes per-project render workspaces and final artifacts. |
| TTS storage root | `STORAGE_ROOT` env default `storage_local`; `TTS_AUDIO_DIR` default `storage_local/tts`. | `services/tts_service/main.py` | TTS reads reference voices from `STORAGE_ROOT/voices` and writes generated MP3s to `TTS_AUDIO_DIR`. |
| Project uploads | `STORAGE_ROOT/uploads/<project_id>/lesson.*`, `cover.*` | `ProjectUploadView` in `services/api/core/views.py` | Rerender reads the original lesson file from this upload directory. |
| Voice references | `STORAGE_ROOT/voices/<voice_id>.wav` | `VoiceUploadView` and TTS service | Required for XTTS voice cloning. |
| Avatar sources/previews | `STORAGE_ROOT/avatars/<user_id>/...` | Avatar profile/prepare/preview views and worker avatar flow | Includes uploads, processed images, preview video, and metrics/locks. |
| Worker project output | `STORAGE_ROOT/<project_id>/images`, `notes`, `audio`, `parts`, and final files in project root. | `_workspace()` in `services/worker/tasks.py` | Current final MP4/SRT are `STORAGE_ROOT/<project_id>/<project_id>.mp4` and `.srt`. |
| HLS output | `STORAGE_ROOT/<project_id>/drm/hls/` | `concat_and_finalize()` in `services/worker/tasks.py` | Used for secure/DRM-capable streaming contract. |
| Playback sidecar | `STORAGE_ROOT/<project_id>/playback_assets.json` | Worker sidecar writer and API playback views | Stores playback metadata and generated asset references. |
| Subtitle files | `STORAGE_ROOT/<project_id>/<project_id>.srt` | Worker finalization and `Job.srt_url` | API can serve as VTT through tokenized stream endpoint. |
| Docker host storage | `storage_local/` | Compose mounts `../storage_local:/app/storage_local` from `infra/docker-compose.yml`. | Canonical local Docker runtime media root. Do not commit runtime media. |
| Old infra storage | `infra/storage_local/` | Previous compose mount used `./storage_local:/app/storage_local`. | Old accidental location; keep only until any leftover local files are manually reconciled. |
| Tracked runtime artifact | `services/api/db.sqlite3` | `git ls-files` includes it. | Do not delete until verified; tests can mutate it if not run with temp DB. |

## Recommended Storage Layout

Recommended future canonical local host root:

```text
storage_local/
  uploads/
    projects/
      <project_id>/
        lesson.*
        cover.*
    voices/
      <voice_id>.wav
    avatars/
      <teacher_id>/
        uploads/
  generated/
    projects/
      <project_id>/
        images/
        notes/
        audio/
        parts/
        final/
          <project_id>.mp4
          <project_id>.srt
          playback_assets.json
        hls/
          index.m3u8
          seg_*.ts
        subtitles/
  tts/
    audio/
  avatar_previews/
  cache/
    tts/
    hf/
    torch/
  models/
  temp/
  logs/
```

Migration rule:

- Do not jump directly to this layout in Phase B1/B2/B3.
- First add a compatibility plan for existing relative paths stored in DB fields such as `Job.result_url`, `Job.srt_url`, cover paths, avatar paths, voice IDs, and playback sidecars.
- Either keep old path resolution indefinitely or write a migration/backfill script that copies old files and rewrites DB/sidecar references safely.

Short-term recommendation:

- Keep `STORAGE_ROOT=/app/storage_local` inside containers.
- Keep existing path semantics until the migration plan is tested.
- For host development, use repo-root `storage_local/` as the canonical local Docker runtime storage root.
- Keep the local compose storage entrypoint enabled so the top-level bind mount is writable by `appuser` in API, worker, worker-avatar, and TTS containers. The entrypoint chmods only the top-level storage root, not existing media trees.

### Local Permission Recovery

The local Docker app services run as `appuser` (`uid=1000`) after startup. On Docker Desktop / Windows bind mounts, `/app/storage_local` can appear as `root:root` mode `755`, which prevents `appuser` from creating new top-level project directories such as `/app/storage_local/<project_id>`.

Compose now mounts `infra/dockerfiles/local-storage-entrypoint.sh` into API, worker, worker-avatar, and TTS. The services start as root, repair only the top-level `STORAGE_ROOT`, then drop to `appuser` with `runuser`.

Apply the fix to existing containers with:

```powershell
docker compose -f infra/docker-compose.yml up -d --force-recreate api worker worker-avatar tts_service
```

For immediate manual recovery on an already-running local stack, this non-recursive command is sufficient because all app services share the same host bind mount:

```powershell
docker compose -f infra/docker-compose.yml exec --user root api sh -lc "chmod 0777 /app/storage_local"
```

Do not run recursive chmod/chown over all runtime media unless a separate diagnosis proves nested ownership is broken.

## Production Storage Direction

MVP:

- Use a local mounted volume as the production-safe near-term option.
- Mount it at a stable container path, ideally `/app/storage_local` until code is abstracted.
- Keep generated runtime files outside git.
- Back up the mounted volume and the database together because DB rows and filesystem relative paths must stay in sync.

Later MinIO/S3:

- Treat current MinIO/S3 env variables as configured but inactive.
- Add a real storage adapter before claiming MinIO/S3 support.
- Hide path construction behind a storage service API before migrating worker/API/TTS reads and writes.
- Decide how TTS reference voices, transient TTS audio, HLS segments, subtitles, and avatar preview files map to object storage.
- Define lifecycle policies for temporary TTS audio, failed render workspaces, old HLS segments, preview files, and model/cache directories.

## Gitignore Recommendations

Current `.gitignore` already covers:

- `.env*` with `!.env.example`
- `storage_local/`
- `outputs/`
- `*.mp4`, `*.wav`, `*.pptx`
- `.tools/`
- `node_modules/`, `.next/`, `out/`, `dist/`
- Python caches and virtualenvs

Recommended future changes:

- Add explicit nested storage ignores: `**/storage_local/`.
- Add local DB ignores only after deciding `services/api/db.sqlite3` is not fixture data: `*.sqlite3`, `services/api/db.sqlite3`.
- Add generated media extensions used by current pipeline: `*.mp3`, `*.m4a`, `*.webm`, `*.mov`, `*.m3u8`, `*.ts`, `*.srt`, `*.vtt`.
- Add logs explicitly: `*.log`, `logs/`, `**/logs/`.
- Add model/cache runtime dirs if they can appear outside `storage_local/`: `models/`, `**/models/`, `cache/`, `**/cache/`, with care not to ignore source fixtures.
- Do not ignore broad `*.json` because source config, tests, package locks, and glossary data are JSON.

## Step-by-Step Future Implementation

Phase B1: env docs/template cleanup

- Update `infra/.env.example` only for correctness and safe placeholders. Done for Django settings, TTS glossary path, Vite API URL, XTTS startup flags, and Google OAuth placeholders.
- Add or clarify `MEDIA_TOKEN_SECRET`, `MEDIA_TOKEN_TTL_SECONDS`, `API_PUBLIC_BASE_URL`, and lesson protection token values.
- Add fuller lightweight dev notes for `XTTS_ENABLED=0`.
- Replace Google OAuth values with placeholders and default `GOOGLE_AUTH_ENABLED=0`.
- Mark MinIO/S3 as optional/inactive until a storage adapter exists.
- Update README/docs wording to match the canonical template.

Phase B2: frontend `VITE_API_BASE_URL` fix

- Done: `services/frontend/src/api.js` reads `import.meta.env.VITE_API_BASE_URL`.
- Done: fallback `http://localhost:8000/api/v1` remains.
- Done: media URL behavior is unchanged.
- Keep running `cd services/frontend && npm run build` after frontend changes.

Phase B3: gitignore/runtime artifact cleanup

- Decide whether `services/api/db.sqlite3` is fixture data or local mutable state.
- If local mutable state, remove it from git and add targeted ignore rules.
- Add explicit ignores for nested `storage_local`, logs, generated media/audio/subtitle/HLS files, and model/cache directories.
- Do not delete user runtime data.

Phase B4: storage layout migration plan

- Reconcile any leftover files from old `infra/storage_local` local checkouts before removing that directory.
- Write copy/migration instructions for developers who still have local runtime media in `infra/storage_local`.
- Add compatibility path resolution or a one-time migration for old project outputs.
- Validate upload, rerender, TTS voice lookup, avatar preview, media streaming, SRT/VTT serving, and HLS playback-token paths.

Phase B5: optional MinIO/S3 adapter plan

- Introduce a storage service abstraction before changing reads/writes.
- Decide which artifacts stay local versus object storage.
- Add config for bucket names, prefixes, signed URLs, retention, and cleanup jobs.
- Migrate only after local mounted-volume behavior is stable.

## Remaining Phase B1 Follow-Up

- Add/clarify `MEDIA_TOKEN_SECRET`, `MEDIA_TOKEN_TTL_SECONDS`, `API_PUBLIC_BASE_URL`, and lesson protection token placeholders.
- Mark MinIO/S3 variables as optional/future because active app storage still uses `STORAGE_ROOT`.
- Update README wording so `infra/.env.example` is clearly the canonical Docker env template.
- env values corrected
- validation result
- commit hash
- what remains for Phase B2/B3/B4
```
