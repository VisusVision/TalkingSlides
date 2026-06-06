# Highlight System Plan

## Goal

Build a production-grade highlight system for Studio and final renders that is fast in preview, deterministic in render, observable in operations, and safe to evolve.

## Current Baseline (May 15, 2026)

- `style`: `none | box | bold`
- `detector`: `auto`
- Studio supports per-page highlight settings and preview.
- Preview image generation exists in API (`/highlight-preview/`) and image fetch endpoint (`/highlight-preview-image/`).
- Renderer logic exists in `services/api/core/highlight_engine.py` (and duplicated in `services/worker/highlight_engine.py`).
- Integration tests exist for basic scene/highlight behavior in `tests/integration/test_scene_backgrounds.py`.

## Known Gaps To Fix First

- Engine duplication between API and worker can drift.
- No explicit versioned highlight spec persisted per scene.
- Preview and final render are not clearly separated as contracts/SLOs.
- Limited detector and style set.
- No dedicated metrics dashboard for highlight latency/fallback/error rates.

## Architecture Direction

1. `Highlight Spec` (source of truth)
- Persist normalized spec per scene:
  - `enabled: bool`
  - `style: none|box|bold|...`
  - `detector: auto|...`
  - `target: block|line|word` (future)
  - `version: "v1"`

2. `Highlight Engine` as shared module
- Move engine to one shared location (for API preview + worker render).
- API preview and worker render must use same engine API.

3. `Pipeline split`
- Preview path: low-latency, fail-open, draft-safe.
- Render path: deterministic, retriable, job-metadata logged.

4. `Artifact strategy`
- Preview artifacts: short TTL, non-public, per-project scoped.
- Final artifacts: tied to render job output and traceable by metadata.

## Delivery Phases

## Phase H0: Stabilize (1 sprint)

Scope:
- Remove engine duplication (single shared engine module).
- Keep current features (`none/box/bold`, `auto` detector).
- Harden preview fetch/auth behavior.

Acceptance:
- Same input spec gives same preview/result across API and worker paths.
- No preview 500s for valid owner flow.
- No stale image behavior after style changes.

## Phase H1: Contract + Persisted Spec (1 sprint)

Scope:
- Introduce normalized `highlight_spec` shape in scene document.
- Add validation helpers and migration compatibility from current fields.
- Explicit API response includes effective spec and engine version.

Acceptance:
- Backward compatibility with existing `highlight_style`, `highlight_enabled`, `highlight_detector`.
- Scene PATCH + preview + refetch preserve exact spec.

## Phase H2: Render Integration (1-2 sprints)

Scope:
- Apply highlight spec during final rerender path (not only preview).
- Persist job metadata:
  - detector used
  - style used
  - fallback used
  - latency ms
  - engine version

Acceptance:
- `Save + Rerender` produces output matching preview semantics.
- Metadata visible in job detail/status payload.

## Phase H3: Quality + Scale (ongoing)

Scope:
- Add detectors (line/word regioning), more styles, confidence/fallback policy.
- Add metrics, alerting, load tests, and regression image tests.

Acceptance:
- p95 preview latency target and fallback/error SLOs are defined and tracked.
- Golden image tests protect visual regressions.

## API & Data Contract (Target)

Scene highlight payload (target normalized form):

```json
{
  "highlight": {
    "enabled": true,
    "style": "box",
    "detector": "auto",
    "target": "block",
    "version": "v1"
  }
}
```

Preview response additions:

```json
{
  "success": true,
  "preview_image_url": "...",
  "fallback_used": false,
  "detector_used": "auto",
  "engine_version": "highlight-v1",
  "latency_ms": 42.5
}
```

## Testing Strategy

1. Unit
- Spec validation/normalization.
- Engine style renderers.
- Detector output shape and boundary checks.

2. Integration
- Owner auth + draft/public page flows.
- Preview generation and image serving.
- PATCH -> preview -> refetch consistency.

3. Visual regression
- Golden fixtures for `none/box/bold`.
- Deterministic input image/text set.

4. Failure tests
- Missing source image.
- Timeout path.
- Unsupported style/detector.

## Observability

Add structured logs and metrics:

- `highlight_preview_requests_total{status,style,detector}`
- `highlight_preview_latency_ms`
- `highlight_preview_fallback_total{reason}`
- `highlight_render_latency_ms`
- `highlight_render_failures_total{reason}`

## Rollout Plan

1. Feature flag `HIGHLIGHT_ENGINE_V1_ENABLED` (default on in local/dev).
2. Enable for internal Studio users first.
3. Watch fallback/error/latency for 1 week.
4. Enable for all Studio projects.
5. Enable render-path enforcement after preview metrics stabilize.

## Immediate Implementation Backlog (Start Now)

1. Create shared engine module and import from both API and worker.
2. Add engine version string and include in preview response.
3. Add normalized highlight spec helper in API scene serializer/path.
4. Add integration tests for style switch (`box -> bold`) with fresh preview artifact assertion.
5. Add basic metrics counters/timers around preview endpoint.

## File Ownership Suggestion

- API contract + scene persistence:
  - `services/api/core/views.py`
  - `services/api/core/serializers.py`
  - `services/api/core/urls.py`
- Engine:
  - `services/api/core/highlight_engine.py` (or new shared module path)
  - `services/worker/highlight_engine.py` (remove or re-export)
- Frontend Studio:
  - `services/frontend/src/pages/Studio.jsx`
  - `services/frontend/src/api.js`
- Tests:
  - `tests/integration/test_scene_backgrounds.py`

