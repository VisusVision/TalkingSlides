# Avatar Slide Defaults Roadmap

This document describes the planned Studio and Watch behavior for publisher-defined per-slide avatar display defaults.

## Current State

Current behavior is not per-slide:

- Publisher/profile-level avatar overlay defaults exist for position, size, and visibility.
- Watch has local avatar overlay controls for show/hide, placement, size, and theater-style display behavior.
- Per-user per-lesson overlay preferences exist for player UI state.
- Avatar generation is non-blocking and produces an overlay track when available.

## Goal

Let publishers define the default avatar display mode for each slide without forcing a video rerender.

Supported per-slide defaults:

- `normal`: show the avatar as a normal overlay using the selected/default placement.
- `theater`: enter a large theater/fullscreen-style avatar view for that slide.
- `hidden`: hide the avatar by default for that slide.

## Chosen Watch Rule

When entering a slide:

- The publisher default for that slide is applied.
- The viewer can override during that slide.
- When the next slide starts, that slide may apply its own publisher default.

This keeps publisher intent predictable while preserving viewer control.

## Viewer Override Behavior

Viewer controls should remain available:

- Show/hide avatar.
- Resize or move the normal overlay.
- Enter or exit theater mode.
- Reset current viewer override.

Viewer overrides should be session/user UI state. They should not mutate the lesson's publisher defaults.

Future control:

- Add a reset-to-lesson-defaults action that clears viewer overrides for the current lesson.

## Suggested Storage

Use transcript-page JSON metadata or a similar per-page metadata store. A practical first version can use a structured field inside `TranscriptPage.editor_document`, for example:

```json
{
  "scene": {
    "avatar_default": {
      "mode": "normal",
      "placement": "top-right",
      "size": "medium"
    }
  }
}
```

The render/playback pipeline should mirror the selected values into `playback_assets.json` or its successor playback manifest so Watch can apply defaults without additional Studio-only reads.

Possible sidecar shape:

```json
{
  "avatar_slide_defaults": [
    {
      "page_key": "s1-p1",
      "mode": "normal",
      "placement": {
        "position": "top-right",
        "size": "medium"
      }
    }
  ]
}
```

## Studio Design

Studio should expose the setting as a per-slide display default:

- Use existing selected-scene context.
- Keep it near avatar/display controls, not inside TTS controls.
- Avoid a second source of truth for avatar enablement or render intent.
- Make clear through state and persistence that this is a playback display default, not a request to regenerate video.

The setting should be saved with the transcript/scene metadata. It should not trigger render or avatar generation by itself.

## Watch Design

Watch should:

- Read `avatar_slide_defaults` from playback metadata.
- Apply the default when slide identity changes.
- Preserve viewer interaction during the active slide.
- Fall back to lesson/profile defaults when no slide default exists.
- Fall back to hidden/disabled when avatar output is unavailable.

## Rerender Rule

Changing display mode should not rerender video.

Mode changes affect playback metadata only:

- `normal` to `hidden`: metadata update only.
- `hidden` to `theater`: metadata update only.
- placement/size default changes: metadata update only.

Avatar source changes, avatar model/runtime changes, or narration/audio changes may still require avatar work. Those are separate from display defaults.

## Implementation Phases

### Phase 1: Metadata Contract

- Define allowed modes and fallback behavior.
- Persist a per-page default in page metadata.
- Mirror defaults into playback sidecar metadata.

### Phase 2: Watch Application

- Apply publisher defaults on slide enter.
- Keep viewer override controls intact.
- Add reset-to-lesson-defaults behavior.

### Phase 3: Studio Controls

- Add a per-slide control in the canonical selected-scene path.
- Save display defaults without queuing render work.
- Add tests that display-mode changes do not enqueue render jobs.

### Phase 4: Analytics and QA

- Add browser coverage for normal, theater, hidden, and viewer override behavior.
- Verify behavior on MP4 and HLS paths.
