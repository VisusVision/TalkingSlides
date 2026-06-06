# Storage Production Readiness

VISUS currently stores uploads, generated media, playback sidecars, avatar assets, cache files, and storage observability snapshots through local filesystem paths rooted at `STORAGE_ROOT`. A small storage adapter boundary exists for low-risk health, retention, sidecar, and observability helpers. Filesystem remains the default and active runtime backend. An S3-compatible adapter foundation now exists behind explicit `STORAGE_BACKEND=s3` configuration, but no upload, render, playback, avatar, TTS, streaming/range, cleanup, quota, or public URL path has been migrated to S3.

This document is the production storage contract for live users. It is an operator and architecture decision record plus the current adapter migration status; it does not switch runtime traffic to S3/MinIO storage, deletion, cleanup automation, quotas, or migrations.

## Audit Findings

Current filesystem dependencies:

- API, render worker, TTS service, and avatar worker all expect a shared readable/writable `STORAGE_ROOT`.
- Django `MEDIA_ROOT` exists, but lesson media, playback assets, avatars, subtitles, reports, and most generated files use explicit `STORAGE_ROOT` paths.
- Render and playback code stores relative paths in database rows and sidecar JSON, then resolves them back under `STORAGE_ROOT`.
- Local Docker Compose mounts repo-root `storage_local/` into API, worker, avatar worker, and TTS containers as `/app/storage_local`.
- MinIO is started by local Compose and S3 env vars are documented. The S3-compatible adapter uses `boto3` and can target AWS S3 or compatible providers such as MinIO, Cloudflare R2, Wasabi, and DigitalOcean Spaces.
- `core.storage_adapter.FilesystemStorageAdapter` is the default adapter. `core.storage_adapter.S3StorageAdapter` is available only through explicit `STORAGE_BACKEND=s3` configuration and is not wired into runtime media delivery.
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

- S3 adapter foundation exists, but no runtime media traffic is switched to it.
- Runtime upload, render, playback, avatar, and TTS media paths still use existing filesystem path handling directly.
- No global quota enforcement.
- No cleanup automation for old render outputs.
- No comprehensive project-delete media cleanup.
- No implemented deletion workflow for orphan media.
- No backup automation in this repository.
- No restore drill evidence required by code.
- No lifecycle policy enforcement.
- No production object storage credentials or bucket policy contract wired into app code.

## Runtime Migration Gate

Runtime storage migration is blocked for now. Filesystem-backed `STORAGE_ROOT` remains the source of truth for uploads, render outputs, playback sidecars, avatar assets, TTS inputs/outputs, profile media, subtitles, recovery audit logs, and operator reports until a separate migration PR records the required evidence below.

The current storage readiness report is observability-only. `system_observability_report` and the Prometheus storage gauges expose configuration, cached snapshot freshness, capacity estimates, retention candidates, and recovery signals; they do not prove S3/MinIO connectivity, bucket policy, object durability, range-read compatibility, signed URL safety, cleanup safety, quota behavior, or rollback readiness. A report showing `runtime_media_migration_implied: false` is expected and must be treated as a migration stop sign, not a partial runtime rollout.

The cached metrics snapshot can be stale unless staging has an assigned owner and cadence for `storage_metrics_snapshot`. Before using snapshot values as migration evidence, an operator must prove that the staging CronJob or equivalent scheduler is pointed at the same durable `STORAGE_ROOT` mounted by API and workers, runs on an approved cadence, and has freshness alert ownership.

Required evidence before any runtime migration PR:

- Complete the [Storage Migration Evidence Packet](STORAGE_MIGRATION_EVIDENCE_PACKET.md) with fresh staging artifacts; this document lists requirements but is not collected evidence.
- Fresh `storage_metrics_snapshot --older-than-days 30 --json` output from staging, with `snapshot_generated_at`, `snapshot_path`, and storage root/bucket target archived.
- Fresh `storage_retention_check --dry-run --older-than-days 30 --json` report from the same staging storage target, archived with the migration ticket.
- Backup/restore evidence proving database state and media storage are restored together into isolated staging, including project detail, playback-token issuance, final MP4 or HLS playback, subtitle access, profile/avatar media if enabled, and one rerender from restored source uploads.
- Rollback plan that names the source of truth for the phase, the data freeze or reconciliation window, reverse-sync expectations if S3 receives writes, and checks that prevent mixed database/object state.
- Named owner and cadence for the storage metrics snapshot CronJob, plus documented response steps for stale or missing snapshots.
- Range-read and media serving strategy for MP4 byte ranges, HLS manifest/segment/key delivery, subtitle conversion/serving, profile images, avatar previews/overlays, cache headers, authorization, and browser seeking behavior.
- Cleanup, quota, and delete strategy with dry-run manifests, manual approval, backup checks, evidence holds, and explicit non-goals for automatic deletion until confirmed tooling exists.
- Render recovery JSONL append design. The current audit log uses local append semantics; object storage needs a reviewed append-compatible design such as database-backed audit records, per-event immutable objects, or whole-object concurrency controls before migration.

Unsafe work while this gate is closed:

