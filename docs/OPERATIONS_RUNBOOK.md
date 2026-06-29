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

The report also includes a `storage_backend` readiness section. Treat it as configuration visibility only:

- `configured_storage_backend` is the raw configured value. `effective_storage_backend` is the canonical value the app will treat as active.
- `STORAGE_BACKEND=local` is a legacy alias. It should appear as `configured_storage_backend: local`, `effective_storage_backend: filesystem`, and `legacy_local_alias_normalized: true`. New environments should use `filesystem`.
- For filesystem mode, `filesystem_root`, `filesystem_root_resolved`, `filesystem_root_status`, `filesystem_root_exists`, `filesystem_root_is_dir`, `filesystem_root_readable`, and `filesystem_root_writable` describe whether the configured storage root is locally usable by the reporting process.
- For S3 mode, the `s3_*_configured` fields report whether required and optional settings are present. They do not prove bucket existence, credentials, permissions, latency, or network reachability.
- `s3_network_probe_performed: false` means the report intentionally did not call S3 or MinIO. Use the optional S3/MinIO integration test or a reviewed staging smoke workflow for connectivity proof.
- `storage.available` and `storage_backend.available` can differ. `storage` reads the cached storage metrics snapshot and may degrade when the snapshot, adapter dependency, or configured storage target is unavailable. `storage_backend` only reports backend readiness metadata.
- `runtime_media_migration_implied: false` means upload, render, playback, avatar, and TTS media paths have not been moved by this report.
- `excluded_capabilities` intentionally lists work not covered by readiness visibility: `s3_listing`, `range_reads`, `signed_urls`, `public_urls`, `cleanup`, `quota`, and `delete`.

Filesystem remains the default runtime backend. The S3 adapter foundation does not mean upload, render, playback, avatar, or TTS runtime media has migrated to S3.

Recommended cadence:

- Start with an operator-approved refresh every 6 hours in staging and production.
- Keep the stale alert threshold at 24 hours until staging data proves a tighter threshold is useful.
- Run a manual refresh after storage migrations, mount changes, large imports, or incident triage when current storage values matter.
- Do not run this command from Prometheus scrapes, request handlers, render workers, or playback paths.

Cron example for a VM-style deployment:

```cron
17 */6 * * * cd /srv/visusvision/services/api && /usr/bin/python manage.py storage_metrics_snapshot --older-than-days 30 >> /var/log/visus-storage-metrics-snapshot.log 2>&1
```

Systemd service example:

```ini
[Unit]
Description=Refresh VisusVision storage metrics snapshot

[Service]
Type=oneshot
WorkingDirectory=/srv/visusvision/services/api
Environment=DJANGO_SETTINGS_MODULE=config.settings
ExecStart=/usr/bin/python manage.py storage_metrics_snapshot --older-than-days 30
```

Systemd timer example:

```ini
[Unit]
Description=Run VisusVision storage metrics snapshot refresh every 6 hours

[Timer]
OnCalendar=*-*-* 00,06,12,18:17:00
Persistent=true

[Install]
WantedBy=timers.target
```

Kubernetes staging CronJob workflow:

- Use `infra/k8s/storage-metrics-snapshot-cronjob.yaml` as the staging/operator-owned starting point.
- Keep `spec.suspend: true` until the staging API image, `api-env` ConfigMap, and storage PVC claim are set to live staging values.
- The storage PVC must be the same durable `STORAGE_ROOT` volume mounted by the API and workers. Do not point the job at a scratch or empty volume.
- The manifest runs `python manage.py storage_metrics_snapshot --older-than-days 30 --json`, uses `concurrencyPolicy: Forbid`, and keeps short job history limits.
- This workflow is not production enablement. Production needs a separate review of schedule, image tags, secrets, storage claim, alert thresholds, and owner escalation.

Apply suspended, then run one manual verification job:

```powershell
kubectl apply -f infra/k8s/storage-metrics-snapshot-cronjob.yaml
kubectl -n vidlab create job storage-metrics-snapshot-manual-<timestamp> --from=cronjob/storage-metrics-snapshot
kubectl -n vidlab logs job/storage-metrics-snapshot-manual-<timestamp>
kubectl -n vidlab patch cronjob storage-metrics-snapshot -p '{"spec":{"suspend":false}}'
```

Before unsuspending, verify the job logs include JSON with `snapshot_path`, and verify `STORAGE_ROOT/observability/storage_metrics_snapshot.json` exists on the shared storage mount.

Metric inventory:

- Render: `active_render_count`, `pending_render_count`, `running_render_count`, `failed_render_count`, `oldest_active_render_age_seconds`.
- Follow-up intents: `pending_intent_count`, `claimed_intent_count`, `dispatched_intent_count`, `oldest_intent_age_seconds`.
- Storage: `total_storage_size_bytes`, `orphan_candidate_count`, `retention_candidate_count`, `reclaimable_bytes_estimate`, `snapshot_generated_at`.
- Recovery: `recovery_candidate_count`, `stale_render_count`, `stale_intent_count`.

Prometheus exposes the scrape-safe subset under stable gauge names:

- Render: `system_observability_render_active_count`, `system_observability_render_pending_count`, `system_observability_render_running_count`, `system_observability_render_failed_count`, `system_observability_render_oldest_active_age_seconds`.
- Follow-up intents: `system_observability_followup_pending_count`, `system_observability_followup_claimed_count`, `system_observability_followup_dispatched_count`, `system_observability_followup_oldest_age_seconds`.
- Storage snapshot: `system_observability_storage_total_bytes`, `system_observability_storage_retention_candidate_count`, `system_observability_storage_orphan_candidate_count`, `system_observability_storage_reclaimable_bytes_estimate`, `system_observability_storage_snapshot_age_seconds`, `system_observability_storage_snapshot_generated_timestamp`.
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
- `VidlabSystemStorageSnapshotUnavailable`: `system_observability_storage_snapshot_available == 0` for 30 minutes.
- `VidlabSystemStorageSnapshotStale`: `system_observability_storage_snapshot_age_seconds > 86400` while the snapshot is available for 1 hour.
- Cached `reclaimable_bytes_estimate` exceeds the reviewed cleanup threshold.
- Any availability gauge is `0` for longer than one scrape interval.

These rule thresholds are initial staging candidates. Tune them against real source sizes, GPU/CPU class, worker concurrency, expected render duration, and normal failed-job cleanup cadence before treating them as production paging alerts. The current severity label is `warning`, matching the existing alert convention without adding alert delivery integration.

Storage snapshot alert response:

1. Confirm whether the CronJob is suspended or failing:

```powershell
kubectl -n vidlab get cronjob storage-metrics-snapshot
kubectl -n vidlab get jobs -l app=storage-metrics-snapshot
kubectl -n vidlab logs job/<latest-storage-metrics-snapshot-job>
```

2. If `VidlabSystemStorageSnapshotUnavailable` fires, inspect `STORAGE_ROOT/observability/storage_metrics_snapshot.json` from an API pod or the shared storage mount. Missing or invalid JSON usually means the job never wrote a valid snapshot or wrote to the wrong volume.
3. If `VidlabSystemStorageSnapshotStale` fires, run a one-off job from the CronJob, then verify `system_observability_storage_snapshot_available 1` and a low `system_observability_storage_snapshot_age_seconds`.
4. Treat reclaimable bytes and candidate counts as investigation signals only. Do not delete storage, trigger cleanup automation, enforce quotas, retry renders, or change playback from this alert.

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

### Render Recovery Report Contract

The render recovery report is an operator visibility contract for stale or disconnected render state. Its purpose is to show investigation candidates and the manual inspection command an operator can run next. It is not a recovery executor.

