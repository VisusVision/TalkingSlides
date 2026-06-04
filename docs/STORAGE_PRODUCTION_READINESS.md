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

Phase B should adopt read-only/runtime report paths. Move existence/read helpers for sidecars, storage snapshots, and safe report reads behind the adapter where the caller already treats missing or invalid files as a warning. Keep render recovery and retention behavior report-only.

Phase C should adopt write paths that can still write to the filesystem adapter. Good candidates are JSON/text sidecars, translated subtitle files, profile image final writes after local processing, and final render metadata writes. The PR must preserve current relative path strings and should not introduce S3 code.

Phase D should handle playback and media serving compatibility. Token generation can still sign relative paths, but stream responses need an adapter-backed read/range contract. HLS manifest rewriting must work from object bytes as well as local files. MP4 byte ranges, subtitle conversion, avatar overlay delivery, and profile image serving need compatibility tests before any backend switch.

Phase E should implement the S3/MinIO adapter behind the same contract only after Phases B-D prove runtime call sites are adapter-ready on the filesystem backend. This phase must define object key prefixes, credentials, private bucket policy, list semantics, atomic write behavior, multipart behavior, and integration tests.

Phase F should add signed URL or private media delivery if needed. The decision is product/security dependent: the existing tokenized proxy can remain the compatibility layer, while direct signed URLs or CDN delivery can be added later for scale. Protected HLS key delivery and private avatar/profile media require the stricter path.

The next PR should be Phase B: adapter-backed read-only sidecar/report path helpers plus tests, with no media writes moved and no S3 implementation.

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
