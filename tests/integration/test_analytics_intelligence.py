import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from urllib.error import URLError

import django
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.analytics_intelligence import (  # noqa: E402
    AnalyticsIntelligenceProviderUnavailable,
    PaidAnalyticsIntelligenceProvider,
    adaptive_analytics_intelligence_timeout,
    analyze_analytics_with_provider_chain,
    analytics_ollama_run_identity,
    build_analytics_intelligence_input,
)
from core.intelligence_progressive import ollama_chunk_timeout_seconds  # noqa: E402
from core.models import (  # noqa: E402
    AnalyticsIntelligenceReport,
    Category,
    LessonComment,
    LessonIntelligenceReport,
    LessonLike,
    LessonProgress,
    Project,
    TranscriptPage,
    UserProfile,
)


pytestmark = pytest.mark.django_db


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_user(username: str, *, role: str = "publisher", is_staff: bool = False) -> User:
    user = User.objects.create_user(username=username, password="pass", is_staff=is_staff)
    UserProfile.objects.create(user=user, role=role)
    return user


def _make_project(
    owner: User,
    title: str,
    *,
    category: Category | None = None,
    published: bool = True,
) -> Project:
    return Project.objects.create(
        title=title,
        description="Analytics intelligence test lesson.",
        user=owner,
        category=category,
        status="ready" if published else "draft",
        moderation_status="approved",
        is_published=published,
    )


def _progress(viewer: User, project: Project, value: int) -> None:
    LessonProgress.objects.create(user=viewer, project=project, progress_pct=value)


def _analyze_url(query: str = "range=30") -> str:
    return f"/api/v1/me/analytics/intelligence/analyze/?{query}"


def _latest_url(query: str = "range=30") -> str:
    return f"/api/v1/me/analytics/intelligence/?{query}"


def _analytics_url(query: str = "range=30") -> str:
    return f"/api/v1/me/analytics/?{query}"


def _text(payload) -> str:
    return json.dumps(payload, sort_keys=True)


class _FakeOllamaResponse:
    def __init__(self, payload: dict | str):
        if isinstance(payload, str):
            self.body = payload.encode("utf-8")
        else:
            self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.body


class _FakeAsyncResult:
    id = "fake-analytics-intelligence-task"


