# Subtitle Translation Provider Options

Subtitle translation is an owner-triggered sidecar generation flow. It creates
translated SRT/WebVTT files from canonical original display-caption cues and
copies the original cue timing. It does not mutate transcripts, TTS settings,
audio, video renders, or original captions.

## Defaults

```env
SUBTITLE_TRANSLATION_ENABLED=true
SUBTITLE_TRANSLATION_PROVIDER=auto
SUBTITLE_TRANSLATION_PROVIDER_CHAIN=api,ollama,libretranslate,argos,mock
SUBTITLE_TRANSLATION_ALLOW_MOCK_FALLBACK=true
SUBTITLE_TRANSLATION_TIMEOUT_SECONDS=20
```

The feature is enabled by default because generation is explicit in Studio and
does not require a paid provider. Rerendering a lesson still creates only
original-language captions.

## Public Request Guardrails

Watch-page public subtitle requests are enabled separately from the provider
chain and are constrained to published, playable lessons:

```env
SUBTITLE_PUBLIC_REQUESTS_ENABLED=true
SUBTITLE_PUBLIC_REQUEST_LANGUAGE_ALLOWLIST=en,ar,tr,fr,de,es
SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_PER_HOUR=10
SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_ANON_PER_HOUR=5
SUBTITLE_PUBLIC_REQUEST_LOCK_SECONDS=300
SUBTITLE_PUBLIC_REQUEST_MAX_ACTIVE_PER_PROJECT=3
SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK=false
```

Public requests use cache-backed rate limits by user/IP plus project, a
project/language generation lock, and the language allowlist above. Owner/staff
Studio generation is intentionally broader and can generate draft/private lesson
tracks.

Missing tracks are not generated inside the API request. The endpoint creates or
reuses a `TranslatedSubtitleTrack` with `status=processing`, enqueues
`worker.tasks.generate_translated_subtitle_track_task`, and returns HTTP 202.
Ready tracks still return immediately with tokenized SRT/VTT URLs. Watch and
Studio poll `GET /api/v1/projects/<id>/subtitle-tracks/` until the track becomes
`ready` or `failed`.

## Auto Provider Chain

`auto` tries providers in `SUBTITLE_TRANSLATION_PROVIDER_CHAIN` order and stops
at the first successful provider:

1. `api`
2. `ollama`
3. `libretranslate`
4. `argos`
5. `mock`

Each generated `TranslatedSubtitleTrack.metadata` records:

- `provider_requested`
- `provider_used`
- `provider_chain_attempts`
- `fallback_used`
- `source_language_code`
- `target_language_code`

If all providers fail or are unavailable, the track is marked `failed` with a
safe readable error and no raw traceback in API output.

## Context-Aware Cue Batching

Translation uses canonical original display-caption cues, not TTS phonetic or
spoken text. Providers receive bounded cue batches with cue IDs, page keys,
chunk indexes, and text. The provider may return translated text only. The
system validates cue count, order, IDs, and timing before writing SRT/WebVTT.

Original cue timings are copied exactly into translated tracks. Providers must
not merge cues, split cues, add timestamps to text, or return explanations.

## API Provider Foundation

The `api` provider is a generic HTTP foundation for a future production
translation provider. It is skipped unless all required settings are present:

```env
SUBTITLE_TRANSLATION_API_PROVIDER=
SUBTITLE_TRANSLATION_API_BASE_URL=
SUBTITLE_TRANSLATION_API_KEY=
SUBTITLE_TRANSLATION_API_MODEL=
```

No paid provider is called by default. Do not commit real API keys.

## Ollama Local LLM

Ollama is the preferred free/local provider for broad language support:

```env
OLLAMA_TRANSLATION_ENABLED=true
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_TRANSLATION_BASE_URL=http://host.docker.internal:11434
OLLAMA_TRANSLATION_MODEL=qwen2.5:7b-instruct
OLLAMA_TRANSLATION_TIMEOUT_SECONDS=60
OLLAMA_TRANSLATION_MAX_CUES_PER_BATCH=40
OLLAMA_TRANSLATION_MAX_CHARS_PER_BATCH=6000
```

`OLLAMA_TRANSLATION_BASE_URL` defaults from `OLLAMA_BASE_URL` when it is not set.
The provider posts contextual cue batches to `/api/generate` and requires JSON
only responses shaped as:

```json
{
  "translations": [
    {"cue_id": "page-key:0", "text": "Translated subtitle text"}
  ]
}
```

If Ollama is unreachable, disabled, or the model is missing, auto mode continues
to LibreTranslate, Argos, then mock if mock fallback is allowed. Subtitle text is
sent only to the configured local Ollama endpoint; tests do not require Ollama.

## LibreTranslate

LibreTranslate can be self-hosted locally. For production, use a dedicated server.
For local development, it is available as an optional Docker Compose service:

```bash
# Start LibreTranslate (may take several minutes to download models)
docker compose -f infra/docker-compose.yml --profile translation up -d libretranslate
```

### Configuration

When running in Compose, the API reaches it via the service name:

```env
LIBRETRANSLATE_BASE_URL=http://libretranslate:5000
LIBRETRANSLATE_API_KEY=
```

When running standalone on the host:

```env
LIBRETRANSLATE_BASE_URL=http://host.docker.internal:5000
```

The provider posts to `/translate`. If the service is unreachable, times out, or
returns an invalid response, auto mode continues to the next provider.

If the optional Compose profile uses `LT_LOAD_ONLY=en,tr`, LibreTranslate only
supports English/Turkish. Keep Ollama earlier in the chain for broader local
language coverage without downloading every LibreTranslate model.

## Argos Translate

Argos is optional and loaded lazily:

```env
ARGOS_TRANSLATE_ENABLED=true
ARGOS_TRANSLATE_PACKAGES_DIR=
ARGOS_TRANSLATE_AUTO_INSTALL=false
```

The Python package and language packages are not required for tests or local
development. If the package or language pair is unavailable, auto mode continues
to the next provider. Package auto-install is disabled unless explicitly enabled.

## Mock Provider

The `mock` provider remains available for development, tests, and demos. It is
not a real translation provider; it prefixes text with the target language code,
for example `[en] Original caption text`.

Disable mock fallback when validating production provider readiness:

```env
SUBTITLE_TRANSLATION_ALLOW_MOCK_FALLBACK=false
```

For public production-like requests, also keep:

```env
SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK=false
```

This prevents public requests from silently returning fake translations when the
real/local providers are unavailable.

## Product Phases

- Current: model, provider chain, mock generation, Studio generation action,
  Watch user-language request dropdown, and public-request hardening for
  on-demand cached tracks.
- Current final CC phase: Watch shows ready tracks and lets viewers request a
  missing supported language; the backend generates or reuses a cached
  `TranslatedSubtitleTrack` asynchronously with rate limits, language allowlist,
  generation lock, and production mock fallback guard.
- Later: provider admin settings, usage reporting, production monitoring, and a
  paid API provider configuration path.
