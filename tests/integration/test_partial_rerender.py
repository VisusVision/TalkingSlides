# pyright: reportMissingImports=false

from io import BytesIO
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

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
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core import views as core_views  # noqa: E402
from core.avatar_runtime_settings import project_avatar_runtime_settings  # noqa: E402
from core.models import Job, Project, RenderFollowUpIntent, TranscriptPage, UserProfile  # noqa: E402
from worker.partial_render_manifest import build_expected_partial_render_manifest  # noqa: E402


def _png_bytes() -> bytes:
    Image = pytest.importorskip("PIL.Image")
    buffer = BytesIO()
    Image.new("RGB", (16, 16), color=(40, 90, 140)).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload_png(name: str = "cover.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, _png_bytes(), content_type="image/png")


def _make_user(username: str, *, role: str = "publisher") -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _make_project(owner: User, tmp_path: Path) -> Project:
    project = Project.objects.create(
        title=f"{owner.username} partial lesson",
        user=owner,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    cover = tmp_path / "uploads" / str(project.id) / "active.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(_png_bytes())
    project.cover_image_original = f"uploads/{project.id}/active.png"
    project.cover_image_processed = project.cover_image_original
    project.save(update_fields=["cover_image_original", "cover_image_processed", "updated_at"])
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}/old.mp4")
    return project


def _make_page(project: Project) -> TranscriptPage:
    return TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="slide-1",
        original_text="Original text",
        narration_text="Original text",
        rich_text_html="Original text",
        subtitle_chunks=["Original text"],
        editor_document={"version": 1, "paragraphs": [{"text": "Original text"}]},
    )


def _preview_slide(
    page: TranscriptPage,
    *,
    display_text: str | None = None,
    narration_text: str | None = None,
    index: int | None = None,
) -> dict:
    row_index = page.order if index is None else index
    narration = narration_text if narration_text is not None else page.narration_text
    display = display_text if display_text is not None else page.original_text
    return {
        "index": row_index,
        "slide_num": row_index + 1,
        "page_key": page.page_key,
        "page_id": page.id,
        "source_slide_index": page.source_slide_index,
        "split_index": page.split_index,
        "display_text": display,
        "original_text": display,
        "narration_text": narration,
        "text": narration,
        "spoken_text": narration,
        "subtitle_chunks": [narration],
        "whiteboard_mode": bool(page.whiteboard_mode),
        "editor_document": page.editor_document or {},
        "duration": 2.0,
        "pause_seconds": 0.25,
        "source_render_method": "pptx_source",
        "source_render_warnings": [],
        "source_render_details": [],
        "source_render_dependency_report": {"renderer": "libreoffice"},
    }


def _preview_sidecar(project: Project, slides: list[dict]) -> dict:
    final_segments = []
    source_render_metadata = []
    for index, slide in enumerate(slides):
        page_key = slide["page_key"]
        final_segments.append(
            {
                "index": index,
                "page_key": page_key,
                "transcript": slide["narration_text"],
                "tts_audio": f"{project.id}/audio/{page_key}.mp3",
                "slide": f"{project.id}/images/{page_key}.png",
                "part_rel_path": f"{project.id}/parts/{page_key}.mp4",
                "duration": slide.get("duration", 2.0),
                "pause_seconds": slide.get("pause_seconds", 0.25),
                "source_render_method": slide.get("source_render_method", "pptx_source"),
                "source_render_dependency_report": slide.get("source_render_dependency_report", {}),
            }
        )
        source_render_metadata.append(
            {
                "index": index,
                "page_key": page_key,
                "method": slide.get("source_render_method", "pptx_source"),
                "warnings": slide.get("source_render_warnings", []),
                "details": slide.get("source_render_details", []),
                "dependency_report": slide.get("source_render_dependency_report", {}),
            }
        )
    sidecar = {
        "final_segments": final_segments,
        "source_render_metadata": source_render_metadata,
    }
    sidecar["partial_render_manifest"] = build_expected_partial_render_manifest(
        project_id=project.id,
        slides=slides,
        previous_playback_assets=sidecar,
        tts_settings=core_views._project_tts_settings(project),
        avatar_options={
            "requested": False,
            "enabled": False,
            "teacher_id": project.user_id,
            "avatar_visible": True,
            "avatar_runtime_settings": project_avatar_runtime_settings(project),
        },
    )
    return sidecar