def _fake_dispatch(calls):
    def dispatch(task_name, *, args=None, kwargs=None, queue=None):
        calls.append({"task_name": task_name, "args": args or [], "kwargs": kwargs or {}, "queue": queue})
        return _FakeAsyncResult()

    return dispatch


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_unauthenticated_denied():
    response = _client().post(_analyze_url(), {}, format="json")

    assert response.status_code in {401, 403}


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_non_publisher_student_denied():
    student = _make_user("ai_student", role="student")

    response = _client(student).post(_analyze_url(), {}, format="json")

    assert response.status_code == 403


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_publisher_can_analyze_own_analytics():
    publisher = _make_user("ai_publisher")
    viewer = _make_user("ai_viewer", role="student")
    lesson = _make_project(publisher, "Publisher analytics lesson")
    _progress(viewer, lesson, 78)
    LessonLike.objects.create(user=viewer, project=lesson)
    LessonComment.objects.create(user=viewer, project=lesson, text="Helpful")

    response = _client(publisher).post(_analyze_url(), {}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "done"
    assert response.data["provider"] == "heuristic"
    assert response.data["fallback_used"] is False
    assert response.data["summary"]
    assert isinstance(response.data["health_score"], int)
    assert response.data["risk_level"] in {"low", "medium", "high"}
    assert response.data["insights"]
    assert response.data["recommendations"]


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_another_publishers_lesson_data_is_not_included():
    publisher = _make_user("ai_scope_owner")
    other_publisher = _make_user("ai_scope_other")
    viewer = _make_user("ai_scope_viewer", role="student")
    own_lesson = _make_project(publisher, "Own analytics lesson")
    other_lesson = _make_project(other_publisher, "Other Publisher Secret Lesson")
    _progress(viewer, own_lesson, 70)
    _progress(viewer, other_lesson, 100)

    response = _client(publisher).post(_analyze_url(), {}, format="json")

    assert response.status_code == 200
    serialized = _text(response.data)
    assert "Own analytics lesson" in serialized
    assert "Other Publisher Secret Lesson" not in serialized
    assert response.data["metadata"]["total_lessons"] == 1


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_empty_analytics_returns_onboarding_suggestions():
    publisher = _make_user("ai_empty_owner")

    response = _client(publisher).post(_analyze_url(), {}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "done"
    assert "No creator lessons" in response.data["summary"]
    assert any("Publish" in item["message"] for item in response.data["recommendations"])


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_low_progress_produces_progress_recommendation():
    publisher = _make_user("ai_low_progress_owner")
    viewer = _make_user("ai_low_progress_viewer", role="student")
    lesson = _make_project(publisher, "Low progress lesson")
    _progress(viewer, lesson, 22)

    response = _client(publisher).post(_analyze_url(), {}, format="json")

    assert response.status_code == 200
    recommendation_text = _text(response.data["recommendations"]).lower()
    insight_text = _text(response.data["insights"]).lower()
    assert "progress" in recommendation_text
    assert "progress" in insight_text


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_high_engagement_produces_positive_insight():
    publisher = _make_user("ai_engaged_owner")
    viewer = _make_user("ai_engaged_viewer", role="student")
    lesson = _make_project(publisher, "Engaged lesson")
    _progress(viewer, lesson, 80)
    LessonLike.objects.create(user=viewer, project=lesson)
    LessonComment.objects.create(user=viewer, project=lesson, text="Great")

    response = _client(publisher).post(_analyze_url(), {}, format="json")

    assert response.status_code == 200
    insight_text = _text(response.data["insights"]).lower()
    assert "strong" in insight_text
    assert "likes" in insight_text or "comments" in insight_text


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_category_imbalance_produces_category_action():
    publisher = _make_user("ai_category_owner")
    viewers = [_make_user(f"ai_category_viewer_{index}", role="student") for index in range(4)]
    dominant = Category.objects.create(name="Dominant Category", slug="dominant-category")
    smaller = Category.objects.create(name="Smaller Category", slug="smaller-category")
    dominant_lesson = _make_project(publisher, "Dominant lesson", category=dominant)
    _make_project(publisher, "Smaller lesson", category=smaller)
    for viewer in viewers:
        _progress(viewer, dominant_lesson, 75)

    response = _client(publisher).post(_analyze_url(), {}, format="json")

    assert response.status_code == 200
    actions_text = _text(response.data["category_actions"]).lower()
    assert "dominant category" in actions_text
    assert "imbalance" in actions_text or "dominates" in _text(response.data["insights"]).lower()


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_no_viewer_username_or_id_exposed():
    publisher = _make_user("ai_privacy_owner")
    viewer = _make_user("unique_private_viewer_name", role="student")
    lesson = _make_project(publisher, "Privacy analytics lesson")
    _progress(viewer, lesson, 64)
    LessonLike.objects.create(user=viewer, project=lesson)

    response = _client(publisher).post(_analyze_url(), {}, format="json")

    serialized = _text(response.data)
    assert response.status_code == 200
    assert "unique_private_viewer_name" not in serialized
    assert "user_id" not in serialized
    assert "viewer_id" not in serialized
    assert "viewer_username" not in serialized


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_get_returns_latest_report_for_current_filter():
    publisher = _make_user("ai_latest_owner")
    viewer = _make_user("ai_latest_viewer", role="student")
    category = Category.objects.create(name="Latest Category", slug="latest-category")
    lesson = _make_project(publisher, "Latest lesson", category=category)
    _progress(viewer, lesson, 88)
    first = _client(publisher).post(_analyze_url("range=30"), {}, format="json")

    latest = _client(publisher).get(_latest_url("range=30"))

    assert first.status_code == 200
    assert latest.status_code == 200
    assert latest.data["id"] == first.data["id"]
    assert latest.data["source_hash"] == first.data["source_hash"]
    assert latest.data["report_source_hash"] == first.data["source_hash"]
    assert latest.data["current_source_hash"] == first.data["source_hash"]
    assert latest.data["is_stale"] is False

    changed_lesson = _make_project(publisher, "Latest changed lesson", category=category)
    _progress(viewer, changed_lesson, 44)
    stale = _client(publisher).get(_latest_url("range=30"))

    assert stale.status_code == 200
    assert stale.data["id"] == first.data["id"]
    assert stale.data["report_source_hash"] == first.data["source_hash"]
    assert stale.data["current_source_hash"] != first.data["source_hash"]
    assert stale.data["is_stale"] is True


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_get_without_report_exposes_current_hash_and_stale():
    publisher = _make_user("ai_empty_report_owner")
    viewer = _make_user("ai_empty_report_viewer", role="student")
    lesson = _make_project(publisher, "Reportless analytics lesson")
    _progress(viewer, lesson, 81)

    latest = _client(publisher).get(_latest_url("range=30"))

    assert latest.status_code == 200
    assert latest.data["status"] == "empty"
    assert latest.data["report_source_hash"] == ""
    assert latest.data["current_source_hash"]
    assert latest.data["is_stale"] is True


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    OLLAMA_ANALYTICS_INTELLIGENCE_BASE_URL="http://127.0.0.1:9",
    ANALYTICS_INTELLIGENCE_TIMEOUT_SECONDS=0.5,
    INTELLIGENCE_CELERY_QUEUE="analytics-intelligence-test",
)
def test_post_analyze_returns_heuristic_and_queues_analytics_ollama(monkeypatch):
    publisher = _make_user("ai_ollama_owner")
    viewer = _make_user("ai_ollama_viewer", role="student")
    lesson = _make_project(publisher, "Ollama fallback lesson")
    _progress(viewer, lesson, 72)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    response = _client(publisher).post(_analyze_url(), {}, format="json")

    assert response.status_code == 200
    assert response.data["provider"] == "heuristic"
    assert response.data["fallback_used"] is True
    assert response.data["enhancement_available"] is True
    assert response.data["enhancement_pending"] is True
    assert response.data["enhancement_status"] == "pending"
    assert response.data["enhancement_provider"] == "ollama"
    attempts = response.data["provider_chain_attempts"]
    assert attempts[0]["provider"] == "ollama"
    assert attempts[0]["status"] == "queued"
    assert dispatch_calls[0]["task_name"] == "worker.tasks.enhance_analytics_intelligence_report"
    assert dispatch_calls[0]["queue"] == "analytics-intelligence-test"

    report = AnalyticsIntelligenceReport.objects.get(pk=response.data["id"])
    enhancement = report.metadata["progressive_enhancement"]
    assert enhancement["queue"] == "analytics-intelligence-test"
    assert enhancement["task_id"] == "fake-analytics-intelligence-task"
    assert enhancement["queued_at"]

    latest = _client(publisher).get(_latest_url())

    assert latest.status_code == 200
    assert latest.data["id"] == response.data["id"]
    assert latest.data["enhancement_pending"] is True


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    INTELLIGENCE_CELERY_QUEUE="shared-intelligence-test",
    INTELLIGENCE_ANALYTICS_CELERY_QUEUE="analytics-intelligence-test",
)
def test_analytics_intelligence_uses_analytics_queue_setting(monkeypatch):
    publisher = _make_user("ai_analytics_queue_owner")
    viewer = _make_user("ai_analytics_queue_viewer", role="student")
    lesson = _make_project(publisher, "Analytics queue lesson")
    _progress(viewer, lesson, 72)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    response = _client(publisher).post(_analyze_url(), {}, format="json")

    assert response.status_code == 200
    assert dispatch_calls[0]["task_name"] == "worker.tasks.enhance_analytics_intelligence_report"
    assert dispatch_calls[0]["queue"] == "analytics-intelligence-test"
    report = AnalyticsIntelligenceReport.objects.get(pk=response.data["id"])
    assert report.metadata["progressive_enhancement"]["queue"] == "analytics-intelligence-test"


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
)
def test_duplicate_analytics_analyze_does_not_enqueue_duplicate_for_same_source(monkeypatch):
    publisher = _make_user("ai_duplicate_owner")
    viewer = _make_user("ai_duplicate_viewer", role="student")
    lesson = _make_project(publisher, "Duplicate analytics lesson")
    _progress(viewer, lesson, 72)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    first = _client(publisher).post(_analyze_url(), {}, format="json")
    second = _client(publisher).post(_analyze_url(), {}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.data["id"] == second.data["id"]
    assert second.data["run_key"] == first.data["run_key"]
    assert second.data["current_run_key"] == first.data["run_key"]
    assert second.data["run_key_matches"] is True
    assert second.data["force"] is False
    assert len(dispatch_calls) == 1


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    OLLAMA_ANALYTICS_INTELLIGENCE_MODEL="qwen-old",
)
def test_analytics_dedupe_run_key_allows_different_model(monkeypatch):
    publisher = _make_user("ai_model_change_owner")
    viewer = _make_user("ai_model_change_viewer", role="student")
    lesson = _make_project(publisher, "Model change analytics lesson")
    _progress(viewer, lesson, 72)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    first = _client(publisher).post(_analyze_url(), {}, format="json")
    first_report = AnalyticsIntelligenceReport.objects.get(pk=first.data["id"])
    first_run_key = first_report.metadata["run_key"]

    with override_settings(OLLAMA_ANALYTICS_INTELLIGENCE_MODEL="qwen-new"):
        second = _client(publisher).post(_analyze_url(), {}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.data["id"] != first.data["id"]
    assert second.data["source_hash"] == first.data["source_hash"]
    assert len(dispatch_calls) == 2
    second_report = AnalyticsIntelligenceReport.objects.get(pk=second.data["id"])
    assert second_report.metadata["run_key"] != first_run_key
    assert second_report.metadata["model"] == "qwen-new"


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    OLLAMA_ANALYTICS_INTELLIGENCE_MODEL="qwen-profile",
    INTELLIGENCE_HARDWARE_PROFILE="local_low",
)
def test_analytics_run_key_changes_with_hardware_profile():
    publisher = _make_user("ai_profile_change_owner")
    viewer = _make_user("ai_profile_change_viewer", role="student")
    lesson = _make_project(publisher, "Profile change analytics lesson")
    _progress(viewer, lesson, 72)
    payload = _client(publisher).get(_analytics_url()).data
    analytics_input = build_analytics_intelligence_input(publisher, payload)
    first = analytics_ollama_run_identity(analytics_input)

    with override_settings(INTELLIGENCE_HARDWARE_PROFILE="production_gpu"):
        second = analytics_ollama_run_identity(analytics_input)

    assert first["run_key"] != second["run_key"]
    assert first["hardware_profile"] == "local_low"
    assert second["hardware_profile"] == "production_gpu"


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
)
def test_analytics_dedupe_allows_different_output_language(monkeypatch):
    publisher = _make_user("ai_language_change_owner")
    viewer = _make_user("ai_language_change_viewer", role="student")
    lesson = _make_project(publisher, "Language change analytics lesson")
    _progress(viewer, lesson, 72)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    first = _client(publisher).post(_analyze_url(), {"output_language": "en"}, format="json")
    second = _client(publisher).post(_analyze_url(), {"output_language": "tr"}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.data["id"] != first.data["id"]
    assert second.data["source_hash"] != first.data["source_hash"]
    assert len(dispatch_calls) == 2


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
)
def test_force_analytics_analyze_requeues_fresh_pending_enhancement(monkeypatch):
    publisher = _make_user("ai_force_requeue_owner")
    viewer = _make_user("ai_force_requeue_viewer", role="student")
    lesson = _make_project(publisher, "Force requeue analytics lesson")
    _progress(viewer, lesson, 72)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    first = _client(publisher).post(_analyze_url(), {}, format="json")
    second = _client(publisher).post(_analyze_url(), {"force": True}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.data["id"] != first.data["id"]
    assert second.data["force"] is True
    assert len(dispatch_calls) == 2
    new_report = AnalyticsIntelligenceReport.objects.get(pk=second.data["id"])
    assert new_report.metadata["force"] is True
    assert new_report.metadata["progressive_enhancement"]["force"] is True
    old_report = AnalyticsIntelligenceReport.objects.get(pk=first.data["id"])
    assert old_report.metadata["progressive_enhancement"]["status"] == "failed"
    assert "superseded" in old_report.metadata["progressive_enhancement"]["error"]


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    INTELLIGENCE_ENHANCEMENT_STALE_SECONDS=60,
)
def test_stale_analytics_pending_enhancement_is_marked_failed(monkeypatch):
    publisher = _make_user("ai_stale_pending_owner")
    viewer = _make_user("ai_stale_pending_viewer", role="student")
    lesson = _make_project(publisher, "Stale pending analytics lesson")
    _progress(viewer, lesson, 72)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    response = _client(publisher).post(_analyze_url(), {}, format="json")
    report = AnalyticsIntelligenceReport.objects.get(pk=response.data["id"])
    metadata = dict(report.metadata)
    enhancement = dict(metadata["progressive_enhancement"])
    enhancement["queued_at"] = (timezone.now() - timedelta(seconds=120)).isoformat()
    metadata["progressive_enhancement"] = enhancement
    report.metadata = metadata
    report.save(update_fields=["metadata", "updated_at"])

    latest = _client(publisher).get(_latest_url())

    assert latest.status_code == 200
    assert latest.data["id"] == response.data["id"]
    assert latest.data["enhancement_pending"] is False
    assert latest.data["enhancement_status"] == "failed"
    assert "stale timeout" in latest.data["enhancement_error_safe"]
    report.refresh_from_db()
    enhancement = report.metadata["progressive_enhancement"]
    assert enhancement["failed_at"]
    assert enhancement["stale"] is True


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    INTELLIGENCE_ENHANCEMENT_STALE_SECONDS=60,
)
def test_stale_analytics_pending_reanalyze_enqueues_again(monkeypatch):
    publisher = _make_user("ai_stale_requeue_owner")
    viewer = _make_user("ai_stale_requeue_viewer", role="student")
    lesson = _make_project(publisher, "Stale requeue analytics lesson")
    _progress(viewer, lesson, 72)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    first = _client(publisher).post(_analyze_url(), {}, format="json")
    old_report = AnalyticsIntelligenceReport.objects.get(pk=first.data["id"])
    metadata = dict(old_report.metadata)
    enhancement = dict(metadata["progressive_enhancement"])
    enhancement["queued_at"] = (timezone.now() - timedelta(seconds=120)).isoformat()
    metadata["progressive_enhancement"] = enhancement
    old_report.metadata = metadata
    old_report.save(update_fields=["metadata", "updated_at"])

    second = _client(publisher).post(_analyze_url(), {}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.data["id"] != first.data["id"]
    assert second.data["enhancement_pending"] is True
    assert len(dispatch_calls) == 2
    old_report.refresh_from_db()
    assert old_report.metadata["progressive_enhancement"]["status"] == "failed"


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
)
def test_analytics_stale_source_hash_allows_new_enhancement(monkeypatch):
    publisher = _make_user("ai_stale_new_owner")
    viewer = _make_user("ai_stale_new_viewer", role="student")
    lesson = _make_project(publisher, "Stale analytics lesson")
    _progress(viewer, lesson, 72)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    first = _client(publisher).post(_analyze_url(), {}, format="json")
    changed_lesson = _make_project(publisher, "Stale analytics changed lesson")
    _progress(viewer, changed_lesson, 44)
    second = _client(publisher).post(_analyze_url(), {}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.data["id"] != second.data["id"]
    assert first.data["source_hash"] != second.data["source_hash"]
    assert len(dispatch_calls) == 2


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_analytics_input_uses_lesson_intelligence_summaries_not_transcripts():
    publisher = _make_user("ai_lesson_quality_owner")
    viewer = _make_user("ai_lesson_quality_viewer", role="student")
    lesson = _make_project(publisher, "Database performance patterns")
    TranscriptPage.objects.create(
        project=lesson,
        order=0,
        source_slide_index=0,
        page_key="secret-page",
        original_text="SECRET_FULL_TRANSCRIPT_TOKEN should never enter analytics prompts.",
        narration_text="SECRET_FULL_TRANSCRIPT_TOKEN should never enter analytics prompts.",
    )
    _progress(viewer, lesson, 22)
    LessonComment.objects.create(user=viewer, project=lesson, text="Please add more examples.")
    LessonIntelligenceReport.objects.create(
        project=lesson,
        requested_by=publisher,
        status="done",
        provider="ollama",
        source_hash="lesson-quality-hash-1",
        summary="Studio summary: advanced indexing lesson has short narration and needs examples.",
        complexity_level="advanced",
        complexity_score=88,
        clarity_warnings=[{"type": "missing_examples", "severity": "medium", "message": "Add a query example."}],
        expanded_narration_suggestions=[
            {"type": "short_narration", "draft_narration": "Expand the database indexing example."}
        ],
    )

    analytics_payload = _client(publisher).get(_analytics_url()).data
    analytics_input = build_analytics_intelligence_input(publisher, analytics_payload)
    provider_payload_text = json.dumps(analytics_input.to_provider_payload(), sort_keys=True)

    assert "Studio summary: advanced indexing" in provider_payload_text
    assert "lesson_intelligence" in provider_payload_text
    assert "missing_examples_signal" in provider_payload_text
    assert '"missing_cover": true' in provider_payload_text
    assert "SECRET_FULL_TRANSCRIPT_TOKEN" not in provider_payload_text
    assert "ai_lesson_quality_viewer" not in provider_payload_text


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_analytics_source_hash_changes_when_lesson_intelligence_changes():
    publisher = _make_user("ai_lesson_quality_hash_owner")
    viewer = _make_user("ai_lesson_quality_hash_viewer", role="student")
    lesson = _make_project(publisher, "Lesson quality hash lesson")
    _progress(viewer, lesson, 35)
    report = LessonIntelligenceReport.objects.create(
        project=lesson,
        requested_by=publisher,
        status="done",
        provider="ollama",
        source_hash="lesson-quality-hash-initial",
        summary="Initial Studio summary.",
        complexity_level="intermediate",
        complexity_score=55,
    )

    first_payload = _client(publisher).get(_analytics_url()).data
    first_input = build_analytics_intelligence_input(publisher, first_payload)
    report.summary = "Updated Studio summary with clearer low-progress signal."
    report.save(update_fields=["summary", "updated_at"])
    second_payload = _client(publisher).get(_analytics_url()).data
    second_input = build_analytics_intelligence_input(publisher, second_payload)

    assert first_input.source_hash != second_input.source_hash


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic",
    ANALYTICS_INTELLIGENCE_MAX_INPUT_CHARS=12000,
)
def test_large_analytics_payload_stays_compact_with_many_lessons():
    publisher = _make_user("ai_compact_62_owner")
    viewer = _make_user("ai_compact_62_viewer", role="student")
    for index in range(62):
        lesson = _make_project(publisher, f"Large catalog lesson {index}")
        if index % 3 == 0:
            _progress(viewer, lesson, 25 + (index % 40))
    analytics_payload = _client(publisher).get(_analytics_url("range=90")).data
    analytics_input = build_analytics_intelligence_input(publisher, analytics_payload)
    provider_payload_text = json.dumps(analytics_input.to_provider_payload(), sort_keys=True)

    assert analytics_input.input_chars <= 12000
    assert len(analytics_input.analytics_payload["lesson_quality"]["weak_lessons"]) <= 10
    assert len(analytics_input.analytics_payload["lesson_quality"]["strong_lessons"]) <= 5
    assert "transcript_pages" not in provider_payload_text
    assert "narration_text" not in provider_payload_text


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
)
def test_analytics_enhancement_task_start_sets_running(monkeypatch):
    publisher = _make_user("ai_task_running_owner")
    viewer = _make_user("ai_task_running_viewer", role="student")
    lesson = _make_project(publisher, "Task running analytics lesson")
    _progress(viewer, lesson, 72)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    response = _client(publisher).post(_analyze_url(), {}, format="json")

    from worker.tasks import _mark_analytics_intelligence_enhancement  # noqa: E402

    _mark_analytics_intelligence_enhancement(response.data["id"], "running", task_id="task-started")
    report = AnalyticsIntelligenceReport.objects.get(pk=response.data["id"])
    enhancement = report.metadata["progressive_enhancement"]

    assert enhancement["status"] == "running"
    assert enhancement["task_id"] == "task-started"
    assert enhancement["started_at"]
    assert report.metadata["provider_chain_attempts"][0]["status"] == "running"


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    OLLAMA_ANALYTICS_INTELLIGENCE_BASE_URL="http://secret-analytics-ollama.local:11434",
    ANALYTICS_INTELLIGENCE_TIMEOUT_SECONDS=120,
    INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS=20,
    ANALYTICS_INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS=20,
)
def test_analytics_ollama_timeout_uses_sync_cap_and_falls_back(monkeypatch):
    publisher = _make_user("ai_ollama_cap_owner")
    viewer = _make_user("ai_ollama_cap_viewer", role="student")
    lesson = _make_project(publisher, "Timeout cap analytics lesson")
    _progress(viewer, lesson, 72)
    captured = {}

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        raise URLError("timed out")

    monkeypatch.setattr("core.analytics_intelligence.urlopen", fake_urlopen)

    analytics_payload = _client(publisher).get(_analytics_url()).data
    analytics_input = build_analytics_intelligence_input(publisher, analytics_payload)
    analysis = analyze_analytics_with_provider_chain(analytics_input, chain=["ollama", "heuristic"])

    assert captured["timeout"] == 20
    assert analysis["provider"] == "heuristic"
    assert analysis["fallback_used"] is True
    attempts = analysis["metadata"]["provider_chain_attempts"]
    assert attempts[0]["provider"] == "ollama"
    assert attempts[0]["status"] in {"skipped", "failed"}
    serialized = json.dumps(analysis)
    assert "secret-analytics-ollama.local" not in serialized


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    OLLAMA_ANALYTICS_INTELLIGENCE_BASE_URL="http://ollama.test:11434",
    ANALYTICS_INTELLIGENCE_TIMEOUT_SECONDS=8,
    INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS=20,
    INTELLIGENCE_BACKGROUND_PROVIDER_TIMEOUT_SECONDS=120,
)
def test_background_success_updates_analytics_report_to_ollama(monkeypatch):
    publisher = _make_user("ai_ollama_success_owner")
    viewer = _make_user("ai_ollama_success_viewer", role="student")
    lesson = _make_project(publisher, "Ollama analytics lesson")
    _progress(viewer, lesson, 84)
    captured = {}
    provider_payload = {
        "provider": "ollama",
        "analytics_summary": "Ollama analytics summary.",
        "health_score": 76,
        "risk_level": "medium",
        "insights": [{"type": "engagement", "message": "Ollama found solid engagement."}],
        "recommendations": [{"type": "completion", "message": "Keep the lesson structure focused."}],
        "lesson_actions": [{"lesson_title": "Ollama analytics lesson", "message": "Review this lesson."}],
        "category_actions": [{"category": "general", "message": "Monitor category balance."}],
        "limitations": [],
    }

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        captured["body"] = request.data.decode("utf-8")
        return _FakeOllamaResponse({"response": json.dumps(provider_payload)})

    monkeypatch.setattr("core.analytics_intelligence.urlopen", fake_urlopen)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    response = _client(publisher).post(_analyze_url(), {}, format="json")
    from worker.tasks import enhance_analytics_intelligence_report  # noqa: E402

    task_result = enhance_analytics_intelligence_report.run(response.data["id"], response.data["source_hash"])
    latest = _client(publisher).get(_latest_url())

    assert response.status_code == 200
    assert response.data["provider"] == "heuristic"
    assert response.data["enhancement_pending"] is True
    assert task_result["status"] == "done"
    assert 45 <= captured["timeout"] < 120
    assert latest.data["provider"] == "ollama"
    assert latest.data["fallback_used"] is False
    assert latest.data["summary"].startswith("Ollama analytics summary.")
    assert latest.data["health_score"] == 76
    assert latest.data["enhancement_status"] == "done"
    report = AnalyticsIntelligenceReport.objects.get(pk=response.data["id"])
    enhancement = report.metadata["progressive_enhancement"]
    assert enhancement["started_at"]
    assert enhancement["finished_at"]
    assert float(enhancement["timeout_seconds"]) >= 0
    assert latest.data["provider_chain_attempts"][0]["status"] == "success"
    assert "Analytics payload" in captured["body"]


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    OLLAMA_ANALYTICS_INTELLIGENCE_BASE_URL="http://ollama.test:11434",
    INTELLIGENCE_OLLAMA_CHUNK_MAX_CHARS=1200,
    INTELLIGENCE_OLLAMA_CHUNK_MAX_ITEMS=2,
    INTELLIGENCE_OLLAMA_CHUNK_TIMEOUT_MIN_SECONDS=10,
    INTELLIGENCE_OLLAMA_CHUNK_TIMEOUT_MAX_SECONDS=25,
)
def test_large_analytics_ollama_background_uses_chunks(monkeypatch):
    publisher = _make_user("ai_chunk_owner")
    viewer = _make_user("ai_chunk_viewer", role="student")
    for index in range(8):
        lesson = _make_project(publisher, f"Chunk analytics lesson {index}")
        _progress(viewer, lesson, 50 + index)
        LessonLike.objects.create(user=viewer, project=lesson)
        LessonComment.objects.create(user=viewer, project=lesson, text=f"Can you explain topic {index} with more examples?")
    captured = []

    def fake_urlopen(request, timeout):
        captured.append({"timeout": timeout, "body": request.data.decode("utf-8")})
        payload = {
            "provider": "ollama",
            "analytics_summary": f"Chunk analytics summary {len(captured)}.",
            "health_score": 70,
            "risk_level": "medium",
            "insights": [{"type": "chunk", "message": f"Chunk {len(captured)} insight."}],
            "recommendations": [{"type": "comments", "message": "Learners want more examples."}],
            "lesson_actions": [{"lesson_title": "Chunk analytics lesson", "message": "Add examples."}],
            "category_actions": [],
            "limitations": [],
        }
        return _FakeOllamaResponse({"response": json.dumps(payload)})

    monkeypatch.setattr("core.analytics_intelligence.urlopen", fake_urlopen)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    response = _client(publisher).post(_analyze_url(), {}, format="json")
    from worker.tasks import enhance_analytics_intelligence_report  # noqa: E402

    task_result = enhance_analytics_intelligence_report.run(response.data["id"], response.data["source_hash"])
    latest = _client(publisher).get(_latest_url())

    assert task_result["status"] == "done"
    assert latest.data["provider"] == "ollama"
    assert len(captured) > 1
    assert all(10 <= item["timeout"] <= 25 for item in captured)
    assert "ai_chunk_viewer" not in json.dumps(captured)
    report = AnalyticsIntelligenceReport.objects.get(pk=response.data["id"])
    enhancement = report.metadata["progressive_enhancement"]
    assert enhancement["phase"] == "done"
    assert enhancement["chunk_count"] == len(captured)
    assert enhancement["completed_chunks"] == len(captured)
    assert enhancement["failed_chunks"] == 0
    assert report.metadata["chunked"] is True
    assert report.metadata["prompt_version"] == "analytics-intelligence-v2"
    assert "Learners want more examples" in json.dumps(latest.data)


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    OLLAMA_ANALYTICS_INTELLIGENCE_BASE_URL="http://ollama.test:11434",
    INTELLIGENCE_OLLAMA_CHUNK_MAX_CHARS=1200,
    INTELLIGENCE_OLLAMA_CHUNK_MAX_ITEMS=1,
)
def test_analytics_chunk_failure_returns_partial_ollama_report(monkeypatch):
    publisher = _make_user("ai_chunk_partial_owner")
    viewer = _make_user("ai_chunk_partial_viewer", role="student")
    for index in range(4):
        lesson = _make_project(publisher, f"Partial analytics lesson {index}")
        _progress(viewer, lesson, 60 + index)
        LessonComment.objects.create(user=viewer, project=lesson, text=f"Question about lesson {index}.")
    calls = {"count": 0}

    def fake_urlopen(request, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise URLError("timed out")
        payload = {
            "provider": "ollama",
            "analytics_summary": "Successful analytics chunk.",
            "health_score": 74,
            "risk_level": "medium",
            "insights": [{"type": "partial", "message": "Recovered partial analytics insight."}],
            "recommendations": [],
            "lesson_actions": [],
            "category_actions": [],
            "limitations": [],
        }
        return _FakeOllamaResponse({"response": json.dumps(payload)})

    monkeypatch.setattr("core.analytics_intelligence.urlopen", fake_urlopen)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    response = _client(publisher).post(_analyze_url(), {}, format="json")
    from worker.tasks import enhance_analytics_intelligence_report  # noqa: E402

    task_result = enhance_analytics_intelligence_report.run(response.data["id"], response.data["source_hash"])
    latest = _client(publisher).get(_latest_url())

    assert task_result["status"] == "partial"
    assert latest.data["provider"] == "ollama"
    assert latest.data["enhancement_status"] == "partial"
    assert latest.data["provider_chain_attempts"][0]["status"] == "partial"
    report = AnalyticsIntelligenceReport.objects.get(pk=response.data["id"])
    enhancement = report.metadata["progressive_enhancement"]
    assert enhancement["failed_chunks"] >= 1
    assert enhancement["partial_enhancement"] is True
    assert report.metadata["partial_enhancement"] is True


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    OLLAMA_ANALYTICS_INTELLIGENCE_BASE_URL="http://ollama.test:11434",
    INTELLIGENCE_OLLAMA_CHUNK_MAX_CHARS=1200,
    INTELLIGENCE_OLLAMA_CHUNK_MAX_ITEMS=1,
    INTELLIGENCE_OLLAMA_CHUNK_TIMEOUT_MIN_SECONDS=10,
    INTELLIGENCE_OLLAMA_CHUNK_TIMEOUT_MAX_SECONDS=25,
    ANALYTICS_INTELLIGENCE_MAX_BACKGROUND_SECONDS=2,
)
def test_analytics_total_budget_exceeded_returns_terminal_partial(monkeypatch):
    publisher = _make_user("ai_chunk_budget_owner")
    viewer = _make_user("ai_chunk_budget_viewer", role="student")
    for index in range(4):
        lesson = _make_project(publisher, f"Budget analytics lesson {index}")
        _progress(viewer, lesson, 60 + index)
        LessonComment.objects.create(user=viewer, project=lesson, text=f"Question about budget lesson {index}.")
    calls = {"count": 0}

    def fake_urlopen(request, timeout):
        calls["count"] += 1
        payload = {
            "provider": "ollama",
            "analytics_summary": "Only the first analytics chunk completed.",
            "health_score": 70,
            "risk_level": "medium",
            "insights": [{"type": "budget", "message": "First analytics chunk completed."}],
            "recommendations": [],
            "lesson_actions": [],
            "category_actions": [],
            "limitations": [],
        }
        return _FakeOllamaResponse({"response": json.dumps(payload)})

    times = iter([0.0, 0.0, 3.0, 3.0])
    monkeypatch.setattr("core.analytics_intelligence.urlopen", fake_urlopen)
    monkeypatch.setattr("core.analytics_intelligence._analytics_total_timeout_budget_seconds", lambda: 2.0)
    monkeypatch.setattr("core.analytics_intelligence.time.monotonic", lambda: next(times, 3.0))
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    response = _client(publisher).post(_analyze_url(), {}, format="json")
    from worker.tasks import enhance_analytics_intelligence_report  # noqa: E402

    task_result = enhance_analytics_intelligence_report.run(response.data["id"], response.data["source_hash"])
    latest = _client(publisher).get(_latest_url())

    assert task_result["status"] == "partial"
    assert latest.data["enhancement_pending"] is False
    assert latest.data["enhancement_status"] == "partial"
    assert calls["count"] == 1
    report = AnalyticsIntelligenceReport.objects.get(pk=response.data["id"])
    enhancement = report.metadata["progressive_enhancement"]
    assert enhancement["completed_chunks"] == 1
    assert enhancement["failed_chunks"] == enhancement["chunk_count"] - 1
    assert any("time budget" in str(item) for item in enhancement["chunk_limitations"])


