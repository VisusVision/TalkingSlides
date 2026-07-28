# Industrial Render Planner Foundation

Phase 1 added planning only. Phase 2 connects the planner to worker dispatch so
auto renders enqueue expensive slide work only for dirty slides. Auto partial
renders in Phase 2 record dirty-slide intermediate artifacts but do not promote
a new final MP4 or playback sidecar; Watch remains on the previous
authoritative final output until a later atomic assembly/promotion phase.

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
jobs and auto partial dispatch jobs do not replace the authoritative baseline
sidecar.

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

## Phase 3 Boundary

Phase 2 prevents unnecessary expensive slide processing. Phase 3 must harden
artifact reuse with deeper media validation, atomic final assembly, and
authoritative output promotion for partial auto renders.
