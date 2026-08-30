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
- `demo_pack.py`: sequential generic/personal/prosody renders, GPU measurement,
  and a separated public portfolio bundle for `avatar-demo-pack-v1`.

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

Avatar Demo Pack V1 drives the real local render boundary three times with one
fingerprinted input contract, then creates a labeled and watermarked comparison
video and runs the evaluation lab. It keeps source paths and individual renders
out of the public artifact directory. See `docs/AVATAR_DEMO_PACK_V1.md`.

## Strong local identity metric

Render quality can use OpenCV YuNet face detection and aligned SFace embeddings
instead of the legacy appearance-correlation heuristic. Install the pinned,
checksum-verified model pack into private storage:

```powershell
python services/scripts/install_avatar_identity_models.py
```

Then configure both model paths:

```text
DIGITAL_TWIN_YUNET_MODEL_PATH=/app/storage_local/models/identity/face_detection_yunet_2023mar.onnx
DIGITAL_TWIN_SFACE_MODEL_PATH=/app/storage_local/models/identity/face_recognition_sface_2021dec.onnx
```

The provider samples the rendered video, aligns faces using five landmarks,
and gates on the tenth-percentile cosine similarity plus minimum face coverage.
Its default cosine threshold (`0.363`) follows the upstream OpenCV SFace
reference. Model hashes, source revision, OpenCV version, frame coverage, and
score distribution are persisted in the quality signal. Configuring only one
model, using modified weights, or providing insufficient face evidence fails
closed. The metric measures render identity consistency; it is not liveness,
consent, or authentication evidence.

Run the metric against existing renders without re-rendering:

```powershell
python services/scripts/evaluate_avatar_identity.py source.png generic.mp4 personal.mp4 prosody.mp4
```

## Strong local lip-sync metric

The avatar worker contains a pinned ByteDance LatentSync checkout and can run
its SyncNet evaluator as a release gate. Install the checksum-pinned 54 MB
evaluation checkpoint into private storage:

```powershell
python services/scripts/install_avatar_syncnet_model.py
```

Configure the model after rebuilding the avatar worker image:

```text
DIGITAL_TWIN_SYNCNET_HOME=/opt/latentsync
DIGITAL_TWIN_SYNCNET_MODEL_PATH=/app/storage_local/models/syncnet/syncnet_v2.model
```

The installer also checksum-verifies the S3FD face detector in the worker's
Torch cache. The provider remuxes the rendered video with the audio requested
by the render, then records SyncNet confidence and the best audio/video offset.
The default locally calibrated gate requires confidence of at least `2.0` and
an absolute offset no greater than two 25 FPS frames (80 ms). Checkpoint
checksum, code and model revisions, license metadata, raw confidence, and
offset evidence are persisted. Missing runtime/model files, unverifiable
revisions, malformed provider output, timeouts, low confidence, and excessive
offset all fail closed. This metric is an automated synchronization signal;
perceived naturalness still requires human review.

The normalized `score` is a threshold-relative quality value, not a
probability: `confidence / (confidence + minimum_confidence / 4)`. Therefore
the configured confidence boundary maps to `0.8`, while the raw upstream value
is always retained for comparison and recalibration.

Evaluate existing renders without generating them again:

```powershell
python services/scripts/evaluate_avatar_lipsync.py speech.wav generic.mp4 personal.mp4 prosody.mp4
```
