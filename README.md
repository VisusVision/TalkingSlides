# AI_ACADEMY

AI_ACADEMY is an AI lecture and video generation system. It turns uploaded lesson source files into narrated lesson videos, stores the generated assets, and exposes a student catalog, lesson player, teacher studio, avatar setup area, and analytics UI.

The repository currently contains a Django REST API, a FastAPI TTS service, a Celery worker pipeline, a Vite React frontend, Docker Compose infrastructure, and integration tests. Some advanced systems are present only as backend/configuration work and are not fully connected in the frontend yet.

## What The System Currently Does

- Accepts lesson uploads from the API and Studio UI using PPTX, PDF, DOCX, TXT, plus optional cover images.
- Extracts slide/page images and speaker notes or source text, with fallbacks when external renderers are unavailable.
- Splits long narration into transcript pages and TTS chunks.
- Generates narration through the TTS service using XTTS v2 when a voice sample exists, then gTTS, then silent audio fallback.
- Applies deterministic Turkish and English TTS preprocessing, including language-specific glossary terms and Turkish number/symbol handling.
- Exposes fail-open TTS preview endpoints for pronunciation checks without starting audio synthesis.
- Renders slide videos, concatenates them into MP4 output, and generates original-language SRT/WebVTT subtitles.
- Stores generated output under `STORAGE_ROOT`, usually `storage_local`.
- Exposes tokenized media playback endpoints and a DRM/HLS playback contract.
- Provides a React frontend for browse, watch, studio, library, settings, and analytics pages.
- Provides teacher avatar upload, validation, preview, and worker-side LivePortrait plus MuseTalk pipeline code.

## High-Level Architecture

| Layer | Current Role | Main Files |
| --- | --- | --- |
| Frontend | Vite/React app for catalog, player, studio, settings, library, analytics | `services/frontend/src`, `services/frontend/package.json` |
| API | Django REST API for projects, auth, catalog, playback tokens, transcript, avatar, social actions | `services/api/config/settings.py`, `services/api/core/views.py`, `services/api/core/urls.py` |
| TTS service | FastAPI service for XTTS v2, gTTS, and silent fallback synthesis | `services/tts_service/main.py`, `services/tts_service/tts_preprocess/` |
| Worker | Celery video generation pipeline, slide extraction, TTS orchestration, video rendering, avatar jobs | `services/worker/tasks.py`, `services/worker/celery_app.py` |
| Shared scripts | FFmpeg helpers, slide extraction, text segmentation, TTS client, avatar runners | `services/scripts/` |
| Avatar runtime | Canonical avatar input, LivePortrait and MuseTalk adapters, validation, timeout handling | `services/avatar/`, `services/worker/avatar_preview_flow.py`, `services/worker/bootstrap_musetalk.py` |
| Database | Django models and migrations for projects, jobs, transcripts, social data, avatar state | `services/api/core/models.py`, `services/api/core/migrations/` |
| Infrastructure | Docker Compose for API, TTS, worker, Redis, Postgres, MinIO, frontend | `infra/docker-compose.yml`, `infra/dockerfiles/` |

## Main Services And Modules

### Backend API

The Django API owns users, projects, jobs, categories, transcripts, public catalog metadata, playback tokens, media streaming, avatar profile state, and social actions.

Key endpoints are registered in `services/api/core/urls.py`:

- Auth: `/api/v1/auth/login/`, `/api/v1/auth/logout/`, `/api/v1/auth/me/`, Google auth endpoints.
- Project upload and status: `/api/v1/projects/`, `/api/v1/projects/<id>/jobs/<job_id>/`.
- Transcript and rerender: `/api/v1/projects/<id>/transcript/`, `/api/v1/projects/<id>/rerender/`.
- Voice/avatar setup: `/api/v1/users/<id>/voice/`, `/api/v1/users/<id>/avatar/`, avatar prepare and preview endpoints.
- TTS preview: `/api/v1/tts/preview/` proxies to the TTS service preview endpoint for fail-open normalization checks.
- Catalog and social: `/api/v1/catalog/`, `/api/v1/catalog/feed/`, like/progress/comments endpoints.
- Secure playback: `/api/v1/projects/<id>/playback-token/`, `/api/v1/stream/<token>/`.

### Frontend

The active frontend is `services/frontend`, not the static prototype HTML files under top-level `frontend/`.

