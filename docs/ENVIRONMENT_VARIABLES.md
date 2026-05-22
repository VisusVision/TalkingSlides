# Environment Variables

This reference is based on `infra/.env.example`. Never commit real values, real `.env` files, provider credentials, signing keys, OAuth secrets, DRM secrets, or generated media paths.

Profile-specific placeholder examples live in:

- `infra/env.local.example`
- `infra/env.staging.example`
- `infra/env.production-secure-stream.example`
- `infra/env.production-drm-protected.example`

Use `scripts/check-production-env.ps1` to validate staging and production-like examples before adapting them to real deployment secrets.

The heavy local services are feature-gated. Minimal local/on-premise deployments can leave Avatar, Intelligence, and visual moderation off; the API exposes the resolved values at `GET /api/v1/capabilities/` so the frontend can hide unavailable UI.

Columns:

- Local: required for ordinary local development.
- Prod: required for production or production-like deployment.
- Default/example: value shown in `infra/.env.example` or the documented local default.

## Django and Security

| Variable | Service | Local | Prod | Default/example | Meaning |
| --- | --- | --- | --- | --- | --- |
| `DJANGO_SETTINGS_MODULE` | API/worker | Yes | Yes | `config.settings` | Django settings module. |
| `SECRET_KEY` | API/worker | Yes | Yes | placeholder | Django signing secret. Use a strong production secret. |
| `DEBUG` | API | Yes | Yes | `True` | Must be `False` in production. |
| `ALLOWED_HOSTS` | API | Yes | Yes | `localhost,127.0.0.1` | Comma-separated API hostnames. No wildcard in production unless explicitly allowed. |
| `CSRF_TRUSTED_ORIGINS` | API | Recommended | Yes | local HTTP origins | HTTPS origins trusted for CSRF. |
| `CORS_ALLOWED_ORIGINS` | API | Recommended | Yes | local frontend origin | Explicit browser origins allowed to call the API. |
| `CORS_ALLOW_ALL_ORIGINS` | API | Optional | No | `True` for local | Local convenience only; production guard rejects allow-all. |
| `API_PUBLIC_BASE_URL` | API | Optional | Yes | `http://localhost:8000` | Public API origin used when request context is unavailable. |
| `GOOGLE_AUTH_ENABLED` | API/frontend | Optional | Optional | `0` | Enables Google auth when OAuth config is present. |
| `GOOGLE_CLIENT_ID` | API/frontend | Optional | If enabled | placeholder | Google OAuth client ID. |
| `GOOGLE_CLIENT_SECRET` | API | Optional | If enabled | placeholder | Google OAuth secret. Never expose in frontend. |
| `GOOGLE_REDIRECT_URI` | API | Optional | If enabled | local callback | Registered Google callback URL. |
| `GOOGLE_REDIRECT_SUCCESS_URL` | API/frontend | Optional | If enabled | local frontend | Frontend URL after OAuth callback. |

## Database, Redis, and Celery

| Variable | Service | Local | Prod | Default/example | Meaning |
| --- | --- | --- | --- | --- | --- |
| `POSTGRES_HOST` | API/worker | Docker local | Yes | `postgres` | Enables Postgres. Missing host falls back to SQLite only in local debug. |
| `POSTGRES_PORT` | API/worker | Docker local | Yes | `5432` | Postgres port. |
| `POSTGRES_DB` | API/worker | Docker local | Yes | `academy_db` | Database name. |
| `POSTGRES_USER` | API/worker | Docker local | Yes | `academy_user` | Database user. |
| `POSTGRES_PASSWORD` | API/worker | Docker local | Yes | placeholder | Database password. Secret. |
| `DATABASE_URL` | Optional tooling | Optional | Optional | constructed example | Present for compatibility; current settings use individual `POSTGRES_*` vars. |
| `REDIS_URL` | API/worker | Yes | Yes | `redis://redis:6379/0` | Redis connection base. |
| `CELERY_BROKER_URL` | worker/API dispatch | Yes | Yes | Redis URL | Celery broker. |
| `CELERY_RESULT_BACKEND` | worker/API | Yes | Yes | Redis URL | Celery result backend. |
| `CELERY_PREFETCH_MULTIPLIER` | worker | Optional | Recommended | `1` | Worker prefetch control. |
| `CELERY_RENDER_QUEUE` | API/worker | Optional | Recommended | `render` | Render queue name. |
| `CELERY_AVATAR_QUEUE` | API/avatar worker | Optional | Recommended | `avatar` | Avatar queue name. |
| `CELERY_RENDER_WORKER_CONCURRENCY` | render worker | Optional | Recommended | `1` | Render worker concurrency. |
| `CELERY_AVATAR_WORKER_CONCURRENCY` | avatar worker | Optional | Recommended | `1` | Avatar worker concurrency. Keep at 1 per GPU until validated. |
| `CELERY_WORKER_QUEUES` | worker | Optional | Recommended | `render` | Queues consumed by a worker process. |

## Storage

