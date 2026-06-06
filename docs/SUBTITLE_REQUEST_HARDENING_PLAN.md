# Subtitle Request Hardening Plan

Phase 6D1 adds backend guardrails for public Watch-page subtitle language
requests. Translations are still generated on demand, cached as
`TranslatedSubtitleTrack`, and never triggered by video rerender.

Subtitle provider calls are asynchronous. The API creates or reuses a processing
track, enqueues `worker.tasks.generate_translated_subtitle_track_task`, and
returns HTTP 202 instead of waiting for Ollama, LibreTranslate, Argos, or API
provider I/O. Clients poll the subtitle-track list until the track becomes
`ready` or `failed`.

## Public Request Policy

Public requests are allowed only for published, playable lessons. Owner/staff
Studio requests keep the existing broader behavior for draft/private lessons.

```env
SUBTITLE_PUBLIC_REQUESTS_ENABLED=true
SUBTITLE_PUBLIC_REQUEST_LANGUAGE_ALLOWLIST=en,ar,tr,fr,de,es
SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_PER_HOUR=10
SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_ANON_PER_HOUR=5
SUBTITLE_PUBLIC_REQUEST_LOCK_SECONDS=300
SUBTITLE_PUBLIC_REQUEST_MAX_ACTIVE_PER_PROJECT=3
```

Unsupported public languages return:

```json
{
  "error": "unsupported_language",
  "details": "This subtitle language is not available for public requests."
}
```

## Rate Limits

Public request limits are cache-backed fixed-window counters scoped to:

- authenticated user id + project id
- anonymous IP address + project id

Rate-limited requests return HTTP 429:

```json
{
  "error": "rate_limited",
  "details": "Too many subtitle generation requests. Try again later."
}
```

Ready translated tracks are returned immediately and do not consume generation
capacity or locks.

## Generation Locks

The backend uses a cache lock per project/language:

```text
subtitle-generate:<project_id>:<language_code>
```

If another request is already generating that language before a processing track
is visible, the endpoint returns HTTP 409:

```json
{
  "error": "generation_in_progress",
  "details": "Subtitle generation for this language is already in progress."
}
```

The lock is released by the background task and expires after
`SUBTITLE_PUBLIC_REQUEST_LOCK_SECONDS`. Once a processing track exists, repeat
requests return HTTP 202 with that track instead of starting another provider
call.

## Mock Fallback Safety

Mock translation remains available for dev, demos, and tests, but public
production-like requests should not silently return fake `[lang]` text.

```env
SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK=false
```

In DEBUG/dev this can be enabled explicitly for local QA. In production, keep it
disabled unless fake demo translations are intentionally acceptable.

## Production TODOs

- Add durable quota tracking beyond cache counters.
- Add provider cost and latency dashboards.
- Add admin controls for public language allowlists.
- Add a dedicated async translation queue if render-queue sharing becomes too
  noisy at scale.
- Add abuse monitoring and alerting.