Current React pages:

- `Home.jsx`: discovery dashboard using catalog/feed data.
- `Browse.jsx`: public catalog browsing and category filtering.
- `Watch.jsx`: lesson player, transcript panel, chapters, local notes, progress save for signed-in users.
- `Studio.jsx`: teacher/publisher project upload, project list, Studio editor workspace, transcript/slides/notes/TTS panels, subtitle track generation, and rerender controls.
- `Settings.jsx`: theme settings plus teacher voice/avatar sample upload and avatar preview flow.
- `Library.jsx`: signed-in lesson workspace and local notes.
- `Analytics.jsx`: admin/project analytics UI with fallback estimates.

The frontend reads `VITE_API_BASE_URL` in `services/frontend/src/api.js` and falls back to `http://localhost:8000/api/v1`.

### Worker And Background Processing

Celery is configured in `services/worker/celery_app.py` and the main task graph is in `services/worker/tasks.py`.

The main video path is:

1. API upload creates a `Project` and `Job`.
2. API enqueues `worker.tasks.process_pptx_to_video`.
3. Worker exports images and notes/text using `services/scripts/pptx_extract.py`.
4. Worker builds transcript pages and TTS chunks using `services/scripts/text_segmentation.py`.
5. Worker calls TTS through `services/scripts/tts_client.py`.
6. Worker renders per-page videos with `services/scripts/ffmpeg_helpers.py`.
7. Worker concatenates output, writes original-language SRT/WebVTT subtitles, syncs transcript timings, writes `playback_assets.json`, and marks the job done.
8. Optional avatar segment rendering can run if avatar options and runtime requirements are satisfied.

Redis is the broker/result backend through `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND`.

### TTS And Voice Cloning

The TTS flow is split between the worker client and FastAPI service:

- Worker prepares text before sending it to TTS: `services/scripts/tts_client.py`.
- TTS service accepts already-prepared chunks: `services/tts_service/main.py`.
- XTTS v2 is attempted when a `voice_id` is present and a reference file exists under `{STORAGE_ROOT}/voices/<voice_id>.wav`.
- gTTS is used as the next fallback.
- Silent MP3 fallback is generated when XTTS and gTTS cannot produce audio.
- Transient XTTS model-load/network/runtime failures reset the cached XTTS state and retry before the existing gTTS/silent fallback chain is used.
- Phase TTS-D1A adds a deterministic in-memory acronym and technical-term resolver after manual/project overrides and glossary processing. Phase TTS-D1B shows `unknown_terms` and `ambiguous_terms` in Studio preview and lets teachers add detected terms to unsaved override rows without blocking render. Current QA coverage verifies Turkish `ASP -> ey es pi` and case-insensitive `Pipeline/pipeline -> payp layn`.
- Phase TTS-L1A/L1B/L1C adds optional Studio pronunciation suggestions for D1 unknown or ambiguous terms plus local Ollama setup docs. The endpoint is disabled by default unless configured, suggestions are teacher-reviewed only, accepted suggestions become normal manual/project overrides, and render/rerender do not call LLM providers.

**TTS Preview Endpoints (Phase 1)**

- `POST /normalization/preview` on the TTS service accepts text and optional manual overrides (technical, abbreviation, mixed-word) and returns normalized/spoken text without synthesizing audio.
- `POST /api/v1/tts/preview/` on the Django API proxies to the TTS service preview endpoint for Studio/frontend usage.
- Preview is fail-open: network errors fall back to local `prepare_text_for_tts` with D1 resolver metadata and then to original text.
- Manual request overrides are protected from downstream re-normalization using placeholder token masking. Example: override `ChatGPT` → `"chat gpt"` stays as `"chat gpt"` in spoken text, not re-normalized to `"chat Ci Pi Ti"`.
- Override priority: `mixed_word_overrides` > `abbreviation_overrides` > `technical_overrides` > default glossary/normalization.
- Request-local overrides do not modify the persistent `glossary.json`.

**Project TTS Settings Persistence (Phase 2)**

- `Project.tts_settings` stores project-level TTS preferences in Django with canonical defaults.
- `ProjectSerializer` returns canonical settings for detail/list responses.
- `PATCH /api/v1/projects/<project_id>/` can update persisted TTS settings.
- Multipart project upload rejects inline `tts_settings`; settings must be updated with project PATCH after upload.

