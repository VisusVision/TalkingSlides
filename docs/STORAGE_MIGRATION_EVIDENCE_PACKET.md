# Storage Migration Evidence Packet

This packet records the staging evidence required before any runtime S3 or media storage migration work starts.

Documentation is not collected evidence. The storage production readiness document, operations runbook, and this template define the gate, but a migration PR must attach fresh staging outputs, backup markers, restore drill results, owner sign-off, and design decisions from the target environment.

## Migration Scope Under Review

| Field | Value |
| --- | --- |
| Environment | |
| Commit SHA | |
| Proposed migration phase | |
| Filesystem source-of-truth retained? | |
| Runtime S3/media migration included? | No, unless this packet is complete and approved. |
| Signed/public URL work included? | No, unless separately approved. |
| Range-read work included? | No, unless separately approved. |
| Cleanup/quota/delete work included? | No, unless separately approved. |

## Artifact Checklist

Collect these artifacts from staging before opening any runtime migration PR:

- [ ] Fresh `system_observability_report --json` output.
  - Artifact path or link:
  - Expected evidence: `runtime_media_migration_implied: false` before migration, expected backend, storage snapshot availability, and no unexplained warnings.
- [ ] Fresh `storage_metrics_snapshot --older-than-days 30 --json` output.
  - Artifact path or link:
  - Expected evidence: current `generated_at`, `snapshot_path`, total bytes, retention candidate count, orphan candidate count, and reclaimable byte estimate.
- [ ] Fresh `storage_retention_check --dry-run --older-than-days 30 --json` output from the same staging storage target.
  - Artifact path or link:
  - Expected evidence: report-only retention and orphan candidates, with no deletion or remediation performed.
- [ ] Storage snapshot CronJob owner, cadence, and escalation path.
  - Owner:
  - Cadence:
  - Escalation:
  - Expected evidence: CronJob or scheduler is live, monitored, and has a stale/missing snapshot response path.
- [ ] Proof that API, workers, and the storage snapshot CronJob use the same durable `STORAGE_ROOT` or PVC.
  - Artifact path or link:
  - Expected evidence: API, render worker, TTS/avatar workers, and CronJob mounts/env all point at the same durable storage target, not scratch storage.
- [ ] Database backup ID.
  - Backup ID:
  - Timestamp:
  - Expected evidence: backup covers the project state used for restore validation.
- [ ] Media/storage snapshot ID or object marker.
  - Snapshot ID or marker:
  - Timestamp:
  - Expected evidence: storage snapshot/object marker aligns with the database backup point.
- [ ] Restore drill result.
  - Artifact path or link:
  - Expected evidence: database and media restored together into isolated staging, with elapsed time, manual steps, missing files, and RPO/RTO notes recorded.
- [ ] Playback, subtitle, profile, avatar, and rerender verification.
  - Artifact path or link:
  - Expected evidence: project detail loads, playback token issuance works, final MP4 or HLS playback works, subtitle track access works, profile/avatar media works if enabled, and one restored project rerenders from restored source uploads.
- [ ] Rollback plan.
  - Artifact path or link:
  - Expected evidence: source of truth, write freeze or reconciliation window, reverse-sync expectations if S3 receives writes, and mixed DB/object state checks.
- [ ] Range-read and media serving strategy.
  - Artifact path or link:
  - Expected evidence: MP4 byte ranges, HLS manifest/segment/key delivery, subtitle conversion/serving, profile images, avatar previews/overlays, cache headers, authorization, and browser seeking behavior are addressed.
- [ ] Cleanup, quota, and delete strategy.
  - Artifact path or link:
  - Expected evidence: dry-run manifests, manual approval, backup checks, evidence holds, quota thresholds, and explicit confirmation that automatic deletion stays disabled until approved tooling exists.
- [ ] Render recovery JSONL append design.
  - Artifact path or link:
  - Expected evidence: local append semantics are replaced or contained by a reviewed object-storage-safe design such as database-backed audit records, per-event immutable objects, or whole-object concurrency controls.

## Acceptance Criteria

A runtime storage migration PR may proceed only when all criteria are met:

- [ ] All required artifacts above are attached to the migration ticket or PR.
- [ ] The evidence was collected from the target staging environment, not local development.
- [ ] The storage metrics snapshot is fresh according to the staging freshness threshold.
- [ ] The retention dry-run report is from the same storage target as the metrics snapshot.
- [ ] Backup and media snapshot markers identify one consistent recovery point.
- [ ] The restore drill proves database and media can be restored together.
- [ ] Playback, subtitle, profile/avatar media if enabled, and one rerender pass after restore.
- [ ] The rollback plan identifies the source of truth for the migration phase.
- [ ] Range-read and serving compatibility has an approved design before serving migration.
- [ ] Cleanup, quota, and delete remain disabled unless a separate approved implementation exists.
- [ ] Render recovery audit logging has an approved append-safe design before object storage migration.
- [ ] Operator owner, backend owner, and release gatekeeper have signed off.

## Sign-Off

| Role | Name | Approval | Date |
| --- | --- | --- | --- |
| Operator owner | | | |
| Backend owner | | | |
| Release gatekeeper | | | |

| Field | Value |
| --- | --- |
| Date | |
| Environment | |
| Commit SHA | |
| Evidence packet location | |
| Migration ticket or PR | |
