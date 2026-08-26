# Avatar Demo Pack V1

Avatar Demo Pack turns the personal-motion work into a reproducible portfolio
artifact. It renders three variants from the same image, performance video,
audio, script hash, model versions, request, and quality preset:

1. `generic`: no personal motion window and no prosody timeline;
2. `personal`: one deterministic Motion Style V2 window;
3. `prosody`: the same personal base selection plus a Prosody V1 timeline.

The performance video is part of the shared experiment fingerprint, but the
generic renderer does not receive it as a driving source. Generic uses the same
portrait through the configured non-personal template/composer path; personal
and prosody receive the performance video. This isolation is recorded in the
motion execution evidence and is required before the generic baseline is
eligible for comparison.

The GPU renders run strictly in that order. V1 never starts concurrent GPU
renders, which makes it suitable for constrained local GPUs and makes the
recorded memory measurements easier to interpret. This is not a guarantee that
every quality preset or model combination fits a particular GPU.

## Prerequisites

- a consented source portrait;
- the corresponding performance video;
- one final speech audio file used unchanged by all variants;
- an accepted `motion-style-v2` package produced during training;
- LivePortrait, MuseTalk, restoration dependencies, FFmpeg, and optional
  configured strong quality providers available to the avatar worker runtime.

The script deliberately does not synthesize speech three times. Reusing one
audio artifact prevents TTS variation from contaminating the motion comparison.

## Configure

Copy
[`avatar-demo-pack-manifest.example.json`](examples/avatar-demo-pack-manifest.example.json)
to a private directory and replace every placeholder path. Keep real-person
source media and the completed output out of Git.

Important fields:

- `inputs.source_image`: processed portrait used by the renderer;
- `inputs.source_video`: consented performance reference;
- `inputs.audio_path`: final speech audio shared by all variants;
- `inputs.motion_style_path`: accepted Motion Style V2 JSON;
- `inputs.script_path` or `inputs.script_hash`: binds the comparison to one
  script without copying its text into public reports;
- `request`: emotion and motion intensity shared by all variants;
- `model_versions` and `render.quality_preset`: fingerprinted invariants.

The runner refuses to start if a personal interval or a varied prosody timeline
cannot be built. This avoids spending GPU time on three clips that do not
actually represent the promised experiment.

## Run

Run from the repository root with an output directory outside the repository:

```powershell
python services/scripts/build_avatar_demo_pack.py D:\avatar-demo-inputs\manifest.json --output-dir D:\avatar-demo-results\case-001
```

On an RTX 4060, keep other GPU-heavy applications closed and begin with the
same preset that already produces a valid single avatar render. The runner
records total GPU memory used before and during each variant through
`nvidia-smi` when available. It reports the measurement scope rather than
claiming process-exclusive VRAM.

The command returns exit code `2` for an invalid contract or missing runtime
dependency. It still writes diagnostic reports but returns `3` when automated
quality evidence does not recommend `prosody`. Use
`--allow-non-prosody-recommendation` only for investigation, not to turn a
failed comparison into a successful portfolio claim.

Use a new or empty output directory for every run. The runner rejects a
non-empty directory so cached videos cannot accidentally be presented as fresh
runtime or VRAM measurements.

## Output

```text
case-001/
  private-renders/
    generic/
      avatar.mp4
      evidence.json
      motion-plan.json
      render-info.json
    personal/
      ...
    prosody/
      ...
  portfolio/
    comparison.mp4
    avatar-evaluation.json
    avatar-evaluation.md
    demo-pack.json
    portfolio-summary.md
```

`comparison.mp4` is a synchronized, labeled, three-panel H.264 video with the
shared audio and an embedded `AI AVATAR DEMO` disclosure. Individual renders
stay under `private-renders`; the public directory contains no source image,
source-video, or audio paths. Nothing is uploaded or published automatically.

## Evidence rules

- The input fingerprint includes hashes of the portrait, performance video,
  audio, script, model versions, request payload, render-engine switches, and
  quality preset.
- The personal and prosody plans use the same deterministic seed and therefore
  the same base personal interval.
- A planned personal window is counted only when the renderer reports that it
  materialized the `motion_style_v2` source.
- Prosody is counted only when the renderer reports a materialized timeline.
- Every candidate must pass identity, lip-sync, temporal, technical, duration,
  artifact, and non-regression gates before it can be recommended.

## Portfolio claim boundary

The generated report supports claims such as “I built a reproducible
generic-vs-personal-vs-prosody experiment with automated non-regression gates.”
It does not prove human-perceived naturalness or commercial-product parity.
Complete the included randomized blind-review scorecard with at least three
reviewers before making a perceptual improvement claim.
