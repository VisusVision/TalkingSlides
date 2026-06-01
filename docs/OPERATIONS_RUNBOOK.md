# Operations Runbook

This is a short operating guide for staging and production. It complements [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md), [DEPLOYMENT_PROFILES.md](DEPLOYMENT_PROFILES.md), and [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

## Health Endpoints

- API health: `/health/`
- API readiness: `/api/v1/ready/`
- TTS readiness: `/ready` on the TTS service
- Prometheus metrics, if configured: `/api/v1/system/metrics/prometheus/`

The API readiness endpoint is lightweight and does not check Redis, Postgres, GPU, or TTS. Use deeper smoke checks for dependencies.

## Logs

Docker Compose local examples:

```powershell
docker compose -f infra\docker-compose.yml logs -f api
docker compose -f infra\docker-compose.yml logs -f worker
docker compose -f infra\docker-compose.yml logs -f worker-avatar
docker compose -f infra\docker-compose.yml logs -f tts_service
docker compose -f infra\docker-compose.yml logs -f redis
docker compose -f infra\docker-compose.yml logs -f postgres
```

In hosted environments, use the platform log viewer and filter by service name, request ID, project ID, job ID, or Celery task ID.

## Restart Services

Restart one service at a time where possible:

```powershell
docker compose -f infra\docker-compose.yml restart api
docker compose -f infra\docker-compose.yml restart worker
docker compose -f infra\docker-compose.yml restart tts_service
```

For production, prefer rolling restarts and worker drain/replace procedures from the hosting platform.

## Celery Queues

Current queue split:

- `render`: base lesson render, extraction, TTS orchestration, subtitles, non-avatar work
- `avatar`: GPU avatar jobs
- legacy `celery`: retained for compatibility/manual checks

Operational checks:

- Is Redis reachable?
- Are workers consuming the expected queue?
- Is render queue depth growing?
- Is avatar queue depth growing while no GPU worker is available?
- Are failed jobs isolated to one task type or system-wide?

Keep avatar worker concurrency at `1` per GPU until benchmarked.

## Observability Report

Use the read-only observability report for a single operator snapshot of render, follow-up intent, storage, and recovery health:

```powershell
cd services\api
python manage.py system_observability_report --pretty
python manage.py system_observability_report --json
python manage.py system_observability_report --storage-root C:\path\to\storage --older-than-days 30
```

Refresh cached storage metrics before expecting Prometheus or Grafana to show current storage values:

```powershell
python manage.py storage_metrics_snapshot --older-than-days 30
python manage.py storage_metrics_snapshot --storage-root C:\path\to\storage --older-than-days 30 --json
```

The snapshot command is the intentional expensive path. It walks storage through the existing retention/orphan/capacity report helper, then writes `STORAGE_ROOT/observability/storage_metrics_snapshot.json`. It does not delete files, enqueue work, perform cleanup, or change render/playback behavior.

Metric inventory:

- Render: `active_render_count`, `pending_render_count`, `running_render_count`, `failed_render_count`, `oldest_active_render_age_seconds`.
- Follow-up intents: `pending_intent_count`, `claimed_intent_count`, `dispatched_intent_count`, `oldest_intent_age_seconds`.
- Storage: `total_storage_size_bytes`, `orphan_candidate_count`, `retention_candidate_count`, `reclaimable_bytes_estimate`, `snapshot_generated_at`.
- Recovery: `recovery_candidate_count`, `stale_render_count`, `stale_intent_count`.

Prometheus exposes the scrape-safe subset under stable gauge names:

- Render: `system_observability_render_active_count`, `system_observability_render_pending_count`, `system_observability_render_running_count`, `system_observability_render_failed_count`, `system_observability_render_oldest_active_age_seconds`.
- Follow-up intents: `system_observability_followup_pending_count`, `system_observability_followup_claimed_count`, `system_observability_followup_dispatched_count`, `system_observability_followup_oldest_age_seconds`.
- Storage snapshot: `system_observability_storage_total_bytes`, `system_observability_storage_retention_candidate_count`, `system_observability_storage_orphan_candidate_count`, `system_observability_storage_reclaimable_bytes_estimate`.
- Recovery: `system_observability_recovery_candidate_count`, `system_observability_recovery_stale_render_count`, `system_observability_recovery_stale_intent_count`.
- Degradation gauges: `system_observability_render_available`, `system_observability_followup_available`, `system_observability_storage_available`, `system_observability_storage_snapshot_available`, `system_observability_storage_scan_skipped`, `system_observability_recovery_available`.

Scrape behavior:

- Render, follow-up intent, and recovery metrics are collected read-only from the database/report helpers on each scrape.
- Each section degrades independently. If a section cannot be inspected, its availability gauge becomes `0` and its numeric gauges remain present with value `0`.
- Storage scans are not run on every scrape because retention/orphan/capacity reporting can traverse `STORAGE_ROOT`. Scrapes read only `STORAGE_ROOT/observability/storage_metrics_snapshot.json`; when it is missing or corrupt, `system_observability_storage_available` becomes `0` and storage value gauges remain present with value `0`.

Alert candidates to tune per deployment:

- `VidlabSystemRecoveryCandidatesPresent`: `system_observability_recovery_candidate_count > 0` for 30 minutes.
- `VidlabSystemRecoveryCandidateSpike`: recovery candidate count increases by more than 5 within 15 minutes.
- `VidlabSystemStaleFollowupIntent`: `system_observability_followup_oldest_age_seconds > 1800` for 30 minutes.
- `VidlabSystemFailedRenderCountHigh`: `system_observability_render_failed_count > 10` for 10 minutes.
- `VidlabSystemFailedRenderCountSpike`: failed render count increases by more than 5 within 15 minutes.
- `VidlabSystemOldestActiveRenderTooHigh`: `system_observability_render_oldest_active_age_seconds > 7200` for 15 minutes.
- `VidlabSystemStaleRenderCandidatesPresent`: `system_observability_recovery_stale_render_count > 0` for 30 minutes.
- `VidlabSystemStorageSnapshotUnavailable`: `system_observability_storage_available == 0` for 30 minutes.
- Cached `reclaimable_bytes_estimate` exceeds the reviewed cleanup threshold.
- Any availability gauge is `0` for longer than one scrape interval.

These rule thresholds are initial staging candidates. Tune them against real source sizes, GPU/CPU class, worker concurrency, expected render duration, and normal failed-job cleanup cadence before treating them as production paging alerts. The current severity label is `warning`, matching the existing alert convention without adding alert delivery integration.

Grafana dashboard:

- `infra/grafana/dashboards/vidlab-render-ops.json` is provisioned by `infra/grafana/provisioning/dashboards/dashboards.yml`.
- The dashboard keeps the existing `vidlab_*` render queue and latency panels and adds panels for system render counts, oldest active render age, follow-up intent counts and age, recovery candidates, cached storage snapshot gauges, and section availability.

The report is intentionally read-only. It does not inspect live Celery task state, retry work, fail jobs, clear intents, remove storage, or perform automatic remediation. Treat warnings and candidates as investigation leads.

## Failed Jobs and Retries

When a job fails:

1. Identify project ID and job ID.
2. Check API logs for request/enqueue errors.
3. Check worker logs for task failure.
4. Check TTS logs if failure occurred during synthesis.
5. Check storage path availability if files are missing.
6. Retry only after the cause is understood.

If retry endpoints are enabled, use idempotency/request IDs. See [PROMETHEUS_AND_RETRY_RUNBOOK.md](PROMETHEUS_AND_RETRY_RUNBOOK.md).

## Render Recovery Reconciliation

Use the report-only render reconciliation command when render jobs appear stuck, a worker has crashed or restarted, Redis/Celery dispatch was interrupted, or transcript edits were saved while a render was active.

```powershell
cd services\api
python manage.py render_recovery_check --dry-run
python manage.py render_recovery_check --dry-run --max-age-hours 6
python manage.py render_recovery_check --dry-run --json
```

The command is intentionally non-mutating. It never enqueues Celery tasks, updates job rows, clears follow-up intents, or deletes files. The `--dry-run` flag is required as an operator safety acknowledgement.

Operator workflow:

1. Run `render_recovery_check --dry-run` and capture the summary counts.
2. For `stuck_render_job`, inspect the `Job` row, project ID, Celery task ID, API enqueue logs, worker logs, and generated files under `STORAGE_ROOT`.
3. For `stuck_followup_intent`, inspect `RenderFollowUpIntent.metadata.active_job_id`, `metadata.dispatched_job_id`, `metadata.celery_task_id`, and the active/completed render history for that project.
4. For `orphan_recovery_candidate`, verify whether the referenced Celery task exists before manually failing, cancelling, or recreating work.
5. Only after confirming no live worker still owns the render should an operator manually update database records or request a new render.

Common recovery windows surfaced by the report:

- A `video_export` job was committed, but Celery dispatch failed before `celery_task_id` was saved.
- A worker marked a job `running` and then crashed before finalization.
- A follow-up intent remained `pending` because the base render never reached a clean terminal path.
- A follow-up intent was `claimed`, but process death occurred between database commit and the post-commit task dispatch.

Limitations:

- The command does not ask Celery for live task state; treat findings as recovery candidates that still require log and broker inspection.
- The command does not decide whether to retry or fail a job. It only reports category, object ID, age, and recommended investigation action.
- The age threshold is operator-controlled. A long legitimate render can appear in the report if `--max-age-hours` is too low for the source file or hardware.

## Manual Render Recovery Actions

Use `render_recovery_action` only after reviewing reconciliation findings and confirming the object ID. The command is operator-driven and requires an explicit `--action`, `--type`, and `--id`.

```powershell
cd services\api
python manage.py render_recovery_action --action inspect --type job --id 123
python manage.py render_recovery_action --action inspect --type intent --id 456 --json
python manage.py render_recovery_action --action resolve --type job --id 123 --confirm
python manage.py render_recovery_action --action ignore --type intent --id 456 --confirm
```

Supported actions:

- `inspect`: prints full object state and the current recovery recommendation, if one exists.
- `resolve`: records that an operator considers the current recovery candidate resolved. This is an annotation-only audit event.
- `ignore`: records that an operator intentionally ignored the current recovery candidate. This is an annotation-only audit event.

Safety rules:

- Dry-run is the default. Without `--confirm`, resolve and ignore print a non-executed result and do not write an audit record.
- `--confirm` is required for an executed resolve or ignore annotation.
- The command does not enqueue Celery tasks, requeue work, delete files, update `Job.status`, update `RenderFollowUpIntent.status`, or call render state-machine helpers.
- Resolve and ignore require the object to still be a current recovery candidate under the selected `--max-age-hours` threshold.

Audit trail:

- `inspect` and confirmed resolve/ignore annotations write JSON records to the render recovery action audit log. By default this is `STORAGE_ROOT/audit/render_recovery_actions.jsonl`; deployments may set `RENDER_RECOVERY_AUDIT_LOG_PATH`.
- The same audit payload is included in command output and emitted through structured application logging.
- Audit records are append-only operational evidence, not product state. If durable in-product annotations are needed later, design that with a migration in a separate PR.

## Redis Checks

Check:

- Redis service availability
- broker URL consistency
- memory pressure
- evictions
- connection errors in API/worker logs

Emergency local check:

```powershell
docker compose -f infra\docker-compose.yml exec redis redis-cli ping
```

## Postgres Checks

Check:

- connection count
- disk usage
- latest backup
- migration status
- slow queries

Emergency local check:

```powershell
docker compose -f infra\docker-compose.yml exec postgres pg_isready
```

## Storage Checks

Check:

- free disk/object storage capacity
- API, worker, TTS, and avatar worker all see the same `STORAGE_ROOT`
- generated media path exists
- permissions on mounted volumes
- backup/retention jobs

Do not manually delete active project folders while jobs are running.

Run the filesystem smoke check when validating a deployment or diagnosing missing media:

```powershell
cd services\api
python manage.py storage_smoke_check
```

The check writes, reads, and deletes a probe file under `STORAGE_ROOT/.storage-smoke/`.

For a report-only capacity, retention, and orphan media review:

```powershell
cd services\api
python manage.py storage_retention_check --dry-run --older-than-days 30
```

Use `--json` when the report needs to be archived or parsed:

```powershell
python manage.py storage_retention_check --dry-run --older-than-days 30 --json
```

The command does not delete files. Treat orphan candidates as investigation leads. Confirm database state and backups before any manual cleanup.

For scrapeable storage metrics, refresh the cached snapshot on an operator-approved cadence:

```powershell
python manage.py storage_metrics_snapshot --older-than-days 30
```

Prometheus never runs the storage walk. It reads the snapshot file only and emits zero-valued storage gauges if the file is missing or invalid.

## Common Emergency Actions

- Pause or scale down workers if jobs are producing bad output.
- Keep API serving existing playback if render workers are unhealthy.
- Disable avatar worker first if GPU jobs are causing resource pressure.
- Switch new playback away from DRM mode if DRM provider is down and product policy allows it.
- Stop public subtitle generation if provider cost or abuse spikes.
- Restore from backup only after confirming rollback target and data-loss window.

## What Not To Do In Production

- Do not set `DEBUG=True`.
- Do not use SQLite fallback.
- Do not set `CORS_ALLOW_ALL_ORIGINS=True`.
- Do not use wildcard `ALLOWED_HOSTS` unless a reviewed edge setup explicitly requires it.
- Do not commit real `.env` files or secrets.
- Do not put DRM keys or provider secrets in the frontend.
- Do not run avatar/GPU work in the API process.
- Do not remove Docker volumes or storage directories without a backup and explicit incident approval.
- Do not increase avatar concurrency without GPU validation.

## CI Failure Triage Quick Path

When CI fails, check artifacts before re-running blindly:

1. Open the failed GitHub Actions run.
2. Download `backend-pytest-junit` and inspect `pytest-report.xml` for the first failing test and error type.
3. Download `frontend-playwright-report` if present and open the Playwright HTML report for failing spec, trace, and screenshot context.
4. If a job ended due to timeout, treat it as reliability/load contention first, not a feature regression by default.
5. Re-run only the failed job once after triage. If the same failure repeats, escalate as deterministic failure.

Concurrency note:

- CI uses branch-scoped concurrency with in-progress cancellation enabled. Older runs on the same branch are expected to stop once a newer commit is pushed.
