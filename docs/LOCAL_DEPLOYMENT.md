# Local Deployment Profiles

The API exposes deployment capabilities at `GET /api/v1/capabilities/`. The frontend uses that response to hide unavailable Avatar, Intelligence, and visual moderation UI while keeping ordinary upload, render, watch, catalog, settings, and text moderation flows available.

Do not commit real `.env` files, SQLite databases, storage roots, media, model caches, or generated render output.

## Minimal on-premise

Use this profile for CPU-only or small local installs where the platform should render lessons and serve catalog/watch flows without optional heavy services.

```env
ENABLE_AVATAR=0
ENABLE_INTELLIGENCE=0
ENABLE_VISUAL_MODERATION=0
ENABLE_LOCAL_XTTS=1
```

Behavior:

- Avatar UI is hidden, avatar endpoints return disabled responses, and render jobs ignore avatar options.
- Studio Intelligence and Analytics Smart Insights are hidden, and intelligence scheduler tasks are skipped.
- Source/text moderation can still run if configured. Visual/OCR/frame scans are marked skipped/disabled, not approved.
- TTS uses XTTS when available and keeps existing fallback behavior when XTTS is unavailable.

## Full on-premise

Use this profile when the host has the local services and GPU capacity for the optional paths.

```env
ENABLE_AVATAR=1
ENABLE_INTELLIGENCE=1
ENABLE_LOCAL_OLLAMA=1
ENABLE_VISUAL_MODERATION=1
ENABLE_LOCAL_XTTS=1
```

Also configure the provider-specific values that the enabled services need, such as avatar engine paths, Ollama base URL/model names, OCR/visual moderation providers, and Celery queues.

## Cloud/production

Production flags should reflect the hardware and managed services available in that deployment. Enable each feature only when its backing service, queue, model files, credentials, and operational monitoring are ready.

Recommended hardware planning:

- No avatar/intelligence: CPU, RAM, and NVMe storage are usually enough for basic upload, render, watch, catalog, and moderation workflows.
- Local XTTS: NVIDIA GPU with 12GB+ VRAM is preferred.
- Avatar and Intelligence: 24-48GB+ GPU capacity is recommended depending on model choice, concurrency, and lesson volume.

## Compatibility notes

- `ENABLE_AVATAR` defaults off, but existing avatar engine env vars still imply enabled when the master flag is unset.
- `ENABLE_INTELLIGENCE` defaults off, but existing `LESSON_INTELLIGENCE_ENABLED=true` or `ANALYTICS_INTELLIGENCE_ENABLED=true` still imply enabled when the master flag is unset.
- `ENABLE_LOCAL_OLLAMA` is only effective when Intelligence is enabled.
- `ENABLE_VISUAL_MODERATION` defaults off, but existing visual/OCR/provider env vars still imply enabled when the master flag is unset.
- `ENABLE_LOCAL_XTTS` defaults on for current local TTS compatibility; fallback TTS behavior remains available when XTTS is disabled or unavailable.
