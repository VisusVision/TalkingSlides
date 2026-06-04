# Storage Production Readiness

VISUS currently stores uploads, generated media, playback sidecars, avatar assets, cache files, and storage observability snapshots through local filesystem paths rooted at `STORAGE_ROOT`. A small filesystem storage adapter exists for low-risk health, retention, and observability helpers. MinIO and S3-style environment variables exist in Docker and env templates, but there is no active S3 adapter and no object-storage read/write path in the application yet.

This document is the production storage contract for live users. It is an operator and architecture decision record plus the current adapter migration status; it does not implement S3/MinIO storage, deletion, cleanup automation, quotas, or migrations.

## Audit Findings

Current filesystem dependencies:

- API, render worker, TTS service, and avatar worker all expect a shared readable/writable `STORAGE_ROOT`.
- Django `MEDIA_ROOT` exists, but lesson media, playback assets, avatars, subtitles, reports, and most generated files use explicit `STORAGE_ROOT` paths.
- Render and playback code stores relative paths in database rows and sidecar JSON, then resolves them back under `STORAGE_ROOT`.
- Local Docker Compose mounts repo-root `storage_local/` into API, worker, avatar worker, and TTS containers as `/app/storage_local`.
- MinIO is started by local Compose and S3 env vars are documented, but active code does not use boto3, django-storages, or an object-storage adapter.
- `core.storage_adapter.FilesystemStorageAdapter` is the only active adapter. Current adoption is limited to storage smoke checks, storage metrics snapshots, and report-only retention traversal.
- Existing storage commands are visibility checks only: `storage_smoke_check`, `storage_retention_check --dry-run`, and `storage_metrics_snapshot`.

Known paths under `STORAGE_ROOT`:

| Path shape | Current use |
| --- | --- |
| `uploads/<project_id>/` | Original source uploads, covers, backgrounds, and upload-scoped files. |
| `<project_id>/` | Render workspace and final render outputs. |
| `<project_id>/playback_assets.json` | Playback sidecar used by API/player payloads. |
| `<project_id>/language_detection.json` | Language detection sidecar. |
| `<project_id>/drm/hls/` | HLS manifests, segments, and encryption sidecars when packaging runs. |
| `<project_id>/subtitles/` | Original and translated subtitle artifacts. |
| `<project_id>/source_backgrounds/` | Worker-prepared source/background render inputs. |
| `<project_id>/highlight_previews/` | Studio highlight preview outputs. |
| `projects/<project_id>/renders/<job_id>/avatar_handoff.json` | Avatar handoff manifest. |
| `avatars/<user_id>/uploads/` | Avatar source images/videos. |
| `avatars/<user_id>/preview/` | Avatar preview outputs. |
| `<project_id>/avatar_segments/` and project render folders | Avatar generated lesson outputs and segment artifacts. |
| `profiles/<user_id>/` | Profile, banner, and logo images. |
| `voices/<voice_id>.wav` | Uploaded/reference voice audio used by TTS. |
| `moderation/video_frames/` | Video frame audit samples when retained. |
| `observability/storage_metrics_snapshot.json` | Cached storage metrics snapshot. |
| `audit/render_recovery_actions.jsonl` | Operator render recovery audit log unless overridden. |
| `.storage-smoke/` | Temporary smoke-check probe files. |
| `tmp/`, `*.tmp`, `*.lock`, `*.part` | Temporary, lock, and partial-write files. |
| `tts/`, `tts_cache/`, `models/`, `cache/` | TTS/model/cache paths in local/container deployments. |

User-critical files:

- Original source uploads needed for rerender, support, audit, and recovery.
- Final render outputs for published or user-visible lessons.
- `playback_assets.json` and HLS/subtitle sidecars needed for playback.
- Profile images, avatar source media, current avatar previews, and current avatar generated outputs.
- Voice reference audio if it is needed to reproduce or rerender narration.
- Operator audit/report snapshots when they are required as incident or release evidence.

Regenerable files:

- Render outputs can be regenerated only when the database row, source upload, transcript, TTS settings, avatar settings, runtime dependencies, and provider behavior are still available.
- HLS sidecars can be regenerated from a valid final MP4 and the same protection policy.
- Translated subtitles can be regenerated only if source captions and provider configuration remain available; external provider output may not be byte-identical.
- Moderation frame samples are generally regenerable from the source video/render output unless retained as compliance evidence.
- TTS generated audio may be regenerable from text/settings/voice source, but provider/model drift means it should not be treated as lossless.

Temporary files:

- `.storage-smoke/` probes.
- `tmp/` contents.
- Old `*.tmp`, `*.lock`, and `*.part` files.
- Current-run avatar preview scratch files after a successful preview handoff.
- Local model/download caches if they are not the only copy of a required model.

Current gaps:

- No active object-storage adapter.
- Runtime upload, render, playback, avatar, and TTS media paths still use existing filesystem path handling directly.
- No global quota enforcement.
- No cleanup automation for old render outputs.
- No comprehensive project-delete media cleanup.
- No implemented deletion workflow for orphan media.
- No backup automation in this repository.
- No restore drill evidence required by code.
- No lifecycle policy enforcement.
- No production object storage credentials or bucket policy contract wired into app code.

## Production Storage Decision

For live multi-user production, the recommended target is S3-compatible object storage such as managed cloud S3 or production-grade MinIO.

Local filesystem storage is not enough for live multi-user production because it couples media durability to one host or volume, complicates horizontal workers, makes backup/restore consistency harder, and makes capacity, lifecycle, encryption, access policy, and disaster recovery depend on host-specific operations. The current filesystem path is acceptable for local development and limited staging, but it should not be described as production-ready for live users without a reviewed durable storage architecture and restore evidence.

A shared filesystem can be acceptable temporarily only when all of these are true:

- It is external to the application host lifecycle.
- API, render worker, TTS, and avatar worker mount the same storage consistently.
- Backups and snapshots are configured independently of app deploys.
- Restore has been tested in staging.
- Capacity and inode alerts exist.
- Operator approval is required before destructive cleanup.
- The deployment owner accepts that object lifecycle, bucket policies, and least-privilege object credentials are not yet available through the app.

Required storage properties for live users:

- Durability across app restarts, node replacement, worker replacement, and deploy rollbacks.
- Coordinated database and media backups with recorded backup IDs or snapshot IDs.
- Encryption in transit and at rest.
- Lifecycle rules for temporary, generated, stale, and audit/report categories.
- Least-privilege credentials for app read/write, backup, restore, and operator inspection.
- Versioning or object lock where legal, compliance, or recovery policy requires it.
- Observability for capacity, error rates, object count, age, restore status, and backup freshness.
- Restore drills that prove a real lesson can be recovered from database plus storage backups.

## Storage Classification Table

| Category | Example path | Owner/model | Criticality | Regenerable | Backup required | Retention policy | Delete behavior | Restore priority |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Original uploads | `uploads/123/source.pptx` | `Project`, upload job metadata | Critical | No | Yes | Keep while project exists and through legal/support window after deletion. | Delete only through future confirmed project-delete media cleanup after backup and retention checks. | P0 |
| Render outputs | `123/123.mp4`, `123/slide_001.png` | `Project`, `Job.result_url`, render sidecars | Critical for published lessons | Conditional | Yes for current/published outputs | Keep latest ready output for active/published lessons; older outputs per rollback window. | Do not delete latest active output; future cleanup must dry-run first. | P0 |
| `playback_assets.json` | `123/playback_assets.json` | Playback token/API sidecar | Critical | Conditional | Yes | Match render output retention. | Delete only with owning render/project deletion. | P0 |
| HLS sidecars | `123/drm/hls/index.m3u8`, `seg_000.ts`, `enc.key` | Playback sidecar/protection policy | Critical when HLS/protected playback is enabled | Conditional | Yes | Match protected playback/render retention. | Delete only with owning render/project deletion; never delete while published playback can reference it. | P0 |
| Translated subtitles | `123/subtitles/en.vtt` | `TranslatedSubtitleTrack` | User-visible | Conditional | Yes for ready tracks | Keep while track is listed/ready and project exists; expire failed/transient generations separately. | Delete only after DB track is removed or marked obsolete by future confirmed cleanup. | P1 |
| Profile images | `profiles/7/avatar.png` | `UserProfile` | User-visible | No | Yes | Keep while account/profile exists and through account deletion retention window. | Delete only through account/profile media cleanup contract. | P1 |
| Avatar source assets | `avatars/7/uploads/source.png` | `UserProfile` avatar fields | Critical if avatar is enabled | No | Yes | Keep while avatar profile is active and rerender/revalidation may be needed. | Delete only when user replaces/removes avatar or account deletion contract applies. | P0/P1 |
| Avatar generated outputs | `avatars/7/preview/output.mp4`, `123/avatar_segments/avatar_001.mp4` | `AvatarRenderJob`, `Project.avatar_output_path`, profile preview fields | User-visible | Conditional | Yes for current outputs | Keep current preview/output; old failed/debug outputs by reviewed window. | Future cleanup must preserve currently referenced preview/output and active jobs. | P1 |
| Moderation frame samples | `moderation/video_frames/...jpg` | `AgentRun`, `AgentFinding`, frame audit metadata | Compliance-dependent | Usually yes | Policy-dependent | Default short retention; retain longer only when moderation/compliance policy requires evidence. | Existing cleanup may remove successful samples when configured; otherwise manual cleanup only after metadata is persisted. | P2 unless evidence hold |
| TTS generated audio | `123/tts/page_001.wav`, `tts_cache/...` | `LessonSegment`, render job, TTS cache | User-visible/intermediate | Conditional | Yes for audio required by current render; cache optional | Keep audio needed for current render/recovery; cache by cost/performance window. | Old cache can be a cleanup candidate after confirming no DB references and rerender path exists. | P1 |
| Voice reference audio | `voices/<voice_id>.wav` | Voice upload/API field or TTS request metadata | Critical if used for cloning/rerender | No | Yes | Keep while voice profile/project needs it and through deletion retention window. | Delete only through voice/profile deletion contract. | P0/P1 |
| Temp/lock/part files | `.storage-smoke/probe-*`, `*.tmp`, `*.lock`, `*.part`, `tmp/*` | Runtime process only | Low, unless active write | No | No | Short age-based retention after job/write timeout. | Safe cleanup candidate only when older than reviewed threshold and no active job owns it. | P3 |
| Audit/report snapshots | `observability/storage_metrics_snapshot.json`, `audit/render_recovery_actions.jsonl` | Operator commands/reports | Operational evidence | Snapshot yes; audit no | Yes if used as release/incident evidence | Keep per release/incident/audit policy; snapshots may be overwritten, audit logs append-only. | Do not delete incident/release evidence before retention expiry. | P2 |
| Model/cache files | `models/`, `cache/`, `tts_cache/hf/` | Runtime dependency cache | Operational | Yes if source available | No, unless cold downloads are unacceptable | Keep by capacity/performance policy. | Delete only during maintenance window; expect cold-start impact. | P3 |

