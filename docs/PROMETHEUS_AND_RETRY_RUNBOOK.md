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
