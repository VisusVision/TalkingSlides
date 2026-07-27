# Industrial Render Planner Foundation

Phase 1 adds planning only. It does not change render dispatch, worker
execution, Celery routing, final concatenation, playback behavior, or UI flows.

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