**Project TTS Settings Generation Wiring (Phase 3)**

- Upload, full rerender, and transcript-triggered rerender paths pass saved project `tts_settings` into the Celery worker.
- The worker passes settings through `tts_client` to synthesis while preserving XTTS → gTTS → silent fallback.
- `provider_preference` is advisory only: it is persisted and carried in metadata, but synthesis does not hard-fail when a preferred provider is unavailable.
- Runtime overrides affect spoken TTS audio text only; captions/SRT and transcript display remain based on original narration text.

**Studio TTS Controls (Phase 4)**

- Studio exposes a `TTS` panel inside the Studio Editor workspace for saving project-level TTS settings and manual pronunciation overrides.
- Studio can preview pronunciation through the backend preview endpoint without synthesizing audio.
- Studio can rerender after saving settings; preview output never mutates transcript text or captions.

**Optional LLM Pronunciation Suggestions (TTS-L1A/L1B)**

- `POST /api/v1/tts/pronunciation-suggestions/` is authenticated and disabled by default.
- The endpoint is fail-open and returns no suggestions unless `TTS_LLM_SUGGESTIONS_ENABLED=true`.
- Optional Ollama calls are capped, timeout-bound, JSON-validated, and sanitized.
- Studio can request suggestions for selected D1 unknown/ambiguous terms after preview.
- Teachers can edit the suggested spoken form/category, accept into draft override rows, or ignore locally.
- Suggestions do not mutate transcripts, project `tts_settings`, render jobs, captions, or synthesis inputs until the teacher explicitly saves accepted draft overrides.
- Local Ollama is optional: install/start Ollama, run `ollama pull llama3.1:8b`, and for Docker Desktop on Windows use `OLLAMA_BASE_URL=http://host.docker.internal:11434`.

Suggested local opt-in config:

```env
TTS_LLM_SUGGESTIONS_ENABLED=true
TTS_LLM_PROVIDER=ollama
OLLAMA_PRONUNCIATION_MODEL=llama3.1:8b
TTS_LLM_SUGGESTION_TIMEOUT_SECONDS=8
TTS_LLM_MAX_TERMS=20
TTS_LLM_CONTEXT_MAX_CHARS=1000
```

Troubleshooting is intentionally fail-open: disabled mode shows a disabled message, provider timeout or malformed provider output returns a readable warning, no suggestions returned still leaves manual override rows available, and rerender uses only saved deterministic overrides.

Text preprocessing lives in `services/tts_service/tts_preprocess/`:

- `normalizer.py`: prepares original, normalized, and spoken text metadata.
- `tr_normalizer.py`: Turkish number/symbol/currency/range handling and Turkish abbreviation protection.
- `glossary.py`: language-keyed glossary loading and longest-match substitution.
- `deterministic_resolver.py`: cached acronym, Turkish-known-word, and curated English technical-term lookup with no network/LLM/DB calls.
- `segmenter.py`: protected span and chunk splitting logic.
- `glossary.json`: default English and Turkish product/phrase pronunciation glossary. Acronyms live in `acronym_pronunciations.json`.

Global glossary CRUD is not implemented. Optional Llama/Ollama pronunciation suggestions have TTS-L1A/L1B Studio-only support and are not part of the render path. XTTS runtime recovery is implemented separately from dictionary and suggestion workflows.

### Video Rendering

Video rendering uses local files under `STORAGE_ROOT`:

- Slide/page images and source text come from `services/scripts/pptx_extract.py`.
- Transcript segmentation comes from `services/scripts/text_segmentation.py`.
- Static slide videos, concatenation, SRT generation, and HLS packaging are in `services/scripts/ffmpeg_helpers.py`.
- Final playback metadata is written by `services/worker/tasks.py` as `playback_assets.json`.

HLS packaging exists in the worker, but the React player is currently a plain `<video src={lesson.stream_url}>` implementation in `services/frontend/src/components/player/VideoStage.jsx`.

### Avatar Flow

Avatar support is partially implemented across backend, frontend settings, and worker code:

- Teacher profile fields and avatar render jobs are modeled in `services/api/core/models.py`.
- Avatar profile upload, prepare, preview, preview status, delete, and overlay endpoints are in `services/api/core/views.py`.
- Frontend voice/avatar sample upload and preview polling are in `services/frontend/src/pages/Settings.jsx`.
- Worker preview and lesson segment rendering are in `services/worker/avatar_preview_flow.py` and `services/worker/tasks.py`.
- LivePortrait plus MuseTalk runtime bootstrapping is in `services/worker/bootstrap_musetalk.py`.
- Canonical avatar pipeline and validation are in `services/avatar/`.

The generated lesson avatar track and `avatar_overlay` playback payload are not rendered by the current React player.

### DRM, Secure Streaming, And Video Protection

The backend and worker contain a playback protection contract:

- Tokenized streaming endpoints: `services/api/core/views.py`.
- Playback token payload with watermark, visibility lock, HLS, DRM metadata, session binding, and heartbeat support: `services/api/core/views.py`.
- HLS packaging and optional AES-128 segment encryption: `services/scripts/ffmpeg_helpers.py`, `services/worker/tasks.py`.
- DRM configuration variables for Widevine, PlayReady, and FairPlay: `services/api/config/settings.py`, `infra/.env.example`.
- Secure playback tests: `tests/integration/test_secure_playback.py`.

The frontend parses some of this payload in `services/frontend/src/api.js`, but `VideoStage.jsx` does not initialize HLS.js, EME/DRM, Shaka, watermarks, visibility lock, or playback heartbeats.

### Storage

The active code uses local filesystem storage through `STORAGE_ROOT`:

- API upload, cover, avatar, voice, media streaming: `services/api/core/views.py`.
- Worker project workspaces and outputs: `services/worker/tasks.py`.
- TTS audio and voice references: `services/tts_service/main.py`.

For local Docker development, repo-root `storage_local/` is the canonical host
runtime storage directory. `infra/docker-compose.yml` mounts it into API, worker,
and TTS containers as `/app/storage_local`, and containers keep
`STORAGE_ROOT=/app/storage_local`. The old `infra/storage_local/` location came
from compose-relative mounts and is no longer the intended local storage root.
Do not commit runtime media. Production should use a mounted external volume or
a future S3/MinIO storage adapter.

Docker Compose defines a MinIO service and `infra/.env.example` defines MinIO/AWS-style variables, but repository code inspection did not find boto3, django-storages, or S3 client usage. MinIO is configured but not active in application storage flow.

## Main User Roles

| Role | Status | Evidence / Main Files |
| --- | --- | --- |
| Student | Implemented | `services/api/core/models.py`, `services/frontend/src/pages/Watch.jsx`, `services/frontend/src/pages/Library.jsx` |
| Teacher | Implemented | `services/api/core/models.py`, `services/frontend/src/lib/auth.js`, `services/frontend/src/pages/Studio.jsx`, `services/frontend/src/pages/Settings.jsx` |
| Publisher | UI exists, backend not wired | UI allows `publisher` in `services/frontend/src/lib/auth.js`; backend `UserProfile.ROLE_CHOICES` has only `teacher` and `student` in `services/api/core/models.py` |
| Staff/admin | Partially implemented | Django staff checks and admin stats in `services/api/core/views.py`; no dedicated admin frontend beyond analytics page |
| Guest/unauthenticated user | Partially implemented | Public catalog/watch APIs exist; several project endpoints use `AllowAny` in `services/api/core/views.py` |

## Main User Flows

| Flow | Status | Evidence / Main Files |
| --- | --- | --- |
| Browse public lessons | Partially implemented | `services/frontend/src/pages/Home.jsx`, `Browse.jsx`, `services/api/core/views.py` |
| Watch generated lesson video | Partially implemented | `services/frontend/src/pages/Watch.jsx`, `VideoStage.jsx`, `services/api/core/views.py` |
| Upload source file and create video job | Implemented | `services/frontend/src/pages/Studio.jsx`, `services/api/core/views.py`, `services/worker/tasks.py` |
| Edit transcript and rerender | Implemented | `services/api/core/views.py`, `services/frontend/src/pages/Studio.jsx`, `services/frontend/src/components/studio/TranscriptEditorPanel.jsx`, `tests/integration/test_transcript_editor_pipeline.py` |
| Upload XTTS voice sample | Implemented | `services/frontend/src/pages/Settings.jsx`, `services/api/core/views.py`, `services/tts_service/main.py` |
| Upload avatar source and preview | Partially implemented | `services/frontend/src/pages/Settings.jsx`, `services/api/core/views.py`, `services/worker/avatar_preview_flow.py` |
| Like/comment/progress | Backend implemented, UI not wired | `services/api/core/views.py`, `services/frontend/src/api.js`; Watch UI only saves progress |
| Secure/DRM playback | Backend implemented, UI not wired | `services/api/core/views.py`, `services/scripts/ffmpeg_helpers.py`, `services/frontend/src/components/player/VideoStage.jsx` |