@override_settings(
    INTELLIGENCE_BACKGROUND_TIMEOUT_MIN_SECONDS=60,
    INTELLIGENCE_BACKGROUND_TIMEOUT_MAX_SECONDS=180,
    INTELLIGENCE_BACKGROUND_TIMEOUT_PER_1000_CHARS=4,
    INTELLIGENCE_BACKGROUND_TIMEOUT_PER_PAGE_SECONDS=2,
    INTELLIGENCE_BACKGROUND_TIMEOUT_PER_COMMENT_SECONDS=1,
)
def test_adaptive_analytics_timeout_scales_and_respects_max():
    small = {
        "input_chars": 1000,
        "analytics": {
            "tables": {"top_lessons": [{"lesson_id": 1}], "recent_lessons": [], "top_categories": []},
            "qualitative_feedback": {"recent_comments": []},
        },
    }
    large = {
        "input_chars": 80000,
        "analytics": {
            "tables": {
                "top_lessons": [{"lesson_id": index} for index in range(30)],
                "recent_lessons": [{"lesson_id": index} for index in range(30)],
                "top_categories": [{"category_slug": f"cat-{index}"} for index in range(20)],
            },
            "qualitative_feedback": {"recent_comments": [{"text": "Useful"} for _ in range(50)]},
        },
    }

    small_timeout = adaptive_analytics_intelligence_timeout(small, base_seconds=120)
    large_timeout = adaptive_analytics_intelligence_timeout(large, base_seconds=120)

    assert small_timeout < large_timeout
    assert large_timeout == 180


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    OLLAMA_ANALYTICS_INTELLIGENCE_BASE_URL="http://ollama.test:11434",
    ANALYTICS_INTELLIGENCE_TIMEOUT_SECONDS=8,
)
def test_invalid_analytics_ollama_json_falls_back_to_heuristic(monkeypatch):
    publisher = _make_user("ai_ollama_invalid_owner")
    viewer = _make_user("ai_ollama_invalid_viewer", role="student")
    lesson = _make_project(publisher, "Invalid JSON fallback lesson")
    _progress(viewer, lesson, 58)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    def fake_urlopen(request, timeout):
        return _FakeOllamaResponse({"response": "not-json"})

    monkeypatch.setattr("core.analytics_intelligence.urlopen", fake_urlopen)

    response = _client(publisher).post(_analyze_url(), {}, format="json")
    from worker.tasks import enhance_analytics_intelligence_report  # noqa: E402

    task_result = enhance_analytics_intelligence_report.run(response.data["id"], response.data["source_hash"])
    latest = _client(publisher).get(_latest_url())

    assert response.status_code == 200
    assert task_result["status"] == "failed"
    assert latest.data["provider"] == "heuristic"
    assert latest.data["fallback_used"] is True
    assert latest.data["enhancement_pending"] is False
    assert latest.data["enhancement_status"] == "failed"
    report = AnalyticsIntelligenceReport.objects.get(pk=response.data["id"])
    enhancement = report.metadata["progressive_enhancement"]
    assert enhancement["started_at"]
    assert enhancement["finished_at"]
    assert enhancement["failed_at"]
    assert "ollama.test" not in json.dumps(latest.data)
    attempts = latest.data["provider_chain_attempts"]
    assert attempts[0]["provider"] == "ollama"
    assert attempts[0]["status"] == "failed"

    second = _client(publisher).post(_analyze_url(), {}, format="json")
    assert second.status_code == 200
    assert second.data["id"] == response.data["id"]
    assert len(dispatch_calls) == 1


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="openai,heuristic",
    ANALYTICS_INTELLIGENCE_ALLOW_EXTERNAL=False,
)
def test_paid_provider_disabled_unless_external_allowed():
    publisher = _make_user("ai_paid_owner")
    viewer = _make_user("ai_paid_viewer", role="student")
    lesson = _make_project(publisher, "Paid provider lesson")
    _progress(viewer, lesson, 66)

    response = _client(publisher).post(_analyze_url(), {}, format="json")

    assert response.status_code == 200
    assert response.data["provider"] == "heuristic"
    assert response.data["fallback_used"] is True
    attempts = response.data["metadata"]["provider_chain_attempts"]
    assert attempts[0]["provider"] == "openai"
    assert attempts[0]["status"] == "skipped"
    assert "disabled" in attempts[0]["error"]

    with pytest.raises(AnalyticsIntelligenceProviderUnavailable, match="disabled"):
        PaidAnalyticsIntelligenceProvider("openai").analyze_analytics({})

    with override_settings(ANALYTICS_INTELLIGENCE_ALLOW_EXTERNAL=True):
        with pytest.raises(AnalyticsIntelligenceProviderUnavailable, match="not implemented"):
            PaidAnalyticsIntelligenceProvider("openai").analyze_analytics({})

    assert AnalyticsIntelligenceReport.objects.filter(requested_by=publisher, provider="heuristic").exists()


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_no_raw_storage_paths_exposed():
    publisher = _make_user("ai_paths_owner")
    viewer = _make_user("ai_paths_viewer", role="student")
    lesson = _make_project(publisher, "Path safe lesson")
    lesson.cover_image_original = "C:/private/storage_local/secret-cover.png"
    lesson.cover_image_processed = "/storage_local/generated-media/secret-cover.webp"
    lesson.save(update_fields=["cover_image_original", "cover_image_processed", "updated_at"])
    _progress(viewer, lesson, 72)

    response = _client(publisher).post(_analyze_url(), {}, format="json")

    serialized = _text(response.data)
    assert response.status_code == 200
    assert "storage_local" not in serialized
    assert "secret-cover" not in serialized
    assert "C:/private" not in serialized


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_turkish_analytics_output_language_returns_turkish_suggestions():
    publisher = _make_user("ai_tr_owner")
    viewer = _make_user("ai_tr_viewer", role="student")
    category = Category.objects.create(name="Türkçe Kategori", slug="turkce-kategori")
    lesson = _make_project(publisher, "Türkçe Analitik Dersi", category=category)
    _progress(viewer, lesson, 24)

    response = _client(publisher).post(_analyze_url(), {"output_language": "tr"}, format="json")

    assert response.status_code == 200
    assert response.data["output_language"] == "tr"
    assert "Bu aralıkta" in response.data["summary"] or "ders" in response.data["summary"].lower()
    serialized = json.dumps(response.data, ensure_ascii=False)
    assert "Öğrenciler" in serialized or "Ders ilerlemesini" in serialized or "görüntüleme" in serialized


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic",
    ANALYTICS_INTELLIGENCE_MAX_INPUT_CHARS=1000,
)
def test_large_analytics_payload_does_not_return_400_due_to_size():
    publisher = _make_user("ai_large_owner")
    viewers = [_make_user(f"ai_large_viewer_{index}", role="student") for index in range(12)]
    category = Category.objects.create(name="Large Dataset Category", slug="large-dataset-category")
    for index in range(14):
        lesson = _make_project(
            publisher,
            f"Large Analytics Lesson {index} with detailed title for compaction testing",
            category=category,
        )
        _progress(viewers[index % len(viewers)], lesson, 20 + (index % 6) * 10)
        if index % 2 == 0:
            LessonLike.objects.create(user=viewers[index % len(viewers)], project=lesson)

    response = _client(publisher).post(_analyze_url("range=90"), {}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "done"
    assert response.data["metadata"]["input_truncated"] is True
    assert response.data["metadata"]["compaction"]["compact_char_count"] > 0


@override_settings(
    ANALYTICS_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic",
    ANALYTICS_INTELLIGENCE_MAX_INPUT_CHARS=1000,
)
def test_large_analytics_payload_returns_limitation_note():
    publisher = _make_user("ai_large_limitation_owner")
    viewer = _make_user("ai_large_limitation_viewer", role="student")
    for index in range(10):
        lesson = _make_project(publisher, f"Large Limitation Lesson {index}")
        _progress(viewer, lesson, 35)

    response = _client(publisher).post(_analyze_url("range=90"), {}, format="json")

    assert response.status_code == 200
    limitations_text = _text(response.data["limitations"]).lower()
    assert "large" in limitations_text or "omitted" in limitations_text
    assert response.data["metadata"]["input_truncated"] is True


@override_settings(ANALYTICS_INTELLIGENCE_ENABLED=True, ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_empty_analytics_localized_onboarding_for_turkish_request():
    publisher = _make_user("ai_empty_tr_owner")

    response = _client(publisher).post(_analyze_url(), {"output_language": "tr"}, format="json")

    assert response.status_code == 200
    assert response.data["output_language"] == "tr"
    assert "henüz" in response.data["summary"]
    assert any("Yayın" in item["message"] or "yayın" in item["message"] for item in response.data["recommendations"])