| Variable | Service | Local | Prod | Default/example | Meaning |
| --- | --- | --- | --- | --- | --- |
| `STORAGE_BACKEND` | API/worker/TTS | Optional | Planned | `local` | Documents intended storage backend. Active code uses filesystem paths. |
| `STORAGE_ROOT` | API/worker/TTS | Yes | Yes | `/app/storage_local` | Shared media root. Must be durable in production. |
| `MEDIA_TOKEN_SECRET` | API | Yes | Yes | placeholder | HMAC secret for media tokens. Strong secret required in production. |
| `MEDIA_TOKEN_TTL_SECONDS` | API | Optional | Recommended | `14400` | Default media token TTL. |
| `LOCAL_STORAGE_PERMISSIVE_CHMOD` | containers | Optional | No | `1` | Local Docker bind-mount permission helper. |
| `LOCAL_STORAGE_DIR_MODE` | containers | Optional | No | `0777` | Local storage directory mode. |
| `MINIO_ROOT_USER` | MinIO | Optional | Optional | placeholder | Local/future MinIO root user. |
| `MINIO_ROOT_PASSWORD` | MinIO | Optional | Optional | placeholder | MinIO root password. Secret. |
| `MINIO_ENDPOINT` | MinIO/S3 clients | Optional | Optional | `minio:9000` | MinIO API endpoint. |
| `MINIO_BUCKET_NAME` | MinIO/S3 clients | Optional | Optional | `academy-media` | Bucket name. |
| `MINIO_USE_SSL` | MinIO/S3 clients | Optional | Optional | `False` | MinIO SSL toggle. |
| `AWS_ACCESS_KEY_ID` | future S3 | Optional | If S3 used | `${MINIO_ROOT_USER}` | S3 access key. Secret in production. |
| `AWS_SECRET_ACCESS_KEY` | future S3 | Optional | If S3 used | `${MINIO_ROOT_PASSWORD}` | S3 secret key. Secret. |
| `AWS_STORAGE_BUCKET_NAME` | future S3 | Optional | If S3 used | `${MINIO_BUCKET_NAME}` | S3 bucket. |
| `AWS_S3_ENDPOINT_URL` | future S3 | Optional | If S3 used | local MinIO URL | S3-compatible endpoint. |
| `GCS_BUCKET_NAME` | future GCS | Optional | If GCS used | placeholder | Google Cloud Storage bucket. |
| `GCS_PROJECT_ID` | future GCS | Optional | If GCS used | placeholder | GCP project. |
| `GOOGLE_APPLICATION_CREDENTIALS` | future GCS | Optional | If GCS used | placeholder path | Service account path. Secret material must not be committed. |

## Media, Playback, HLS, and DRM

| Variable | Service | Local | Prod | Default/example | Meaning |
| --- | --- | --- | --- | --- | --- |
| `DRM_ENABLED` | API/frontend policy | Optional | If DRM | `0` | Enables DRM contract. |
| `DRM_PROVIDER_NAME` | API | Optional | If DRM | `external` | Provider label. |
| `DRM_PREFERRED_SYSTEM` | API | Optional | If DRM | `widevine` | Preferred DRM system. |
| `DRM_KEY_SYSTEM` | API | Optional | Legacy | empty | Legacy single-system key system. |
| `DRM_LICENSE_URL` | API/player | Optional | If DRM | empty | License server URL. Provider-managed. |
| `DRM_CERTIFICATE_URL` | API/player | Optional | FairPlay | empty | Certificate URL. |
| `DRM_ASSET_ID_PREFIX` | API | Optional | If DRM | `lesson-` | Asset ID prefix. |
| `DRM_CONTENT_ID_PREFIX` | API | Optional | If DRM | `project-` | Content ID prefix. |
| `DRM_PLAYBACK_SESSION_PREFIX` | API | Optional | If DRM | `playback` | Playback session ID prefix. |
| `DRM_WIDEVINE_ENABLED` | API | Optional | If Widevine | `0` | Enables Widevine metadata. |
| `DRM_WIDEVINE_KEY_SYSTEM` | API/player | Optional | If Widevine | `com.widevine.alpha` | Widevine EME key system. |
| `DRM_WIDEVINE_LICENSE_URL` | API/player | Optional | If Widevine | empty | Widevine license URL. |
| `DRM_WIDEVINE_CERTIFICATE_URL` | API/player | Optional | Optional | empty | Widevine certificate URL if needed. |
| `DRM_WIDEVINE_CONTENT_TYPE` | API/player | Optional | If Widevine | `video/mp4` | Content type hint. |
| `DRM_PLAYREADY_ENABLED` | API | Optional | If PlayReady | `0` | Enables PlayReady metadata. |
| `DRM_PLAYREADY_KEY_SYSTEM` | API/player | Optional | If PlayReady | `com.microsoft.playready` | PlayReady EME key system. |
| `DRM_PLAYREADY_LICENSE_URL` | API/player | Optional | If PlayReady | empty | PlayReady license URL. |
| `DRM_PLAYREADY_CERTIFICATE_URL` | API/player | Optional | Optional | empty | PlayReady certificate URL if needed. |
| `DRM_PLAYREADY_CONTENT_TYPE` | API/player | Optional | If PlayReady | `video/mp4` | Content type hint. |
| `DRM_FAIRPLAY_ENABLED` | API | Optional | If FairPlay | `0` | Enables FairPlay metadata. |
| `DRM_FAIRPLAY_KEY_SYSTEM` | API/player | Optional | If FairPlay | `com.apple.fps.1_0` | FairPlay key system. |
| `DRM_FAIRPLAY_LICENSE_URL` | API/player | Optional | If FairPlay | empty | FairPlay license URL. |
| `DRM_FAIRPLAY_CERTIFICATE_URL` | API/player | Optional | If FairPlay | empty | FairPlay certificate URL. |
| `DRM_FAIRPLAY_CONTENT_TYPE` | API/player | Optional | If FairPlay | `application/vnd.apple.mpegurl` | FairPlay content type. |
| `DRM_STREAMING_ENABLED` | worker/API | Optional | Recommended | `0` local | Enables DRM/HLS streaming sidecar behavior. |
| `DRM_HLS_ENCRYPTION_ENABLED` | worker/API | Optional | If protected HLS | `0` | Enables HLS encryption packaging. |
| `DRM_HLS_KEY_ROTATION_SECONDS` | worker/API | Optional | Optional | `0` | HLS key rotation interval. |
| `LESSON_PROTECTION_DEFAULT_MODE` | API | Optional | Yes | `public` local | `public`, `secure_stream`, or `drm_protected`. |
| `LESSON_PROTECTION_ALLOW_MP4_FALLBACK` | API | Optional | Recommended | `1` | Allows MP4 fallback outside strict DRM mode. |
| `LESSON_PROTECTION_FORCE_WATERMARK_FOR_PROTECTED` | API | Optional | Recommended | `0` local | Forces watermark in protected modes. |
| `LESSON_PROTECTION_TOKEN_TTL_PUBLIC_SECONDS` | API | Optional | Recommended | `14400` | Public token TTL. |
| `LESSON_PROTECTION_TOKEN_TTL_SECURE_SECONDS` | API | Optional | Recommended | `14400` | Secure-stream token TTL. |
| `LESSON_PROTECTION_TOKEN_TTL_DRM_SECONDS` | API | Optional | If DRM | `600` | DRM token TTL. |
| `LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION` | API | Optional | Recommended | `1` | Binds playback to session. |
| `LESSON_PROTECTION_REQUIRE_HLS_ENCRYPTION_FOR_DRM` | API | Optional | If DRM | `0` local | Requires encrypted HLS for DRM mode. |
| `LESSON_PROTECTION_REQUIRE_DRM_METADATA_FOR_DRM` | API | Optional | If DRM | `1` | Requires DRM metadata before DRM playback. |

