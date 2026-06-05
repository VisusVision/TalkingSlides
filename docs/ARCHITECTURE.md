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

## Storage Architecture Contract

The current runtime storage implementation is filesystem-backed. API, render worker, TTS service, avatar worker, and playback endpoints read and write relative paths under `STORAGE_ROOT`. Local Compose mounts repo-root `storage_local/` into containers as `/app/storage_local`. MinIO and S3-style env vars exist, but there is no active object-storage adapter yet.

`core.storage_adapter.FilesystemStorageAdapter` is the first adapter boundary. It is filesystem-only and currently used by low-risk storage smoke, metrics snapshot, and report-only retention helpers. It preserves existing relative path formats and does not change public URLs, database fields, sidecar JSON, render outputs, playback serving, avatar generation, or TTS generation.

The runtime adoption map keeps object storage behind the current relative-path contract. Original uploads, render outputs, playback sidecars, HLS assets, translated subtitles, profile images, avatar source/generated assets, moderation frame samples, TTS audio, storage reports, and temp/lock/part files have different migration risks. Anything opened by LibreOffice, PyMuPDF, OpenCV, Pillow, ffmpeg, LivePortrait, MuseTalk, or XTTS must remain locally materialized until that caller has an explicit temp-file or streaming contract. Playback compatibility also needs an adapter-backed range/read contract before S3/MinIO can serve MP4/HLS/subtitle/avatar/profile media.

Safe migration order:

1. Keep the existing low-risk filesystem adapter helpers as Phase A.
2. Move read-only sidecar/report reads behind the adapter as Phase B.
3. Move write paths that can still write to the filesystem adapter as Phase C.
4. Prove playback/media serving compatibility as Phase D.
5. Implement the S3/MinIO adapter as Phase E.
6. Add signed URL or private media delivery as Phase F only if the product/security model needs it.

The S3/MinIO adapter is designed but not implemented. The design keeps current storage-root-relative DB and JSON values as the durable compatibility contract, maps those relative paths to private bucket keys only inside the adapter, requires local materialization for ffmpeg, Pillow, OpenCV, LibreOffice/PyMuPDF, XTTS, LivePortrait, and MuseTalk, and requires adapter-backed range/proxy or signed-URL tests before MP4/HLS/avatar/profile delivery can move to object storage.

For live multi-user production, the target architecture is S3-compatible object storage such as managed cloud S3 or production-grade MinIO. A durable shared filesystem may be used only as a temporary bridge when it is externally backed up, mounted consistently by every service, monitored, capacity-alerted, and restore-tested in staging.

Storage is classified by durability and deletion risk:

- Critical: original uploads, current published render outputs, playback sidecars, HLS assets, profile/avatar source media, voice reference audio, and current avatar outputs.
- Conditional/regenerable: generated renders, translated subtitles, HLS sidecars, TTS audio, moderation samples, and avatar generated outputs. These still need backups when they are user-visible or expensive/impossible to reproduce exactly.
- Temporary: smoke probes, old temp/lock/part files, scratch directories, and optional caches.
- Operational evidence: storage metrics snapshots and render recovery audit logs.

Database and media storage must be backed up and restored together. Project deletion currently removes database state but does not guarantee comprehensive media cleanup. Runtime media migration to the adapter, quotas, retention execution, destructive cleanup, orphan reconciliation, and MinIO/S3 delivery remain future implementation work and must not be described as production-ready until implemented.

See [STORAGE_PRODUCTION_READINESS.md](STORAGE_PRODUCTION_READINESS.md) for the full backup/restore, quota, retention, deletion, runtime adoption map, S3/MinIO adapter design RFC, media delivery contract, rollback plan, test matrix, and implementation roadmap.

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

Production observability is split between scrapeable Prometheus text and operator-run reports. The Prometheus endpoint at `/api/v1/system/metrics/prometheus/` exposes cache-backed worker failure, retry, duration, queue wait, enqueue latency, and read-only system observability gauges. The endpoint is token-protected when `PROMETHEUS_METRICS_TOKEN` is configured, and local Prometheus/Grafana config lives under `infra/`.

The read-only `python manage.py system_observability_report` command provides a point-in-time health snapshot without changing application behavior. It reads `Job`, `RenderFollowUpIntent`, cached storage metrics, and render recovery findings, then reports render counts, follow-up intent counts, storage size and reclaim estimates, recovery candidate counts, and environment warnings. Each section degrades independently if the database, storage root, cached snapshot, or optional reporting helper is unavailable.

The scrapeable system observability gauges reuse the same read-only render, follow-up intent, and recovery inspection paths. Storage retention/orphan/capacity scans are intentionally not run on every Prometheus scrape because they can walk the storage tree. Operators refresh storage metrics intentionally with `python manage.py storage_metrics_snapshot`, which writes `STORAGE_ROOT/observability/storage_metrics_snapshot.json`. Prometheus and Grafana read only that cached JSON file for storage totals, retention candidates, orphan candidates, reclaimable byte estimates, snapshot availability, generated timestamp, and snapshot age. If the snapshot is missing or invalid, scrapes keep zero-valued storage gauges present and set `system_observability_storage_available 0` and `system_observability_storage_snapshot_available 0` without traceback.

For Kubernetes staging, `infra/k8s/storage-metrics-snapshot-cronjob.yaml` provides a suspended-by-default operator workflow for refreshing that cache every 6 hours after the staging API image, environment ConfigMap, and durable storage PVC are bound by the deployment owner. The CronJob runs the API image with `python manage.py storage_metrics_snapshot --older-than-days 30 --json`, forbids overlapping runs, and mounts the same `STORAGE_ROOT` volume used by API and workers. It is a staging example only; production enablement requires a separate rollout review and is not automatic.

The provisioned Grafana dashboard in `infra/grafana/dashboards/vidlab-render-ops.json` now includes panels for the scrape-safe render, follow-up intent, recovery, cached storage snapshot, and section availability gauges. Prometheus alert rules in `infra/prometheus-alert-rules.yml` include initial staging-tuned warning candidates for sustained recovery candidates, stale follow-up intents, failed render count growth, oldest active render age, missing storage snapshots, and stale storage snapshots. They are deliberately investigation signals only; no alert delivery, remediation, retry, cleanup, or render execution behavior is coupled to these rules.

Both the command and scrape path intentionally do not mutate database rows, enqueue Celery tasks, inspect live broker task ownership, delete storage files, retry renders, or change playback behavior. They are operator visibility layers for dashboards, alert design, and incident triage.

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
