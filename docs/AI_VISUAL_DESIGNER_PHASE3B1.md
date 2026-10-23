# Phase 3B-1 — Pure segment contracts

Starting branch: `feature/ai-visual-designer`, verified HEAD
`02798a8ce7fb174b5349a05e59208863a2ffdcc5`.
This implements only commit 1 of the authoritative Phase 3B design. No production
caller, persistence, dispatch, rendering, audio reuse or publication is activated.

## Boundaries and trust

`core.visual_design.segment_contracts` uses only Python's standard library and
existing pure visual-design modules. Import it directly; existing package exports
and Phase 1–3A modules are unchanged. In particular, `visual-render-snapshot.v1`
retains `purpose=isolated_frame.v1`, its original validation and canonical hashes.
It is a reference input, not a complete speech execution recipe.

| Contract | Inputs checked | Identity |
|---|---|---|
| `SegmentAudioProof` / `visual-segment-audio-proof.v1` | Explicit producer/build/receipt, baseline Job/run/manifest/page, protected and spoken text, concrete language/provider/voice, supported settings, sample/model/preparation/pronunciation dependencies, audio and processing chain | Audio-proof hash, checked against an **independent expected receipt digest and producer ID** |
| `SegmentRequest` / `visual-segment-request.v1` | Frozen snapshot UUID/hash/revision, owner/project, exactly one page/order/source/split, scene/design/fonts, trusted proof, saved subtitle chunks/local cues/absolute inputs, bounded codec/runtime recipe | Domain-separated `execution_hash` |
| `SegmentReceipt` / `visual-segment-receipt.v1` | Exact request/snapshot/page/design, independent expected frame ref, audio/cue/proof/producer, recipe/runtime, attempt generation, output refs, measured stream claims and private acceptance | Separate receipt hash; always `publishing_allowed=false` |

Trust parameters must come from independently authenticated server records in a
later stage. Passing a payload's own freshly calculated digest back as its expected
hash is **not** producer authentication. The pure class checks consistency against
the supplied anchor; it cannot establish that the caller obtained it honestly.
No function takes a legacy audio path and retroactively invents producer history.
Synthetic audio attestations are rejected unless `allow_synthetic=True` is explicitly
supplied by the engineering tests. This is not a production enrollment mechanism.

The caller must read the snapshot through the existing validated server boundary,
including `validate_frozen_design` and asset-byte checks. Structural source/plan,
selection, proposal and confirmation associations are checked again here, but
these pure contracts neither read private assets nor enforce live ownership,
revocation, row locks, storage confinement or attempt fencing. A frozen approval
hash preserves history; it never overrides a current revocation.

## Contract semantics

All fields are required unless their schema explicitly permits null. See the
complete JSON fixtures for exact shapes. Unknown fields/versions fail. Indices
are bounded integers excluding bool; zero is valid. Exact JSON comparison avoids
Python's `False == 0` identity ambiguity. Strings, object keys, arrays, nesting,
nodes and total UTF-8 payload size are bounded; nonfinite values and invalid
Unicode scalars fail. `parse_contract_json` additionally rejects duplicate keys.

Canonicalization uses the unchanged Phase 3A `canonical_json` / `content_hash`:
UTF-8, exact Unicode, sorted keys, compact separators, finite JSON numbers, with
`visual-design-hash.v1\n<domain>\n<canonical JSON>` before SHA-256. No normalization,
text trimming or rewriting occurs. Protected and effective spoken text remain
separate. Attempts, leases, timestamps and Job progress are not request fields.
Attempt generation belongs only to the receipt and must match an independently
supplied expected generation. Changing a request dependency changes execution
identity; a retry generation does not.

