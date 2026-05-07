"""
Celery application for AI_ACADEMY worker service.

Initialisation order
--------------------
1. Set DJANGO_SETTINGS_MODULE so Django can locate its config.
2. Call django.setup() to initialise the app registry — this MUST happen
   before any task module imports Django models at the top level, and before
   Celery reads the CELERY_* settings from Django's settings object.
3. Create the Celery app and bind it to Django settings.
4. Auto-discover tasks in the ``worker`` package.
"""

import os

import django

# Step 1 — point Django at the project settings (resolved via PYTHONPATH=/app/api)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Step 2 — initialise Django's app registry so that
#   • ORM model imports inside task functions succeed without
#     "Model … doesn't declare an explicit app_label" errors
#   • CELERY_* settings can be read from django.conf.settings
django.setup()

from celery import Celery  # noqa: E402  (must follow django.setup())
from celery.schedules import crontab  # noqa: E402
from kombu import Queue  # noqa: E402

# Step 3 — create the Celery app
#   broker/backend defaults point at the Docker Compose redis service;
#   they are overridden by CELERY_BROKER_URL / CELERY_RESULT_BACKEND read
#   from Django settings via config_from_object below.
app = Celery(
    "worker",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
)

# Merge CELERY_* keys from Django settings (overrides the constructor defaults)
app.config_from_object("django.conf:settings", namespace="CELERY")

render_queue = str(os.environ.get("CELERY_RENDER_QUEUE", "render") or "render").strip() or "render"
render_fast_queue = str(os.environ.get("CELERY_RENDER_FAST_QUEUE", "render_fast") or "render_fast").strip() or "render_fast"
render_quality_queue = str(os.environ.get("CELERY_RENDER_QUALITY_QUEUE", "render_quality") or "render_quality").strip() or "render_quality"
avatar_queue = str(os.environ.get("CELERY_AVATAR_QUEUE", "avatar") or "avatar").strip() or "avatar"
legacy_queue = str(os.environ.get("CELERY_LEGACY_QUEUE", "celery") or "celery").strip() or "celery"

app.conf.task_default_queue = str(os.environ.get("CELERY_TASK_DEFAULT_QUEUE", render_queue) or render_queue).strip()
app.conf.task_queues = tuple(
    Queue(queue_name)
    for queue_name in dict.fromkeys([render_queue, render_fast_queue, render_quality_queue, avatar_queue, legacy_queue])
)
app.conf.task_routes = {
    "worker.tasks.process_pptx_to_video": {"queue": render_queue},
    "worker.tasks.export_project": {"queue": render_queue},
    "worker.tasks.synthesize_and_render_slide": {"queue": render_queue},
    "worker.tasks.concat_and_finalize": {"queue": render_queue},
    "worker.tasks.merge_and_finalize_segments": {"queue": render_queue},
    "worker.tasks.mark_project_render_failed": {"queue": render_queue},
    "worker.tasks.cleanup_cancelled_project_artifacts": {"queue": render_queue},
    "worker.tasks.cleanup_orphan_render_artifacts": {"queue": render_queue},
    "worker.tasks.render_avatar_preview": {"queue": avatar_queue},
    "worker.tasks.render_avatar_segment": {"queue": avatar_queue},
    "worker.tasks.render_avatar_lesson": {"queue": avatar_queue},
    "worker.tasks.fallback_avatar_render": {"queue": avatar_queue},
    "worker.tasks.avatar_cache_cleanup": {"queue": avatar_queue},
    "worker.tasks.cleanup_avatar_cache": {"queue": avatar_queue},
}

# Step 4 — discover tasks.py in the worker package
_orphan_cleanup_enabled = str(os.environ.get("CELERY_ORPHAN_CLEANUP_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}
_orphan_cleanup_interval_minutes = max(1, int(os.environ.get("CELERY_ORPHAN_CLEANUP_INTERVAL_MINUTES", "30")))
_orphan_cleanup_min_age_hours = max(1, int(os.environ.get("CELERY_ORPHAN_CLEANUP_MIN_AGE_HOURS", "6")))

if _orphan_cleanup_enabled:
    app.conf.beat_schedule = {
        "cleanup-orphan-render-artifacts": {
            "task": "worker.tasks.cleanup_orphan_render_artifacts",
            "schedule": crontab(minute=f"*/{_orphan_cleanup_interval_minutes}"),
            "kwargs": {"min_age_hours": _orphan_cleanup_min_age_hours},
            "options": {"queue": render_queue},
        }
    }

app.autodiscover_tasks(["worker"])
