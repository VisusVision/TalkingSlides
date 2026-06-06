# Prometheus Metrics and Retry Runbook

## Prometheus metrics endpoint

- Endpoint: `/api/v1/system/metrics/prometheus/`
- Access policy:
  - `staff/superuser` authenticated users can access.
  - Non-staff callers must send `X-Metrics-Token: <PROMETHEUS_METRICS_TOKEN>`.
  - If neither condition is met, API returns `401`.

## Token configuration

- Environment variable: `PROMETHEUS_METRICS_TOKEN`
- Keep it non-empty and high-entropy in production.
- If empty, only staff-authenticated access works.

## Production example

```bash
PROMETHEUS_METRICS_TOKEN="replace-with-strong-random-token"
```

## Scrape config note

- Prefer token-based scrape with `X-Metrics-Token` header injected by ingress/reverse-proxy (or a sidecar).
- Direct Prometheus custom header injection support depends on your Prometheus version and deployment pattern.
- Example scrape target through a protected proxy endpoint:

```yaml
scrape_configs:
  - job_name: visus-api
    metrics_path: /api/v1/system/metrics/prometheus/
    static_configs:
      - targets: ["metrics-proxy.internal.example.com:443"]
    scheme: https
```

- Production safety: do not leave `PROMETHEUS_METRICS_TOKEN` empty unless endpoint access is strictly staff-only on a private network boundary.

## Worker reliability foundation

- Celery workers use late ACK semantics by default (`CELERY_TASK_ACKS_LATE=true`) so a task is acknowledged after execution rather than when it is reserved.
- `CELERY_TASK_REJECT_ON_WORKER_LOST=true` asks Celery to requeue work when a worker process disappears. This improves crash visibility but requires task paths to stay idempotent before broader retries are enabled.
- Redis broker visibility is controlled by `CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS` and defaults to 12 hours. Keep it longer than the longest expected render/avatar task to avoid duplicate in-flight delivery.
- Global task limits are available as `CELERY_TASK_SOFT_TIME_LIMIT` and `CELERY_TASK_TIME_LIMIT`. They default to `0`/disabled to avoid changing current render behavior; set them per environment after staging measurements.
- `CELERY_RESULT_EXPIRES` defaults to 24 hours to keep result backend growth bounded.
- Current retry behavior remains intentionally limited. This foundation does not rewrite task retries, attach retry-storm guards, or change chord/render flow.
- Stuck jobs should be investigated through Job rows that remain `pending` or `running` longer than expected, worker logs keyed by project/job id, and Prometheus worker failure/retry/duration counters. Automated sweeping/reconciliation and DLQ/quarantine are future hardening items.

## Render job-scoped update deployment note

- Render finalization now carries an explicit render job id through Celery chord callbacks when dispatched by the updated worker.
- Mixed rolling deployments with old and new render workers are risky because old workers may not accept the newer callback argument shape.
- Drain in-flight render workers before deploying this change, then restart all render/avatar workers together so queued callbacks and consumers use the same task signatures.
- Existing queued tasks that do not include `job_id` still fall back to the legacy latest-project-job update path.

## Retry endpoint behavior

- Endpoint: `POST /api/v1/projects/<project_id>/jobs/<job_id>/retry/`
- Retry allowed statuses:
  - `failed`
  - `cancelled`
- Retry rejected statuses:
  - `pending`
  - `running`
  - `done`

## Idempotency and request_id

- `request_id` is required for retry safety.
- Accepted from request body `request_id` (or `retry_request_id`) and `Idempotency-Key` header.
- Same `request_id` for the same project/job type returns idempotent replay response (`200`) instead of creating duplicate jobs.
- New retry creates a new job and returns `202`.

## Edge cases

- Original lesson source file missing: returns `400`.
- Caller not project owner/staff: returns `403`.
- Job not found: returns `404`.
- Queue admission guard can reject under saturation with `429` and retry hints.

## CI observability artifacts

For pipeline debugging and regression triage:

- Backend test runs publish a JUnit XML artifact named `backend-pytest-junit` containing `pytest-report.xml`.
- Frontend e2e runs publish `frontend-playwright-report` when Playwright output exists.
- Artifact absence with `if-no-files-found: ignore` is non-fatal by design and should not be interpreted as a successful test pass.

Use artifacts as the first source of truth before manual reruns.