**Scenes:** `completed_generated.v1` owns its display text and uses
`display_overlay=none`; narration and subtitles stay enabled. Source and grounded
source hashes remain distinct. Candidate/selection/proposal/confirmation identities,
plan/preview/schema/template/renderer versions, SVG and regular/bold font pins are
bound to the snapshot. `pinned_original_raster.v1` requires a pinned original raster;
its `contains_text=null` makes no OCR assertion. Original recovery is always
`explicit_new_request.v1`, with missing assets failing. Invalid selected designs
cannot silently become originals. Existing overlay-dependent whiteboard/custom/
source-background bridges are deferred.

**Audio:** only `reuse_verified_audio.v1`. The recipe requires AAC-LC in an M4A
input so future H.264/AAC composition can copy the encoded audio without
reprocessing. PCM/MP3 input is currently unsupported rather than silently encoded
at segment time. The producer receipt includes both container digest and AAC
packet-payload digest, measured duration/activity window and a hash-linked,
versioned post-processing history with implementation/settings identities.
Built-in voices explicitly have no sample; cloned voices require an attested WAV
sample ref. Model/config/preparation/pronunciation hashes are mandatory. Only
explicit neutral speed/pitch/gain semantics are supported. Captured snapshot TTS
settings must match the producer's associated settings hash. Unknown/auto/cached/
silent providers or fallback audio fail eligibility. This freezes provenance for
reuse; it does not define fresh synthesis or prove present legacy controls work.

**Captions:** verbatim saved chunks, one ordered cue per chunk, integer milliseconds,
nonoverlapping cues within the attested audible window, audio duration + hold =
target duration, maximum 120 seconds. Absolute snapshot timing and timeline are
retained separately and verbatim. `frozen_baseline.v1` requires a producer-associated
cue hash plus exact conversion from frozen absolute times. A deliberately nullable
baseline cue hash represents unavailable provenance and cannot satisfy that policy.
`result_allocator.v1` records already prepared approximate cues and
`allocate_chunk_timings.v1` provenance; this module does not run or replace the
existing allocator. Later server code must actually use that algorithm before
freezing such cues, not accept a browser's allocation claim. Hold is excluded from
cues; normalized spoken text never replaces authored caption text.

**Recipe:** `private_visual_segment.v1`, `h264-aac-copy.v1`, 1920×1080, 25 fps,
libx264/H.264, CRF 23, medium, yuv420p, AAC-LC stream copy and sidecar subtitles.
Explicit FFmpeg binary/build/version, librsvg/fontconfig identities and font hash
are required. The runtime hash must appear in an independently supplied supported
runtime set. Limits: 1 MiB JSON, 16 MiB audio, 32 MiB output MP4, 128 KiB per caption,
64 MiB attempt budget, 180-second execution / 10-second probe deadlines, 80 ms
media duration tolerance. These are contract limits, not measured capacity claims.

**Receipts:** MP4/SRT/VTT refs have strict role/type/size/hash identities. Audio packet
payload/sample format must match the attested input; frame geometry, pixel format,
fps, frame-count consistency, audio/video/container durations and cue end bounds
are checked. Receipt fields are a future validator's **claims**, not file inspection.
The future worker must probe/hash actual outputs and fence completion before
persisting acceptance. The hypothetical fixture outputs do not exist. Segment
receipts fail Phase 3A frame-receipt validation, and frame receipts fail segment
validation. Neither receipt authorizes publication or playback replacement.

## Fixtures and regression coverage

`tests/fixtures/visual_segments/` contains the frozen three-page snapshot,
request/audio-proof/receipt JSON triples, independent test trust anchors, explicit
mapping, narration transcript, real WAV/AAC bytes, producer metadata, README and
Flite notice. Total checked-in speech audio is about 90 KiB. The original sentence
“Plants need light and water to grow.” was synthesized once using already installed
offline Flite SLT tooling, then encoded as AAC. No application provider was called,
no dependency/model was downloaded for audio, and no GPU was used. See the fixture
README for exact commands, rights, build hashes, measured properties and digests.
The pure suite does not execute Flite, FFmpeg or a TTS service.