## Setup

For the short coworker setup path, start with `docs/LOCAL_DEVELOPMENT_QUICKSTART.md`. For a focused command checklist, use `docs/REPO_HEALTH_CHECK.md`.

### Docker Compose

Docker Compose is defined in `infra/docker-compose.yml`.

```powershell
Copy-Item infra/.env.example infra/.env
docker compose -f infra/docker-compose.yml up --build

# To start optional translation services (e.g. LibreTranslate):
docker compose -f infra/docker-compose.yml --profile translation up -d libretranslate
```

Local runtime media is stored under repo-root `storage_local/` and mounted into
containers as `/app/storage_local`.

Services exposed by Compose:

- API: `http://localhost:8000`
- TTS service: `http://localhost:8001`
- Frontend: `http://localhost:3000`
- Redis: `localhost:6379`
- Postgres: `localhost:5432`
- MinIO API/console: `localhost:9000`, `localhost:9001`

The worker image bootstraps LivePortrait and MuseTalk before starting Celery. A GPU-capable Docker environment and the expected model/runtime paths are required for the full avatar path. API/frontend/TTS development may be easier to run separately if avatar runtime bootstrapping is not available.

### Local API

```powershell
cd services/api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DJANGO_SETTINGS_MODULE = "config.settings"
python manage.py migrate
python manage.py runserver 8000
```

Without `POSTGRES_HOST`, the API falls back to SQLite at `services/api/db.sqlite3`.

### Local TTS Service

```powershell
cd services/tts_service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:STORAGE_ROOT = "..\..\storage_local"
uvicorn main:app --host 0.0.0.0 --port 8001
```

Set `XTTS_ENABLED=0` to skip XTTS attempts and rely on gTTS/silent fallback during lightweight development.

Use `XTTS_LOAD_RECOVERY_ATTEMPTS` and `XTTS_LOAD_RECOVERY_BACKOFF_SEC` to tune retries for likely transient XTTS model-load/network failures. `HF_HUB_DISABLE_SSL_VERIFY` is intentionally not enabled by default; prefer a populated local model cache and use SSL bypass only as an emergency local/dev workaround.

### Local Worker

From the repository root:

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.settings"
$env:PYTHONPATH = "$PWD\services\api;$PWD\services;$PWD\services\scripts;$PWD\services\tts_service"
$env:STORAGE_ROOT = "$PWD\storage_local"
$env:TTS_SERVICE_URL = "http://localhost:8001"
celery -A worker worker --loglevel=info --pool=solo
```

On Linux/macOS, use `:` instead of `;` in `PYTHONPATH`.

### Local Frontend

```powershell
cd services/frontend
npm install
npm run dev -- --host 0.0.0.0 --port 3000
```

The frontend currently expects the API at `http://localhost:8000/api/v1`.

## Environment Variables

Use `infra/.env.example` as the starting point. Review values before running anything production-like.

