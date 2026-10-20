# Industrial Render Planner Foundation

Phase 1 added planning only. Phase 2 connects the planner to worker dispatch so
auto renders enqueue expensive slide work only for dirty slides. Phase 3
assembles dirty-slide outputs with validated reusable baseline parts and
promotes a new authoritative final only after media validation succeeds.

## Fingerprint Inputs

Each slide receives a deterministic SHA256 fingerprint over canonical JSON.
Only render-affecting inputs are included:

- transcript/display/subtitle text
- slide/background image identity
- duration and pause timing
- voice ID, speed, and pitch
- language
- TTS provider and canonical project TTS settings
- avatar requested/enabled state, visible state, source identity hash,
  preview-source hash, source validity, preview-stale state, moderation status,
  selected avatar engine/runtime, model version, quality, reference type,
  position, and size when avatar rendering is enabled
- render resolution
- render pipeline version

Non-render metadata is ignored, including timestamps, theme, notifications,
sidebar state, and opened tabs.

The active render pipeline default resolution is `1920x1080`, matching the
FFmpeg slide and avatar-composition helpers. The render pipeline version is
owned by `core.render_planner.RENDER_PIPELINE_VERSION`; changing render
semantics must bump that value.

Project TTS settings are canonicalized through the same backend helper used by
the API and worker. `provider_preference=auto` remains a distinct render input
and continues to mean the TTS service attempts XTTS v2 first before gTTS
fallback. Explicit `provider_preference=gtts` is preserved as a provider input
and invalidates existing render artifacts.

## Dirty Rules

A slide is dirty when its current fingerprint differs from the previous planner
manifest or when a required previous artifact is missing or corrupt.

Planner reasons are sanitized reason tokens:

- `TranscriptChanged`
- `ImageChanged`
- `TimingChanged`
- `VoiceChanged`
- `LanguageChanged`
- `ProviderChanged`
- `AvatarChanged`
- `AvatarVisibilityChanged`
- `AvatarPositionChanged`
- `RenderResolutionChanged`
- `PipelineVersionChanged`
- `SlideOrderChanged`
- `MissingArtifact`
- `CorruptArtifact`
- `NewSlide`
- `RemovedSlide`
- `MissingBaseline`

The planner output lists slide numbers, page keys, dirty/reusable status, and
reason tokens. It never returns storage paths.

## Global Invalidation

These shared inputs invalidate every slide:

- global voice change
- global language change
- global TTS provider/settings change
- avatar identity/source change
- avatar engine/runtime change
- project-level avatar enabled/disabled change
- current project-level avatar visibility, position, or size defaults
- render resolution change
- pipeline version change

Slide-local transcript, image, and timing changes invalidate only the affected
slide unless a future phase explicitly promotes them to a broader render action.

Current avatar rendering is still project-level. When enabled and visible, the
worker burns avatar segments into final slide parts before concatenation; hidden
or disabled avatar states remain absent from final media. The planner therefore
treats current project-level avatar source, engine/runtime, visibility, and
layout defaults as global invalidators.

The manifest stores resolved per-slide avatar layout in each slide input. That
keeps room for a later per-slide data model without changing the planner shape:
future slide-local show/hide, position, or size overrides can dirty only the
affected slide. No per-slide avatar persistence model is introduced in this
foundation PR.

## Render Modes

The worker accepts a backward-compatible render mode:

- `auto`: compare the current planner manifest with the latest safe
  authoritative baseline. Valid reusable slides are skipped; dirty slides run
  the normal TTS/avatar/composition task. Missing or unsafe baselines fall back
  to full render.
- `full`: render every active slide and finalize the normal authoritative
  output. Initial uploads and explicit recovery requests use this mode.
- `selected`: preserve existing selected-page rerender behavior using explicit
  page keys. Selected slides are rendered and merged through the existing
  selected rerender finalization path.

Clients that omit render mode remain compatible. Initial upload explicitly
uses `full`; project rerender defaults to `auto`; transcript-selected rerenders
use `selected`; `force_full` or equivalent full mode requests force `full`.

## Phase 2 Dispatch Flow

After export, transcript sync, moderation, language detection, TTS settings,
and avatar options are resolved, the worker captures an immutable job-scoped
render plan under `projects/<project>/renders/<job>/render_plan.json`.

The captured plan includes project/job IDs, render mode, effective mode,
baseline job ID, planner manifest hash, dirty and reusable page keys, ordered
page keys, fallback reason tokens, pipeline version, and stage results. Public
APIs do not expose storage paths from this file.

