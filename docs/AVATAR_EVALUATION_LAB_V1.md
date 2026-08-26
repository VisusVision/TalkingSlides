# Avatar Evaluation Lab V1

Avatar Evaluation Lab compares three renders of the same avatar input:

1. `generic`: the existing non-personal motion fallback;
2. `personal`: Motion Planner V2 with a selected personal performance window;
3. `prosody`: personal motion plus a materialized Prosody V1 timeline.

The lab answers a narrow, reproducible question: did the newer motion path add
measured capability without regressing the existing identity, lip-sync,
temporal, duration, or technical checks? It does not claim that automated
scores prove human-perceived naturalness or parity with a commercial product.

## Fair comparison contract

All three variants must use the same source image, audio, script, model
versions, quality preset, and other render inputs. Each variant carries the
same non-empty `input_fingerprint`. The evaluator rejects missing, duplicate,
or mismatched variants rather than producing an unfair ranking.

Change only the motion capability under test:

| Variant | Personal window | Materialized prosody timeline |
| --- | :---: | :---: |
| `generic` | No | No |
| `personal` | Yes | No |
| `prosody` | Yes | Yes |

Use a fixed seed when the engine supports one. Preserve the render manifest,
motion plan, validation output, model versions, and artifact hashes alongside
each video. For a portfolio demo, repeat the suite with multiple scripts and
seeds; do not present a single favorable clip as a benchmark.

## Manifest and evidence

Start from
[`avatar-evaluation-manifest.example.json`](examples/avatar-evaluation-manifest.example.json).
Each variant can point to one pre-collected evidence JSON object:

```json
{
  "id": "case-001-personal",
  "kind": "personal",
  "input_fingerprint": "same-sha256-for-all-three-variants",
  "quality_report": {
    "identity": {"score": 0.91, "passed": true, "assurance": "strong", "provider": "provider-name"},
    "lip_sync": {"score": 0.86, "passed": true, "assurance": "verified", "provider": "provider-name"},
    "temporal": {"score": 0.89, "passed": true, "assurance": "verified", "provider": "provider-name"},
    "technical": {"strict_validation_passed": true, "audio_match": true}
  },
  "motion_validation": {
    "duration_mismatch": false,
    "duration_delta_seconds": 0.03,
    "duration_tolerance_seconds": 0.45,
    "avatar_visual_motion_score": 0.62,
    "quality_checks": {"landmark_stable": true}
  },
  "motion_plan": {
    "personal_window_selected": true,
    "execution": {"prosody_timeline_materialized": false}
  },
  "runtime": {"render_seconds": 12.4, "peak_vram_mb": 5480},
  "artifacts": {"video_path": "artifacts/personal.mp4"}
}
```

Alternatively, provide `video_path`, `audio_path`, and `source_image_path` for
every variant. The CLI then runs the repository's existing render validation
and quality evaluator. If the fingerprint is omitted in this mode, it is
derived from the source-image hash, audio hash, and optional `context.script_hash`.

## Run the lab

After replacing the example paths with collected evidence, run from the
repository root:

```powershell
python services/scripts/evaluate_avatar_variants.py docs/examples/avatar-evaluation-manifest.example.json --output-dir artifacts/evaluation/case-001
```

The command writes deterministic `avatar-evaluation.json` and
`avatar-evaluation.md` reports. CI or a release job can make regressions fatal:

```powershell
python services/scripts/evaluate_avatar_variants.py manifest.json --fail-on-regression --require-recommendation prosody
```

Exit code `2` means the comparison contract or an input file is invalid, `3`
means a regression was found, and `4` means the required recommendation was
not reached.

## Recommendation policy

The evaluator starts with `generic` and promotes a candidate only when:

- technical validation, audio match, duration, artifact, landmark, and all
  three quality signals are available and acceptable;
- the motion plan contains the capability expected for that variant;
- identity, lip-sync, temporal, aggregate quality, duration, artifact, and
  technical non-regression checks pass.

JSON deltas retain exact candidate-minus-baseline values. Positive duration,
render-time, and VRAM deltas mean the candidate used more of that quantity;
they are measurements, not automatic quality wins.

## Human review

Automated metrics are only the first gate. Randomize and hide variant names,
use at least three reviewers, and score every clip from 1 to 5 on:

- identity consistency;
- lip-sync naturalness;
- head and expression naturalness;
- transition smoothness;
- emotion and emphasis fit.

Keep reviewer-level results and report the number of cases, reviewers,
preference rate, central tendency, and uncertainty. Do not replace an
unfavorable manual result with the automated recommendation.

## Known limits

- Motion proxy scores do not prove that a person perceives the motion as more
  natural or more like the subject.
- Heuristic identity and lip-sync providers are not biometric-grade evidence.
- V1 compares already-produced artifacts; it does not schedule all three
  renders itself. `AVATAR_DEMO_PACK_V1.md` supplies the local sequential runner
  and comparison artifact layer.
- The report supports an honest engineering claim about reproducibility and
  non-regression, not a HeyGen-equivalence claim.
