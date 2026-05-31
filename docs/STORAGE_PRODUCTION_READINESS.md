# Storage Production Readiness

VISUS currently uses filesystem-backed runtime storage. `STORAGE_ROOT` is the shared media root for API, render worker, TTS service, and avatar worker. MinIO/S3 environment variables exist for local/future planning, but the application does not yet use a real S3 adapter.

## Current Storage Map

| Area | Location |
| --- | --- |
| Source uploads | `STORAGE_ROOT/uploads/<project_id>/` |
| Render outputs | `STORAGE_ROOT/<project_id>/` |
| Playback sidecars | `STORAGE_ROOT/<project_id>/playback_assets.json` |
| Language detection sidecar | `STORAGE_ROOT/<project_id>/language_detection.json` |
| HLS assets | `STORAGE_ROOT/<project_id>/drm/hls/` |
| Translated subtitles | `STORAGE_ROOT/<project_id>/subtitles/` |
| Profile images | `STORAGE_ROOT/profiles/<user_id>/` |
| Avatar source/preview/output | `STORAGE_ROOT/avatars/` and project render folders |
| Video frame audit samples | `STORAGE_ROOT/moderation/video_frames/` |
| TTS cache/audio | `TTS_AUDIO_DIR`, normally under shared storage for containers |

The API also defines Django `MEDIA_ROOT`, but generated lesson media and playback assets use `STORAGE_ROOT`.

## Production Validation

When `DEBUG=False`, Django now validates `STORAGE_ROOT` during settings import:

- `STORAGE_ROOT` must be explicitly configured.
- It must be an absolute path.
- It must already exist.
- It must be a directory.
- It must be readable and writable by the app process.

This catches missing mounts and read-only storage before the API or workers accept traffic.

## Smoke Check

Run this before deploys, after volume changes, and during incident triage:

```powershell
cd services\api
python manage.py storage_smoke_check
```

The command writes, reads, and deletes a small probe file under `STORAGE_ROOT/.storage-smoke/`. It does not touch project media.

To check a specific mount:

```powershell
python manage.py storage_smoke_check --storage-root /mnt/visus-media
```

## Lifecycle And Cleanup

Current cleanup is limited and feature-specific:

- Successful video frame audit samples can be deleted after the audit.
- Avatar preview runs clear current-run preview artifacts.
- Project deletion does not provide a comprehensive storage cleanup contract.
- Render output retention is not globally enforced.

Production operators must define a retention policy before broad usage:

- Keep source uploads at least as long as rerender/recovery is required.
- Keep latest ready render assets for every published lesson.
- Keep older render outputs only for the rollback window.
- Keep audit samples according to moderation and compliance policy.
- Alert before disk/object storage reaches capacity.

Do not manually delete active project folders while jobs are pending or running.

## Backup Policy

Backups must cover both database rows and media files. Restoring only Postgres or only storage can leave projects pointing to missing assets.

Minimum production policy:

- Back up Postgres and `STORAGE_ROOT` on independent schedules.
- Record backup time, storage path/snapshot id, and database backup id together.
- Test restore into staging before relying on the policy.
- Keep media backup retention aligned with legal and product retention requirements.
- Confirm backups before destructive cleanup or project deletion tooling.

## MinIO / S3 Status

MinIO is present in local Docker Compose and S3-style env vars are documented, but active code reads and writes local filesystem paths. Moving to MinIO/S3 requires a storage adapter and path contract work across API, worker, TTS, and avatar flows.

If MinIO/S3 is adopted later, production design must include:

- Least-privilege app credentials.
- TLS/encryption in transit.
- Encryption at rest and key management.
- Versioning or object lock where retention policy requires it.
- Replication or backup strategy.
- Bucket lifecycle rules.
- Access logs and operational metrics.

MinIO's own security guidance emphasizes least privilege, object locking/versioning, encryption at rest, TLS, identity providers, and replication as core production controls.

## Remaining Risks

- No global quota enforcement.
- No automatic cleanup for old render outputs.
- No project-delete asset cleanup contract.
- No active S3/MinIO adapter.
- No storage capacity metric emitted by the app.
- No orphan media reconciliation job.
