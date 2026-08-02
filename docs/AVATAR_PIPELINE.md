# Avatar Pipeline

This document describes the current stable avatar MVP. For longer-term planning, keep [avatar-production-roadmap.md](avatar-production-roadmap.md).

## Stable MVP Direction

The stable local production path is:

```text
TTS audio
  -> LivePortrait with calm external motion template when configured
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

LivePortrait supplies face and head motion. For lecture avatars, the preferred image path is an external calm driving template that is not committed to git:

```text
AVATAR_LIVEPORTRAIT_CALM_IMAGE_TEMPLATE=storage_local/avatar_templates/calm_lecture_driver.mp4
AVATAR_LIVEPORTRAIT_DRIVER_SOURCE_POLICY=
```

When the policy is blank, the runner uses `calm_template_for_image` only if `AVATAR_LIVEPORTRAIT_CALM_IMAGE_TEMPLATE` points to a valid video. If the calm template is missing, the current vetted d11 template remains the placeholder fallback for now:

```text
AVATAR_LIVEPORTRAIT_VETTED_IMAGE_TEMPLATE=/opt/liveportrait/assets/examples/driving/d11.mp4
AVATAR_LIVEPORTRAIT_ALLOW_VETTED_TEMPLATE_FALLBACK=1
```

`composer_for_image` remains available as an explicit debug policy or with `AVATAR_LIVEPORTRAIT_ALLOW_COMPOSER_FALLBACK=1`, but it is not the production default because its blink path is not a real eyelid/keypoint driver.

Head-motion tuning remains an area for polish. The goal is professional teaching posture, natural blinking, and stable low-motion output rather than exaggerated movement. Calm template media should live under local or object storage, such as `storage_local/avatar_templates/`, and must not be committed.

### Personal performance video

For a verified personal avatar, a video reference is used as the real
LivePortrait driving source. The capture flow records 30 seconds and asks for a
small nod, a brief smile, natural blinking, gentle eyebrow motion, and a return
to a neutral expression. This gives the driver enough variation to preserve the
person's characteristic face and head performance after MuseTalk replaces the
mouth motion with the lesson audio.

When the reference is longer than a generated segment, the runner probes
candidate intervals with FFmpeg frame-difference energy and deterministically
chooses a balanced-motion window. It avoids always reusing the neutral opening
seconds and avoids selecting an excessively active interval. The behavior is
controlled by:

```text
AVATAR_LIVEPORTRAIT_PERFORMANCE_WINDOW_ENABLED=1
AVATAR_LIVEPORTRAIT_PERFORMANCE_WINDOW_MIN_MAD=0.40
AVATAR_LIVEPORTRAIT_PERFORMANCE_WINDOW_TARGET_MAD=0.80
AVATAR_LIVEPORTRAIT_PERFORMANCE_WINDOW_MAX_MAD=1.60
AVATAR_LIVEPORTRAIT_PERFORMANCE_WINDOW_STEP_SECONDS=1.5
AVATAR_LIVEPORTRAIT_PERFORMANCE_WINDOW_CACHE_VERSION=1
```

This stage transfers face expression and head pose; it does not synthesize hand,
arm, or full-body gestures. Those require a separate pose-conditioned full-body
renderer and a compositing contract beyond the current overlay pipeline.

## MuseTalk

MuseTalk provides lip sync against generated lesson audio. The worker can route MuseTalk through a persistent service first, with standalone fallback intentionally disabled by default in the template.

Before starting `worker-avatar`, provision and validate the local MuseTalk model bundle with [AVATAR_MODEL_PROVISIONING.md](AVATAR_MODEL_PROVISIONING.md). The repository does not ship model weights, and a normal `worker-avatar` start can consume pending jobs from the real `avatar` queue.

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
- [AVATAR_MODEL_PROVISIONING.md](AVATAR_MODEL_PROVISIONING.md)
- [avatar-production-roadmap.md](avatar-production-roadmap.md)
- [TRADEOFFS_PLUS_MINUS.md](TRADEOFFS_PLUS_MINUS.md)
