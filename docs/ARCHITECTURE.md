# Architecture

VISUS VidLab is a multi-service lesson generation system. Source files become narrated video lessons, playback assets, optional translated subtitles, and optional avatar overlays.

## Repository Layout

| Path | Role |
| --- | --- |
| `services/api` | Django REST API, settings, models, serializers, views, auth, catalog, playback tokens, moderation APIs |
| `services/worker` | Celery app and background task pipeline for extraction, TTS orchestration, rendering, subtitles, and avatar jobs |
| `services/tts_service` | FastAPI TTS service for XTTS/fallback synthesis and text normalization |
| `services/frontend` | Vite React app for Studio, Watch, Catalog, Settings, Library, and Analytics |
| `services/scripts` | Shared extraction, FFmpeg, TTS client, subtitle, and runner helpers |
| `services/avatar` | Avatar preprocessing, validation, and adapter code |
| `infra` | Docker Compose, Dockerfiles, env template, and local infrastructure helpers |
| `storage_local` | Local runtime storage for uploads, renders, subtitles, avatar files, and cache data |
| `tests/integration` | Integration and regression tests for API, worker, TTS, playback, moderation, and avatar flows |

## Service Responsibilities

### API

The Django API owns users, roles, projects, jobs, transcripts, catalog visibility, playback tokens, media streaming, avatar state, subtitle track metadata, and moderation/admin endpoints.

### Worker

The Celery worker performs long-running jobs. The render worker handles source extraction, transcript sync, TTS orchestration, video rendering, HLS/sidecar generation, and job status updates. The avatar worker handles GPU-heavy avatar overlay generation.

### TTS Service

The TTS service normalizes text and synthesizes narration. XTTS is the preferred voice path when configured; local fallback behavior keeps development and tests moving when full TTS is unavailable.

### Frontend

The React app presents the teacher Studio, student Watch experience, public Catalog, Settings, Library, and Analytics screens. It reads `VITE_API_BASE_URL` to locate the API.

## Render Pipeline

```text
upload
  -> project/job row
  -> source extraction
  -> transcript pages and TTS chunks
  -> TTS synthesis
  -> slide/page video render
  -> MP4 concat
  -> subtitles and playback_assets.json
  -> optional HLS/package sidecars
  -> playback token and Watch player
```

Important properties:

- The API should not perform heavy render work inline.
- Render jobs are tracked through `Job` rows.
- Runtime media is stored under `STORAGE_ROOT`.
- Final public catalog visibility depends on publish state, moderation state, project readiness, and completed video export jobs.

### Render Recovery And Reconciliation

Render recovery starts with report-only reconciliation. The API and worker continue to own normal render transitions, while `python manage.py render_recovery_check --dry-run` inspects durable state for operator-visible recovery candidates.

Tracked lifecycle:

```text
Job(video_export)
  pending -> running -> done
                      -> failed

RenderFollowUpIntent
  pending -> claimed -> cleared
                    -> cancelled
```

Follow-up intents are created when transcript changes arrive while a render is already active. After the active render completes, the worker claims a pending intent, reserves a new `video_export` job, dispatches it after transaction commit, then clears the intent once dispatch succeeds. Dispatch failures cancel the intent and mark the reserved job failed.

The reconciliation command detects stale active jobs, stale active follow-up intents, missing task IDs, and intents whose metadata references a render job that is no longer active or no longer exists. It does not mutate data, enqueue work, or delete artifacts.

Manual recovery actions are implemented as an operator-only audit layer through `python manage.py render_recovery_action`. The command supports `inspect`, `resolve`, and `ignore` for explicit `job` or `intent` IDs. `inspect` prints model state and recommendations. `resolve` and `ignore` are annotation-only audit events; they do not update `Job` rows, update `RenderFollowUpIntent` rows, dispatch Celery tasks, retry renders, or clear state-machine transitions.

Dry-run is the default action mode. Executed annotations require `--confirm`; unconfirmed resolve/ignore requests print a non-executed result and do not write audit records. Inspect and confirmed annotations append operator-visible JSONL audit records. The audit file is intentionally outside the database so this layer adds no tables, fields, migrations, scheduled tasks, or background repair loops.

### Observability Foundation

Production observability is split between scrapeable Prometheus text and operator-run reports. The Prometheus endpoint at `/api/v1/system/metrics/prometheus/` exposes cache-backed worker failure, retry, duration, queue wait, and enqueue latency metrics. The endpoint is token-protected when `PROMETHEUS_METRICS_TOKEN` is configured, and local Prometheus/Grafana config lives under `infra/`.

The read-only `python manage.py system_observability_report` command provides a point-in-time health snapshot without changing application behavior. It reads `Job`, `RenderFollowUpIntent`, storage retention data, and render recovery findings, then reports render counts, follow-up intent counts, storage size and reclaim estimates, recovery candidate counts, and environment warnings. Each section degrades independently if the database, storage root, or optional reporting helper is unavailable.

The command intentionally does not mutate database rows, enqueue Celery tasks, inspect live broker task ownership, delete storage files, retry renders, or change playback behavior. It is an operator visibility layer for dashboards, alert design, and incident triage.

## Avatar Pipeline

```text
base video ready first
  -> avatar handoff manifest
  -> avatar queue
  -> LivePortrait motion
  -> MuseTalk lip sync
  -> optional restoration
  -> avatar track
  -> Watch overlay payload
```

Avatar rendering is intentionally non-blocking. A lesson can publish and play with the base video while avatar processing is queued, processing, or failed. The Watch payload disables `avatar_overlay.enabled` until an avatar artifact is ready and active.

See [AVATAR_PIPELINE.md](AVATAR_PIPELINE.md).

## Secure Playback Token Flow

```text
Watch page
  -> catalog/detail or playback-token request
  -> API chooses playback mode
  -> API signs short-lived media tokens
  -> frontend receives tokenized URLs and policy
  -> stream endpoint validates token and serves media
  -> optional heartbeat/session lock enforcement
```

Playback modes:

- `public`: basic tokenized MP4/HLS behavior for local/simple deployment.
- `secure_stream`: tokenized HLS/MP4 fallback with watermark and session-lock policy.
- `drm_protected`: contract for vendor-backed DRM playback.

See [SECURE_PLAYBACK_DRM.md](SECURE_PLAYBACK_DRM.md).

## Operational Docs

- Production deployment: [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)
- Environment variables: [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md)
- Moderation: [MODERATION_OPERATIONS.md](MODERATION_OPERATIONS.md)
- Metrics and retries: [PROMETHEUS_AND_RETRY_RUNBOOK.md](PROMETHEUS_AND_RETRY_RUNBOOK.md)
