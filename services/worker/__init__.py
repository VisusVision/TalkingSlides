"""worker package - exposes the Celery app for `celery -A worker worker` invocation."""

from .celery_app import app as celery_app

# Compatibility aliases for Celery autodiscovery entrypoints.
app = celery_app
celery = celery_app

__all__ = ["celery_app", "app", "celery"]
