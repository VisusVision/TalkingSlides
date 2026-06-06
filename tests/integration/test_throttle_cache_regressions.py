# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.contrib.auth.models import AnonymousUser  # noqa: E402
from rest_framework.test import APIRequestFactory  # noqa: E402

from core import views  # noqa: E402
from core.models import Category, Job, Project, UserProfile  # noqa: E402
from core.views import CategoryListView, LoginView  # noqa: E402


@pytest.mark.django_db
def test_drf_global_throttle_classes_and_login_rate_present():
    throttle_classes = settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_CLASSES", [])
    throttle_rates = settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {})

    assert "rest_framework.throttling.AnonRateThrottle" in throttle_classes
    assert "rest_framework.throttling.UserRateThrottle" in throttle_classes
    assert "rest_framework.throttling.ScopedRateThrottle" in throttle_classes
    assert "login" in throttle_rates


@pytest.mark.django_db
def test_login_view_has_scoped_throttle():
    assert getattr(LoginView, "throttle_scope", None) == "login"


@pytest.mark.django_db
def test_media_stream_view_disables_drf_throttle_for_segment_traffic():
    assert getattr(views.MediaStreamView, "throttle_classes", None) == []


@pytest.mark.django_db
def test_category_list_anonymous_cache_roundtrip(monkeypatch):
    class _CacheStub:
        def __init__(self):
            self.store = {}
            self.get_calls = 0
            self.set_calls = 0

        def get(self, key):
            self.get_calls += 1
            return self.store.get(key)

        def set(self, key, value, timeout=None):
            self.set_calls += 1
            self.store[key] = value

    cache_stub = _CacheStub()
    monkeypatch.setattr(views, "cache", cache_stub)

    Category.objects.create(name="Science")

    request = APIRequestFactory().get("/api/v1/categories/")
    request.user = AnonymousUser()

    response_1 = CategoryListView.as_view()(request)
    assert response_1.status_code == 200
    assert cache_stub.get_calls >= 1
    assert cache_stub.set_calls == 1

    response_2 = CategoryListView.as_view()(request)
    assert response_2.status_code == 200
    # Second request should hit cache and avoid writing again.
    assert cache_stub.set_calls == 1


@pytest.mark.django_db
def test_catalog_list_and_feed_response_shape_compatible():
    teacher = User.objects.create_user(username="catalog_teacher", password="pass")
    UserProfile.objects.create(user=teacher, role="teacher")
    category = Category.objects.create(name="Math")
    project = Project.objects.create(
        title="Algebra 101",
        user=teacher,
        category=category,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        result_url=f"{project.id}/{project.id}.mp4",
    )

    rf = APIRequestFactory()
    anon = AnonymousUser()

    list_request = rf.get("/api/v1/catalog/")
    list_request.user = anon
    list_response = views.CatalogListView.as_view()(list_request)
    assert list_response.status_code == 200
    assert isinstance(list_response.data, list)
    assert list_response.data
    first = list_response.data[0]
    assert "like_count" in first
    assert "comment_count" in first
    assert "has_video" in first

    feed_request = rf.get("/api/v1/catalog/feed/")
    feed_request.user = anon
    feed_response = views.CatalogFeedView.as_view()(feed_request)
    assert feed_response.status_code == 200
    assert "sections" in feed_response.data
    assert isinstance(feed_response.data["sections"], list)


def test_scalability_index_migration_dependency_and_names():
    module = __import__(
        "core.migrations.0027_add_scalability_composite_indexes",
        fromlist=["Migration"],
    )
    migration = module.Migration
    assert ("core", "0026_notification") in migration.dependencies

    names = []
    for op in migration.operations:
        if hasattr(op, "index") and getattr(op, "index", None) is not None:
            names.append(op.index.name)
    assert names
    # PostgreSQL identifier limit: 63 characters.
    assert all(len(name) <= 63 for name in names)