| Area | Variables | Current Notes |
| --- | --- | --- |
| Django | `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DJANGO_SETTINGS_MODULE` | `settings.py` expects `config.settings`; local shell runs can rely on the SQLite fallback when `POSTGRES_HOST` is unset |
| Database | `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `SQLITE_TEST_DATABASE_PATH` | Postgres when host is set, SQLite otherwise |
| Redis/Celery | `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `CELERY_WORKER_CONCURRENCY`, `CELERY_PREFETCH_MULTIPLIER`, `CELERY_WORKER_QUEUES` | Used by API settings and Celery worker |
| Storage | `STORAGE_ROOT`, `API_PUBLIC_BASE_URL`, `MEDIA_TOKEN_SECRET`, `MEDIA_TOKEN_TTL_SECONDS` | Active local filesystem storage root |
| MinIO/S3 | `MINIO_*`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_ENDPOINT_URL` | Configured but not active in code paths found |
| TTS | `TTS_SERVICE_URL`, `TTS_SERVICE_TIMEOUT`, `TTS_READY_TIMEOUT`, `TTS_PREPROCESSING_ENABLED`, `TTS_GLOSSARY_PATH`, `XTTS_ENABLED`, `XTTS_PRELOAD_ON_STARTUP`, `XTTS_WARMUP_BLOCKING`, `XTTS_LOAD_RECOVERY_ATTEMPTS`, `XTTS_LOAD_RECOVERY_BACKOFF_SEC`, `TTS_AUDIO_DIR`, `TTS_FALLBACK_DURATION`, `TTS_LLM_SUGGESTIONS_ENABLED`, `TTS_LLM_PROVIDER`, `OLLAMA_BASE_URL`, `OLLAMA_PRONUNCIATION_MODEL`, `TTS_LLM_SUGGESTION_TIMEOUT_SECONDS`, `TTS_LLM_MAX_TERMS`, `TTS_LLM_CONTEXT_MAX_CHARS` | `.env.example` glossary path points to `/app/tts_preprocess/glossary.json`; XTTS transient recovery retries before the existing gTTS/silent fallback chain; L1A/L1B suggestion support is disabled by default and API/Studio-only |
| Subtitle translation | `SUBTITLE_TRANSLATION_ENABLED`, `SUBTITLE_TRANSLATION_PROVIDER`, `SUBTITLE_TRANSLATION_PROVIDER_CHAIN`, `SUBTITLE_TRANSLATION_ALLOW_MOCK_FALLBACK`, `SUBTITLE_TRANSLATION_TIMEOUT_SECONDS`, `SUBTITLE_PUBLIC_REQUEST_*`, `SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK`, `SUBTITLE_TRANSLATION_API_*`, `OLLAMA_TRANSLATION_*`, `LIBRETRANSLATE_*`, `ARGOS_TRANSLATE_*` | Owner-triggered translated subtitle sidecar generation is enabled by default with `auto`; paid/API providers are skipped unless configured, Ollama is the preferred free/local context-aware provider, LibreTranslate and Argos are optional fallbacks, public requests are allowlisted/rate-limited/locked, and mock is clearly dev/demo only |
| Avatar | `AVATAR_ENGINE`, `AVATAR_LIVEPORTRAIT_*`, `AVATAR_MUSETALK_*`, `MUSETALK_*`, `AVATAR_PREVIEW_*`, `AVATAR_ORCH_*`, `AVATAR_GPU_SERIAL_LOCK_ENABLED`, `AVATAR_ENABLE_COMPOSITE_LESSON` | Full path requires GPU runtime, model assets, command templates, and worker bootstrap success |
| DRM/protection | `DRM_*`, `LESSON_PROTECTION_*`, `LECTURE_WATERMARK_ENABLED`, `LECTURE_VISIBILITY_LOCK_ENABLED` | Backend contract exists; frontend player wiring is incomplete |
| Google auth | `GOOGLE_AUTH_ENABLED`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `GOOGLE_REDIRECT_SUCCESS_URL` | Backend and login modal support redirect flow; deployment values need verification |
| Frontend | `VITE_API_BASE_URL` in `.env.example` | Used by `services/frontend/src/api.js`; defaults to `http://localhost:8000/api/v1` |

## Tests

Python integration tests live under `tests/integration`.

```powershell
pip install -r requirements-test.txt
pytest tests/integration -q -rs
```

Many tests import service dependencies directly, so focused runs may also require the service requirements:

```powershell
pip install -r services/api/requirements.txt -r services/worker/requirements.txt -r services/tts_service/requirements.txt
```

Some tests skip when local DB schema is stale, ffmpeg is missing, or optional MuseTalk/LivePortrait smoke gates are not enabled. The frontend test script currently only prints `No frontend unit tests configured yet.`

## Important Folders

| Path | Purpose |
| --- | --- |
| `services/api/` | Django API, settings, models, serializers, views, migrations |
| `services/frontend/` | Active Vite React frontend |
| `services/worker/` | Celery app, video pipeline, avatar preview flow |
| `services/tts_service/` | FastAPI TTS service and TTS preprocessing |
| `services/scripts/` | Shared slide extraction, FFmpeg, TTS client, avatar runner scripts |
| `services/avatar/` | Avatar preprocessing, canonical pipeline, adapters, validation |
| `infra/` | Docker Compose, Dockerfiles, env template |
| `tests/integration/` | Backend, worker, TTS, playback, avatar integration tests |
| `frontend/` | Static HTML design prototypes, not the active React app |
| `storage_local/` | Local runtime storage for uploaded/generated artifacts |

