# Digital Twin V2 Core

This package is the model-independent application core for a verified personal
digital twin. It separates lifecycle policy from GPU implementation:

- `domain.py`: immutable API/domain objects and lifecycle states.
- `ports.py`: model, storage, quality, provenance, and audit interfaces.
- `orchestrator.py`: consent-gated training and render DAG.
- `quality.py`: production acceptance thresholds.
- `motion_analysis.py`: sampled face landmarks, head pose, gaze, blink,
  expression and motion coverage for the versioned `motion-style-v2` package.
- `motion_planning.py`: deterministic emotion/intensity routing to a personal
  calm, natural, or expressive interval with a safe legacy-window fallback.

The current LivePortrait + MuseTalk pipeline should be connected through a
`PortraitRenderer` adapter. A future video-reference diffusion model connects
through the same port. Full-body generation is intentionally a separate
`BodyRenderer`; it must never be silently substituted with a talking-head
renderer.

This directory does not own Django persistence, Celery dispatch, or model
loading. Those belong in adapters so pure domain tests remain fast and the same
orchestrator can run locally, on GPU workers, or against a provider.

The motion analyzer uses the worker's optional `face-alignment` model and
OpenCV. If landmark inference is unavailable it emits a truthful `limited`
report, retaining face-presence coverage and diagnostics instead of inventing
behavioral signals. Run it directly during capture/debugging with:

```powershell
python services/scripts/analyze_performance_video.py path/to/performance.mp4 --output motion-style.json
```

Digital Twin renders consume the resulting profile as `motion-plan-v2`. The
selected interval, seed, style, renderer preset, fallback reason, and executed
LivePortrait window are persisted on the render and in its engine trace. This
makes repeated renders reproducible and prevents cache reuse across different
personal motion windows.
