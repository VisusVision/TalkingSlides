# Digital Twin V2 Core

This package is the model-independent application core for a verified personal
digital twin. It separates lifecycle policy from GPU implementation:

- `domain.py`: immutable API/domain objects and lifecycle states.
- `ports.py`: model, storage, quality, provenance, and audit interfaces.
- `orchestrator.py`: consent-gated training and render DAG.
- `quality.py`: production acceptance thresholds.
- `motion_analysis.py`: sampled face landmarks, head pose, gaze, blink,
  expression and motion coverage for the versioned `motion-style-v2` package.

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
