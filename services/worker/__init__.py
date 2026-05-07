"""worker package – exposes the Celery app for `celery -A worker worker` invocation.

Importing `celery_app` performs Django setup which is not available in all
execution contexts (for example, lightweight unit tests). Import the
application lazily and tolerate failure so tests that only need internal
modules (like `avatar_preview_flow`) can import this package without
requiring a full Django config.
"""
try:
	from .celery_app import app as celery_app  # type: ignore
except Exception:
	celery_app = None

__all__ = ["celery_app"]
