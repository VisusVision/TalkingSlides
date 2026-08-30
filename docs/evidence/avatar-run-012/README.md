# Avatar Run 012 - Real MuseTalk Recovery Evidence

Run 012 is a controlled fault-injection experiment on the local RTX 4060. A
localhost-only proxy forwarded MuseTalk health checks normally, returned HTTP
503 for the first inference request, and forwarded the second inference to the
real GPU-backed service.

## Result

- First inference: controlled HTTP 503
- Classification: `transient_service_transport:service_http_503`
- Retry count: `1`
- Second inference: passed in `131.03` seconds
- Total recovery time: `132.4704` seconds
- Recovery exhausted: no
- LivePortrait rerun: no
- LivePortrait handoff SHA-256 unchanged: yes

The recovered output is a 15-second, 1024x1024, 25 FPS H.264/AAC video. The
private media is not committed to GitHub.

## Strong quality gates

| Gate | Result | Evidence |
|---|:---:|---|
| Canonical technical validation | passed | 375 frames, animation score 4.2097, no detected artifact |
| Identity | passed | YuNet/SFace strong assurance, cosine P10 0.8212, 24/24 face coverage |
| Lip sync | passed | LatentSync SyncNet confidence 2.50, AV offset -40 ms |
| Temporal stability | passed | score 1.0 |

The final strict quality decision was `passed` and the model/runtime hashes
used by both strong providers were verified.

## Negative control

An earlier setup attempt reached the real model but could not write its output
because the test directory was owned by root and not writable by the MuseTalk
service user. The recovery layer correctly treated that HTTP 500 permission
error as non-transient and did not issue another retry. That failed run remains
in private local storage; it is not presented as the successful experiment.

## Reproducibility

- Recovery implementation: `901b0a72ab071b55c743244d2b81af889ed96e69`
- Fault harness: `ac200d5c61a9350f3e66dc636f9abb1b9987fd27`
- Fault protocol: `musetalk-recovery-fault-v1`
- Recovery evidence contract: `avatar-musetalk-recovery-evidence-v1`
- GPU: NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB

The fault proxy binds only to `127.0.0.1`. The machine-readable evidence does
not contain absolute media, workspace, or model paths.

## Claim boundary

This run proves bounded recovery from one injected MuseTalk HTTP transport
failure while preserving the completed LivePortrait handoff. It does not prove
recovery from CUDA/OOM, model corruption, arbitrary worker termination, or
commercial avatar platform parity.
