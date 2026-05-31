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

## Failed Jobs and Retries

When a job fails:

1. Identify project ID and job ID.
2. Check API logs for request/enqueue errors.
3. Check worker logs for task failure.
4. Check TTS logs if failure occurred during synthesis.
5. Check storage path availability if files are missing.
6. Retry only after the cause is understood.

If retry endpoints are enabled, use idempotency/request IDs. See [PROMETHEUS_AND_RETRY_RUNBOOK.md](PROMETHEUS_AND_RETRY_RUNBOOK.md).

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
