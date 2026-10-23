# Phase 3B-4 — Private narrated segments and verified completion

Baseline: `fe226c9204fd1970002afc15945566ea85bd40c2`, verified clean on
`feature/ai-visual-designer`. This implements isolated media execution only.
There is no task registration/dispatch, HTTP Run route, Studio integration,
producer service, normal Save & Rerender integration or publication.

## Components and exact inputs

| Component | Responsibility |
|---|---|
| `worker/visual_segment_tasks.py::execute_private_segment` | Direct bounded internal entry point: persisted Run UUID, expected execution hash, already claimed generation and token |
| `worker/completed_scene.py::compose_frozen_frame` | Existing validated completed SVG/pinned original raster compositor; optional explicit executable/deadline retains existing defaults |
| `scripts/ffmpeg_helpers.py::create_slide_video` | Existing encoder helper with strict opt-in `verified_aac_copy` branch; legacy command construction/defaults unchanged |
| `core/visual_segment_validation.py` | Installed native build observation, exact caption serialization/parsing, independent output probing, sealed in-process media witness |
| `core/visual_segment_runs.py::accept_verified_segment` | Short transaction accepts one independently validated private result, with exact revision/authorization/attempt fences |
| Migration `0043_verified_private_segment_completion` | Receipt consistency, accepted claim identity and maximum three attempts; blank public Job result fields remain mandatory |

The worker reloads the immutable request from its specific Run, validates the
snapshot and server producer association, and uses exact pinned SVG/raster/fonts,
narration and cues. It never calls the normal task/finalizer/provider stack or
resolves a latest Job. Zero source index, split identity and stable page mapping
remain explicit. Phase 3A snapshot and Phase 3B-1 request/audio/receipt schemas,
accepted payloads, canonical hashes and pure fixture trust anchors are unchanged.

**Production remains unavailable:** `_producer_dependencies()` still returns
`None`. There is no environment switch, HTTP argument or production synthetic
trust bypass. Tests alone inject the existing independently anchored engineering
producer. Runtime observation does not authenticate audio provenance or enroll a
production producer. Existing lesson audio is never retroactively certified.

## AAC copy and visual hold

The private helper explicitly uses one video stream and one pinned AAC-LC stream,
libx264, CRF 23, medium, yuv420p, 1920×1080 and 25 fps. Audio uses `-c:a copy`:
no filter, apad, speed/gain operation, trim, resample or re-encode. The static video
extends to the frozen audio-plus-200-ms target; `-shortest` is not used. Explicit
protocol/container options, a subprocess deadline and output byte cap bound work.
The original helper branch still constructs its previous commands, including its
ordinary audio encoding/padding behavior. Ordinary rendering is not opted in.

Observed fixture: audio **2,335 ms**, frozen target **2,535 ms**, video/container
**2,520 ms / 63 frames** on the measured runtime. The actual visual hold is 185 ms
because of 25-fps frame quantization, inside the unchanged **80 ms** tolerance.
Audio duration stays 2,335 ms; the encoded AAC packet payload remains exactly
`sha256:05c9c0e0b1cfe5b55f5c72a697f62a74aa900da42ed94b1ea9d86195ddc44d91`.
Container MP4 bytes are not compared to M4A container bytes.

Completed scenes own their display text. No transcript overlay or burned-in
subtitle pass is applied. Narration and sidecar subtitles remain enabled.
Only completed generated scenes and pinned original rasters are supported;
invalid selected designs never silently become originals. Whiteboard/custom/
source-background bridges, avatars and full-lesson assembly remain excluded.

## Native validation and caption safety

The independently configured verifier checks its installed FFmpeg/ffprobe binary
hashes. Execution compares the frozen, independently supported runtime with actual
FFmpeg version identity and hashes of linked native libraries, including x264,
librsvg, fontconfig and font dependencies. Static/unknown runtime layouts fail.
Font bytes and configuration stay bound to the existing request and compositor.
The version field uses a bounded opaque SHA-256 identity of actual version output;
it does not weaken the v1 field grammar to accept arbitrary command output.

