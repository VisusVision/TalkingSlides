# Parallel slide-processing pipeline

## Architecture

```
send_task("worker.tasks.process_pptx_to_video", [project_id, pptx_path, voice_id, …])
  │
  └─ process_pptx_to_video  [orchestrator, runs on one worker]
       │
       ├─ export_project.apply()          ← inline, same worker process
       │    • exports slide PNGs (COM → LibreOffice → python-pptx fallback)
       │    • extracts speaker-note .txt files via python-pptx
       │    • returns list of slide descriptor dicts
       │
       └─ chord(
            group(                              ← all dispatched simultaneously
              synthesize_and_render_slide(slide_0, …),
              synthesize_and_render_slide(slide_1, …),
              …
              synthesize_and_render_slide(slide_N, …),
            ),
            concat_and_finalize(project_id)     ← runs after ALL slides succeed
          ).apply_async()
```

### Task responsibilities

| Task | What it does | Returns |
|---|---|---|
| `export_project` | PNG export + speaker-note extraction | `list[SlideDescriptor]` |
| `synthesize_and_render_slide` | TTS → MP3, ffprobe duration, ffmpeg PNG+audio → part MP4 | `{index, slide_num, part_path, duration, text}` |
| `concat_and_finalize` | Sorts parts, ffmpeg concat → final MP4, SRT, updates Job DB | `{final_video, srt, parts, durations, n_slides}` |

### Fail-fast behaviour

With Celery's default `CELERY_CHORD_PROPAGATES = True`, if **any** slide task
raises after exhausting retries the chord callback (`concat_and_finalize`) is
skipped and the chord result is marked `FAILURE`.  The orchestrator
(`process_pptx_to_video`) catches export-step failures immediately.  In both
cases `_update_job` writes `status="failed"` and the full traceback to
`Job.error_message`.

### `synthesize_and_render_slide` retries

The per-slide task is configured with `max_retries=2, default_retry_delay=10s`.
Transient TTS/ffmpeg failures are retried before the chord failure path fires.

### Progress tracking

| Stage | `Job.progress` | Celery state |
|---|---|---|
| Orchestrator received | 0 | `PROGRESS` |
| Export done | 10 | `PROGRESS` |
| Per-slide TTS/render | — | `PROGRESS` (per-task state) |
| Concat started | 90 | — |
| SRT written | 95 | — |
| Final video ready | 100 | `SUCCESS` |

`Job.progress` requires a `progress = IntegerField(default=0)` column — add it
to `services/api/core/models.py` and run `makemigrations`.  The worker guards
with `hasattr(job, "progress")` so it runs safely without the migration.

---

## Phase A fixes applied

### Dockerfile.worker
- **Removed duplicate `useradd`** — the original had two `RUN useradd` blocks;
  the second always failed, leaving the image running as root.
