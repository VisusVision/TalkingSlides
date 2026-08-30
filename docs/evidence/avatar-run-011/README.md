# Avatar Run 011 - Strict Model-Verified Evidence

Run 011 is a real local RTX 4060 experiment produced from one consented input
contract. It rendered `generic`, `personal`, and `prosody` variants sequentially
with the same portrait, speech audio, script hash, quality preset, and model
versions. The run used the V2 portfolio contract merged in PR #236.

## Result

- Automated recommendation: `prosody`
- Fair comparison contract: passed
- Strong YuNet/SFace identity gate: passed for all variants
- Strong LatentSync SyncNet gate: passed for all variants
- Technical and temporal gates: passed for all variants
- Model-verified identity and lip sync: true
- Quality regressions: none

| Variant | Automated quality | Identity P10 | Face coverage | SyncNet confidence | AV offset | Render time | Peak total VRAM | Eligible |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| Generic | 90.28 | 0.7342 | 24/24 | 2.34 | 0 ms | 230.639 s | 7871 MB | yes |
| Personal | 89.92 | 0.7085 | 24/24 | 2.44 | +40 ms | 262.601 s | 6134 MB | yes |
| Prosody | 90.75 | 0.7393 | 24/24 | 2.60 | +40 ms | 254.936 s | 7848 MB | yes |

Personal versus generic changed automated quality by `-0.36` points without
crossing any regression threshold. Prosody versus personal improved automated
quality by `+0.83` points, identity by `+0.0308`, and normalized lip sync by
`+0.0088`. The resulting recommendation is therefore `prosody`.

## Reproducibility

- Code revision: `d229184be6016907cb73f3e00ed1f319ce35ff84`
- Evaluation contract: `avatar-evaluation-v2`
- Demo contract: `avatar-demo-pack-v2`
- Execution: sequential, one GPU render at a time
- GPU: NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB
- Evaluation JSON SHA-256:
  `107263b22cdf8fc5d9e8c51c08e6bec520ef326e1ea763e89b291c396aee1dd0`
- Private comparison video SHA-256:
  `72edd6cfb64f660bce8731eda50db23fb7af142b3099ea21a3fb2aa2ab8b4da2`
- Comparison media contract: H.264/AAC, 1440x720, 25 FPS, 15 seconds,
  `AI AVATAR DEMO` watermark

The machine-readable report is committed as `avatar-evaluation.json`. A path
leak scan found no absolute source, audio, model, or private workspace paths in
the published evidence.

## Claim boundary

The real-person comparison video remains in private local storage and is not
committed to GitHub. This report proves reproducible automated gates and
non-regression under the recorded contract. It does not prove commercial
HeyGen parity or human-perceived naturalness. The randomized blind review in
the evaluation protocol remains required before making a perceptual quality
claim.