- Upload, render, playback, avatar, or TTS media migration.
- Signed URL or public URL delivery.
- Range-read implementation.
- Cleanup, quota, or delete implementation.
- Backend switch to S3 for runtime traffic.

S3 adapter configuration:

| Setting | Default | Notes |
| --- | --- | --- |
| `STORAGE_BACKEND` | `filesystem` | Must be `s3` to select the S3 adapter. Unknown values fail closed. |
| `S3_ENDPOINT_URL` | empty | Optional for AWS S3; set for MinIO/R2/Wasabi/Spaces endpoints. |
| `S3_BUCKET_NAME` | empty | Required when `STORAGE_BACKEND=s3`. |
| `S3_ACCESS_KEY_ID` | empty | Required when `STORAGE_BACKEND=s3`; do not commit secrets. |
| `S3_SECRET_ACCESS_KEY` | empty | Required when `STORAGE_BACKEND=s3`; do not commit secrets. |
| `S3_REGION_NAME` | empty | Optional provider region. |
| `S3_KEY_PREFIX` | empty | Optional safe prefix; absolute paths and `..` traversal are rejected. |
| `S3_USE_SSL` | `true` | Passed to boto3 client construction. |
| `S3_VERIFY_SSL` | `true` | Passed to boto3 client construction. |

Normal CI uses mocked S3 clients only. Real MinIO/S3 integration tests are optional and skipped unless `STORAGE_S3_INTEGRATION=1` and explicit `S3_*` environment variables are present.

Optional MinIO/S3 adapter integration test:

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

The bucket must already exist. Local Compose publishes MinIO at `localhost:9000`; inside Compose networks the existing template uses `MINIO_ENDPOINT=minio:9000`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, and `MINIO_BUCKET_NAME`. Map those values to the adapter test variables above. The integration harness creates a unique prefix under optional `S3_KEY_PREFIX`, writes only probe objects under that prefix, deletes only the exact probe keys it created, never deletes the bucket, never lists or deletes outside the test prefix, and verifies traversal rejection before an S3 client call. It also asserts that the adapter does not expose public URL generation.

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

## S3/MinIO Adapter Design RFC

This section is design only. It does not add an S3/MinIO adapter, change runtime behavior, change API response formats, add migrations, add dependencies, enable cleanup, enforce quotas, or move files. The filesystem adapter remains the only active adapter.

### RFC Audit Findings

Current adapter primitives:

- `resolve_path(relative_path)` normalizes a storage-root-relative path and returns a local `Path`.
- `exists`, `read_bytes`, `write_bytes`, `read_text`, `write_text`, `delete_file`, `make_dirs`, `iter_files`, and `iter_children` operate against local files.
- Path traversal protection rejects absolute paths, drive-qualified paths, and `..` segments.
- Current writes are whole-object writes. Phase C sidecar writes still rely on filesystem temp-file plus replace behavior outside the adapter.
- Iteration currently returns local `Path` objects, which leaks filesystem assumptions into report helpers.

Primitives missing for S3/MinIO:

- An object identity type that can represent `bucket`, `key`, `size`, `etag`, `last_modified`, `content_type`, and optional metadata without requiring a local `Path`.
- Range reads or streaming reads for MP4/HLS/profile/avatar delivery.
- Content type and metadata on write.
- Conditional create/replace behavior, including expected ETag or generation checks where supported.
- Atomic whole-object publish semantics for sidecars and manifests. S3 has no POSIX rename.
- Multipart upload support for large MP4 and avatar outputs.
- Paginated prefix listing with delimiter support and predictable error mapping.
- Signed URL generation or explicit "not supported" behavior.
- Local materialization helpers for engines that require pathnames.
- Checksum/ETag validation hooks for large downloads and upload verification.

Paths needing local materialization before S3 adoption:

- Source extraction inputs for LibreOffice, PyMuPDF, python-docx, python-pptx, Pillow, and poppler-style renderers.
- ffmpeg inputs and outputs for slide videos, concat, MP4 finalization, HLS packaging, subtitle conversion, moderation frame sampling, and avatar composition.
- OpenCV/Pillow avatar validation and preprocessing inputs/outputs.
- XTTS voice reference WAV files and generated audio when the TTS service expects filesystem paths.
- LivePortrait and MuseTalk source images, source videos, audio inputs, intermediate files, and output MP4s.
- Provider/OCR/moderation paths that currently receive local frame/image paths.
- Lock, temp, part, and current-run scratch files.

Paths that can stream directly once adapter support exists:

- Whole-text JSON sidecars such as `playback_assets.json`, `language_detection.json`, subtitle metadata sidecars, and avatar handoff manifests.
- SRT/VTT subtitle files and translated subtitle tracks.
- Profile/banner/logo processed images when served through app proxy or signed URL.
- MP4/HLS segment/avatar media for delivery only, after range/proxy/signed URL behavior is implemented.
- Report snapshots such as `observability/storage_metrics_snapshot.json` when listing and write semantics are adapter-compatible.

Paths that need signed URLs or app-proxy delivery:

- Published MP4 playback, HLS manifests, HLS segments, HLS encryption keys, subtitles, avatar overlay streams, avatar preview videos, profile assets, and voice/avatar/source uploads.
- Private or teacher-only assets should default to app-proxy token enforcement until direct signed URLs and CDN policy are reviewed.
- HLS encryption keys must remain app-proxy/tokenized unless a DRM/key service explicitly owns key delivery.

