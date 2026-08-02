# Digital Twin V2 Core

This package is the model-independent application core for a verified personal
digital twin. It separates lifecycle policy from GPU implementation:

- `domain.py`: immutable API/domain objects and lifecycle states.
- `ports.py`: model, storage, quality, provenance, and audit interfaces.
- `orchestrator.py`: consent-gated training and render DAG.
- `quality.py`: production acceptance thresholds.

The current LivePortrait + MuseTalk pipeline should be connected through a
`PortraitRenderer` adapter. A future video-reference diffusion model connects
through the same port. Full-body generation is intentionally a separate
`BodyRenderer`; it must never be silently substituted with a talking-head
renderer.

This directory does not own Django persistence, Celery dispatch, or model
loading. Those belong in adapters so pure domain tests remain fast and the same
orchestrator can run locally, on GPU workers, or against a provider.