Reusable eligibility is conservative. A reusable slide must have a matching
planner fingerprint, a previous manifest entry, a previous final segment,
required composed segment/audio/slide-image references, non-empty files under
the current storage root, a successful baseline job, compatible pipeline
version, matching project/page identity, and compatible avatar burn-in state.
Uncertainty marks the slide dirty. Phase 2 uses bounded filesystem checks;
full repeated FFprobe validation is reserved for the atomic assembly/promotion
phase.

For auto renders:

- zero dirty slides enqueue no slide tasks and leave the existing output
  authoritative
- one dirty slide enqueues one slide task
- reusable slides do not enqueue TTS, LivePortrait, MuseTalk, or composition
- missing baseline, invalid manifest, planner errors, or force-full requests
  dispatch a full render and record the fallback reason

For successful full finalization, `playback_assets.json` embeds
`render_planner_manifest` so future auto renders have a safe baseline. Failed
jobs do not replace the authoritative baseline sidecar.

## Phase 3 Assembly And Promotion

Auto dirty-slide jobs consume the immutable job-scoped render plan captured
before dispatch. The assembly step uses the plan's ordered page keys, dirty
page keys, reusable page keys, current planner manifest, pipeline version, and
baseline job ID. It does not recompute order from mutable project state after
slide work completes.

For each ordered page key:

- dirty pages use the new slide task result for the same project/job/page key
- reusable pages use the previous authoritative sidecar's `final_segments` and
  planner artifacts for the matching page key and fingerprint
- page selection is never based on filename sort order

The worker records an internal assembly manifest under
`projects/<project>/renders/<job>/staging/assembly_manifest.json`. The manifest
stores project ID, job ID, baseline job ID, pipeline version, planner manifest
hash, ordered parts, source (`new` or `reused`), fingerprint, relative part
reference, and origin job ID. Private absolute paths are not part of the
manifest.

Before concatenation, each part must pass the media contract:

- referenced file exists and is non-empty
- FFprobe succeeds
- video stream exists
- audio stream exists
- duration is positive and within a tolerant range of expected duration
- resolution matches the render contract
- reusable part fingerprint and baseline job identity match the captured plan

Validation failures mark the current job failed, record a concise stage reason,
and leave the prior completed job and sidecar authoritative. A corrupt reusable
part is never silently assembled.

The assembled final for auto partial jobs is written to a job-scoped immutable
namespace:

`<project>/renders/<job>/final/<project>.mp4`

SRT and VTT are regenerated from the complete assembled render-result snapshot
so later subtitle offsets reflect changed slide durations while reusable later
parts remain valid. If HLS packaging is enabled by the existing protection
mode, it runs from the validated assembled MP4 and its sidecar metadata is
promoted with the same playback sidecar.

Promotion sequence:

1. validate dirty and reusable parts
2. write staging assembly and validation reports
3. re-check the job is still the latest current render job
4. concatenate to the job-scoped final namespace
5. validate the final MP4 with FFprobe
6. regenerate SRT/VTT and optional HLS
7. write the complete playback sidecar with `atomic_assembly` and
   `final_validation`
8. update the job's `result_url`/`srt_url` and mark it done

The API authority model remains latest completed `video_export` job plus
`playback_assets.json`. Watch, Studio preview, catalog, publish, and download
continue to select the latest completed job; partial staging files are never
referenced by a completed job before successful finalization. Older/stale jobs
may finish validation or staging, but the finalizer re-checks current job
identity before promotion and stale jobs are marked terminal without replacing
the latest output.

The filesystem backend uses local materialized files because FFmpeg, avatar,
and TTS tooling require paths. Object storage remains behind the existing
adapter boundary for sidecar JSON; production object-storage media promotion
must use immutable uploaded objects and pointer/sidecar updates last. Runtime
S3 media serving remains gated by the broader storage migration plan.

## Failure And Concurrency Boundary

Dirty-slide task failure marks the job failed through the existing chord
errback/failure path. The previous authoritative final output and playback
sidecar remain intact. Retry behavior reuses the captured job plan where the
existing task retry/idempotency rules apply; Phase 2 does not add a distributed
lock system.

Existing active-render dedupe and follow-up intents still prevent obvious
same-project render storms. The captured plan prevents mid-run replanning from
mutable project state. Older/stale jobs use the existing current-job checks
before finalization or Phase 2 result recording.

## Release Gates

Render changes that affect this flow must prove one-slide partial promotion,
zero-dirty no-op behavior, insert/delete/reorder assembly, corrupt reusable
part rejection, stale job rejection, Watch/download agreement, avatar burn-in
without duplicate overlay, XTTS output, and explicit gTTS behavior. Unit tests
and CI are not sufficient by themselves; real media evidence is required before
merge recommendation.