Paths that must keep relative DB/JSON values:

- `Job.result_url`, `Job.srt_url`, `TranslatedSubtitleTrack.srt_path`, `TranslatedSubtitleTrack.vtt_path`, `Project.avatar_output_path`, avatar/profile fields on `UserProfile`, voice reference conventions, and all `playback_assets.json` relative path fields.
- Existing sidecars must continue storing storage-root-relative strings such as `<project_id>/playback_assets.json`, `<project_id>/<project_id>.mp4`, `<project_id>/subtitles/en.vtt`, and `avatars/<user_id>/preview/preview.mp4`.
- API responses that currently expose tokenized URLs or relative-path-derived values must not change in the adapter PR.

Object key naming requirements:

- Treat the current normalized relative path as the canonical object key suffix.
- Add only an environment-selected deployment prefix before that suffix, for example `prod/`, `staging/`, or `restore/<drill-id>/`.
- Do not include leading slashes, Windows drive letters, `..`, empty path segments, query strings, fragments, or URL-decoded traversal variants.
- Preserve case and file extensions because content type, token validation, and sidecar references rely on them.
- Keep project/user/job IDs in the same path positions for operator traceability and rollback tooling.
- Use temporary object keys only under a reserved internal prefix such as `.tmp/` and never store them in DB rows or sidecars.

Consistency and atomicity differences:

- Filesystem writes can use parent mkdir plus temp file plus atomic replace on the same volume. S3/MinIO writes are whole-object PUT/COPY operations and cannot atomically rename a temp object into place.
- S3 now provides strong read-after-write consistency for new and overwritten objects in AWS regions, but the adapter contract should still tolerate retryable stale reads for S3-compatible services and proxies.
- Listing is paginated and may be slower or more expensive than filesystem traversal. Report helpers must not assume recursive local walks are cheap.
- Directory existence is virtual. `make_dirs` should be a no-op or marker-object decision, not a required object-storage operation.
- File locks and partial files do not map to object storage. Job coordination must remain in the database, broker, or local worker scratch space.
- Atomic sidecar publish should be defined as "a reader sees the old complete object or the new complete object, never partial bytes." Implementation can use direct whole-object PUT for small JSON, conditional PUT where supported, or temp-key plus copy/promote with cleanup, but readers must not follow temp keys.

Rollback constraints:

- Returning `STORAGE_BACKEND` to filesystem is simple only before S3 writes are enabled for user-visible objects.
- Once S3 receives writes, rollback requires either dual-write evidence, a completed one-way migration with frozen writes, or a reverse copy from bucket prefix back to `STORAGE_ROOT`.
- Mixed state is the main risk: database rows may continue to store relative paths, so a missing object in either backend looks like missing media even if the other backend has it.
- Rollback plans must define the source of truth per phase and prohibit writes to two sources without reconciliation reports.

### S3/MinIO Adapter Contract

Interface expectations:

- Keep relative path inputs and stored values unchanged.
- Reject traversal before building keys.
- Provide whole-object `exists`, `read_bytes`, `write_bytes`, `read_text`, `write_text`, and `delete_file` behavior compatible with the filesystem adapter.
- Add future adapter-neutral operations for object metadata, range reads, streaming reads, signed URLs, local materialization, and prefix listing.
- Map backend-specific errors to stable storage errors: missing object, permission denied, invalid path, transient service failure, timeout, conflict/precondition failed, checksum mismatch, and quota/capacity/exhausted storage where the provider exposes it.

Key mapping:

- `normalize_relative_path("uploads/123/source.pptx")` maps to `<prefix>/uploads/123/source.pptx`.
- `normalize_relative_path("123/playback_assets.json")` maps to `<prefix>/123/playback_assets.json`.
- The bucket name is never stored in the database or sidecars.
- Prefix changes are deployment changes and must be treated like storage migrations.

Bucket and prefix strategy:

- Prefer one private bucket per environment when operations and IAM allow it, for example `visusvision-prod-media` and `visusvision-staging-media`.
- A single bucket with strict prefixes is acceptable for MinIO or constrained deployments only if IAM/policy can isolate prefixes.
- Use prefixes for environment, restore drills, and possibly tenant sharding later. Do not introduce tenant path changes until the current relative-path contract has migration tooling.
- Keep app media separate from backups, database dumps, model caches, and CDN logs.

Content type handling:

- Adapter writes should accept an optional `content_type`.
- Infer a safe fallback from extension only when the caller does not provide one.
- Important mappings include `video/mp4`, `application/vnd.apple.mpegurl` or `application/x-mpegURL` for `.m3u8`, `video/mp2t` for `.ts`, `text/vtt`, `application/x-subrip` or `text/plain` for `.srt`, `application/json`, `image/jpeg`, `image/png`, `audio/wav`, and `audio/mpeg`.
- Never rely on bucket defaults for HLS keys, JSON, subtitles, or media playback.

Metadata handling:

- Allow small immutable metadata such as project ID, job ID, owner user ID, object category, source relative path, content hash/checksum, render ID, and created-by service.
- Do not store secrets, playback tokens, DRM keys, PII beyond already necessary ownership IDs, or raw moderation findings in object metadata.
- Metadata must be optional because not every S3-compatible backend preserves or exposes it the same way.

Private object policy:

- Buckets should be private by default. No public-read ACLs.
- API, worker, TTS, and avatar services get runtime read/write only for the configured media prefix.
- Direct browser access requires short-lived signed URLs or CDN signed URLs/cookies after token policy review.
- Existing tokenized app-proxy behavior remains the compatibility layer until direct signed delivery is proven.

Encryption expectations:

- Require TLS to managed S3 or MinIO unless the endpoint is an explicitly isolated local development network.
- Require server-side encryption at rest for production. Managed S3 can use SSE-S3 or SSE-KMS depending on compliance needs. MinIO must use its production encryption/KMS setup if it is the production object store.
- Client-side encryption is out of scope for the first adapter unless a separate threat model requires it.

Credential and least-privilege requirements:

- Runtime credentials should allow only `GetObject`, `PutObject`, multipart upload actions, `DeleteObject` only after delete workflows exist, and `ListBucket` restricted to the configured prefix.
- Separate credentials are required for backup/restore, lifecycle administration, and operator inspection.
- Root MinIO credentials must not be used by the app outside local development.
- Rotate credentials through the platform secret manager and verify rollback credentials before rollout.

Versioning and lifecycle recommendation:

- Enable versioning for production buckets or prefixes before first S3 writes when cost and compliance allow it.
- Define lifecycle rules separately for temp objects, multipart aborts, generated historical renders, moderation samples, audit/report snapshots, and current user-critical media.
- Configure abort-incomplete-multipart cleanup.
- Do not enable lifecycle deletion for active project media until DB-aware cleanup and retention manifests exist.

Error handling and retry/backoff:

- Missing object maps to the same behavior callers currently expect from missing files.
- Permission errors and invalid credentials are deployment incidents, not silent fallback cases.
- Use bounded retries with jittered exponential backoff for transient network, timeout, throttling, and 5xx errors.
- Do not retry non-idempotent write promotion unless the operation is explicitly idempotent by key and checksum.
- Surface checksum mismatch, partial upload failure, and precondition failure as hard errors for the owning job.

### Local Materialization Contract

Object storage is not a replacement for local scratch space. The adapter must provide a reviewed way to materialize objects for local-file-only engines.

Engines requiring local paths:

- ffmpeg requires local input/output paths for concat, transcode, HLS packaging, subtitle muxing/conversion, moderation frame extraction, and avatar composition.
- Pillow can operate on bytes for simple images, but current profile/avatar code often uses local paths and should be treated as requiring materialization until refactored.
- OpenCV avatar validation and video inspection require local files.
- LibreOffice, PyMuPDF, python-docx, python-pptx, and poppler-style source extraction require local source files and local output directories.
- XTTS currently looks up voice references under `STORAGE_ROOT/voices` and writes audio to local output paths.
- LivePortrait and MuseTalk require local source image/video/audio paths, model paths, scratch paths, and output paths.

Download-to-temp rules:

- Download to a worker-local temp directory, not into the durable `STORAGE_ROOT` namespace.
- Use unique per-job/per-operation directories containing project ID, job ID, and a random suffix.
- Materialize inputs before invoking the engine and upload finished outputs only after validation succeeds.
- Never store temp local paths in DB rows, sidecars, API responses, or playback tokens.

Cleanup expectation:

- Use context-managed temp directories where possible.
- Delete temp files after successful upload and after handled failures.
- On process crash, rely on OS temp cleanup or an operator-reviewed scratch cleanup procedure, not repository cleanup automation.
- Do not delete durable objects as part of temp cleanup.

Size limits and timeouts:

- Define per-category max materialization sizes before enabling S3 writes: source uploads, final MP4, HLS batch, avatar preview, avatar lesson track, voice reference, profile image, subtitle text, and JSON sidecar.
- Apply download, upload, and materialization timeouts separately from engine execution timeouts.
- Large media should use multipart upload/download or streaming copy to disk, not full in-memory reads.

Checksum/ETag validation:

- For small JSON/text sidecars, content length plus read-after-write verification is enough initially.
- For large media, record and verify provider checksum where available. Do not assume ETag is an MD5 for multipart or encrypted objects.
- Use application-level SHA-256 only where the cost is acceptable and the hash is needed for correctness, dedupe, moderation evidence, or avatar/source identity.

Failure behavior:

- If materialization fails, fail the owning operation without writing partial DB state.
- If engine output succeeds locally but upload fails, mark the job failed or retryable according to the existing job contract and keep enough logs to locate local scratch if still present.
- If upload succeeds but DB finalization fails, the object may be an orphan candidate and must be covered by future reconciliation reports.
- Do not silently fall back to filesystem storage after an S3 write path is selected for a job.

### Media Delivery Contract

MP4/private playback:

- Preserve current tokenized playback responses while adding adapter-backed serving underneath.
- App-proxy streaming is the compatibility-first path because it keeps permission checks, heartbeat/session checks, and token validation in Django.
- Direct signed MP4 URLs may be added later for scale, but only if token TTL, range requests, revocation limits, CDN behavior, and private lesson policy are accepted.
- Range requests are required before MP4 delivery can move to S3 for real playback.

HLS manifest, segment, and key strategy:

- Package HLS locally, then upload the manifest, segments, and encryption sidecars as a batch after validation.
- Manifest rewriting must support object-backed manifest bytes and tokenized child references.
- Segments can be app-proxy streamed or signed directly after URL/token policy review.
- HLS encryption keys must remain private and tokenized. They should not be public, cacheable without controls, or exposed through long-lived signed URLs.
- Partial HLS upload must not publish a manifest that references missing segments.

Subtitles:

- SRT/VTT objects remain private by default and are delivered through existing tokenized subtitle URLs or short-lived signed URLs.
- Generated subtitle track DB values must remain relative paths.
- Text content type and UTF-8 encoding should be explicit.

Profile and avatar media:

- Profile assets may use signed URLs or proxy delivery depending on public profile policy and CDN cache requirements.
- Avatar source assets and previews are private teacher-owned media and should default to app-proxy/tokenized access.
- Avatar overlay streams in lessons follow playback policy and should not bypass session/token checks.

Signed URL vs app-proxy decision points:

- Use app-proxy when authorization is dynamic, playback heartbeat/session state matters, protected HLS keys are involved, range behavior is not fully validated, or object keys must stay hidden.
- Use signed URLs when the object is safe for short-lived direct delivery, CDN/range performance is required, and revocation limits are acceptable.
- CDN compatibility requires stable cache keys, content type, cache-control policy, range support, and no leakage of private bucket names or permanent object URLs.

Secure playback compatibility:

- Playback heartbeat, token TTL, watermark policy, visibility lock, MP4 fallback decisions, DRM metadata, HLS encryption requirements, and session binding must be enforced before object delivery.
- Object storage must not introduce public bypass URLs for media that the secure playback contract expects to gate.

### Migration Plan And Rollback

Phased implementation plan:

1. Add an S3/MinIO adapter behind feature flag/env config while keeping `filesystem` as the default and only active production path.
2. Add fake-S3/MinIO integration tests for key mapping, metadata, list pagination, missing objects, overwrites, multipart behavior, and private-object reads.
3. Move additional read paths behind the adapter on the filesystem backend first.
4. Move safe sidecar writes such as JSON/text metadata that do not require range serving or local engines.
5. Move upload writes only after source extraction materialization is implemented and upload DB finalization is transactional enough to avoid missing-object rows.
6. Move render outputs by writing locally first, validating output, then uploading final artifacts and sidecars as a publish batch.
7. Move playback/HLS delivery only after range reads, manifest rewriting, key delivery, CDN/proxy policy, and secure playback compatibility tests pass.
8. Add backup/restore drill evidence for database plus object storage, including restored playback and one rerender from restored source uploads.
9. Add a production rollout checklist with credentials, bucket policy, versioning/lifecycle, monitoring, restore drill IDs, and rollback source-of-truth decision.

Rollback strategy:

- Before any S3 writes: set the backend flag back to filesystem and redeploy.
- During read-only adoption: rollback is code-only because filesystem remains the source of truth.
- During dual-read experiments: filesystem should remain authoritative unless the release ticket explicitly declares otherwise.
- During write adoption: choose one of two models before rollout:
  - Dual-write with reconciliation reports and a clear primary source of truth.
  - One-way migration with a write freeze, object copy, verification manifest, and cutover marker.
- Avoid mixed DB/object state by finalizing DB rows only after object writes verify, and by recording failed publish attempts as job failures rather than partial success.
- A filesystem rollback after S3-only writes requires a reverse sync from the object prefix to `STORAGE_ROOT`, verification against DB/sidecar references, and a freeze or reconciliation window.

### Required Test Matrix

Tests required before implementation is considered safe:

- Unit tests for key mapping, path traversal, slash normalization, URL-decoded traversal, Windows drive paths, reserved temp prefixes, and prefix isolation.
- Adapter compatibility tests for exists/read/write/delete/text/list/missing-object/error behavior shared by filesystem and S3.
- MinIO integration tests for bucket creation assumptions, private policy, credentials, prefix isolation, pagination, content type, metadata, overwrite behavior, multipart upload, and provider errors.
- Upload/download roundtrip tests for source uploads, profile images, avatar source uploads, voice references, and large media.
- Playback sidecar roundtrip tests for `playback_assets.json`, `language_detection.json`, avatar handoff manifests, and subtitle sidecars.
- Signed URL and app-proxy tests for authorization, expiry, cache headers, token mismatch, private objects, and no public bucket bypass.
- Range request tests for MP4 start/end/open-ended ranges, invalid ranges, HEAD behavior if supported, content length, content range, and browser-compatible seeking.
- HLS tests for manifest rewrite, segment delivery, encrypted key delivery, missing segment failure, partial upload protection, and CDN-safe cache headers.
- Local materialization tests for ffmpeg, Pillow, OpenCV, LibreOffice/PyMuPDF extraction, XTTS voice reference reads, LivePortrait inputs, and MuseTalk inputs using temp directories.
- Failure injection tests for timeout, throttling, 5xx, permission denied, missing bucket, bad credentials, checksum mismatch, interrupted multipart upload, stale read, and upload success followed by DB failure.
- Rollback tests for filesystem fallback before S3 writes, read-only rollback, dual-write mismatch detection, reverse-sync manifest validation, and no mixed DB/object state after failed publish.

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