## Current Feature Status Summary

| Area | Status | Evidence / Main Files |
| --- | --- | --- |
| PPTX/PDF/DOCX/TXT upload flow | Implemented | `services/api/core/views.py`, `services/frontend/src/pages/Studio.jsx`, `tests/integration/test_project_upload_category.py` |
| Slide/page image rendering/export | Partially implemented | `services/scripts/pptx_extract.py`, `services/worker/tasks.py`, `tests/integration/test_docx_extraction.py` |
| Speaker notes/source transcript extraction | Partially implemented | `services/scripts/pptx_extract.py`, `services/worker/tasks.py` |
| Transcript pages and chunk metadata | Implemented | `services/api/core/models.py`, `services/api/core/serializers.py`, `services/worker/tasks.py` |
| Transcript editor UI | Implemented | `services/api/core/views.py`, `services/frontend/src/pages/Studio.jsx`, `services/frontend/src/components/studio/TranscriptEditorPanel.jsx`, `tests/integration/test_transcript_editor_pipeline.py` |
| Slide splitting and long-slide segmentation | Implemented | `services/scripts/text_segmentation.py`, `tests/integration/test_text_segmentation.py` |
| TTS generation | Implemented | `services/tts_service/main.py`, `services/scripts/tts_client.py`, `tests/integration/test_tts_service_text_quality.py` |
| XTTS v2 voice cloning | Partially implemented | `services/tts_service/main.py`, `services/api/core/views.py`, `services/frontend/src/pages/Settings.jsx` |
| gTTS and silent fallback | Implemented | `services/tts_service/main.py`, `services/scripts/tts_client.py`, `tests/integration/test_tts_client_readiness.py` |
| Turkish text normalization | Implemented | `services/tts_service/tts_preprocess/tr_normalizer.py`, `tests/integration/test_tts_text_normalization.py` |
| Glossary pronunciation handling | Implemented | `services/tts_service/tts_preprocess/glossary.py`, `services/tts_service/tts_preprocess/glossary.json` |
| Project-level TTS settings persistence, generation wiring, and Studio controls | Implemented | `services/api/core/models.py`, `services/api/core/serializers.py`, `services/api/core/views.py`, `services/worker/tasks.py`, `services/scripts/tts_client.py`, `services/tts_service/main.py`, `services/frontend/src/components/studio/TtsSettingsPanel.jsx`, `tests/integration/test_project_tts_settings.py` |
| Spelling-aware Turkish dictionary first and English fallback per word | Partially implemented (D1A/D1B) | `services/tts_service/tts_preprocess/deterministic_resolver.py` reports `unknown_terms`/`ambiguous_terms`, applies acronym and curated technical fallback rules including `ASP -> ey es pi` and `Pipeline/pipeline -> payp layn`, and Studio can add detected terms to draft override rows |
| Optional Llama/Ollama pronunciation suggestions | Implemented (L1A/L1B/L1C docs) | `POST /api/v1/tts/pronunciation-suggestions/` is disabled by default, fail-open, capped, provider-validated, and never called from render/rerender; Studio can request, edit, accept, or ignore suggestions into draft override rows; optional local Ollama setup and troubleshooting are documented |
| Shared/global glossary override UI/API | Planned / not implemented | `services/frontend/src/pages/Settings.jsx`, `services/api/core/urls.py`; project-level overrides are already saved from `services/frontend/src/components/studio/TtsSettingsPanel.jsx` |
| Original subtitle/SRT/WebVTT generation | Implemented | `services/scripts/ffmpeg_helpers.py`, `services/worker/tasks.py`, `tests/integration/test_pipeline_minimal.py`, `tests/integration/test_subtitle_cue_builder.py` |
| Translated subtitle track model/provider abstraction | Implemented (Phase 3) | `services/api/core/models.py`, `services/api/core/subtitle_translation.py`, `services/api/core/views.py`, `docs/SUBTITLE_TRANSLATION_PHASE_3_PLAN.md` |
| Translated subtitle generation | Implemented (Phase 4/5/6B/6D1 + async user request UI) | `POST /api/v1/projects/<id>/subtitle-tracks/` reuses ready tracks immediately or returns `202` and queues `worker.tasks.generate_translated_subtitle_track_task` for missing tracks; the task writes tokenized translated SRT/WebVTT sidecars from original display cues when `SUBTITLE_TRANSLATION_ENABLED=true`; `VideoStage.jsx` shows ready tracks and `Watch.jsx` lets viewers request missing supported languages on demand, poll until ready/failed, then refreshes and selects the cached track; provider chain support tries configured API, local Ollama, LibreTranslate, Argos, then dev/demo mock fallback without requiring paid keys; public requests for published lessons are constrained by allowlist, rate limits, and generation locks; LLM translation is batched by cue context and preserves original cue timing |
| MP4 video rendering | Implemented | `services/worker/tasks.py`, `services/scripts/ffmpeg_helpers.py` |
| Rerender after edits | Implemented | `services/api/core/views.py`, `services/frontend/src/pages/Studio.jsx`, `services/frontend/src/components/studio/TranscriptEditorPanel.jsx`, `tests/integration/test_transcript_editor_pipeline.py` |
| Publisher/studio UI | Partially implemented | `services/frontend/src/pages/Studio.jsx`, `services/frontend/src/lib/auth.js` |
| Public viewer UI | Partially implemented | `services/frontend/src/pages/Watch.jsx`, `services/frontend/src/components/player/VideoStage.jsx` |
| Admin/publisher/user role separation | Partially implemented | `services/api/core/models.py`, `services/frontend/src/lib/auth.js`, `services/api/core/views.py` |
| Unauthenticated user behavior | Partially implemented | `services/api/core/views.py`, `services/frontend/src/app/ProtectedRoute.jsx`, `services/frontend/src/pages/Watch.jsx` |
| Like/comment restrictions | Backend implemented, UI not wired | `services/api/core/views.py`, `services/frontend/src/api.js` |
| Analytics page | Partially implemented | `services/frontend/src/pages/Analytics.jsx`, `services/api/core/views.py` |
| DRM and secure streaming contract | Backend implemented, UI not wired | `services/api/core/views.py`, `services/scripts/ffmpeg_helpers.py`, `tests/integration/test_secure_playback.py` |
| HLS packaging | Backend implemented, UI not wired | `services/worker/tasks.py`, `services/scripts/ffmpeg_helpers.py`, `services/frontend/package.json` |
| DASH/Shaka/Clear Key/raw-key player integration | Planned / not implemented | No Shaka or DASH player code found in `services/frontend/src` |
| Watermark and visibility lock | Backend implemented, UI not wired | `services/api/config/settings.py`, `services/api/core/views.py`, `services/frontend/src/components/player/VideoStage.jsx` |
| MinIO/S3 storage | Configured but not active | `infra/docker-compose.yml`, `infra/.env.example`; no boto3/django-storages code found |
| Local filesystem storage | Implemented | `services/api/core/views.py`, `services/worker/tasks.py`, `services/tts_service/main.py` |
| Celery/Redis background workers | Implemented | `services/worker/celery_app.py`, `services/worker/tasks.py`, `infra/docker-compose.yml` |
| Database models and migrations | Implemented | `services/api/core/models.py`, `services/api/core/migrations/` |
| Google sign-in | Partially implemented | `services/api/core/views.py`, `services/frontend/src/components/ui/AuthModal.jsx`, `tests/integration/test_google_auth.py` |
| Avatar profile upload and preview | Partially implemented | `services/api/core/views.py`, `services/frontend/src/pages/Settings.jsx`, `services/worker/avatar_preview_flow.py` |
| LivePortrait plus MuseTalk runtime | Partially implemented | `services/worker/bootstrap_musetalk.py`, `services/avatar/`, `services/scripts/liveportrait_runner.py`, `services/scripts/musetalk_service.py` |
| Avatar overlay playback | Backend implemented, UI not wired | `services/api/core/views.py`, `services/api/core/models.py`, `services/frontend/src/api.js`, `VideoStage.jsx` |
| Docker setup | Partially implemented | `infra/docker-compose.yml`, `infra/dockerfiles/` |
| Frontend tests | Planned / not implemented | `services/frontend/package.json` |

For detailed gaps and recommended next work, see `docs/UNFINISHED_WORK.md`.