## TTS and Pronunciation

| Variable | Service | Local | Prod | Default/example | Meaning |
| --- | --- | --- | --- | --- | --- |
| `TTS_SERVICE_URL` | API/worker | Yes | Yes | `http://tts_service:8001` | TTS service URL. |
| `TTS_PREPROCESSING_ENABLED` | TTS/scripts | Optional | Recommended | `true` | Enables text preprocessing. |
| `TTS_MAX_CHARS_PER_CHUNK` | TTS | Optional | Recommended | `500` | Hard chunk size. |
| `TTS_TARGET_CHARS_PER_CHUNK` | TTS | Optional | Recommended | `280` | Preferred chunk size. |
| `TTS_SENTENCE_PAUSE_MS` | TTS | Optional | Optional | `250` | Sentence pause. |
| `TTS_PARAGRAPH_PAUSE_MS` | TTS | Optional | Optional | `450` | Paragraph pause. |
| `TTS_SLIDE_PAUSE_MS` | TTS | Optional | Optional | `700` | Slide pause. |
| `TTS_GLOSSARY_PATH` | TTS | Optional | Recommended | `/app/tts_preprocess/glossary.json` | Glossary path. |
| `XTTS_ENABLED` | TTS | Optional | If XTTS | `1` | Enables XTTS attempts. |
| `ENABLE_LOCAL_XTTS` | API/frontend/TTS | Optional | Optional | `1` | Deployment capability flag for local XTTS. When disabled or unavailable, existing gTTS/silence fallback behavior continues. `XTTS_ENABLED` remains a backward-compatible alias for the TTS service. |
| `XTTS_PRELOAD_ON_STARTUP` | TTS | Optional | Optional | `1` | Preloads XTTS model. |
| `XTTS_WARMUP_BLOCKING` | TTS | Optional | Optional | `0` | Blocks startup for warmup when enabled. |
| `XTTS_LOAD_RECOVERY_ATTEMPTS` | TTS | Optional | Recommended | `2` | XTTS transient recovery attempts. |
| `XTTS_LOAD_RECOVERY_BACKOFF_SEC` | TTS | Optional | Recommended | `2.0` | Recovery backoff. |
| `TTS_LLM_SUGGESTIONS_ENABLED` | API/frontend | Optional | Optional | `false` | Enables Studio-only pronunciation suggestions. |
| `TTS_LLM_PROVIDER` | API | Optional | If suggestions | `ollama` | Suggestion provider. |
| `OLLAMA_BASE_URL` | API/TTS | Optional | If Ollama | local host URL | Ollama endpoint. |
| `OLLAMA_PRONUNCIATION_MODEL` | API | Optional | If suggestions | `llama3.1:8b` | Pronunciation suggestion model. |
| `TTS_LLM_SUGGESTION_TIMEOUT_SECONDS` | API | Optional | If suggestions | `8` | Suggestion timeout. |
| `TTS_LLM_MAX_TERMS` | API | Optional | If suggestions | `20` | Max terms per request. |
| `TTS_LLM_CONTEXT_MAX_CHARS` | API | Optional | If suggestions | `1000` | Prompt context limit. |
| `OPENAI_API_KEY`, `ELEVENLABS_API_KEY` | TTS/API if enabled | No | If provider used | commented placeholders | External provider keys. Secrets. |

## Lesson and Analytics Intelligence