## Backup And Restore Contract

Backups must cover Postgres and object/media storage together. Restoring only the database can leave rows pointing to missing media. Restoring only media can leave orphaned objects with no product state.

Minimum production contract:

- Record a database backup ID and storage snapshot/version marker for each backup point.
- Align backup cadence with the expected write volume for uploads, renders, subtitles, avatars, and profile media.
- Start with a target RPO of 15 minutes for database state and 60 minutes for object/media storage, then tighten or loosen only after measuring cost and risk.
- Start with a target RTO of 4 hours for full service restore and 1 hour for single-project media restore in staging drills.
- Keep backups encrypted and access-controlled separately from application runtime credentials.
- Retain backups long enough to satisfy product, legal, support, and account deletion policies.
- Test restore into staging before v1.0.0 and after any storage architecture change.

Restore drill procedure:

1. Select a staging project with an original upload, completed render, playback sidecar, subtitles, and avatar/profile media if enabled.
2. Record the project ID, latest job ID, database backup ID, and storage snapshot/object version marker.
3. Restore the database into an isolated staging database.
4. Restore media into an isolated storage root or bucket prefix.
5. Point API, worker, TTS, and avatar worker to the restored database and storage target.
6. Run `python manage.py check`.
7. Run `python manage.py storage_smoke_check`.
8. Run `python manage.py storage_retention_check --dry-run --older-than-days 30 --json` and archive the report.
9. Verify project detail, playback-token issuance, final MP4 or HLS playback, subtitle track access, profile images, and avatar media if applicable.
10. Rerender one restored project only after confirming source uploads and settings are present.
11. Record elapsed restore time, missing files, broken references, manual steps, and any RPO data loss.

Evidence required before v1.0.0:

- Documented production storage backend decision.
- Successful staging restore drill with database and media restored together.
- Backup cadence and retention policy approved by the release owner.
- Storage smoke check output from the production-like mount/bucket.
- `storage_retention_check --dry-run --json` archived from staging.
- Storage metrics snapshot freshness alert or operator check defined.
- Manual destructive-action approval process documented.

## Quota, Retention, And Delete Contract

Initial quota policy:

- Define per-tenant or per-teacher storage quotas before live broad usage.
- Track at least original uploads, current render outputs, subtitles, avatar media, profile media, and cache separately.
- Start with soft quota warnings before hard blocking.
- Do not count temporary files against user quota if they are owned by active jobs and cleaned by operator workflow.

Initial alert thresholds:

- Warn at 70% total storage capacity.
- Escalate at 85% total storage capacity.
- Treat 95% as an incident threshold requiring upload/render throttling or operator intervention.
- Warn when storage metrics snapshot age exceeds 24 hours.
- Warn when orphan candidate or retention candidate bytes exceed an operator-reviewed threshold.

Retention categories:

- Active project critical media: retain while project exists and is recoverable.
- Published lesson playback media: retain while published and through rollback/support window after unpublish/delete.
- Original uploads: retain while rerender/recovery/support policy requires them.
- Generated historical renders: retain by rollback window, then clean only through dry-run/confirm tooling.
- Moderation samples: retain by moderation/compliance policy; default short retention unless evidence hold applies.
- Temporary/lock/partial files: retain only long enough to avoid racing active jobs.
- Audit/report evidence: retain by release, incident, and compliance policy.

Safe cleanup candidates:

- Old `.storage-smoke/` probes.
- Old files under `tmp/`.
- Old `*.tmp`, `*.lock`, and `*.part` files after active job checks.
- Old retained moderation frame samples when metadata is persisted and no evidence hold applies.
- Cache/model files only during maintenance when cold-start impact is accepted.
- Orphan-shaped project/upload/avatar directories only after database validation, backup confirmation, and manual approval.

Project-delete media cleanup contract:

- Current project deletion removes database state but does not provide comprehensive media cleanup.
- Future project-delete cleanup must enumerate every referenced object before deletion: upload files, render outputs, playback sidecars, HLS assets, subtitle tracks, avatar handoff/output files, cover/background files, moderation samples, and audit/report evidence tied to the project.
- Cleanup must run in dry-run mode by default and produce a manifest of objects, byte counts, DB references, and excluded evidence-hold files.
- Executed deletion must require explicit confirmation and operator/user authorization.
- Deletion must be idempotent and safe to retry.
- Deletion logs must be retained as audit evidence.

Orphan reconciliation workflow:

1. Run `python manage.py storage_retention_check --dry-run --older-than-days 30 --json`.
2. Review orphan candidates against database state, recent jobs, and support tickets.
3. Confirm a current database backup and storage snapshot exist.
4. Produce a deletion manifest and obtain manual approval.
5. Delete only approved candidates through future confirmed tooling or a documented break-glass runbook.
6. Rerun the dry-run report and archive before/after evidence.

Manual approval is required before any destructive action involving user uploads, published playback media, avatar source/generated media, subtitles, profile media, audit logs, or orphan directories. Automated cleanup must not be enabled until the dry-run/confirm workflow and backup evidence are implemented.

## Implementation Roadmap

1. Storage adapter abstraction: started with a filesystem-only adapter for low-risk health, retention, and observability helpers. High-risk runtime media paths intentionally remain on existing direct filesystem behavior.
2. Runtime path migration plan: map upload, render, playback, avatar, TTS, subtitles, and moderation paths to adapter operations without changing stored relative paths or response payloads.
3. MinIO/S3 adapter: wire S3-compatible object storage for API, worker, TTS, avatar, playback streaming, and sidecar access with least-privilege credentials.
4. Backup/restore drill: implement operator-owned staging drill automation or scripts and archive evidence for database plus object storage consistency.
5. Quota and retention execution: add dry-run first, then explicit-confirm execution for safe cleanup candidates and quota reporting.
6. Project-delete media cleanup: add idempotent manifest-based deletion for all project-owned media, with audit logs and evidence holds.
7. CDN/private media delivery if needed: add private object delivery, signed URLs or tokenized proxy behavior, CDN caching policy, and protected playback integration.

## Current Validation Commands

Run before release and after storage config changes:

```powershell
cd services\api
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py storage_smoke_check
python manage.py storage_retention_check --dry-run --older-than-days 30
python manage.py storage_metrics_snapshot --older-than-days 30
```

These commands do not make storage production-ready. They validate settings, schema drift, filesystem access, report-only retention/orphan candidates, and cached storage metrics.

## Remaining Risks

- The active application still depends on filesystem paths.
- MinIO/S3 is configured but inactive.
- The adapter abstraction is filesystem-only and currently adopted only by low-risk helper/reporting code.
- Upload, render, playback, avatar, and TTS paths have not been migrated to the adapter yet.
- There is no implemented quota enforcement.
- There is no implemented project-delete media cleanup.
- There is no implemented orphan deletion workflow.
- There is no cleanup automation for old generated renders.
- Backup and restore are an operator contract, not repository automation.
- Generated artifacts may be hard to reproduce exactly because render, TTS, subtitle, and avatar providers can drift.