`render_recovery_check` is report-only and requires `--dry-run`. `--json` changes only the output format; it does not enable apply behavior. The command reads durable `Job` and `RenderFollowUpIntent` state and emits a point-in-time report with:

- `summary`: aggregate counts and oldest stuck age.
- `warnings`: inspection warnings when a read path degrades.
- `findings`: one entry per detected recovery candidate.
- `object_summaries`: findings grouped by affected object.
- `manual_command_summary`: findings grouped by suggested manual command.

Each `findings` entry includes object identity, age, detail, a recommended investigation action, and remediation-plan fields. Current remediation-plan fields are:

- `candidate_action`
- `action_mode`
- `risk_level`
- `requires_operator_checks`
- `mutation_if_applied`
- `dedupe_impact`
- `suggested_manual_command`
- `apply_eligible`
- `apply_blockers`
- `finding_priority`
- `precondition_token`
- `metadata_hash`
- `proposed_conditional_update`
- `required_confirm_token`

The `remediation_plan` object appears as a promoted top-level field in `render_recovery_action --json` output for the selected current candidate. It uses the same field names as the remediation-plan fields embedded in a reconciliation finding. In the current contract, `action_mode` is `report_only` and `apply_eligible` is `false`.

`object_summaries` is derived entirely from `findings`. Each summary groups findings for one affected object and includes:

- `object_type`
- `object_id`
- `finding_count`
- `primary_finding`
- `highest_risk_level`
- `candidate_actions`
- `apply_eligible`
- `apply_blockers`
- `precondition_tokens`

`manual_command_summary` is also derived entirely from `findings`. Each summary groups findings by `suggested_manual_command` and includes:

- `command`
- `object_count`
- `candidate_actions`
- `highest_risk_level`
- `requires_operator_checks`
- `apply_eligible`
- `apply_blockers`

Example `findings` snippet:

```json
[
  {
    "category": "orphan_recovery_candidate",
    "object_type": "Job",
    "object_id": 123,
    "project_id": 45,
    "age_seconds": 14400,
    "age_hours": 4.0,
    "recommended_action": "Inspect job dispatch evidence before planning any manual state change.",
    "detail": "pending_without_task_id dispatch_window_candidate",
    "candidate_action": "inspect_pending_video_export_without_task_id",
    "action_mode": "report_only",
    "risk_level": "high",
    "requires_operator_checks": [
      "confirm celery_task_id is still blank on the Job row"
    ],
    "mutation_if_applied": "No mutation is performed by this report.",
    "dedupe_impact": "would_unblock_render_dedupe_if_failed_or_cancelled",
    "suggested_manual_command": "python manage.py render_recovery_action --action inspect --type job --id 123",
    "apply_eligible": false,
    "apply_blockers": [
      "apply mode is intentionally not implemented; this report is evidence-only"
    ],
    "finding_priority": "01_pending_without_task_id_dispatch_window",
    "precondition_token": "<sha256>",
    "metadata_hash": null,
    "proposed_conditional_update": "SELECT ... WHERE id = 123 ...",
    "required_confirm_token": "future-apply:job:123:inspect_pending_video_export_without_task_id:<sha256>"
  }
]
```

Example `object_summaries` snippet:

```json
[
  {
    "object_type": "Job",
    "object_id": 123,
    "finding_count": 2,
    "primary_finding": "inspect_pending_video_export_without_task_id",
    "highest_risk_level": "high",
    "candidate_actions": [
      "inspect_pending_video_export_without_task_id",
      "inspect_stale_active_video_export"
    ],
    "apply_eligible": false,
    "apply_blockers": [
      "apply mode is intentionally not implemented; this report is evidence-only"
    ],
    "precondition_tokens": [
      "<sha256-a>",
      "<sha256-b>"
    ]
  }
]
```

Example `manual_command_summary` snippet:

```json
[
  {
    "command": "python manage.py render_recovery_action --action inspect --type job --id 123",
    "object_count": 1,
    "candidate_actions": [
      "inspect_pending_video_export_without_task_id",
      "inspect_stale_active_video_export"
    ],
    "highest_risk_level": "high",
    "requires_operator_checks": true,
    "apply_eligible": false,
    "apply_blockers": [
      "apply mode is intentionally not implemented; this report is evidence-only"
    ]
  }
]
```

Compatibility expectations:

- Existing documented fields should remain stable for report consumers.
- Additive fields may be added at the top level, inside findings, or inside derived summaries.
- Consumers should ignore unknown top-level fields and unknown nested fields.
- Consumers should not treat `suggested_manual_command`, `proposed_conditional_update`, or `required_confirm_token` as permission to mutate state.

Current safety boundaries:

- No apply mode.
- No retry.
- No requeue.
- No state mutation.
- No terminalization.
- No intent cancellation.
- No artifact deletion.

### Future Apply Mode Guardrails

This section is design-only. It does not authorize an apply mode, mutation behavior, retries, requeues, terminalization, intent cancellation, artifact deletion, or automatic recovery. Any future state-changing Render Recovery work must be reviewed and implemented in a separate PR with tests and an explicit rollout plan.

Minimum gates for any future apply mode:

- Explicit operator confirmation for the exact object, action, and expected state.
- `required_confirm_token` matching the current report output.
- `precondition_token` matching the current report output.
- `metadata_hash` validation for intent metadata and current-state validation for every target object.
- Transaction boundaries around every read-check-write sequence.
- Idempotency strategy for repeated operator submissions, command retries, and partially observed results.
- Audit log or event emission before and after any accepted mutation attempt.
- Dry-run parity that prints the same target, precondition, and planned mutation evidence without writing state.
- Rollback or compensating-action plan for every allowed mutation.
- Per-action allowlist; unknown actions must remain rejected.
- Blocked-by-default behavior unless every gate passes.
- No artifact deletion unless separately designed and approved.
- No broad automatic recovery loop.
- No retry or requeue behavior without separate design approval.
- No terminalization behavior without separate design approval.
- No intent cancellation behavior without separate design approval.

Recommended rollout phases:

- Phase 0: report-only and dry-run only. This is the current state.
- Phase 1: single-object inspect-confirm flow with no mutation.
- Phase 2: one low-risk single-object action behind an explicit operator token.
- Phase 3: narrow allowlisted actions only after audit evidence, transaction tests, idempotency tests, and rollback/compensation tests.
- Phase 4: broader recovery only if separately approved with incident ownership, monitoring, and rollback criteria.

Hard stop conditions:

- Stale `metadata_hash`.
- Mismatched `precondition_token`.
- Mismatched `required_confirm_token`.
- Object state changed since the report or inspect output was generated.
- Missing audit evidence.
- Unknown action.
- High-risk action without separate approval.
- CI or test failure in the apply-mode PR.
- Storage or source-of-truth uncertainty.

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
- database backup ID and matching storage snapshot/object version marker
- quota warning thresholds and current usage trend
- pending destructive cleanup approvals

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

Manual verification:

```powershell
python manage.py storage_metrics_snapshot --older-than-days 30 --json
python manage.py system_observability_report --json
```

Confirm the report shows `storage.available: true`, a non-empty `snapshot_generated_at`, a low `snapshot_age_seconds`, and the expected `storage_backend.effective_storage_backend`. For filesystem-backed runtime, also confirm `storage_backend.metrics.filesystem_root_status: ok`. Then check Prometheus:

```powershell
curl http://localhost:8000/api/v1/system/metrics/prometheus/ | findstr system_observability_storage_snapshot
```

Expected freshness gauges:

- `system_observability_storage_snapshot_available 1`
- `system_observability_storage_snapshot_age_seconds` close to the time since refresh
- `system_observability_storage_snapshot_generated_timestamp` greater than `0`

Production storage contract:

- Live multi-user production should target S3-compatible object storage such as managed cloud S3 or production-grade MinIO. The current app still uses filesystem paths by default; do not assume S3 is serving runtime media.
- A boto3-backed S3 adapter foundation exists and is selected only with `STORAGE_BACKEND=s3`. It requires `S3_BUCKET_NAME`, `S3_ACCESS_KEY_ID`, and `S3_SECRET_ACCESS_KEY`; optional settings are `S3_ENDPOINT_URL`, `S3_REGION_NAME`, `S3_KEY_PREFIX`, `S3_USE_SSL`, and `S3_VERIFY_SSL`. Do not commit credentials.
- MinIO/S3 integration tests are optional and skipped unless `STORAGE_S3_INTEGRATION=1` and explicit `S3_*` environment variables are present. Normal CI uses mocked S3 clients.
- Do not configure production runtime traffic to S3 until a follow-up rollout moves selected safe paths after staging proof and records rollback evidence.
- A shared filesystem is only a temporary production bridge when it is durable, externally backed up, mounted consistently by every service, monitored for capacity, and restore-tested in staging.
- Database and media storage must be backed up and restored together. Record the database backup ID and storage snapshot/object marker as one recovery point.
- Before v1.0.0, run a staging restore drill that proves project detail, playback, subtitles, profile/avatar media if enabled, and one rerender from restored source uploads.
- Destructive cleanup requires manual approval, current backup evidence, a dry-run manifest, and archived before/after reports. There is no approved automatic deletion path yet.
- Project deletion currently does not guarantee comprehensive media cleanup. Treat project-owned uploads/renders/subtitles/avatar media as remaining storage obligations until a manifest-based cleanup PR lands.

Initial storage incident thresholds:

- Warn at 70% capacity.
- Escalate at 85% capacity.
- Treat 95% capacity as an incident that may require pausing uploads/renders.
- Investigate storage metrics snapshots older than 24 hours.
- Investigate orphan or retention candidate bytes above the operator-reviewed threshold.

See [STORAGE_PRODUCTION_READINESS.md](STORAGE_PRODUCTION_READINESS.md) for the full classification, backup/restore, quota, retention, and deletion contract.

Optional local MinIO adapter integration test:

```powershell
docker compose -f infra\docker-compose.yml up -d minio

$env:STORAGE_S3_INTEGRATION="1"
$env:S3_ENDPOINT_URL="http://localhost:9000"
$env:S3_BUCKET_NAME="academy-media"
$env:S3_ACCESS_KEY_ID="minioadmin"
$env:S3_SECRET_ACCESS_KEY="change-me-minio-password"
$env:S3_REGION_NAME="us-east-1"
$env:S3_KEY_PREFIX="local-minio-adapter-tests"
$env:S3_USE_SSL="false"
$env:S3_VERIFY_SSL="false"

py -m pytest tests/test_storage_service.py -q
```

Create the bucket first through the MinIO console or an operator-approved `mc mb` command if it does not already exist. The test uses a unique prefix per run, deletes only the probe objects it created, never deletes the bucket, never lists outside the prefix, and does not switch runtime traffic away from the filesystem adapter.

## Docker Worker Build Triage

The worker image is expected to build much more slowly than the API image because it installs GPU/avatar runtime dependencies during the image build. On a cold cache, long pauses in these steps usually indicate dependency download, wheel unpack, or model snapshot export rather than an application-code failure:

- CUDA PyTorch wheels from `download.pytorch.org`.
- OpenMMLab `mmcv` prebuilt wheel, `mmdet`, and `mmpose` installation.
- `onnxruntime-gpu` and related LivePortrait dependencies.
- MuseTalk and LivePortrait repository clones.
- Hugging Face `KlingTeam/LivePortrait` snapshot download into `/opt/liveportrait/pretrained_weights`.

Use plain progress output when diagnosing build stalls:

```powershell
docker compose -f infra\docker-compose.yml build --progress=plain worker
```

