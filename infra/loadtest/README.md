# Load Test Quickstart (k6)

## Prerequisites
- k6 installed
- At least one teacher token and project IDs

## Example run

```powershell
$env:BASE_URL = "http://localhost:8000"
$env:TOKEN = "<teacher-token>"
$env:PROJECT_IDS = "1,2,3,4"
$env:RENDER_PROFILE = "fast"
k6 run infra/loadtest/k6-rerender.js
```

## Notes
- Script intentionally accepts `202` and `429` as healthy behavior under load.
- `429` means backpressure policy is protecting system latency.
