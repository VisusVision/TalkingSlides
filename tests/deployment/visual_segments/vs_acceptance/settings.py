"""Refuse normal settings/credentials/hosts; isolated internal Compose only."""
import os
from pathlib import Path

EXPECTED = {
    'POSTGRES_HOST': 'vs-postgres', 'POSTGRES_DB': 'visual_segments_engineering',
    'POSTGRES_USER': 'visual_engineering', 'POSTGRES_PASSWORD': 'engineering-only-not-production',
    'CELERY_BROKER_URL': 'redis://vs-redis:6379/13',
    'VISUAL_RENDER_PRIVATE_ROOT': '/engineering-private',
}
if any(os.environ.get(k) != v for k,v in EXPECTED.items()):
    raise RuntimeError('Refusing engineering fixture injection outside isolated Compose')
if not Path('/engineering-control/container-only.marker').is_file():
    raise RuntimeError('Missing read-only engineering controller mount')
from config.settings import *  # noqa: E402,F403

INSTALLED_APPS = [*INSTALLED_APPS, 'vs_acceptance.apps.EngineeringOnlyConfig']
DEBUG = False
ALLOWED_HOSTS = ['api', 'localhost', '127.0.0.1', 'testserver']
VISUAL_RENDER_PRIVATE_ROOT = EXPECTED['VISUAL_RENDER_PRIVATE_ROOT']
VISUAL_SEGMENT_PRODUCER_ROOTS = {'engineering': '/engineering-producer'}
STATIC_ROOT = '/tmp/engineering-static'
MEDIA_ROOT = '/tmp/engineering-media'
CACHES = {'default': {'BACKEND':'django.core.cache.backends.locmem.LocMemCache'}}
INTELLIGENCE_OLLAMA_CALIBRATION_ENABLED = False
# Real external Redis, never eager or an in-process memory transport.
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_DEFAULT_QUEUE = CELERY_RENDER_QUEUE = 'render'
CELERY_BROKER_TRANSPORT_OPTIONS = {'visibility_timeout': 10, 'socket_connect_timeout': 3, 'socket_timeout': 3}
CELERY_RESULT_BACKEND = 'redis://vs-redis:6379/14'

# Direct internal Gunicorn HTTP has no TLS terminator in this test network.
# This does not change production settings or establish reverse-proxy acceptance.
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