The optional MinIO/S3 adapter integration test is not part of normal CI and must not be enabled by default in shared pipelines. It is a targeted confidence check for `S3StorageAdapter` against a real S3-compatible endpoint only; filesystem remains the default runtime adapter.

## Remaining Risks

- The active application still depends on filesystem paths.
- MinIO/S3 is configured but inactive.
- The adapter abstraction is filesystem-only and currently adopted by low-risk helper/reporting code plus a small set of Phase B read-only sidecar/existence helpers.
- Upload, render, playback, avatar, and TTS paths have not been migrated to the adapter yet.
- There is no implemented quota enforcement.
- There is no implemented project-delete media cleanup.
- There is no implemented orphan deletion workflow.
- There is no cleanup automation for old generated renders.
- Backup and restore are an operator contract, not repository automation.
- Generated artifacts may be hard to reproduce exactly because render, TTS, subtitle, and avatar providers can drift.

## Runtime Adapter Adoption Map

This map is for safe adapter adoption only. This PR is docs/design only and does not change runtime behavior. It keeps every current stored path value and every API response shape unchanged. The first implementation PRs should still use the filesystem adapter behind the existing relative-path contract; S3/MinIO implementation comes later.

| Category | Current code location | Current path format | Read/write/delete behavior | DB/JSON stored value | Adapter migration strategy | S3/MinIO implications | Risk level | Recommended PR phase |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Original uploads | `services/api/core/views.py` upload helpers and project/background upload views; `services/worker/tasks.py` `_latest_lesson_upload_path` | `uploads/<project_id>/...` under `STORAGE_ROOT` | API writes multipart chunks to local files; worker reads local source files for extraction/rerender; no comprehensive media delete | `Project.cover_image_*`, transcript scene/background JSON, render metadata may store relative paths | Introduce adapter writes/exists/read wrappers while continuing to materialize local files for extractors that require paths | Object storage needs temp local materialization for LibreOffice/PyMuPDF/ffmpeg inputs or direct streaming into a scratch file; uploads remain private objects | High | Phase C, then Phase D compatibility |
| Render outputs | `services/worker/tasks.py` `_workspace`, `synthesize_and_render_slide`, `concat_and_finalize`; `services/api/core/views.py` playback/detail serializers | `<project_id>/images`, `audio`, `parts`, `<project_id>.mp4`, `.srt`, `.vtt`, `draft_renders/<token>/...` | Worker writes images/audio/parts/final MP4/SRT/VTT; API reads final outputs for tokenized streaming; older outputs not automatically cleaned | `Job.result_url`, `Job.srt_url`, `playback_assets.json` `mp4_rel_path`, `srt_rel_path`, `vtt_rel_path`, `slides`, `tts_audio`, `final_segments` | Add adapter read/write helpers at sidecar/final-output boundaries first; keep ffmpeg pipeline local until temp-file contract exists | ffmpeg concat/render requires local input/output paths; object storage write should happen after local output finalization or via staged temp file then upload | High | Phase C for writes, Phase D for serving |
| `playback_assets.json` | `services/worker/tasks.py` `_write_playback_sidecar`, `_read_playback_sidecar`; `services/api/core/views.py` `_playback_sidecar_for_job` | `<project_id>/playback_assets.json` | Worker atomically writes via local temp file and replace; API reads JSON; no standalone delete | JSON stores storage-root-relative media paths and playback policy fields | Move read/write to adapter text/json operations, preserving temp-then-replace semantics for filesystem | Object storage has no POSIX rename; adapter needs write-to-temp-key/copy/promote or direct conditional put with whole-object replace | Medium | Phase C |
| HLS sidecars/segments | `services/worker/tasks.py` `_package_hls_assets_for_playback`; `services/scripts/ffmpeg_helpers.py` `package_hls_stream`; `services/api/core/views.py` HLS token rewrite/streaming | `<project_id>/drm/hls/index.m3u8`, `seg_*.ts`, `enc.key` | ffmpeg writes manifest, segments, optional key; API rewrites manifests with tokens and serves manifest/segments/key from local files | `playback_assets.json` `hls.manifest_rel_path`, `segment_glob`, encryption metadata | Keep packaging local; adapter can publish finished manifest/segments as a batch after local packaging, then serving can read through adapter | S3 needs manifest child-path resolution without local `Path.relative_to`; segment delivery can use signed URLs or proxy streaming; encrypted key delivery must stay private/tokenized | High | Phase D, then Phase F |
| Translated subtitles | `services/api/core/subtitle_translation.py`; `services/worker/tasks.py` `generate_translated_subtitle_track_task`; `services/api/core/views.py` track URL/token generation | `<project_id>/subtitles/<language>.srt`, `.vtt` | Worker/API generation writes SRT/VTT files; API reads existence and streams through tokenized subtitle URLs; no cleanup | `TranslatedSubtitleTrack.srt_path`, `vtt_path`, metadata | Move SRT/VTT write/read/existence to adapter first; keep stored relative paths unchanged | Object storage is straightforward for whole-object text reads/writes; public delivery should remain tokenized or signed | Medium | Phase C |
| Profile images | `services/api/core/views.py` `_save_profile_asset`, `UserProfileAssetView` | `profiles/<user_id>/<kind>_original.*`, `<kind>_processed.jpg` | API writes upload, processes via Pillow local file, deletes invalid original on processing error, serves processed image | `UserProfile.banner_image_*`, `logo_image_*` relative paths | Adapter can store original/processed, but processing still needs local temp file or byte-stream Pillow workflow | S3 object write is simple after processing; serving can be proxy streaming or signed URL depending profile privacy | Medium | Phase C for writes, Phase D/F for delivery |
| Avatar source assets | `services/api/core/views.py` `AvatarProfileView`; `services/avatar/preprocess.py`; `services/api/core/avatar_source_validation.py`; `services/api/core/avatar_image_moderation.py` | `avatars/<teacher_id>/uploads/...`, `avatars/<teacher_id>/<source_hash>/...` | API writes uploads; preprocessing reads/writes identity package and reference images/videos; validation/moderation reads local files; no replacement cleanup contract | `UserProfile.avatar_image_original`, `avatar_image_processed`, `avatar_video_original`, `avatar_video_processed`, source hashes/status | Keep avatar source and preprocessing local until avatar pipeline accepts bytes/temp files explicitly; adapter can wrap final relative path existence/hash helpers first | S3 requires temp local copies for OpenCV/Pillow/avatar engines and careful privacy controls for biometric-like media | High | Phase C only after temp-file contract; likely Phase E/F |
| Avatar generated outputs | `services/worker/avatar_preview_flow.py`; `services/worker/tasks.py` avatar segment/lesson overlay paths; `services/avatar/pipeline.py` debug/stage outputs | `avatars/<teacher_id>/preview/preview.mp4`, `preview.wav`, `preview_source.mp3`; `<project_id>/avatar_segments/avatar_*.mp4`; `<project_id>/avatar/avatar_track.mp4`; `projects/<project_id>/renders/<job_id>/avatar_handoff.json` | Worker writes preview audio/video, current-run cleanup of preview scratch, segment MP4s, avatar track, handoff manifests; API serves preview/overlay via tokenized stream | `UserProfile.avatar_preview_video`, `avatar_last_preview_path`, `Project.avatar_output_path`, `AvatarRenderJob.output_path`, playback sidecar avatar fields | Split metadata/sidecar adoption from media bytes; keep engine inputs/outputs local and upload finished artifacts after validation | GPU/avatar tools require local filesystem; S3 needs staged local workspace, upload-on-success, and proxy/signed delivery for private previews | High | Phase C for manifests, Phase D/F for media delivery |
| Moderation frame samples | `services/worker/tasks.py` video frame audit helpers; `services/worker/ai_agents/video_frame_moderation.py`; moderation providers | `moderation/video_frames/<project_id>/<job_id>/frame_*.jpg` | Worker samples frames with ffmpeg to local output; providers read local image paths; existing configurable cleanup can delete successful samples | Agent run/finding metadata stores frame path/location details and audit summary stores counts/status | Do not move first. Later, store retained evidence frames via adapter only after moderation evidence policy and cleanup semantics are explicit | S3 can retain evidence frames, but providers still likely need local temp files; object retention/legal hold policy may matter | High | Not before Phase D/F |
| TTS generated audio | `services/worker/tasks.py` `synthesize_and_render_slide`; `services/scripts/tts_client.py`; `services/tts_service/main.py` and cache helpers | Render audio under `<project_id>/audio/...`; voice references under `voices/<voice_id>.wav`; service/model caches outside or under configured local paths | Worker asks TTS client/service to write output file path; ffmpeg reads local audio; voice upload writes local WAV; caches are local runtime state | `LessonSegment.segment_tts_path`, playback sidecar `tts_audio`, `VoiceProfile.voice_id` maps to `voices/<voice_id>.wav` convention | Keep synthesis output local for ffmpeg; adapter can publish finished audio and wrap voice reference reads after TTS service contract changes | S3 needs either TTS service object read/write support or API/worker temp-file handoff; voice references are private and should not use public URLs | High | Phase C after TTS temp/object contract |
| Storage snapshots/reports | `services/api/core/storage_metrics_snapshot.py`; `services/api/core/storage_retention.py`; `services/api/core/render_recovery_actions.py` | `observability/storage_metrics_snapshot.json`, `audit/render_recovery_actions.jsonl`, report output to stdout | Metrics snapshot uses adapter for JSON write/read; retention report traverses adapter; render recovery audit log still writes direct local JSONL | Snapshot stores operational metrics; audit JSONL stores operator annotations | Keep adapter-backed snapshot/report helpers as Phase A/B pattern; consider adapter append/write helper for audit JSONL separately | S3 list/traversal can be expensive; audit append needs object-append strategy or DB-backed audit if object store lacks append | Low/Medium | Phase A already, Phase B next |
| Temp/lock/part files | `services/api/core/storage_health.py` smoke probes; `services/worker/tasks.py` JSON temp files; avatar/ffmpeg helpers; file locks | `.storage-smoke/*`, `*.tmp`, `*.lock`, `*.part`, temp dirs beside outputs or OS temp | Runtime creates temp files, replaces final files, deletes smoke probes and some current-run scratch; no new cleanup automation planned | Usually not stored; temp names may appear in logs only | Keep local. Adapter should expose explicit atomic write primitives rather than treating temp files as durable objects | S3 has no POSIX locks/rename; multipart upload and conditional put semantics must be designed separately | Medium | Do not migrate as objects; design per operation |

