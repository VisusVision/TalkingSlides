# Release Checklist

Use this checklist for staging and production releases. It is intentionally operational and should be copied into a release ticket when needed.

Release policy, versioning, tag naming, approval, and GitHub Release steps are defined in [RELEASE_PROCESS.md](RELEASE_PROCESS.md). The release tag is the source of truth for a shipped product version, and [../CHANGELOG.md](../CHANGELOG.md) must contain the matching version entry before tagging.

## 1. Branch Status

- Confirm the branch is current with the intended base branch.
- Confirm no unrelated files are modified.
- Confirm no real `.env`, database, `storage_local`, generated media, `node_modules`, or `dist` files are staged.
- Confirm the target profile is selected: `staging`, `secure_stream`, or `drm_protected`.
- Confirm the release branch name, target version, release owner, and approver are recorded in the release ticket.
- Confirm the changelog entry matches the intended release tag.

```powershell
git status --short
git log --oneline -5
```

## 2. Tests

Run the fast checks first:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_ready_endpoint.py -q
.\.venv\Scripts\python.exe -m pytest tests\integration\test_secure_playback.py -q
.\.venv\Scripts\python.exe -m pytest tests\integration\test_lesson_publishing_flow.py -q
```

Add feature-specific tests for the release. For avatar changes, include avatar overlay/non-blocking tests. For moderation changes, include the moderation integration subset.

## 3. Migrations

```powershell
cd services\api
..\..\.venv\Scripts\python.exe manage.py check
..\..\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
cd ..\..
```

Before production traffic, run migrations as a release job:

```powershell
python manage.py migrate --noinput
```

## 4. Frontend Build

```powershell
cd services\frontend
npm ci
npm run build
cd ..\..
```

Review build warnings. Existing chunk-size warnings are not release blockers by themselves, but new build errors are.

## 5. Docker Build

Build the images expected for the target environment:

```powershell
docker compose -f infra\docker-compose.yml build api frontend worker tts_service
```

If testing avatar worker on a GPU host:

```powershell
docker compose -f infra\docker-compose.yml build worker-avatar
```

## 6. Environment Validation

Validate the env file for the target profile. The checker does not print secret values and does not modify files.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check-production-env.ps1 -EnvFile infra\env.staging.example -Profile staging
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check-production-env.ps1 -EnvFile infra\env.production-secure-stream.example -Profile secure_stream
```

For production, validate the real deployment environment through the platform secret/config view, not by committing a real env file.

Run the storage smoke check against the mounted production media root before serving traffic:

```powershell
cd services\api
python manage.py storage_smoke_check
cd ..\..
```

## 7. Backup and Rollback

- Confirm latest Postgres backup completed.
- Confirm media storage backup or snapshot policy is healthy.
- Confirm previous image/release artifact is available.
- Confirm rollback command or platform rollback process.
- Confirm worker rollback/drain plan if task payloads changed.
- Record the exact git SHA and image tag.
- Record whether migrations are backward-compatible and whether rollback requires database restore.

## 8. Pre-deploy Smoke

Staging smoke:

- API readiness: `/api/v1/ready/`
- frontend loads
- login works
- upload small source file
- render job reaches done
- catalog visibility matches publish state
- playback token returns a base video URL

## 9. Playback Smoke

For `secure_stream`:

- published lesson plays
- tokenized media URL works
- HLS manifest exists when packaging is expected
- MP4 fallback follows policy
- watermark appears when enabled
- heartbeat/session lock does not produce false positives
- a deliberate second-session test returns the expected 409/session behavior

For `drm_protected` later:

- no MP4 fallback
- DRM metadata is present
- license server is reachable
- browser/player matrix is verified
- missing DRM config fails closed with a clear message

## 10. Avatar Smoke Optional

Run only on a validated GPU environment:

- avatar source upload and validation
- avatar preview status reaches ready or a clear failure state
- base lesson render completes without waiting for avatar
- avatar overlay job queues separately
- avatar failure does not block playback
- avatar-only rerender does not create a new base render job

## 11. HLS Smoke

- Confirm `playback_assets.json` includes HLS metadata when expected.
- Confirm tokenized manifest URL returns a manifest.
- Confirm segment URLs are tokenized.
- Confirm fallback behavior for missing manifest.
- Confirm `drm_protected` does not serve MP4 fallback.

## 12. Production Deploy Steps

1. Freeze release SHA and image tags.
2. Validate env/profile.
3. Build and push images.
4. Run database migrations.
5. Deploy API.
6. Deploy render worker.
7. Deploy TTS service.
8. Deploy frontend.
9. Deploy avatar worker only if GPU profile is enabled.
10. Run post-deploy smoke.
11. Watch logs and metrics.
12. Create or finalize the GitHub Release only after production approval and smoke evidence are recorded.

## 13. Post-deploy Monitoring

Watch:

- API 5xx and 4xx spikes
- render queue depth
- avatar queue depth
- failed jobs
- Redis connectivity
- Postgres connections and slow queries
- TTS readiness and latency
- storage capacity
- playback 403/404/409 rates
- HLS manifest/segment errors

Keep the release owner available until smoke and metrics are stable.

## 14. CI Reliability Notes

- CI runs are branch-concurrent; newer pushes cancel older in-progress runs on the same branch.
- Job-level timeouts are expected safeguards. Timeout failures should be triaged as capacity/flakiness signals first.
- For backend test failures, inspect the `backend-pytest-junit` artifact (`pytest-report.xml`) before rerun.
- For e2e failures, inspect `frontend-playwright-report` artifact when available.
- Flaky rerun approach: rerun the failed job once after artifact triage; repeated failure should be treated as deterministic and fixed before release.
