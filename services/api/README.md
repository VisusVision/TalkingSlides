# AI_ACADEMY – Django API Service

A minimal Django REST Framework backend for the AI Academy edu-voice platform.

## Project structure

```
services/api/
├── manage.py
├── requirements.txt
├── Dockerfile.dev
├── config/           # Django project package
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
└── core/             # Main application
    ├── models.py     # Teacher, VoiceProfile, Project, Slide, Job
    ├── serializers.py
    ├── views.py
    └── urls.py
```

## Quick start (local dev without Docker)

### 1. Create & activate a virtual environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

From the repo root, copy the shared env example and edit as needed:

```bash
cp infra/.env.example infra/.env
```

For a plain local shell run, export only the values you need. SQLite fallback is used when `POSTGRES_HOST` is absent:

```dotenv
SECRET_KEY=any-random-string
DEBUG=True
```

### 4. Run migrations

```bash
python manage.py migrate
```

### 5. Create a superuser

```bash
python manage.py createsuperuser
```

### 6. Start the dev server

```bash
python manage.py runserver
```

API is available at <http://localhost:8000/api/v1/>

Django admin at <http://localhost:8000/admin/>

## Running with Docker Compose

From the **repo root**:

```bash
# copy and fill in secrets
cp infra/.env.example infra/.env

# build and start all services
docker compose -f infra/docker-compose.yml up --build

# run migrations inside the running container
docker compose -f infra/docker-compose.yml exec api python manage.py migrate

# create superuser
docker compose -f infra/docker-compose.yml exec api python manage.py createsuperuser
```

### Protection mode config reload behavior

- Runtime services read environment from `infra/.env` (not `infra/.env.example`).
- After changing `LESSON_PROTECTION_DEFAULT_MODE`, restart `api` and `worker`:

```bash
docker compose -f infra/docker-compose.yml up -d --force-recreate api worker
```

- Local/dev precedence is:
    - explicit `LESSON_PROTECTION_DEFAULT_MODE=public` -> effective mode is `public`
    - legacy per-lesson sidecar mode does not override explicit public
    - otherwise sidecar mode can be used for rendered lesson-specific policies

### Avatar composite engine config

The `liveportrait+musetalk` engine reads runtime env from `infra/.env` via Docker Compose `env_file` for both `api` and `worker`.

Active composite defaults:

```dotenv
AVATAR_ENGINE=liveportrait+musetalk
AVATAR_LIVEPORTRAIT_HOME=/opt/liveportrait
AVATAR_LIVEPORTRAIT_ENTRYPOINT=/opt/liveportrait/inference.py
AVATAR_LIVEPORTRAIT_MODEL_PATH=/opt/liveportrait/pretrained_weights
AVATAR_LIVEPORTRAIT_TIMEOUT_SECONDS=180
AVATAR_LIVEPORTRAIT_CMD=python /app/scripts/liveportrait_runner.py --source_image "{source_image}" --source_video "{source_video}" --audio_path "{audio_path}" --output_path "{output_path}" --liveportrait_home "${AVATAR_LIVEPORTRAIT_HOME}" --liveportrait_entrypoint "${AVATAR_LIVEPORTRAIT_ENTRYPOINT}" --liveportrait_model_path "${AVATAR_LIVEPORTRAIT_MODEL_PATH}" --timeout_seconds "${AVATAR_LIVEPORTRAIT_TIMEOUT_SECONDS}"
AVATAR_MUSETALK_CMD=python /app/scripts/musetalk_runner.py --source_image "{source_image}" --source_video "{source_video}" --audio_path "{audio_path}" --output_path "{output_path}"
AVATAR_PREVIEW_USE_LIVEPORTRAIT=1
AVATAR_PREVIEW_USE_MUSETALK=1
AVATAR_PREVIEW_USE_RESTORATION=0
```

Important separation:

- `AVATAR_LIVEPORTRAIT_HOME` and `AVATAR_LIVEPORTRAIT_ENTRYPOINT` must point to LivePortrait code/runtime install.
- `AVATAR_LIVEPORTRAIT_MODEL_PATH` is model/data storage only.
- Do not point runtime home/entrypoint to `/app/storage_local/...`.

Startup policy:

- If LivePortrait or MuseTalk startup checks fail, worker bootstrap fails fast.
- The application no longer downgrades to legacy avatar engines or a musetalk-only preview path.