### What Must Stay Local For Now

These runtime paths should not move to object storage until their callers have an explicit temp-file or streaming contract:

- Source extraction and render inputs that LibreOffice, PyMuPDF, OpenCV, Pillow, ffmpeg, LivePortrait, MuseTalk, and XTTS open by pathname.
- HLS packaging outputs while ffmpeg is writing manifests and segments.
- Current-run avatar preview scratch files, audio contract files, and debug/stage reports.
- Moderation frame samples while local providers and OCR read frame paths.
- Lock files, partial files, temp files, and any operation depending on POSIX rename or filesystem locking.
- Any path currently returned to the frontend/API as a storage-root-relative string or tokenized URL payload. Those formats must remain unchanged until a separate API compatibility PR is approved.

## Runtime Migration Strategy

Phase A is already done for low-risk helper boundaries: `FilesystemStorageAdapter` exists, storage smoke checks use it, storage metrics snapshots use it, and report-only retention traversal uses it. These helpers validate adapter shape without touching user media flows.

Phase B has adapter-backed read-only helpers for playback/language sidecar JSON reads, translated-subtitle source-language sidecar reads, and safe relative-path existence checks. Continue moving only read-only/runtime report paths where callers already treat missing or invalid files as a warning. Keep render recovery and retention behavior report-only.