def _write_preview_sidecar(tmp_path: Path, project: Project, sidecar: dict) -> None:
    sidecar_dir = tmp_path / str(project.id)
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    (sidecar_dir / "playback_assets.json").write_text(json.dumps(sidecar), encoding="utf-8")


def _preview_payload_page(page: TranscriptPage, **overrides) -> dict:
    payload = {
        "id": page.id,
        "page_key": page.page_key,
        "order": page.order,
        "source_slide_index": page.source_slide_index,
        "split_index": page.split_index,
        "original_text": page.original_text,
        "display_text": page.original_text,
        "narration_text": page.narration_text,
        "rich_text_html": page.rich_text_html,
        "subtitle_chunks": list(page.subtitle_chunks or []),
        "whiteboard_mode": bool(page.whiteboard_mode),
        "editor_document": page.editor_document or {},
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_cover_only_update_does_not_create_full_render_job(tmp_path):
    owner = _make_user("partial_cover_owner")
    project = _make_project(owner, tmp_path)
    before = Job.objects.filter(project=project, job_type="video_export").count()

    with override_settings(STORAGE_ROOT=str(tmp_path), VISUAL_MODERATION_AUTO_ENABLED=False):
        response = _client(owner).post(
            f"/api/v1/projects/{project.id}/cover/",
            {"cover_file": _upload_png()},
            format="multipart",
        )

    assert response.status_code == 200
    assert response.data["render_required"] is False
    project.refresh_from_db()
    assert project.draft_data["metadata"]["cover_dirty"] is True
    assert project.draft_data["project"]["cover_image_original"].startswith(f"uploads/{project.id}/cover_")
    assert Job.objects.filter(project=project, job_type="video_export").count() == before


@pytest.mark.django_db
def test_cover_only_update_creates_cover_moderation_scan_marker(tmp_path):
    owner = _make_user("partial_cover_marker")
    project = _make_project(owner, tmp_path)

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        ENABLE_VISUAL_MODERATION=True,
        VISUAL_MODERATION_AUTO_ENABLED=False,
    ):
        response = _client(owner).post(
            f"/api/v1/projects/{project.id}/cover/",
            {"cover_file": _upload_png("new-cover.png")},
            format="multipart",
        )

    project.refresh_from_db()
    marker = project.moderation_summary["visual_asset_scan"]
    assert response.status_code == 200
    assert marker["asset_type"] == "cover"
    assert marker["status"] == "needs_rescan"
    assert marker["cover_hash"]


@pytest.mark.django_db
def test_transcript_page_edit_marks_changed_page_for_text_moderation(tmp_path):
    owner = _make_user("partial_text_owner")
    project = _make_project(owner, tmp_path)
    page = _make_page(project)

    response = _client(owner).patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {"pages": [{"id": page.id, "original_text": "Changed text"}]},
        format="json",
    )

    project.refresh_from_db()
    changed = project.moderation_summary["editor_text_changed"]
    assert response.status_code == 200
    assert changed["changed_page_keys"] == ["slide-1"]
    assert changed["content_hash"]


@pytest.mark.django_db
def test_source_upload_triggers_full_pipeline(monkeypatch, tmp_path):
    owner = _make_user("partial_source_owner")
    sent = {}

    def fake_dispatch(task_name, *, args, kwargs=None, queue=None):
        sent["task_name"] = task_name
        sent["args"] = args
        sent["queue"] = queue
        return SimpleNamespace(id="render-task")

    monkeypatch.setattr(core_views, "_dispatch_celery_task", fake_dispatch)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _client(owner).post(
            "/api/v1/projects/",
            {"lesson_file": SimpleUploadedFile("lesson.txt", b"hello", content_type="text/plain")},
            format="multipart",
        )

    assert response.status_code == 202
    assert sent["task_name"] == core_views._PROCESS_PROJECT_RENDER_TASK
    assert sent["args"][8] is None
    assert Job.objects.filter(status="pending", job_type="video_export").exists()


@pytest.mark.django_db
def test_metadata_only_edit_does_not_render_video(tmp_path):
    owner = _make_user("partial_metadata_owner")
    project = _make_project(owner, tmp_path)
    before = Job.objects.filter(project=project, job_type="video_export").count()

    response = _client(owner).patch(
        f"/api/v1/projects/{project.id}/",
        {"category_name": "Metadata only"},
        format="json",
    )

    assert response.status_code == 200
    assert Job.objects.filter(project=project, job_type="video_export").count() == before