If `AVATAR_PREVIEW_USE_RESTORATION=1`, `AVATAR_PREVIEW_RESTORE_CMD` is also required and worker bootstrap will fail fast when it is missing.

## API endpoints (v1)

| Resource   | Endpoint              | Methods                |
|------------|-----------------------|------------------------|
| Teachers   | `/api/v1/teachers/`   | GET, POST, PUT, DELETE |
| Projects   | `/api/v1/projects/`   | GET, POST, PUT, DELETE |
| Slides     | `/api/v1/slides/`     | GET, POST, PUT, DELETE |
| Jobs       | `/api/v1/jobs/`       | GET, POST, PUT, DELETE |

Query-param filters:
- `/api/v1/projects/?teacher=<id>`
- `/api/v1/slides/?project=<id>`

## DRM integration contract

The repo now exposes a vendor-neutral DRM playback contract that is ready to be connected to an external DRM provider. The application does not generate commercial DRM licenses by itself. You still need an external DRM service that can package keys, issue licenses, and manage provider credentials.

### Required `.env` values

Set the values below in your `.env` file. Only non-secret player initialization values are exposed to the frontend.

```dotenv
DRM_ENABLED=1
DRM_PROVIDER_NAME=external
DRM_PREFERRED_SYSTEM=widevine

DRM_ASSET_ID_PREFIX=lesson-
DRM_CONTENT_ID_PREFIX=project-
DRM_PLAYBACK_SESSION_PREFIX=playback

DRM_WIDEVINE_ENABLED=1
DRM_WIDEVINE_KEY_SYSTEM=com.widevine.alpha
DRM_WIDEVINE_LICENSE_URL=https://drm.example.com/widevine/license
DRM_WIDEVINE_CERTIFICATE_URL=
DRM_WIDEVINE_CONTENT_TYPE=video/mp4

DRM_PLAYREADY_ENABLED=0
DRM_PLAYREADY_KEY_SYSTEM=com.microsoft.playready
DRM_PLAYREADY_LICENSE_URL=
DRM_PLAYREADY_CERTIFICATE_URL=
DRM_PLAYREADY_CONTENT_TYPE=video/mp4

DRM_FAIRPLAY_ENABLED=0
DRM_FAIRPLAY_KEY_SYSTEM=com.apple.fps.1_0
DRM_FAIRPLAY_LICENSE_URL=
DRM_FAIRPLAY_CERTIFICATE_URL=
DRM_FAIRPLAY_CONTENT_TYPE=application/vnd.apple.mpegurl

DRM_STREAMING_ENABLED=1
DRM_HLS_ENCRYPTION_ENABLED=1

LESSON_PROTECTION_DEFAULT_MODE=secure_stream
LESSON_PROTECTION_ALLOW_MP4_FALLBACK=1
LESSON_PROTECTION_FORCE_WATERMARK_FOR_PROTECTED=1
LESSON_PROTECTION_TOKEN_TTL_PUBLIC_SECONDS=14400
LESSON_PROTECTION_TOKEN_TTL_SECURE_SECONDS=14400
LESSON_PROTECTION_TOKEN_TTL_DRM_SECONDS=600
LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION=1
LESSON_PROTECTION_REQUIRE_HLS_ENCRYPTION_FOR_DRM=1
LESSON_PROTECTION_REQUIRE_DRM_METADATA_FOR_DRM=1
```

### Backend contract

`GET /api/v1/projects/<id>/playback-token/` returns safe playback metadata only. It includes:

- DRM enabled/configured/ready flags
- protection mode
- encrypted manifest URL
- asset ID and content ID
- playback session ID
- preferred DRM system and per-system metadata for Widevine, PlayReady, and FairPlay
- whether MP4 fallback is allowed
- whether watermark is forced
- whether playback is bound to the current session

The payload does not include raw storage paths, encryption keys, private signing keys, or provider secrets.

### Frontend usage

The secure player reads the DRM block from the playback response, selects the preferred key system, sets up EME when available, and only sends the browser the license URL, certificate URL, asset ID, content ID, and playback session ID needed for player initialization. `drm_protected` lessons refuse silent MP4 downgrade and show a clear error when DRM cannot be initialized.

### What remains external

You still need a DRM vendor or internal license service for:

- key management
- commercial packaging and entitlement policies
- provider credentials and signing material
- Widevine / PlayReady / FairPlay compliance requirements
- any provider-specific license request customization beyond the generic browser EME contract in this repo
