# Runtime Storage And Lesson Visibility Diagnosis

## Executive Summary

Lessons are not visible in the public UI for two separate reasons:

1. The public catalog is empty because Docker Postgres currently has `0` published projects. The database does contain projects and completed jobs, but every inspected project has `is_published=false`. `GET /api/v1/catalog/` correctly returns `[]` because `CatalogListView` filters `Project.objects.filter(is_published=True, jobs__status="done")`.
2. Media files for recent completed jobs were not where the Docker API and worker expected them. The original diagnosis found compose mounting host `infra/storage_local` into containers as `/app/storage_local`, while rendered lesson files existed under repo-root `storage_local`.

Follow-up fix: local Docker now uses repo-root `storage_local/` as the canonical host runtime storage directory, mounted into containers as `/app/storage_local`. The in-container `STORAGE_ROOT` remains unchanged.

At the start of diagnosis, the API container was also stopped with exit code `137`, so host requests to `localhost:8000` failed. Starting the existing API container, without rebuilding, made API requests work again. The worker image build timeout is likely unrelated to lesson visibility because the already-built worker container is running and the visibility failures are explained by API availability, publication state, and storage mount mismatch.

## Active Docker Storage

Resolved command:

```powershell
docker compose -f infra/docker-compose.yml config > docker-compose.resolved.yml
```

Resolved mounts:

| Service | Container `STORAGE_ROOT` | Host path mounted to `/app/storage_local` | Uses same storage as API? |
| --- | --- | --- | --- |
| API | `/app/storage_local` | `C:\Users\Abdur\IdeaProjects\AI_ACADEMY\storage_local` | Yes |
| Worker | `/app/storage_local` | `C:\Users\Abdur\IdeaProjects\AI_ACADEMY\storage_local` | Yes |
| TTS service | `/app/storage_local` | `C:\Users\Abdur\IdeaProjects\AI_ACADEMY\storage_local` | Yes |
| MinIO helper mount | `/storage_local` | `C:\Users\Abdur\IdeaProjects\AI_ACADEMY\storage_local` | Same host folder, different container path |

Conclusion: Docker now uses repo-root `storage_local` for local development. The earlier `infra/storage_local` behavior came from `./storage_local` resolving relative to the `infra/` directory.

## Local Docker Storage Permissions

Follow-up permission diagnosis found a second local Docker storage issue:

- API, worker, worker-avatar, and TTS containers run application code as `appuser` (`uid=1000`, `gid=1000`).
- The repo-root bind mount appears in containers as `/app/storage_local` owned by `root:root` with mode `755`.
- Existing project folders created by `appuser` are usually writable, but a new top-level project folder such as `/app/storage_local/<project_id>` cannot be created when the root mount itself is not writable by `appuser`.
- This explains failures like `PermissionError: [Errno 13] Permission denied: '/app/storage_local/<project_id>'`.

Implemented local-compose fix:

- `infra/docker-compose.yml` now starts API, worker, worker-avatar, and TTS through `infra/dockerfiles/local-storage-entrypoint.sh`.
- The entrypoint runs as root only long enough to `mkdir -p "$STORAGE_ROOT"` and chmod the top-level storage root according to `LOCAL_STORAGE_DIR_MODE` when `LOCAL_STORAGE_PERMISSIVE_CHMOD=1`.
- The entrypoint then uses `runuser` to drop back to `appuser` before running Django, Celery, or Uvicorn.
- The chmod is intentionally not recursive; it does not rewrite ownership or modes for existing user media.

To apply the compose fix to existing local containers:

```powershell
docker compose -f infra/docker-compose.yml up -d --force-recreate api worker worker-avatar tts_service
```

If a running local stack needs immediate recovery before services are recreated, this one-time non-recursive command fixes only the top-level shared mount:

```powershell
docker compose -f infra/docker-compose.yml exec --user root api sh -lc "chmod 0777 /app/storage_local"
```

Then verify from each app service:

```powershell
docker compose -f infra/docker-compose.yml exec api sh -lc "test -w /app/storage_local && echo writable"
docker compose -f infra/docker-compose.yml exec worker sh -lc "test -w /app/storage_local && echo writable"
docker compose -f infra/docker-compose.yml exec worker-avatar sh -lc "test -w /app/storage_local && echo writable"
docker compose -f infra/docker-compose.yml exec tts_service sh -lc "test -w /app/storage_local && echo writable"
```