@pytest.mark.django_db
def test_project_detail_exposes_sanitized_latest_render_analysis(tmp_path):
    owner = _make_user("partial_analysis_detail_owner")
    project = _make_project(owner, tmp_path)
    before_jobs = Job.objects.filter(project=project, job_type="video_export").count()
    before_intents = RenderFollowUpIntent.objects.filter(project=project).count()
    sidecar_dir = tmp_path / str(project.id)
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    (sidecar_dir / "playback_assets.json").write_text(
        json.dumps(
            {
                "partial_render_analysis": {
                    "version": 1,
                    "mode": "report_only",
                    "generated_from": "partial_render_manifest",
                    "old_manifest": {"artifacts": {"tts_audio": f"{project.id}/audio/private.mp3"}},
                    "classifier": {
                        "available": True,
                        "notes": ["old_playback_assets_missing", f"{project.id}/audio/private.mp3"],
                        "result": {
                            "summary": {
                                "display_text_changed": 1,
                                "bad/key": 7,
                            },
                            "global_reasons": [],
                            "pages": {
                                "slide-1": {
                                    "page_key": "slide-1",
                                    "index": 0,
                                    "classification": "display_text_changed",
                                    "reasons": ["display_text_changed", f"{project.id}/parts/part_001.mp4"],
                                    "requires_full": False,
                                    "missing_artifacts": [
                                        "tts_audio",
                                        "slide_image",
                                        f"{project.id}/audio/slide_001.mp3",
                                        "C:/secret/audio.mp3",
                                    ],
                                    "artifacts": {"composed_segment": f"{project.id}/parts/part_001.mp4"},
                                }
                            },
                        },
                    },
                    "plan": {
                        "version": 1,
                        "mode": "report_only",
                        "summary": {"recompose_visual_only_future": 1},
                        "pages": {
                            "slide-1": {
                                "page_key": "slide-1",
                                "classification": "display_text_changed",
                                "reasons": ["display_text_changed"],
                                "recommended_action": "recompose_visual_only_future",
                                "future_only": True,
                                "actual_behavior_changed": False,
                            }
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _client(owner).get(f"/api/v1/projects/{project.id}/")

    assert response.status_code == 200
    analysis = response.data["latest_render_analysis"]
    assert analysis["mode"] == "report_only"
    assert analysis["classifier"]["available"] is True
    assert analysis["classifier"]["summary"] == {"display_text_changed": 1}
    assert analysis["classifier"]["pages"]["slide-1"] == {
        "page_key": "slide-1",
        "index": 0,
        "classification": "display_text_changed",
        "reasons": ["display_text_changed"],
        "requires_full": False,
        "missing_artifacts": ["tts_audio", "slide_image"],
        "missing_artifact_count": 2,
    }
    assert analysis["plan"]["summary"] == {"recompose_visual_only_future": 1}
    assert analysis["plan"]["pages"]["slide-1"]["recommended_action"] == "recompose_visual_only_future"
    serialized = json.dumps(analysis)
    assert "audio/private.mp3" not in serialized
    assert "parts/part_001.mp4" not in serialized
    assert "C:/secret" not in serialized
    assert Job.objects.filter(project=project, job_type="video_export").count() == before_jobs
    assert RenderFollowUpIntent.objects.filter(project=project).count() == before_intents


@pytest.mark.django_db
def test_partial_render_preview_returns_prediction_only_without_mutation(tmp_path):
    owner = _make_user("partial_preview_owner")
    project = _make_project(owner, tmp_path)
    page = _make_page(project)
    _write_preview_sidecar(tmp_path, project, _preview_sidecar(project, [_preview_slide(page)]))
    before_jobs = Job.objects.filter(project=project, job_type="video_export").count()
    before_intents = RenderFollowUpIntent.objects.filter(project=project).count()
    before_project = {
        "status": project.status,
        "is_published": project.is_published,
        "moderation_status": project.moderation_status,
        "moderation_summary": project.moderation_summary,
        "draft_data": project.draft_data,
    }
    before_page = {
        "original_text": page.original_text,
        "narration_text": page.narration_text,
        "editor_document": page.editor_document,
        "subtitle_chunks": page.subtitle_chunks,
    }

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _client(owner).post(
            f"/api/v1/projects/{project.id}/partial-render-preview/",
            {
                "pages": [
                    _preview_payload_page(
                        page,
                        original_text="Visible text changed",
                        display_text="Visible text changed",
                    )
                ]
            },
            format="json",
        )

    assert response.status_code == 200
    assert response.data["mode"] == "prediction_only"
    assert response.data["source"] == "request_payload"
    assert response.data["available"] is True
    assert response.data["summary"]["recompose_visual_only_future"] == 1
    assert response.data["pages"][0]["recommended_action"] == "recompose_visual_only_future"
    assert response.data["pages"][0]["future_only"] is True
    assert response.data["pages"][0]["actual_behavior_changed"] is False
    assert Job.objects.filter(project=project, job_type="video_export").count() == before_jobs
    assert RenderFollowUpIntent.objects.filter(project=project).count() == before_intents

    project.refresh_from_db()
    page.refresh_from_db()
    assert {
        "status": project.status,
        "is_published": project.is_published,
        "moderation_status": project.moderation_status,
        "moderation_summary": project.moderation_summary,
        "draft_data": project.draft_data,
    } == before_project
    assert {
        "original_text": page.original_text,
        "narration_text": page.narration_text,
        "editor_document": page.editor_document,
        "subtitle_chunks": page.subtitle_chunks,
    } == before_page


@pytest.mark.django_db
def test_partial_render_preview_maps_narration_and_structural_changes(tmp_path):
    owner = _make_user("partial_preview_actions")
    project = _make_project(owner, tmp_path)
    first = _make_page(project)
    second = TranscriptPage.objects.create(
        project=project,
        order=1,
        source_slide_index=1,
        split_index=0,
        page_key="slide-2",
        original_text="Second text",
        narration_text="Second text",
        rich_text_html="Second text",
        subtitle_chunks=["Second text"],
        editor_document={"version": 1, "paragraphs": [{"text": "Second text"}]},
    )
    _write_preview_sidecar(
        tmp_path,
        project,
        _preview_sidecar(project, [_preview_slide(first), _preview_slide(second)]),
    )

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        narration_response = _client(owner).post(
            f"/api/v1/projects/{project.id}/partial-render-preview/",
            {
                "pages": [
                    _preview_payload_page(first, narration_text="Narration changed", subtitle_chunks=["Narration changed"]),
                    _preview_payload_page(second),
                ]
            },
            format="json",
        )
        structural_response = _client(owner).post(
            f"/api/v1/projects/{project.id}/partial-render-preview/",
            {
                "pages": [
                    _preview_payload_page(second, order=0, source_slide_index=1),
                    _preview_payload_page(first, order=1, source_slide_index=0),
                ]
            },
            format="json",
        )

    assert narration_response.status_code == 200
    narration_page = next(item for item in narration_response.data["pages"] if item["page_key"] == first.page_key)
    assert narration_page["recommended_action"] == "rerun_tts_avatar_future"
    assert narration_page["actual_behavior_changed"] is False
    assert structural_response.status_code == 200
    assert structural_response.data["summary"]["full_rerender_required_future"] >= 1
    assert any(
        page["recommended_action"] == "full_rerender_required_future"
        for page in structural_response.data["pages"]
    )


@pytest.mark.django_db
def test_partial_render_preview_missing_or_invalid_sidecar_returns_safe_fallback(tmp_path):
    owner = _make_user("partial_preview_missing")
    missing_project = _make_project(owner, tmp_path)
    missing_page = _make_page(missing_project)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        missing_response = _client(owner).post(
            f"/api/v1/projects/{missing_project.id}/partial-render-preview/",
            {"pages": [_preview_payload_page(missing_page)]},
            format="json",
        )

    assert missing_response.status_code == 200
    assert missing_response.data["mode"] == "prediction_only"
    assert missing_response.data["available"] is False
    assert missing_response.data["summary"]["full_rerender_required_future"] >= 1
    assert "old_manifest_missing_or_invalid" in missing_response.data["notes"]

    invalid_project = _make_project(owner, tmp_path)
    invalid_page = _make_page(invalid_project)
    sidecar_dir = tmp_path / str(invalid_project.id)
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    (sidecar_dir / "playback_assets.json").write_text("{", encoding="utf-8")

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        invalid_response = _client(owner).post(
            f"/api/v1/projects/{invalid_project.id}/partial-render-preview/",
            {"pages": [_preview_payload_page(invalid_page)]},
            format="json",
        )

    assert invalid_response.status_code == 200
    assert invalid_response.data["available"] is False
    assert invalid_response.data["summary"]["unknown_requires_full"] >= 1


@pytest.mark.django_db
def test_partial_render_preview_uses_dirty_draft_and_sanitizes_paths(tmp_path):
    owner = _make_user("partial_preview_draft")
    project = _make_project(owner, tmp_path)
    page = _make_page(project)
    sidecar = _preview_sidecar(project, [_preview_slide(page)])
    sidecar["final_segments"][0]["tts_audio"] = f"{project.id}/audio/private.mp3"
    sidecar["final_segments"][0]["part_rel_path"] = f"{project.id}/parts/private.mp4"
    sidecar["partial_render_manifest"] = build_expected_partial_render_manifest(
        project_id=project.id,
        slides=[_preview_slide(page)],
        previous_playback_assets=sidecar,
        tts_settings=core_views._project_tts_settings(project),
        avatar_options={
            "requested": False,
            "enabled": False,
            "teacher_id": project.user_id,
            "avatar_visible": True,
            "avatar_runtime_settings": project_avatar_runtime_settings(project),
        },
    )
    _write_preview_sidecar(tmp_path, project, sidecar)

    project.draft_data = {
        "metadata": {"dirty": True, "transcript_dirty": True, "render_required": True},
        "project": {"tts_settings": core_views._project_tts_settings(project)},
        "transcript_pages": [
            {
                "id": page.id,
                "order": page.order,
                "source_slide_index": page.source_slide_index,
                "split_index": page.split_index,
                "page_key": page.page_key,
                "original_text": "Draft visible text",
                "narration_text": page.narration_text,
                "rich_text_html": "Draft visible text",
                "editor_document": page.editor_document,
                "subtitle_chunks": page.subtitle_chunks,
                "whiteboard_mode": page.whiteboard_mode,
            }
        ],
    }
    project.save(update_fields=["draft_data", "updated_at"])
    before_jobs = Job.objects.filter(project=project, job_type="video_export").count()
    before_intents = RenderFollowUpIntent.objects.filter(project=project).count()

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _client(owner).post(
            f"/api/v1/projects/{project.id}/partial-render-preview/",
            {},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["source"] == "dirty_draft"
    assert response.data["summary"]["recompose_visual_only_future"] == 1
    serialized = json.dumps(response.data)
    assert "audio/private.mp3" not in serialized
    assert "parts/private.mp4" not in serialized
    assert f"{project.id}/audio" not in serialized
    assert Job.objects.filter(project=project, job_type="video_export").count() == before_jobs
    assert RenderFollowUpIntent.objects.filter(project=project).count() == before_intents


@pytest.mark.django_db
def test_project_detail_latest_render_analysis_missing_or_invalid_sidecar_returns_null(tmp_path):
    owner = _make_user("partial_analysis_missing_owner")
    missing_project = _make_project(owner, tmp_path)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        missing_response = _client(owner).get(f"/api/v1/projects/{missing_project.id}/")

    assert missing_response.status_code == 200
    assert missing_response.data["latest_render_analysis"] is None

    invalid_project = _make_project(owner, tmp_path)
    sidecar_dir = tmp_path / str(invalid_project.id)
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    (sidecar_dir / "playback_assets.json").write_text("{", encoding="utf-8")

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        invalid_response = _client(owner).get(f"/api/v1/projects/{invalid_project.id}/")

    assert invalid_response.status_code == 200
    assert invalid_response.data["latest_render_analysis"] is None


@pytest.mark.django_db
def test_project_list_does_not_expose_or_read_latest_render_analysis(monkeypatch, tmp_path):
    owner = _make_user("partial_analysis_list_owner")
    project = _make_project(owner, tmp_path)

    def fail_sidecar_read(*_args, **_kwargs):
        raise AssertionError("project list must not read playback_assets.json")

    monkeypatch.setattr(core_views, "_playback_sidecar_for_job", fail_sidecar_read)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _client(owner).get("/api/v1/projects/")

    assert response.status_code == 200
    row = next(item for item in response.data if item["id"] == project.id)
    assert "latest_render_analysis" not in row
