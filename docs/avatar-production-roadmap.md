# Avatar Production Roadmap

## 1. Current Stable Direction

The production-quality avatar path is:

```text
TTS -> LivePortrait -> MuseTalk -> optional restoration
```

LivePortrait provides natural head and face motion. MuseTalk provides lip sync against the generated lesson audio. Optional restoration can improve visual quality, but it must stay switchable so fast previews and constrained GPU deployments can skip it.

MuseTalk-only fast mode is not the production default because it can look lip-only without enough natural head or face motion. It must remain opt-in for development and preview use only.

Avatar rendering is non-blocking. The base lesson can publish first, remain playable, and receive the avatar overlay later when the background avatar job finishes.

## 2. Production Goals

- Keep lessons publishable even when avatar rendering is still processing.
- Support many publishers by using separate avatar workers and background queues.
- Keep the high-quality workflow as the default.
- Make expensive stages optional and configurable.
- Add caching and reusable persona assets.
- Keep legal and safety protections around face and voice cloning.
- Keep avatar failure from breaking lesson playback.

## 3. Runtime Modes

Important switches and configuration ideas:

```text
AVATAR_ENGINE=liveportrait+musetalk
AVATAR_ALLOW_MUSETALK_ONLY_FAST_MODE=0
AVATAR_PREVIEW_USE_RESTORATION=0/1
AVATAR_FINAL_USE_RESTORATION=0/1
AVATAR_BACKEND=local/provider/auto
AVATAR_PROVIDER_FALLBACK_TO_LOCAL=true/false
```

`AVATAR_ENGINE=liveportrait+musetalk` is the local production mode and should remain the default. It preserves the intended chain of TTS, LivePortrait, MuseTalk, and optional restoration.

Local preview mode should use the same conceptual pipeline but allow cheaper settings, shorter scripts, lower resolution, and restoration disabled by default.

MuseTalk-only fast/dev mode is allowed only when both of these are set:

```text
AVATAR_ALLOW_MUSETALK_ONLY_FAST_MODE=1
AVATAR_ENGINE=musetalk
```

Optional restoration should be independently controlled for preview and final renders.

Future provider mode can be introduced with `AVATAR_BACKEND=local/provider/auto`, but it should not replace the stable local workflow by default.

## 4. Multi-user Production Handling

The render queue handles base lesson rendering. The avatar queue handles GPU avatar work. `worker-avatar` runs separately from the base render worker so slow avatar jobs do not block lesson publishing.

Avatar worker concurrency should usually be `1` per GPU unless benchmarks prove that higher concurrency is stable. Autoscaling should be based on avatar queue depth, GPU availability, and average render duration.

Paid users can later receive priority queues or higher concurrency budgets. Avatar rendering must be resumable and retryable where possible. If avatar rendering fails, the base lesson remains published and playable without the avatar overlay.

## 5. Sidecar-backed Handoff Plan

Current risk: very large `ordered_results` or render metadata should not be passed directly through Celery for huge lessons.

Proposed solution:

- Write render and avatar handoff manifests to storage.
- Pass only `project_id`, `job_id`, and `manifest_path` through Celery messages.
- Later support MinIO or S3 for distributed workers.

Example paths:

```text
storage_local/projects/<project_id>/renders/<job_id>/avatar_handoff.json
storage_local/projects/<project_id>/renders/<job_id>/avatar_result_manifest.json
```

Manifest fields:

- `project_id`
- `base_job_id`
- `avatar_job_id`
- `ordered_slide_results`
- `audio_paths`
- `slide_video_paths`
- `avatar_settings`
- `engine_chain`
- `status`
- `errors`
- `timestamps`

## 6. Avatar Persona Bank

The safer first production option is a platform-approved avatar persona bank:

- Platform-approved AI avatars/personas.
- Publishers choose from approved avatars.
- No arbitrary face cloning at first.
- Platform avatars can be cached and optimized.
- Premium users can later unlock more personas and options.

Avatar types:

- `platform_approved`
- `verified_personal`
- `unverified_blocked`