Requests retain source indices `[0,2,0]`, split indices `[1,0,0]`, orders `[0,1,2]`
and three stable keys throughout scene, audio and caption references. Reorder tests
change display order while keeping original baseline order and source/split binding.
Tests cover strict schemas, independent-trust rejection, narration immutability,
unsafe inputs, source/template mismatch, missing/duplicate keys, font/runtime and
audio dependency changes, caption bounds, completed/original semantics, teacher
confirmation association, receipt separation and non-publication. The snapshot's
Phase 3A golden hash stays
`sha256:e3029ba276ee6befc116c68ec263a8d0276aafd4db6f14e20e036b3ffe389dd2`.

## Verification

Executed local commands and final results are recorded below. Hosted CI results
are reported with the implementation commit; no media/concurrency/platform
acceptance for future segment execution is claimed.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=services/api pytest \
  -c /dev/null --noconftest -q tests/test_visual_segment_contracts.py
# 216 passed. No Django configuration/database/plugin or media tool required.

pytest -q tests/test_visual_segment_contracts.py tests/test_visual_design_contracts.py \
  tests/test_visual_design_planner.py tests/test_visual_design_svg.py \
  tests/test_visual_design_grounding.py tests/integration/test_visual_render_snapshots.py \
  tests/integration/test_visual_render_planning.py tests/integration/test_completed_scene_prototype.py
# 346 passed, 1 skipped, 63 subtests passed (10.95 s); PostgreSQL-only race skipped locally.

DJANGO_SETTINGS_MODULE=config.settings_test python services/api/manage.py check
# System check identified no issues (0 silenced).
DJANGO_SETTINGS_MODULE=config.settings_test python services/api/manage.py makemigrations --check --dry-run
# No changes detected.

USE_SQLITE_FOR_TESTS=1 INTELLIGENCE_OLLAMA_CALIBRATION_ENABLED=false pytest -q tests
# 2546 passed, 1 failed, 12 skipped, 63 subtests passed (103.58 s).
# Sole failure: unmodified MuseTalk process-group cleanup; details below.
```

The local environment exposes process-group ID zero for a marked child in the
unmodified `test_orphan_check_detects_and_kills_marked_warmup_process` test; its
`killed_count` assertion fails. This unrelated local process-control limitation
is reported, not skipped or patched. PostgreSQL race checks and Docker smoke
require hosted CI here (no local PostgreSQL/Docker executable). Existing Phase 3A
frame tests exercise actual local FFmpeg/librsvg frames; **no narrated video segment,
MP4 output, segment media acceptance or production producer attestation was run**.

## Phase 3B-2 prerequisites — not implemented

1. Bounded private audio/media pin adapter and independently authenticated producer
   receipt lookup; preserve old image/font asset limits. Unknown legacy provenance
   remains ineligible. Pin/check source/owner/page/hash/type/size before reuse.
2. Existing planner adapters gain the designed opt-in font/runtime and synthesis
   dependency hashes, preserving legacy semantics; no second dirty-page planner.
3. Later stages need persisted immutable requests, lock/revision checks, idempotency,
   cancellation/revocation/attempt fencing and private delivery. Inferred candidates
   must stay unavailable for execution until the revocation gate exists.
4. Later media execution must use the existing compositor with an explicitly tested
   AAC stream-copy option, real probes/caption validation and pinned runtime/fonts.
   The present contracts do not modify its defaults or certify its compatibility.
5. Patch the five legacy truthiness sites separately before any normal-render bridge:
   `tasks.py::_sync_transcript_pages_from_export` (3559/3577),
   `tasks.py::_build_render_slides_from_draft` (3739/3747), and the `views.py`
   moderation descriptor (7299). Preserve zero, split identity and source-template
   association independently of display order; no such worker fix is included here.

Stop at Phase 3B-1. Normal Save & Rerender, draft promotion and authoritative
publication remain untouched and require separate authorization.
