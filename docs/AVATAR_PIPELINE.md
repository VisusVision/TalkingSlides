# Avatar Pipeline

This document describes the current stable avatar MVP. For longer-term planning, keep [avatar-production-roadmap.md](avatar-production-roadmap.md).

## Stable MVP Direction

The stable local production path is:

```text
TTS audio
  -> LivePortrait with vetted d11 motion template
  -> MuseTalk lip sync
  -> optional restoration
  -> avatar track
  -> Watch overlay
```

The avatar track is an overlay, not a blocker for base lesson playback.

## Non-blocking Lesson Behavior

The base render finishes first. Once the base video and playback assets are ready, the lesson can be published and watched. Avatar work is queued separately. While avatar status is `queued`, `processing`, `failed`, or `none`, playback continues with the base video and `avatar_overlay.enabled=false`.

When avatar output is ready, active, visible, and backed by an existing artifact, playback payloads can include an avatar stream URL and overlay placement defaults.

## LivePortrait

LivePortrait supplies natural face and head motion. The current safe image path favors a vetted d11 template for image-driven avatars:

```text
AVATAR_LIVEPORTRAIT_DRIVER_SOURCE_POLICY=vetted_template_for_image
AVATAR_LIVEPORTRAIT_VETTED_IMAGE_TEMPLATE=/opt/liveportrait/assets/examples/driving/d11.mp4
```

Head-motion tuning remains an area for polish. The goal is professional teaching posture, natural blinking, and stable low-motion output rather than exaggerated movement.

## MuseTalk

MuseTalk provides lip sync against generated lesson audio. The worker can route MuseTalk through a persistent service first, with standalone fallback intentionally disabled by default in the template.

## Restoration

Restoration is optional and should remain switchable. Preview defaults can skip restoration for speed. Final renders can enable restoration where GPU budget and quality targets justify it. Restoration failure should not block the base lesson.

## Avatar-only Rerender

Avatar-only rerender uses existing base playback assets and an avatar handoff manifest. It should enqueue only avatar work, not rerender the full base video. This keeps publisher iteration cheaper and keeps existing playback stable.

## Watch Overlay Controls

The Watch player receives avatar overlay payload fields such as:

- `avatar_processing_status`
- `avatar_available`
- `avatar_overlay.enabled`
- `avatar_overlay.stream_url`
- `avatar_overlay.placement`
- per-user or owner placement defaults

Overlay controls are expected to let users show/hide, move, and size the avatar without changing the base video asset.

## Limitations

- Real avatar generation requires a validated NVIDIA GPU runtime.
- LivePortrait head motion and low-motion validator tuning need more production QA.
- Persona/avatar bank support is deferred.
- Provider-based avatar rendering is deferred.
- Object storage support for distributed avatar workers is not fully abstracted yet.
- Avatar failures should stay visible in status surfaces but must not block lesson playback.

## Related Docs

- [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)
- [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md)
- [avatar-production-roadmap.md](avatar-production-roadmap.md)
- [TRADEOFFS_PLUS_MINUS.md](TRADEOFFS_PLUS_MINUS.md)