| Variable | Service | Local | Prod | Default/example | Meaning |
| --- | --- | --- | --- | --- | --- |
| `ENABLE_INTELLIGENCE` | API/frontend/worker | Optional | Optional | `0` | Master deployment flag for Lesson Intelligence and Analytics Intelligence. When disabled, API endpoints return a disabled response, automatic scheduling is skipped, and frontend intelligence UI is hidden. Existing `LESSON_INTELLIGENCE_ENABLED=true` or `ANALYTICS_INTELLIGENCE_ENABLED=true` still imply enabled for backward compatibility when `ENABLE_INTELLIGENCE` is unset. |
| `ENABLE_LOCAL_OLLAMA` | API/worker | Optional | If Ollama | `0` unless intelligence uses Ollama | Enables local Ollama enhancement under the master intelligence flag. If disabled, heuristic intelligence can still run when intelligence is enabled. |
| `LESSON_INTELLIGENCE_ENABLED` | API/frontend | Optional | Optional | follows `ENABLE_INTELLIGENCE` | Enables lesson quality analysis under the master intelligence flag. |
| `LESSON_INTELLIGENCE_PROVIDER_CHAIN` | API | Optional | Optional | `heuristic` | Provider order, for example `ollama,heuristic`. |
| `ANALYTICS_INTELLIGENCE_ENABLED` | API/frontend | Optional | Optional | follows `ENABLE_INTELLIGENCE` | Enables creator analytics insights under the master intelligence flag. |
| `ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN` | API | Optional | Optional | `heuristic` | Provider order, for example `ollama,heuristic`. |
| `LESSON_INTELLIGENCE_TIMEOUT_SECONDS` | API | Optional | If Ollama | `30` | Configured provider timeout before sync cap. |
| `ANALYTICS_INTELLIGENCE_TIMEOUT_SECONDS` | API | Optional | If Ollama | `30` | Configured provider timeout before sync cap. |
| `INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS` | API | Optional | Recommended | `20` | Upper bound for synchronous Ollama calls so API workers can return fallback before Gunicorn timeout. |
| `INTELLIGENCE_BACKGROUND_ENHANCEMENT_ENABLED` | API/worker | Optional | Recommended | `true` | Queues background Ollama enhancement while returning heuristic reports immediately. |
| `INTELLIGENCE_HARDWARE_PROFILE` | API/worker | Optional | Recommended | `local_mid` | Selects model/chunk/timeout defaults. Allowed: `local_low`, `local_mid`, `production_gpu`. Explicit model and timeout env vars still win. |
| `INTELLIGENCE_BACKGROUND_PROVIDER_TIMEOUT_SECONDS` | worker | Optional | Legacy/fallback | `120` | Legacy background Ollama timeout input. Adaptive background settings now determine the effective timeout. |
| `INTELLIGENCE_BACKGROUND_TIMEOUT_MIN_SECONDS` | worker | Optional | Recommended | `60` | Minimum adaptive background intelligence timeout. |
| `INTELLIGENCE_BACKGROUND_TIMEOUT_MAX_SECONDS` | worker | Optional | Recommended | `300` | Maximum adaptive background intelligence timeout. |
| `INTELLIGENCE_BACKGROUND_TIMEOUT_PER_1000_CHARS` | worker | Optional | Recommended | `4` | Additional adaptive timeout per 1000 input characters. |
| `INTELLIGENCE_BACKGROUND_TIMEOUT_PER_PAGE_SECONDS` | worker | Optional | Recommended | `2` | Additional adaptive timeout per lesson page or analytics row. |
| `INTELLIGENCE_BACKGROUND_TIMEOUT_PER_COMMENT_SECONDS` | worker | Optional | Recommended | `1` | Additional adaptive timeout per recent analytics comment. |
| `INTELLIGENCE_OLLAMA_CHUNK_MAX_CHARS` | worker | Optional | Recommended | `6000` | Maximum lesson/analytics content target for one background Ollama chunk. |
| `INTELLIGENCE_OLLAMA_CHUNK_MAX_PAGES` | worker | Optional | Optional | `8` | Maximum lesson pages grouped into one Ollama lesson chunk. |
| `INTELLIGENCE_OLLAMA_CHUNK_MAX_ITEMS` | worker | Optional | Optional | `10` | Maximum analytics rows grouped into one Ollama analytics chunk. |
| `INTELLIGENCE_OLLAMA_CHUNK_ROW_THRESHOLD` | worker | Optional | Optional | `40` | Analytics row count that can still use one-shot Ollama when prompt size is small. |
| `INTELLIGENCE_OLLAMA_CHUNK_CONCURRENCY` | worker | Optional | Optional | profile default | Profile-aware local Ollama chunk concurrency hint. Keep `1` for local CPU/shared hosts; production GPU can use `2-4` if the Ollama host can handle it. |
| `INTELLIGENCE_OLLAMA_CHUNK_TIMEOUT_MIN_SECONDS` | worker | Optional | Recommended | profile default | Minimum timeout for one background Ollama chunk request. Local profiles default to `130` so `qwen2.5:7b` has enough time to finish compact JSON chunks on CPU-bound machines. |
| `INTELLIGENCE_OLLAMA_CHUNK_TIMEOUT_MAX_SECONDS` | worker | Optional | Recommended | profile default | Maximum timeout for one background Ollama chunk request. Local profiles default to `240`; analytics still has a lower total task budget. |
| `INTELLIGENCE_OLLAMA_TOTAL_TIMEOUT_MAX_SECONDS` | worker | Optional | Recommended | `600` | Maximum total budget for one chunked Ollama enhancement task. |
| `INTELLIGENCE_RETRY_COOLDOWN_SECONDS` | API/frontend | Optional | Recommended | `60` | Cooldown before a manual Retry Ollama click can create a new attempt after an Ollama fallback failure. `force=true` bypasses it for explicit user actions. |
| `ANALYTICS_INTELLIGENCE_MAX_BACKGROUND_SECONDS` | worker | Optional | Recommended | `180` | Analytics-specific total background budget; defaults lower than lesson/shared budget so large analytics jobs terminalize sooner. |
| `ANALYTICS_INTELLIGENCE_AUTO_ENABLED` | API/worker | Optional | Recommended | `true` | Enables event-driven creator analytics intelligence scheduling. |
| `ANALYTICS_INTELLIGENCE_MIN_AUTO_INTERVAL_SECONDS` | API/worker | Optional | Recommended | `3600` | Minimum interval between automatic analytics intelligence schedules for routine events. |
| `ANALYTICS_INTELLIGENCE_MIN_PROGRESS_EVENT_DELTA` | API/worker | Optional | Recommended | `5` | Progress-event threshold used with throttling before scheduling analytics intelligence again. |
| `ANALYTICS_INTELLIGENCE_RECENT_COMMENTS_LIMIT` | API/worker | Optional | Recommended | `20` | Max recent comments included as sanitized qualitative analytics feedback. |
| `ANALYTICS_INTELLIGENCE_COMMENT_MAX_CHARS` | API/worker | Optional | Recommended | `280` | Max characters per recent comment included in analytics intelligence input. |
| `INTELLIGENCE_CELERY_QUEUE` | API/worker | Optional | Recommended | render queue | Queue used for progressive intelligence enhancement tasks. Local compose consumes `render` by default; use a dedicated queue only if a worker consumes it. |
| `INTELLIGENCE_CELERY_QUEUE_DEFAULT` | API/worker | Optional | Optional | render queue | Fallback queue name used when `INTELLIGENCE_CELERY_QUEUE` is unset. |
| `INTELLIGENCE_LESSON_CELERY_QUEUE` | API/worker | Optional | Optional | `INTELLIGENCE_CELERY_QUEUE` | Queue for lesson intelligence schedule/enhancement tasks. Use a dedicated higher-priority worker if lessons should not wait behind analytics. |
| `INTELLIGENCE_ANALYTICS_CELERY_QUEUE` | API/worker | Optional | Optional | `INTELLIGENCE_CELERY_QUEUE` | Queue for analytics intelligence schedule/enhancement tasks. |
| `INTELLIGENCE_RECOMMENDED_DEDICATED_QUEUE` | docs/config | Optional | Optional | `intelligence` | Documented target queue name for dedicated low-priority intelligence workers. |
| `INTELLIGENCE_ENHANCEMENT_STALE_SECONDS` | API | Optional | Recommended | `900` | Pending/running enhancement age before it is marked failed so polling can stop and re-analyze can queue again. |
| `LESSON_INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS` | API | Optional | Recommended | global cap | Lesson-specific synchronous cap. |
| `ANALYTICS_INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS` | API | Optional | Recommended | global cap | Analytics-specific synchronous cap. |
| `LESSON_INTELLIGENCE_BACKGROUND_PROVIDER_TIMEOUT_SECONDS` | worker | Optional | Recommended | global background timeout | Lesson-specific background Ollama timeout. |
| `ANALYTICS_INTELLIGENCE_BACKGROUND_PROVIDER_TIMEOUT_SECONDS` | worker | Optional | Recommended | global background timeout | Analytics-specific background Ollama timeout. |
| `CELERY_INTELLIGENCE_QUEUE` | API/worker | Optional | Optional | legacy alias | Backward-compatible alias for `INTELLIGENCE_CELERY_QUEUE`. |
| `OLLAMA_LESSON_INTELLIGENCE_BASE_URL`, `OLLAMA_ANALYTICS_INTELLIGENCE_BASE_URL` | API | Optional | If Ollama | `OLLAMA_BASE_URL` fallback | Local Ollama endpoints. |
| `OLLAMA_LESSON_INTELLIGENCE_MODEL`, `OLLAMA_ANALYTICS_INTELLIGENCE_MODEL` | API | Optional | If Ollama | profile default | Local Ollama models. Environment overrides win. Missing models are reported as safe Ollama failures with heuristic fallback. |
| `OLLAMA_LESSON_INTELLIGENCE_NUM_PREDICT`, `OLLAMA_ANALYTICS_INTELLIGENCE_NUM_PREDICT` | API/worker | Optional | Recommended | `900` / `700` | Maximum Ollama generated tokens per request to keep local JSON responses bounded. |