Before acceptance, actual private pins are hashed/read and independently checked:

* MP4 container, exactly one H.264 video and one AAC-LC audio stream, no other
  stream types; 1920×1080, yuv420p, 25 fps and zero page-local origins.
* Declared and decoded frame counts, full decoding, frame/duration consistency,
  audio/video/container durations and the unchanged target tolerance.
* Encoded x264 SEI parameters consistent with the supported CRF-23/medium recipe.
* Input audio integrity plus unchanged AAC packet payload, codec/profile/rate/
  channels and duration. The trusted producer establishes narration association;
  nonzero signal alone does not prove words or voice identity.
* First/middle/last decoded frames: valid geometry, nonblank content and bounded
  downsampled difference from the exact validated input frame. This is lossy-codec
  raster inspection on the supported build, not universal pixel equivalence/OCR.
* SRT and WebVTT parsed independently: exact cue count, text, order, millisecond
  timings and bounds, with coverage ending before the visual hold.

General subtitle writers strip/normalize/drop text. The private strict adapter
reuses their timestamp formatters while preserving authored Unicode, spaces and
single line breaks. It rejects ambiguous blank-line blocks, CR/NUL/control
characters, timing-arrow text and literal markup/entity delimiters (`<`, `&`)
rather than silently rewriting content. This deliberately conservative limitation
can be extended with a separately tested round-trip text encoding policy later.
No caption allocator, translation, forced alignment or current TranscriptPage read
supplies execution captions.

## Private files, failures and atomic completion

Every invocation gets a fresh owner/project/Run/generation/random attempt directory
under the explicitly configured private root, checked through no-follow directory
descriptors. Directories are 0700 and media files 0600. No caller path or URL is an
execution input. Final MP4/SRT/VTT pins use existing atomic no-overwrite CAS storage.
Inputs, intermediate copies and outputs share one conservative 64 MiB attempt
budget; MP4 remains limited to 32 MiB, each sidecar 128 KiB and audio 16 MiB.
Unaccepted pins/staging files confer no result authorization. No broad deletion
or garbage collector is introduced; failed attempt files need later controlled
reference-aware retention.

Native work/hash scans happen outside database locks. Only the independent
validator mints a sealed immutable in-process witness. JSON receipts, a Boolean,
a report-only eligibility result or self-computed hash cannot mark a Job done.
This is a trusted server-code capability, not a signature against malicious
administrator code; no witness is exposed over HTTP or broker transport.

Acceptance locks in the established owner → Project → source/control rows → Job →
Run order and rechecks request/snapshot, current saved revision, producer controls,
approval/revocation, pins/stat identities, generation/token and unexpired lease.
The receipt and private Job success commit in the same transaction. Generic Job
updates still cannot set private success. Receipt fields cannot be written through
normal save/update/bulk paths. Public `result_url`/`srt_url` remain blank.

Identical callbacks/deliveries return the same accepted result; different/stale
attempts cannot overwrite it. Cancellation/revocation before acceptance wins the
fence. Later cancellation/revocation denies access while retaining the historical
accepted receipt/hash and Job success identity. The internal access-policy function
rechecks live controls, producer authorization, current source policy and pins;
there is no delivery endpoint. Ordinary saved edits conservatively block stale
completion/delivery; A never reads replacement content from B.

Leases are not silently extended. An expired lease cannot complete. At most three
claims (including takeovers/retries) are allowed. Bounded execution timeout or
classified temporary I/O may release a claim for explicit retry; the third failed
execution is terminal. Integrity/schema/scene/caption/runtime errors are terminal,
never a request for new TTS or another visual. Cancellation is not an accepted
completion. No retries or pending intents are automatically dispatched.

## Reproducible real examples

```sh
python docs/visual_design/phase3b4/generate_examples.py \
  --private-root /tmp/talkingslides-private-phase3b4-new
```