Phase C is partially adopted for the lowest-risk worker JSON sidecar writes: `playback_assets.json` and `language_detection.json` now resolve paths and parent directories through the filesystem adapter while preserving the existing filesystem temp-file replace behavior and JSON bytes. Remaining Phase C candidates include other JSON/text metadata sidecars and reports only after focused review. Media binary writes, translated subtitle SRT/VTT outputs, upload writes, render video/audio outputs, avatar/TTS generation, streaming/range responses, cleanup, and S3/MinIO stay pending.

Phase D should handle playback and media serving compatibility. Token generation can still sign relative paths, but stream responses need an adapter-backed read/range contract. HLS manifest rewriting must work from object bytes as well as local files. MP4 byte ranges, subtitle conversion, avatar overlay delivery, and profile image serving need compatibility tests before any backend switch.

Phase E should implement the S3/MinIO adapter behind the same contract only after Phases B-D prove runtime call sites are adapter-ready on the filesystem backend. This phase must define object key prefixes, credentials, private bucket policy, list semantics, atomic write behavior, multipart behavior, and integration tests.

Phase F should add signed URL or private media delivery if needed. The decision is product/security dependent: the existing tokenized proxy can remain the compatibility layer, while direct signed URLs or CDN delivery can be added later for scale. Protected HLS key delivery and private avatar/profile media require the stricter path.

The next PR should continue Phase C with another narrow JSON/text metadata writer, such as render recovery audit reporting or avatar handoff manifests, only if existing filesystem behavior and stored relative paths can be preserved. Media writes, streaming compatibility, cleanup automation, and S3/MinIO remain separate phases.

## Future Adapter Test Plan

Future adapter adoption should add tests in these groups before a non-filesystem backend is enabled:

- Path traversal tests for adapter `resolve_path`, token rel-path decoding, sidecar child resolution, upload destination normalization, subtitle track paths, profile image paths, avatar paths, and HLS manifest child references.
- Adapter compatibility tests for `exists`, `read_bytes`, `write_bytes`, `read_text`, `write_text`, delete-file behavior, missing-object behavior, listing/traversal behavior, and atomic/whole-object write semantics.
- Fixture-based filesystem behavior tests proving current relative path formats, file contents, and sidecar JSON are unchanged when code is routed through the filesystem adapter.
- Playback asset read/write tests for `playback_assets.json`, `language_detection.json`, HLS sidecar payloads, avatar overlay fields, and draft render prefixes.
- Upload path tests for project source uploads, cover/background/profile image uploads, voice upload, and avatar source upload traversal rejection.
- Render output path tests for slide images, TTS audio, part MP4s, final MP4/SRT/VTT, HLS packaging outputs, avatar segments, and handoff manifests.
- Serving tests for MP4 range requests, subtitle conversion, HLS manifest rewriting, HLS segment/key delivery, avatar overlay stream tokens, and profile image privacy/cache headers.
- S3 fake/MinIO integration tests later, after the adapter exists, covering bucket prefix isolation, private-object reads, object listing pagination, overwrite behavior, missing object mapping, multipart/large object behavior, and signed URL/proxy delivery choices.