The Dockerfiles use BuildKit pip cache mounts so repeated local builds and CI retries can reuse downloaded Python wheels without baking pip caches into the final image. CI also uses a GitHub Actions Buildx cache for the docker-smoke job. A dependency step can still be slow on the first run for a branch, after cache eviction, after requirements or Dockerfile dependency layers change, or when upstream package/model hosts are slow.

Treat the build as a likely real failure when the same package, clone, or snapshot command exits with a non-zero status on repeated attempts. Treat it as likely cache/cold-download slowness when logs continue to show large wheel downloads, extraction, or Hugging Face file transfer without an exit status.

Avatar-capable worker images must install `mmcv`/`mmpose` in Docker, not in the Windows or host Python environment. Installing `mmcv` into `.venv` does not help `worker-avatar`. The worker Dockerfile installs `mmcv` from prebuilt wheels only and never falls back to a source build.

Use the OpenMMLab index when the network can reach it:

```powershell
docker compose -f infra\docker-compose.yml --profile avatar build --no-cache --build-arg INSTALL_OPENMMLAB_DEPS=1 --build-arg DOWNLOAD_LIVEPORTRAIT_WEIGHTS=1 worker-avatar
```

Use a local/offline wheel when `download.openmmlab.com` is blocked. Get a compatible `mmcv` wheel on another machine or network, then save it outside git:

```powershell
New-Item -ItemType Directory -Force local_wheels
# Put the downloaded wheel here, for example:
# local_wheels\mmcv-2.0.1-cp310-cp310-manylinux1_x86_64.whl

docker compose -f infra\docker-compose.yml --profile avatar build --no-cache --build-arg INSTALL_OPENMMLAB_DEPS=1 --build-arg DOWNLOAD_LIVEPORTRAIT_WEIGHTS=1 --build-arg MMCV_LOCAL_WHEEL=local_wheels/mmcv-2.0.1-cp310-cp310-manylinux1_x86_64.whl worker-avatar
docker compose -f infra\docker-compose.yml --profile avatar run --rm --no-deps worker-avatar python -c "import mmcv, mmpose; print(mmcv.__version__)"
```

`local_wheels/` and `*.whl` are ignored by git. Do not put downloaded wheels under a tracked path.

Use `MMCV_WHEEL_URL` when you have an internal artifact URL:

```powershell
docker compose -f infra\docker-compose.yml --profile avatar build --no-cache --build-arg INSTALL_OPENMMLAB_DEPS=1 --build-arg DOWNLOAD_LIVEPORTRAIT_WEIGHTS=1 --build-arg MMCV_WHEEL_URL=https://example.internal/mmcv-2.0.1-cp310-cp310-manylinux1_x86_64.whl worker-avatar
```

Use a prebuilt heavy avatar worker image when local heavy builds are not practical:

```powershell
docker pull registry.example.internal/ai-academy-worker-avatar:cuda118-mmcv201
docker tag registry.example.internal/ai-academy-worker-avatar:cuda118-mmcv201 ai_academy_worker:local
docker compose -f infra\docker-compose.yml --profile avatar up -d --no-build --force-recreate worker-avatar
docker compose -f infra\docker-compose.yml --profile avatar run --rm --no-deps worker-avatar python -c "import mmcv, mmpose; print(mmcv.__version__)"
```

If no local wheel exists and `MMCV_WHEEL_URL` is empty, the build checks `MMCV_FIND_LINKS` and installs `mmcv==${MMCV_VERSION}` with `--only-binary=:all:`. An unreachable OpenMMLab wheel index fails fast with instructions instead of attempting a source build.

`worker-avatar` is behind the Compose `avatar` profile. The Windows runtime wrapper passes that profile only for `-Profile avatar` and `-Profile full`, while preserving `--no-build` and `--pull never`.

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
- The `docker-smoke` job performs real API, worker, and TTS image builds. Its timeout is intentionally longer than backend/frontend jobs because the worker image contains large CUDA, PyTorch, MuseTalk, LivePortrait, and Hugging Face dependency steps.