Use the project's Python test environment. The command requires existing
FFmpeg/ffprobe/librsvg/fontconfig/DejaVu and test dependencies, no GPU/model download.
It creates only isolated synthetic test records, private media and JSON receipts.
It fails if required native media is unavailable. Output is `examples/` below the
explicit 0700 private root: five MP4/SRT/VTT/receipt sets and `manifest.json`.
Use a fresh root for each reproduction; pytest's disposable `test-runtime` is
explicitly an engineering test directory, never a production artifact root.

Source fixtures: `tests/fixtures/visual_segments/media_examples.json`; genuine
spoken narration and rights/producer metadata: existing `narration.m4a`, WAV and
README in that directory. No new audio was synthesized. The same known English
sentence is deliberately reused as the protected engineering narration across
synthetic visual examples; this does not attest arbitrary real-lesson content.

The five examples are a Turkish process flowchart, structured comparison,
verified **12/18 cm** chart, typography-only slide and original raster fallback.
All generated MP4s were actually decoded/probed. A contact sheet of decoded frames
was visually inspected: labels/units are legible, diagrams meaningful, no duplicate
text or visible clipping. No MP4 binaries are committed. The concise observed
`docs/visual_design/phase3b4/validation-report.json` records real output hashes,
sizes, stream properties and runtime for reproducibility, not cross-build goldens.

## Verification and remaining gates

Executed local verification:

| Check | Actual result |
|---|---|
| Final media + Run suite | 92 passed, 12 PostgreSQL-only skips (161.04 s) |
| Final error/Unicode checks after last error-classification refinement | 4 passed (8.12 s) |
| Final five-scene regeneration with actual encoder-parameter validation | 5 passed (26.36 s) |
| Phase 3A/3B-1/3B-2, planners, partial/draft/scoped render and legacy media regression | 571 passed, 2 PostgreSQL-only skips (26.03 s) |
| Standalone pure Phase 3B-1, no Django/native runtime | 216 passed (0.96 s) |
| Full local backend before final validator tightening | 2,731 passed, 1 failed, 24 skipped, 63 subtests passed (256.00 s) |
| Django check / migration consistency | No issues / no changes detected |
| npm ci / frontend unit / build | Passed / 350 passed / passed (existing chunk-size warning) |
| Both npm audits / Python dependency audit | Zero vulnerabilities / no known vulnerabilities |
| Local npm run e2e | Chromium executable absent; browser launch blocked, not functional acceptance |

The sole local full-suite failure is the unchanged
`test_musetalk_warmup_cleanup.py::test_orphan_check_detects_and_kills_marked_warmup_process`
process-group cleanup limitation. It is neither patched nor suppressed. Final hosted
CI must run the complete final tree, including PostgreSQL and actual media tests;
its exact results/URL are reported with the commit in the completion report. Local media tests include real composition and
negative malformed-media cases; PostgreSQL-only cases are explicitly skipped on
SQLite and executed by the mandatory hosted PostgreSQL job. CI runs the media
suite with `VISUAL_MEDIA_REQUIRE=1` in both full backend and PostgreSQL gates,
so missing native tooling cannot silently satisfy media acceptance.

Frontend/dependency/business-logic files are unchanged. Both npm audit gates remain
active (production moderate is also now explicit in CI). The existing unrelated
local MuseTalk process-group limitation is not patched/suppressed. Local Chromium
is absent; a failed browser launch is not a passing E2E test. Hosted Chromium,
full backend, PostgreSQL, Python audit and Docker build results must be read from
the final implementation commit's CI, not borrowed from an earlier commit.

Before 3B-5/3B-6: separately authorize narrow private API/delivery/dispatch and
reconciliation; retain all fences through GET/HEAD/Range; provision persistent
API/worker private mount/UID/permissions and test built-image cross-container
visibility, restart persistence, security and retention. Docker build smoke alone
is not deployment acceptance. Real lessons still require a separately authorized,
independently authenticated producer source. No production readiness, owner-facing
availability, Save & Rerender or Phase 3C publication acceptance is claimed.

**Stop after 3B-4.**