Keep synchronous Ollama timeout caps lower than the API/Gunicorn worker timeout. Docker uses Gunicorn without an explicit `--timeout`, so the effective default is 30 seconds; a provider timeout above that can kill the worker before heuristic fallback is returned. Long-running local LLM analysis should use the background job/polling flow before raising these caps.

For the current local worker, use `INTELLIGENCE_CELERY_QUEUE=render`. If you prefer `INTELLIGENCE_CELERY_QUEUE=celery`, configure `CELERY_WORKER_QUEUES=celery,render` or run a dedicated worker for `celery`.

Production should prefer dedicated intelligence workers with concurrency `1`. Use `INTELLIGENCE_LESSON_CELERY_QUEUE` and `INTELLIGENCE_ANALYTICS_CELERY_QUEUE` if lesson enhancement should have priority over long analytics jobs; otherwise both default to `INTELLIGENCE_CELERY_QUEUE`.

Studio Intelligence is the detailed lesson analyzer. Analytics Intelligence should stay compact: it uses creator metrics, weak/strong lesson stats, sanitized comments, cover signals, and selected `LessonIntelligenceReport` summaries instead of full transcripts.

## Subtitle Translation and Moderation-adjacent Providers

| Variable | Service | Local | Prod | Default/example | Meaning |
| --- | --- | --- | --- | --- | --- |
| `SUBTITLE_TRANSLATION_ENABLED` | API/worker | Optional | Optional | `true` | Enables subtitle generation support. |
| `SUBTITLE_TRANSLATION_PROVIDER` | API/worker | Optional | Optional | `auto` | Provider selection. |
| `SUBTITLE_TRANSLATION_PROVIDER_CHAIN` | API/worker | Optional | Optional | `api,ollama,libretranslate,argos,mock` | Provider fallback order. |
| `SUBTITLE_TRANSLATION_ALLOW_MOCK_FALLBACK` | worker | Optional | No for prod | `true` | Dev/demo mock fallback. |
| `SUBTITLE_TRANSLATION_TIMEOUT_SECONDS` | worker | Optional | Recommended | `20` | Provider timeout. |
| `SUBTITLE_TRANSLATION_TARGET_LANGUAGES` | API | Optional | Optional | empty | Allowed/target languages. |
| `SUBTITLE_PUBLIC_REQUESTS_ENABLED` | API | Optional | Optional | `true` | Enables public Watch requests. |
| `SUBTITLE_PUBLIC_REQUEST_LANGUAGE_ALLOWLIST` | API | Optional | Recommended | language list | Public language allowlist. |
| `SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_PER_HOUR` | API | Optional | Recommended | `10` | Authenticated request rate. |
| `SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_ANON_PER_HOUR` | API | Optional | Recommended | `5` | Anonymous request rate. |
| `SUBTITLE_PUBLIC_REQUEST_LOCK_SECONDS` | API | Optional | Recommended | `300` | Generation lock TTL. |
| `SUBTITLE_PUBLIC_REQUEST_MAX_ACTIVE_PER_PROJECT` | API | Optional | Recommended | `3` | Active generation cap. |
| `SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK` | API/worker | Optional | No by default | commented false | Explicit prod mock fallback override. |
| `SUBTITLE_TRANSLATION_API_PROVIDER` | worker | Optional | If API provider | empty | External translation provider label. |
| `SUBTITLE_TRANSLATION_API_BASE_URL` | worker | Optional | If API provider | empty | External provider base URL. |
| `SUBTITLE_TRANSLATION_API_KEY` | worker | Optional | If API provider | empty | External provider key. Secret. |
| `SUBTITLE_TRANSLATION_API_MODEL` | worker | Optional | If API provider | empty | External model name. |
| `OLLAMA_TRANSLATION_ENABLED` | worker | Optional | If Ollama | `true` | Enables local Ollama translation provider. |
| `OLLAMA_TRANSLATION_BASE_URL` | worker | Optional | If Ollama | host URL | Ollama translation endpoint. |
| `OLLAMA_TRANSLATION_MODEL` | worker | Optional | If Ollama | `qwen2.5:7b-instruct` | Translation model. |
| `OLLAMA_TRANSLATION_TIMEOUT_SECONDS` | worker | Optional | If Ollama | `60` | Translation timeout. |
| `OLLAMA_TRANSLATION_MAX_CUES_PER_BATCH` | worker | Optional | If Ollama | `40` | Cue batch size. |
| `OLLAMA_TRANSLATION_MAX_CHARS_PER_BATCH` | worker | Optional | If Ollama | `6000` | Batch char limit. |
| `LIBRETRANSLATE_BASE_URL` | worker | Optional | If used | `http://localhost:5000` | LibreTranslate endpoint. |
| `LIBRETRANSLATE_API_KEY` | worker | Optional | If used | empty | LibreTranslate key. Secret if set. |
| `ARGOS_TRANSLATE_ENABLED` | worker | Optional | If used | `true` | Enables Argos fallback. |
| `ARGOS_TRANSLATE_PACKAGES_DIR` | worker | Optional | If used | empty | Argos package directory. |
| `ARGOS_TRANSLATE_AUTO_INSTALL` | worker | Optional | Usually no | `false` | Auto-install Argos packages. |