## Env Sources

| Source | Finding |
| --- | --- |
| `infra/.env` | Exists locally and is read by Compose through `env_file: .env`. It contains `STORAGE_ROOT=/app/storage_local`, `POSTGRES_HOST=postgres`, and `TTS_SERVICE_URL=http://tts_service:8001`. |
| `infra/.env.example` | Updated to use `DJANGO_SETTINGS_MODULE=config.settings`, `TTS_GLOSSARY_PATH=/app/tts_preprocess/glossary.json`, `VITE_API_BASE_URL=http://localhost:8000/api/v1`, and placeholder Google OAuth values. |
| Root `.env` | Not found. |
| Compose overrides | API and worker explicitly set `DJANGO_SETTINGS_MODULE=config.settings` and `STORAGE_ROOT=/app/storage_local`, so API/worker are not using the stale Django settings module from `infra/.env`. |

There is no conflicting root env file. The active Docker storage setting is the compose/env combination that resolves to `/app/storage_local` inside containers and repo-root `storage_local` on the host.

## Active Database Source

Docker API is using Postgres, not `services/api/db.sqlite3`.

Evidence:

- `infra/docker-compose.yml` starts `postgres` with named volume `infra_postgres_data`.
- Resolved env has `POSTGRES_HOST=postgres`, `POSTGRES_DB=academy_db`, and `POSTGRES_USER=academy_user`.
- `services/api/config/settings.py` uses Postgres whenever `POSTGRES_HOST` is present and falls back to SQLite only when it is absent.
- Read-only Postgres query succeeded against `academy_db`.

Read-only counts:

```text
projects: 41
jobs: 282
published_projects: 0
draft_projects: 41
```

Recent projects/jobs:

```text
140 | TTS                         | is_published=false | done | 140/140.mp4 | 140/140.srt
139 | Avatar valid source runtime | is_published=false | done | 139/139.mp4 | 139/139.srt
138 | Avatar invalid source runtime | is_published=false | done | 138/138.mp4 | 138/138.srt
```

Conclusion: the UI is not empty because Docker DB has no rows. It has rows, but the public catalog has no publishable rows.

## API Behavior

Initial state:

- `infra-api-1` was exited with `ExitCode=137`.
- `docker inspect` reported `OOMKilled=false`.
- Host requests to `http://localhost:8000/api/v1/catalog/`, `/projects/`, and `/categories/` failed with connection errors while API was stopped.

After starting the existing API container with `docker compose -f infra/docker-compose.yml start api`:

| Endpoint | Result | Interpretation |
| --- | --- | --- |
| `GET /api/v1/catalog/` | `200`, body `[]` | Public catalog query works but returns no lessons because no projects are published. |
| `GET /api/v1/catalog/feed/` | `200`, all sections empty | Feed uses catalog/public lesson inputs and is empty for the same publication reason. |
| `GET /api/v1/categories/` | `200`, category list returned | API and database are reachable. |
| `GET /api/v1/projects/` | `401 Unauthorized` | Expected without auth token; Studio project list requires auth. |

API logs also showed earlier `Not Found` entries for:

```text
/api/v1/catalog/140/
/api/v1/projects/140/playback-token/
/api/v1/catalog/139/
/api/v1/projects/139/playback-token/
/api/v1/media/avatars/.../processed.png
```

Those 404s are consistent with unpublished lessons and missing mounted media files.

## Media Path Verification

DB path examples:

```text
project 140: result_url=140/140.mp4, srt_url=140/140.srt
project 139: result_url=139/139.mp4, srt_url=139/139.srt
```

Inside API container:

```text
STORAGE_ROOT=/app/storage_local
MISSING: /app/storage_local/140/140.mp4
MISSING: /app/storage_local/140/140.srt
MISSING: /app/storage_local/139/139.mp4
MISSING: /app/storage_local/139/139.srt
FOUND:   /app/storage_local/avatar_stage_metrics.json
```

Inside worker container:

```text
STORAGE_ROOT=/app/storage_local
WORKER_MISSING_140_MP4
WORKER_FOUND_METRICS
```

On the host:

```text
FOUND:   storage_local/140/140.mp4
FOUND:   storage_local/140/140.srt
FOUND:   storage_local/139/139.mp4
FOUND:   storage_local/uploads/140/lesson.docx
MISSING: infra/storage_local/140/140.mp4
MISSING: infra/storage_local/140/140.srt
```

Conclusion: before the mount fix, moving generated files from `infra/storage_local` to repo-root `storage_local` made Docker API/worker unable to see the files. After the mount fix, DB-relative paths such as `140/140.mp4` should resolve when the files exist under repo-root `storage_local`.

## Frontend API URL

Current frontend API client reads `import.meta.env.VITE_API_BASE_URL` and falls back to `http://localhost:8000/api/v1`.

Findings:

- `services/frontend/src/api.js` now supports the Vite env variable and trims trailing slashes.
- `infra/.env.example` uses `VITE_API_BASE_URL=http://localhost:8000/api/v1`.
- The frontend container runs Vite on `localhost:3000` and serves source from `services/frontend`.

For a browser running on the host at `http://localhost:3000`, the fallback `http://localhost:8000/api/v1` remains correct as long as the API container is running and publishing port `8000`.

## Worker Build Failure

Worker build failure is likely unrelated to lesson visibility.

Evidence from `infra/dockerfiles/Dockerfile.worker`:

- The heavy worker image installs PyTorch/CUDA packages, OpenMMLab/MMPose, MuseTalk, LivePortrait, and downloads LivePortrait weights.
- The reported failure happened during Python dependency download/install around the MuseTalk/LivePortrait block.
- Existing `infra-worker-1` is already running from a previously built image.

The current lesson visibility problem can be reproduced without rebuilding the worker:

- API was initially stopped.
- Public catalog has zero published projects.
- Docker-mounted storage does not contain the DB-referenced media files.

Recommended later worker build improvement:

- Add a lightweight worker image/target for non-avatar development.
- Make avatar/MuseTalk/LivePortrait dependencies optional or build them in a separate GPU/avatar worker image.
- Keep the existing heavy image for full avatar runtime validation.

## Root Cause Hypothesis

Most likely current causes, in order:

1. Public UI empty: all Docker Postgres projects are drafts (`is_published=false`), so `/api/v1/catalog/` returns `[]`.
2. Direct watch/playback for recent projects previously failed because DB rows pointed to `140/140.mp4`, `139/139.mp4`, etc., while Docker mounted `infra/storage_local`. The mount fix makes repo-root `storage_local` visible to API/worker again.
3. API was stopped at the start of diagnosis, so any frontend request to `localhost:8000` failed until the existing API container was started.

The local Docker mount has now been corrected to use repo-root `storage_local`.

## Safest Recovery Steps

Do not publish projects or bulk-migrate data without explicit approval.

Immediate recovery after the mount and permission fixes:

1. Recreate/start the app containers without rebuilding the heavy worker image:

   ```powershell
   docker compose -f infra/docker-compose.yml up -d --force-recreate api worker worker-avatar tts_service frontend
   ```

2. Confirm root storage is visible and writable inside the API container:

   ```powershell
   Invoke-WebRequest -UseBasicParsing http://localhost:8000/api/v1/catalog/
   docker compose -f infra/docker-compose.yml exec api sh -lc "test -e /app/storage_local/140/140.mp4 && echo ok"
   docker compose -f infra/docker-compose.yml exec api sh -lc "test -w /app/storage_local && echo writable"
   ```

3. If the goal is public catalog visibility, publish selected projects intentionally through a backend/admin action or a small controlled script. Do not bulk-publish everything without reviewing access expectations.

Later planned work:

1. Reconcile any leftover old files in `infra/storage_local` manually.
2. Keep DB rows and storage files synchronized.
3. Then implement the Phase B2 frontend `VITE_API_BASE_URL` cleanup separately.

## Do Not Do Yet

- Do not rebuild the heavy worker image just to fix lesson visibility.
- Do not move runtime storage again without a reviewed migration plan.
- Do not delete `services/api/db.sqlite3` as part of this issue; Docker is using Postgres.
- Do not change in-container `STORAGE_ROOT`; keep `/app/storage_local` stable.
- Do not touch MuseTalk/LivePortrait/avatar dependencies for this diagnosis.