## 7. Cacheable Avatar Artifacts

Cacheable artifacts include:

- Normalized avatar source image or video.
- Face crop and alignment.
- Landmarks.
- Bounding box and mask.
- LivePortrait source preparation.
- Identity/source package.
- Motion template metadata.
- MuseTalk face-region metadata.
- Restoration reference setup.
- Source hash and validation state.

Every new audio track still requires lip-sync generation, so caching helps but does not make avatar rendering free.

## 8. LivePortrait Motion Template Strategy

The current issue is that motion can become exaggerated because low-motion outputs previously failed validation. The plan is to preserve natural motion quality rather than forcing exaggerated motion just to satisfy validators.

Plan:

- Do not force exaggerated motion just to satisfy validation.
- Create approved natural motion templates.
- Support subtle blinking.
- Support subtle head motion.
- Preserve professional teaching posture.
- Avoid unstable user-provided driving videos by default.
- Tune validators to detect real natural motion, not only high motion.
- Keep strict artifact and jitter checks.

Motion presets:

- `calm_teacher`
- `natural_teacher`
- `expressive_teacher`
- `minimal_motion_preview`

## 9. Optional Restoration

Restoration should be switchable. It should be disabled for fast preview and enabled for final, premium, or high-quality renders when GPU budget allows.

Restoration failure should not fail the whole lesson unless strict mode is enabled. Any restoration result still needs to pass validation and moderation before public use.

## 10. API Avatar Provider, Future Optional

Provider API support is optional, not default.

Future backend:

```text
AVATAR_BACKEND=local/provider/auto
```

Rules:

- Local workflow remains the stable default.
- Provider rendering can be used for overflow, burst capacity, or premium plans if cost makes sense.
- Provider output still needs moderation and validation.
- External provider use should require configuration and possibly user/project consent.
- Fallback to local can be enabled.
- Never send personal face or voice data externally unless explicitly allowed.

## 11. Safety and Legal Guardrails

Guardrails:

- Platform-approved avatars first.
- Verified personal avatar later.
- Consent confirmation.
- Liveness/selfie challenge.
- Avatar bound to publisher account.
- No celebrity or public figure cloning.
- No third-party face upload without permission.
- Voice consent for cloned voice.
- Admin kill switch.
- Reporting and takedown flow.
- Visible or invisible AI watermark idea.
- Audit logs.
- Moderation before public use.

This is not legal advice. Local legal review is needed before public launch.

## 12. Premium / Plan Controls

Future plan gates:

- Free: no avatar or limited platform avatars.
- Standard: platform avatars plus limited minutes.
- Pro/publisher: higher avatar minutes, restoration, and priority queue.
- Enterprise/school: verified personal avatars and admin controls.

## 13. Failure Handling

Expected behavior:

- Base video publish must not fail because avatar is slow.
- Avatar states are `queued`, `processing`, `ready`, and `failed`, with `none` for disabled/no avatar.
- If avatar fails, the lesson remains public without avatar.
- User can hide avatar without canceling processing or deleting artifacts.
- Add a retry button later.
- Stale avatar results should not override newer lesson renders.
- Job IDs and source hashes should prevent stale writes.

## 14. Future Implementation Phases

Phase 1: Documentation and current non-blocking workflow stabilization.

Phase 2: Sidecar manifest handoff.

Phase 3: Persona bank data model and admin seed flow.

Phase 4: Avatar artifact caching.

Phase 5: Motion template library and validator tuning.

Phase 6: Optional restoration controls.

Phase 7: Plan and permission gating.

Phase 8: Verified personal avatar/voice flow.

Phase 9: Optional provider backend.

## 15. Rules for Future Codex Work

- Do not make MuseTalk-only the production default.
- Do not block publishing on avatar.
- Do not weaken moderation.
- Do not remove LivePortrait.
- Do not remove MuseTalk.
- Do not rewrite avatar from scratch.
- Keep changes small and PR-based.
- Push stable milestones to private backup.
- Never commit generated files, secrets, or media.
