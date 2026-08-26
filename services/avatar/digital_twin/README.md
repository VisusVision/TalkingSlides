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
- `prosody.py`: lightweight PCM energy, speech-activity, pause, and emphasis
  analysis for versioned `prosody-v1` render timing.
- `evaluation.py`: fair-input comparison, non-regression checks, numeric
  deltas, and blind-review scaffolding for `avatar-evaluation-v1`.

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

Prosody-Aware Motion Timing V1 analyzes the synthesized speech after TTS and
adds a bounded personal performance timeline to the motion plan. LivePortrait
materializes the calm/natural/expressive source intervals as a duration-matched
crossfaded driving montage. The audio analyzer and montage are fail-open: when
decoding, timing validation, or montage generation fails, the single-window
Motion Planner V2 path remains active and the fallback reason is persisted.

Avatar Evaluation Lab V1 compares generic, personal, and prosody render
evidence under one input fingerprint. It writes stable JSON and Markdown,
rejects unsupported recommendations, and leaves perceptual naturalness claims
to a blind human review. See `docs/AVATAR_EVALUATION_LAB_V1.md` for the capture
contract and CLI workflow.