## Avatar and GPU

| Variable | Service | Local | Prod | Default/example | Meaning |
| --- | --- | --- | --- | --- | --- |
| `ENABLE_AVATAR` | API/frontend/worker-avatar | Optional | Optional | `0` | Master deployment flag for avatar profile, preview, overlay, and render scheduling. When disabled, avatar endpoints return disabled responses, render jobs ignore avatar options, worker avatar scheduling is skipped, and frontend avatar UI is hidden. Existing avatar engine env vars still imply enabled when this is unset. |
| `AVATAR_ENGINE` | API/worker-avatar | Optional | If avatar | `liveportrait+musetalk` | Selected avatar engine chain. |
| `AVATAR_BOOTSTRAP_ON_WORKER_STARTUP` | worker-avatar | Optional | Recommended | `0` local template | Controls runtime bootstrap. |
| `MUSETALK_HOME`, `MUSETALK_MODEL_PATH`, `MUSETALK_ENGINE_VERSION` | worker-avatar | If avatar | If avatar | `/opt/musetalk`, model path | MuseTalk runtime/model config. |
| `AVATAR_LIVEPORTRAIT_HOME`, `AVATAR_LIVEPORTRAIT_MODEL_PATH`, `AVATAR_LIVEPORTRAIT_ENTRYPOINT` | worker-avatar | If avatar | If avatar | `/opt/liveportrait` paths | LivePortrait runtime/model config. |
| `AVATAR_LIVEPORTRAIT_CMD`, `AVATAR_MUSETALK_CMD` | worker-avatar | If avatar | If avatar | runner commands | Real engine command templates. |
| `AVATAR_LIVEPORTRAIT_CALM_IMAGE_TEMPLATE` | worker-avatar | Optional | Recommended when available | `storage_local/avatar_templates/calm_lecture_driver.mp4` | External calm lecture driving template. This is media and must not be committed. |
| `AVATAR_LIVEPORTRAIT_VETTED_IMAGE_TEMPLATE` | worker-avatar | If avatar | If avatar | d11 template path | Vetted placeholder image driving template fallback. |
| `AVATAR_LIVEPORTRAIT_DRIVER_SOURCE_POLICY` | worker-avatar | Optional | Recommended blank unless overriding | blank, `calm_template_for_image`, `vetted_template_for_image`, `composer_for_image` | Blank auto-selects a valid calm template, otherwise falls back to d11. Composer is explicit/debug-only. |
| `AVATAR_LIVEPORTRAIT_ALLOW_COMPOSER_FALLBACK` | worker-avatar | Optional | Optional/debug | `0` | Allows composer fallback when no usable template is available. Keep off for production calm-template routing. |
| `AVATAR_LIVEPORTRAIT_ALLOW_VETTED_TEMPLATE_FALLBACK` | worker-avatar | Optional | Recommended until calm template is proven | `1` | Allows d11 fallback when a configured calm template is missing or invalid. |
| `AVATAR_MUSETALK_SERVICE_ENABLED`, `AVATAR_MUSETALK_SERVICE_PORT`, `AVATAR_MUSETALK_ROUTE` | worker-avatar | If avatar | If avatar | service route values | Persistent MuseTalk service behavior. |
| `AVATAR_PREVIEW_USE_LIVEPORTRAIT`, `AVATAR_PREVIEW_USE_MUSETALK`, `AVATAR_PREVIEW_USE_RESTORATION` | worker-avatar | Optional | Recommended | `1`, `1`, `0` | Preview stage toggles. |
| `AVATAR_ENABLE_COMPOSITE_LESSON`, `AVATAR_ALLOW_COMPOSITE_FALLBACK` | worker-avatar | Optional | Optional | `0`, `0` | Composite lesson controls. |
| `AVATAR_GPU_SERIAL_LOCK_ENABLED`, `AVATAR_GPU_SERIAL_LOCK_PATH` | worker-avatar | Optional | Recommended | `1`, lock path | Serializes GPU jobs. |
| `AVATAR_STAGE_TIMEOUT_*`, `AVATAR_ORCH_*`, `AVATAR_PREVIEW_*_TIMEOUT_*` | worker-avatar | Optional | Recommended | see template | Stage and orchestration timeouts. |
| `AVATAR_PREVIEW_LIVEPORTRAIT_*`, `AVATAR_LIVEPORTRAIT_STABILIZE_*`, `AVATAR_MIN_EYE_BLINK_CHANGE_COMPOSER` | worker-avatar | Optional | Tuning | see template | Motion/stability validation tuning. |
| `AVATAR_PREVIEW_RESTORE_CMD`, `AVATAR_PREVIEW_RESTORATION_MODEL` | worker-avatar | Optional | If restoration | empty, `codeformer` | Restoration command/model. |
| `AVATAR_REAL_FALLBACK_ENGINE`, `AVATAR_SADTALKER_CMD`, `AVATAR_WAV2LIP_*`, `AVATAR_VIDEO_REFERENCE_CMD` | worker-avatar | Optional | If enabled | empty/commented | Optional fallback engines. |
| `TORCH_HOME`, `XDG_CACHE_HOME` | worker-avatar/TTS | Optional | Recommended | storage cache paths | Model/cache locations. |

