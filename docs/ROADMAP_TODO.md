# Roadmap and TODO

Developer-only. This file is suitable for the developer branch. Main should receive only stable, polished, user-facing docs.

## Near-term

- Validate Windows setup scripts on a clean Windows 11 machine.
- Add CI for backend tests, frontend build, and docs/script parse checks.
- Decide the production storage strategy: durable shared filesystem first, or object storage adapter.
- Add operational runbooks for backup, restore, retention, and media cleanup.
- Add deployment automation for API, workers, TTS, frontend, and GPU avatar worker.
- Expand production monitoring around render queue depth, avatar queue depth, failed jobs, TTS readiness, playback 4xx/5xx, and storage usage.

## Avatar

- Polish LivePortrait head motion presets.
- Improve low-motion validation so natural teacher posture is accepted.
- Add automatic avatar placement based on slide content.
- Add persona/avatar bank for approved platform avatars.
- Improve avatar status visibility in Studio and Watch.
- Validate avatar-only rerender on target GPU hardware.
- Decide when restoration should be enabled by default.

## Secure Playback and DRM

- Complete production DRM vendor integration.
- Implement or validate Shaka/EME license request handling per vendor.
- Add production HLS packaging verification and retention cleanup.
- Add admin-facing playback/session diagnostics.
- Add end-to-end browser tests for public, secure_stream, and DRM-unavailable states.

## Product and UX

- Add like/comment UI if public social learning remains in scope.
- Add richer admin screens for moderation, jobs, users, and storage.
- Add stronger auth token lifecycle and frontend session handling.
- Add visual scene editing beyond transcript page controls.
- Add drag-and-drop page reorder once the model/UI contract is stable.

## TTS and Subtitles

- Expand curated pronunciation dictionaries only from observed project needs.
- Keep LLM suggestions Studio-only and non-render-path.
- Validate production subtitle providers in staging.
- Add provider cost/rate dashboards before broad public subtitle requests.

## Documentation

- Split developer-only notes from main-ready docs before merging to main.
- Keep service READMEs aligned with the root documentation suite.
- Add screenshots or short videos for Studio, Watch, and Settings once UI stabilizes.