- **Fixed `PYTHONPATH`** — was `/app/api:/app:/app/services` (the last entry
  doesn't exist in the image); corrected to `/app/api:/app`.
- **`/tmp/lo_user` created + chowned** before switching to `appuser`.
- Added `fonts-liberation` to apt packages for better LibreOffice rendering.

### celery_app.py
- Added `django.setup()` **before** creating the `Celery` app so that task
  modules can import Django ORM models without the
  *"Model … doesn't declare an explicit app_label"* error.

### settings.py + apps.py
- Changed `INSTALLED_APPS` entry from `"core"` to `"api.core"` so the app
  label matches the Python module path when `PYTHONPATH=/app`.
- Updated `CoreConfig.name` in `api/core/apps.py` from `"core"` to `"api.core"`
  (Django raises `ImproperlyConfigured` if these differ).

### pptx_extract.py
- LibreOffice invocations now pass
  `--env:UserInstallation=file:///tmp/lo_user` (configurable via
  `LO_USER_INSTALLATION` env var) to avoid dconf / home-dir permission errors.
- `export_slide_images` catches `FileNotFoundError` (soffice absent) and any
  `RuntimeError` from the LibreOffice subprocess and falls back to a
  python-pptx + Pillow white-placeholder strategy so the pipeline never crashes.

### tasks.py
- **`concat_and_finalize`** — removed `bind=True` from the decorator.  With
  `bind=True` Celery injects the task instance as the first positional arg,
  causing `results` to silently receive `self` and `project_id` to receive the
  actual results list.  The chord callback must not use `bind=True`.

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `STORAGE_ROOT` | `storage_local/output` | Root dir for per-project workspace |
| `TTS_SERVICE_URL` | `http://tts_service:8001` | Internal TTS microservice base URL |
| `ELEVEN_API_KEY` | *(none)* | ElevenLabs API key (`tts_mode="eleven"`) |
| `LO_USER_INSTALLATION` | `/tmp/lo_user` | LibreOffice per-process profile dir |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/0` | Celery result backend |
| `CELERY_WORKER_CONCURRENCY` | `1` | Local worker process pool size. Keep `1` on small single-GPU machines so avatar GPU jobs are not run concurrently. |
| `CELERY_PREFETCH_MULTIPLIER` | `1` | Keep `1` for long render tasks so one worker process does not reserve multiple jobs. |
| `AVATAR_PREVIEW_TASK_SOFT_TIME_LIMIT_SECONDS` | adaptive, local `.env`: `7200` | Celery soft limit for `worker.tasks.render_avatar_preview`; must exceed the sum of adaptive stage budgets plus safety margin. |
| `AVATAR_PREVIEW_TASK_HARD_TIME_LIMIT_SECONDS` | soft + margin, local `.env`: `7500` | Celery hard limit for `worker.tasks.render_avatar_preview`; must be greater than the soft limit. |
| `AVATAR_GPU_SERIAL_LOCK_ENABLED` | `1` | Enables a file lock around avatar GPU tasks so preview/segment jobs serialize even if worker concurrency is raised. |
| `MUSETALK_CHUNK_MAX_SECONDS` | unset, local `.env`: `15` | Enables existing MuseTalk entrypoint chunking for long previews without changing quality settings. |
| `AVATAR_MUSETALK_TIMEOUT_MAX_SECONDS` | `7200` | Hardware/history-aware cap for MuseTalk stage prediction; explicit stage timeout overrides still win. |
| `AVATAR_MUSETALK_TIMEOUT_SAFETY_MULTIPLIER` | `1.4` | Multiplies the selected MuseTalk runtime estimate before caps are applied. |
| `AVATAR_MUSETALK_TIMEOUT_HISTORY_ENABLED` | `1` | Enables recent successful MuseTalk timing history and current-run sidecar history for timeout prediction. |
| `AVATAR_MUSETALK_TIMEOUT_LOW_VRAM_MULTIPLIER` | `1.35` | Extra multiplier for low-VRAM GPUs after no better similar history is available. |

Protection mode note:
- `LESSON_PROTECTION_DEFAULT_MODE` is read from process environment.
- After changing this value in `infra/.env`, recreate `worker` and `api` containers so sidecar generation and playback policy stay in sync.

Recommended settings for slide-heavy workloads:

```bash
CELERY_CONCURRENCY=8
CELERY_PREFETCH_MULTIPLIER=1
```

Recommended local settings for 4GB single-GPU avatar work:

```bash
CELERY_WORKER_CONCURRENCY=1
CELERY_PREFETCH_MULTIPLIER=1
AVATAR_GPU_SERIAL_LOCK_ENABLED=1
AVATAR_MUSETALK_TIMEOUT_MAX_SECONDS=7200
AVATAR_MUSETALK_TIMEOUT_SAFETY_MULTIPLIER=1.4
AVATAR_MUSETALK_TIMEOUT_HISTORY_ENABLED=1
AVATAR_MUSETALK_TIMEOUT_LOW_VRAM_MULTIPLIER=1.35
AVATAR_PREVIEW_MUSETALK_TIMEOUT_MAX_SECONDS=1800
AVATAR_PREVIEW_TASK_SOFT_TIME_LIMIT_SECONDS=7200
AVATAR_PREVIEW_TASK_HARD_TIME_LIMIT_SECONDS=7500
MUSETALK_CHUNK_MAX_SECONDS=15
```

For the observed local 4GB RTX 3050 workload of roughly 37.4s, 598 frames,
and 3 MuseTalk chunks, the history-aware MuseTalk stage budget should be about
3990s with these settings. Keep the preview task soft/hard limits above that
budget plus the other avatar stage margins.

The legacy `AVATAR_PREVIEW_TASK_SOFT_TIMEOUT_SECONDS` and
`AVATAR_PREVIEW_TASK_HARD_TIMEOUT_SECONDS` names are still read for backward
compatibility, but the `*_TIME_LIMIT_SECONDS` names should be used for new
configuration.

---

## Run commands

### Start workers (Docker Compose)

```bash
docker compose up worker
```

### Start a worker manually (local dev)

```bash
# from repo root
celery -A services.worker worker --loglevel=info --concurrency=4 -Q celery
# or from services/worker/
celery -A worker worker --loglevel=info --concurrency=4
```

### Submit a job from Python

```python
from celery import Celery

app = Celery(broker="redis://localhost:6379/0", backend="redis://localhost:6379/0")

result = app.send_task(
    "worker.tasks.process_pptx_to_video",
    args=["proj-123", "/tmp/deck.pptx", "rachel"],
    kwargs={"pause_sec": 1.5, "lang_hint": "en", "tts_mode": "service"},
)
print("orchestrator task id:", result.id)
response = result.get(timeout=30)   # blocks until orchestrator dispatches chord
print("chord id:", response["chord_id"])
print("slides:  ", response["n_slides"])
```

### Smoke-test individual tasks

```bash
# Ping
celery -A worker call worker.tasks.ping

# Export only
celery -A worker call worker.tasks.export_project \
  --args='["proj-123", "/tmp/deck.pptx"]'
```

### Monitor with Flower

```bash
celery -A worker flower --port=5555
# open http://localhost:5555
```