## Moderation

Many moderation flags are defined in Django settings and documented in [MODERATION_OPERATIONS.md](MODERATION_OPERATIONS.md). Add them to deployment env only when the corresponding provider or automation path is ready.

| Variable | Service | Local | Prod | Default/example | Meaning |
| --- | --- | --- | --- | --- | --- |
| `SOURCE_MODERATION_AUTO_ENABLED` | worker/API | Optional | Recommended after validation | `false` | Runs source/text moderation automatically. |
| `SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION` | worker/API | Optional | Recommended | `true` | Blocks render on rejected source when source moderation is enabled. |
| `SOURCE_MODERATION_PHASE` | worker/API | Optional | Optional | `source_scan` | Moderation phase label. |
| `ENABLE_VISUAL_MODERATION` | API/frontend/worker | Optional | Optional | `0` | Master deployment flag for visual asset, OCR, and video frame visual checks. Text/source moderation can still run while this is disabled. Disabled visual scans are recorded as skipped/disabled instead of approved. Existing visual/OCR/provider env vars still imply enabled when this is unset. |
| `VISUAL_MODERATION_AUTO_ENABLED` | worker/API | Optional | Optional | `false` | Enables visual asset moderation automation. |
| `VISUAL_MODERATION_BLOCK_RENDER_ON_REJECTION` | worker/API | Optional | Policy-dependent | `false` | Blocks render on visual rejection. |
| `VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION` | API | Optional | Policy-dependent | `false` | Blocks publishing when visual findings reject content. |
| `VISUAL_MODERATION_PHASE` | API | Optional | Optional | `visual_asset_scan` | Visual moderation phase label. |
| `VISUAL_MODERATION_SCAN_COVER` | worker/API | Optional | Optional | `true` | Includes cover images. |
| `VISUAL_MODERATION_SCAN_SLIDES` | worker/API | Optional | Optional | `true` | Includes slide images. |
| `AZURE_CONTENT_SAFETY_ENABLED` | API/worker | Optional | If Azure used | `false` | Enables Azure Content Safety provider. |
| `AZURE_CONTENT_SAFETY_ENDPOINT` | API/worker | Optional | If Azure used | empty | Azure endpoint. |
| `AZURE_CONTENT_SAFETY_KEY` | API/worker | No | If Azure used | empty | Azure key. Secret. |
| `AZURE_CONTENT_SAFETY_API_VERSION` | API/worker | Optional | If Azure used | `2024-09-01` | Azure API version. |
| `AZURE_CONTENT_SAFETY_CATEGORIES` | API/worker | Optional | If Azure used | `sexual,violence,self_harm,hate` | Categories to request. |
| `AZURE_CONTENT_SAFETY_BLOCK_SEVERITY` | API/worker | Optional | If Azure used | `4` | Severity threshold. |
| `AVATAR_IMAGE_MODERATION_AUTO_ENABLED` | API/worker | Optional | Recommended for avatar production | `false` | Enables avatar source image moderation. |
| `AVATAR_IMAGE_MODERATION_BLOCK_ON_REJECTION` | API/worker | Optional | Recommended | `true` | Blocks rejected avatar sources. |
| `AVATAR_IMAGE_MODERATION_REQUIRE_APPROVAL` | API/worker | Optional | Policy-dependent | `false` | Requires explicit approval before avatar generation. |
| `OCR_MODERATION_AUTO_ENABLED` | worker/API | Optional | Optional | `false` | Enables OCR moderation. |
| `OCR_MODERATION_BLOCK_RENDER_ON_REJECTION` | worker/API | Optional | Policy-dependent | `false` | Blocks render on OCR rejection. |
| `OCR_MODERATION_PHASE` | worker/API | Optional | Optional | `ocr_slide_scan` | OCR moderation phase label. |
| `OCR_MODERATION_SCAN_SLIDES` | worker/API | Optional | Optional | `true` | Includes slide images for OCR. |
| `OCR_MODERATION_PROVIDER` | worker/API | Optional | If OCR used | `noop` | OCR provider. |
| `AZURE_OCR_ENABLED` | worker/API | Optional | If Azure OCR used | `false` | Enables Azure OCR. |
| `AZURE_OCR_ENDPOINT` | worker/API | Optional | If Azure OCR used | empty | Azure OCR endpoint. |
| `AZURE_OCR_KEY` | worker/API | No | If Azure OCR used | empty | Azure OCR key. Secret. |
| `AZURE_OCR_API_VERSION` | worker/API | Optional | If Azure OCR used | `2024-02-29-preview` | Azure OCR API version. |
| `AZURE_OCR_MODEL` | worker/API | Optional | If Azure OCR used | `prebuilt-read` | Azure OCR model. |
| `AZURE_OCR_TIMEOUT_SECONDS` | worker/API | Optional | If Azure OCR used | `30` | OCR timeout. |
| `AZURE_OCR_MAX_IMAGE_BYTES` | worker/API | Optional | If Azure OCR used | `10485760` | OCR image size cap. |
| `AZURE_OCR_LANG_HINTS` | worker/API | Optional | If Azure OCR used | `en,tr,ar` | OCR language hints. |
| `VIDEO_FRAME_AUDIT_AUTO_ENABLED` | worker/API | Optional | Optional | `false` | Enables post-render frame audit. |
| `VIDEO_FRAME_AUDIT_PHASE` | worker/API | Optional | Optional | `video_frame_audit` | Frame audit phase label. |
| `VIDEO_FRAME_AUDIT_EVERY_SECONDS` | worker/API | Optional | Optional | `10` | Sampling interval. |
| `VIDEO_FRAME_AUDIT_MAX_FRAMES` | worker/API | Optional | Optional | `5` | Max sampled frames. |
| `VIDEO_FRAME_AUDIT_RUN_VISUAL_CHECK` | worker/API | Optional | Optional | `true` | Runs visual check on sampled frames. |
| `VIDEO_FRAME_AUDIT_RUN_OCR` | worker/API | Optional | Optional | `false` | Runs OCR on sampled frames. |
| `VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION` | API | Optional | Policy-dependent | `false` | Blocks publish on rejected frame audit. |
| `VIDEO_FRAME_AUDIT_RETAIN_FRAMES` | worker/API | Optional | Usually no | `false` | Retains sampled frames. |
| `VIDEO_FRAME_AUDIT_FRAME_RETENTION_DAYS` | worker/API | Optional | If retaining | `7` | Retention window. |
| `VIDEO_FRAME_AUDIT_CLEANUP_ON_SUCCESS` | worker/API | Optional | Recommended | `true` | Deletes sampled frames after successful audit. |
| `AI_AGENTS_LOCAL_LLM_ENABLED` | API | Optional | Optional | `false` | Enables local LLM moderation provider. |
| `AI_AGENTS_OLLAMA_BASE_URL` | API | Optional | If local LLM | `http://localhost:11434` | Local LLM endpoint. |
| `AI_AGENTS_TEXT_MODEL` | API | Optional | If local LLM | `qwen2.5:7b-instruct` | Text moderation model. |
| `AI_AGENTS_LLM_TIMEOUT_SECONDS` | API | Optional | If local LLM | `8` | LLM moderation timeout. |
| `TRANSLATION_MODERATION_ENABLED` | API/worker | Optional | Optional | `false` | Enables translation-to-English moderation bridge. |
| `TRANSLATION_MODERATION_PROVIDER` | API/worker | Optional | If enabled | `none` | Translation moderation provider. |
| `TRANSLATION_MODERATION_TIMEOUT_SECONDS` | API/worker | Optional | If enabled | `20` | Provider timeout. |
| `TRANSLATION_MODERATION_TARGET_LANGUAGE` | API/worker | Optional | If enabled | `en` | Bridge target language. |
| `TRANSLATION_MODERATION_BASE_URL` | API/worker | Optional | If enabled | local service URL | Translation moderation endpoint. |

## Frontend Vite Flags

| Variable | Service | Local | Prod | Default/example | Meaning |
| --- | --- | --- | --- | --- | --- |
| `VITE_API_BASE_URL` | frontend | Yes | Yes | `http://localhost:8000/api/v1` | Browser API base URL. |
| `VITE_PLAYER_ENABLE_HLS` | frontend | Optional | Recommended | `true` | Enables HLS player path. |
| `VITE_PLAYER_ENABLE_DRM_SHAKA` | frontend | Optional | If DRM | `false` | Enables DRM/Shaka path when implemented/configured. |
| `VITE_PLAYER_WATERMARK_ENABLED` | frontend | Optional | Recommended | `true` | Enables watermark UI behavior. |
| `VITE_PLAYER_VISIBILITY_LOCK_ENABLED` | frontend | Optional | Recommended | `true` | Enables visibility lock behavior. |
| `VITE_PLAYER_HEARTBEAT_ENABLED` | frontend | Optional | Recommended | `true` | Enables playback heartbeat. |
| `VITE_PLAYER_HEARTBEAT_INTERVAL_MS` | frontend | Optional | Recommended | `25000` | Heartbeat interval. |

## Production Required Minimum

At minimum, production should provide:

```text
DEBUG=False
SECRET_KEY=<strong secret>
POSTGRES_HOST=<host>
POSTGRES_DB=<db>
POSTGRES_USER=<user>
POSTGRES_PASSWORD=<secret>
REDIS_URL=<redis>
MEDIA_TOKEN_SECRET=<strong secret>
ALLOWED_HOSTS=<api domain>
CSRF_TRUSTED_ORIGINS=https://<api-or-frontend-domain>
CORS_ALLOWED_ORIGINS=https://<frontend-domain>
API_PUBLIC_BASE_URL=https://<api-domain>
VITE_API_BASE_URL=https://<api-domain>/api/v1
```
