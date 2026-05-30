# pyright: reportMissingImports=false

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
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core import views  # noqa: E402
from core.drafts import promote_project_draft  # noqa: E402
from core.models import Job, Project, TranscriptPage, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402


def _make_user(username: str, *, role: str = "publisher") -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_project(owner: User, title: str = "Draft render lesson") -> Project:
    project = Project.objects.create(
        title=title,
        user=owner,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}/old.mp4")
    return project


def _make_page(project: Project, *, order: int, text: str, page_key: str | None = None) -> TranscriptPage:
    return TranscriptPage.objects.create(
        project=project,
        order=order,
        source_slide_index=order,
        split_index=0,
        page_key=page_key or f"s{order + 1}-p1",
        original_text=text,
        narration_text=text,
        rich_text_html=text,
        subtitle_chunks=[text],
        editor_document={
            "version": 1,
            "html": text,
            "paragraphs": [{"index": 0, "text": text}],
            "text": {"narration_customized": False, "display_text_customized": False},
        },
    )


def _save_draft_text(project: Project, page: TranscriptPage, text: str):
    return _client(project.user).patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {
            "draft_only": True,
            "pages": [{"id": page.id, "original_text": text, "narration_text": text}],
        },
        format="json",
    )


@pytest.mark.django_db
def test_save_only_draft_exists_and_active_page_is_unchanged():
    owner = _make_user("draft_rerender_save_owner")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="Active text")

    response = _save_draft_text(project, page, "Safe draft text")

    assert response.status_code == 200
    project.refresh_from_db()
    page.refresh_from_db()
    assert project.draft_data["metadata"]["dirty"] is True
    assert page.narration_text == "Active text"


@pytest.mark.django_db
def test_save_and_rerender_queues_draft_mode_without_mutating_active_page(tmp_path, monkeypatch):
    owner = _make_user("draft_rerender_queue_owner")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="Active queue text")
    upload_dir = tmp_path / "uploads" / str(project.id)
    upload_dir.mkdir(parents=True)
    (upload_dir / "lesson.txt").write_text("lesson", encoding="utf-8")

    sent_tasks = []
    monkeypatch.setattr(views, "_get_voice_id", lambda *_args, **_kwargs: "voice_test")
    monkeypatch.setattr(
        views,
        "_celery_app",
        SimpleNamespace(
            send_task=lambda *args, **kwargs: (
                sent_tasks.append((args, kwargs)) or SimpleNamespace(id="draft-rerender-task")
            )
        ),
    )

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _client(owner).patch(
            f"/api/v1/projects/{project.id}/transcript/",
            {
                "draft_only": True,
                "trigger_rerender": True,
                "pages": [{"id": page.id, "narration_text": "Queued draft text"}],
            },
            format="json",
        )

    assert response.status_code == 200
    assert response.data["rerender_strategy"] == "draft_full"
    assert sent_tasks
    assert sent_tasks[0][1]["kwargs"] == {"use_draft": True, "job_id": response.data["rerender_job"]["id"]}
    page.refresh_from_db()
    assert page.narration_text == "Active queue text"


@pytest.mark.django_db
def test_safe_draft_promotion_updates_active_rows_and_clears_draft():
    owner = _make_user("draft_promote_owner")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="Original public text")
    _save_draft_text(project, page, "Promoted safe text")

    catalog_before = _client().get(f"/api/v1/catalog/{project.id}/")
    project.refresh_from_db()
    result = promote_project_draft(
        project,
        render_outputs={
            "page_timeline": [
                {"page_key": page.page_key, "start": 0, "end": 2, "duration": 2, "chunk_timeline": []}
            ]
        },
    )

    page.refresh_from_db()
    project.refresh_from_db()
    catalog_after = _client().get(f"/api/v1/catalog/{project.id}/")
    assert result["status"] == "promoted"
    assert project.draft_data == {}
    assert page.narration_text == "Promoted safe text"
    assert catalog_before.data["transcript_pages"][0]["narration_text"] == "Original public text"
    assert catalog_after.data["transcript_pages"][0]["narration_text"] == "Promoted safe text"


@pytest.mark.django_db
def test_unsafe_draft_moderation_does_not_promote_or_hide_public_version(monkeypatch):
    monkeypatch.setattr("django.conf.settings.SOURCE_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr("django.conf.settings.SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION", True, raising=False)
    owner = _make_user("draft_block_owner")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="Safe active public text")
    old_job = project.jobs.filter(status="done").first()
    _save_draft_text(project, page, "I will kill you tomorrow.")
    Job.objects.create(project=project, job_type="video_export", status="running", progress=10)

    result = worker_tasks._run_auto_source_moderation_for_draft(project.id)
    worker_tasks._mark_draft_render_blocked(project.id, result)

    project.refresh_from_db()
    page.refresh_from_db()
    old_job.refresh_from_db()
    failed_job = project.jobs.order_by("-created_at", "-id").first()
    studio_response = _client(owner).get(f"/api/v1/projects/{project.id}/transcript/")
    public_response = _client().get(f"/api/v1/catalog/{project.id}/")

    assert result["block_render"] is True
    assert project.status == "ready"
    assert project.is_published is True
    assert project.moderation_status == "approved"
    assert page.narration_text == "Safe active public text"
    assert old_job.result_url.endswith("/old.mp4")
    assert failed_job.status == "failed"
    assert project.draft_data["metadata"]["moderation_status"] == "revision_required"
    assert studio_response.data["pages"][0]["narration_text"] == "I will kill you tomorrow."
    assert public_response.data["transcript_pages"][0]["narration_text"] == "Safe active public text"


@pytest.mark.django_db
def test_split_draft_pages_promote_as_active_pages():
    owner = _make_user("draft_split_promote_owner")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="First")

    response = _client(owner).post(
        f"/api/v1/projects/{project.id}/transcript/actions/",
        {
            "draft_only": True,
            "action": "split_page",
            "page_id": page.id,
            "parts": [{"narration_text": "First A"}, {"narration_text": "First B"}],
        },
        format="json",
    )
    assert response.status_code == 200

    project.refresh_from_db()
    promote_project_draft(project)
    active_pages = list(project.transcript_pages.filter(is_active=True).order_by("order", "id"))
    assert [active.narration_text for active in active_pages] == ["First A", "First B"]
    assert [active.original_text for active in active_pages] == ["First A", "First B"]
    assert [active.page_key for active in active_pages] == ["s1-p1", "s1-p2"]
    assert [active.subtitle_chunks for active in active_pages] == [["First A"], ["First B"]]
    assert active_pages[0].editor_document["paragraphs"][0]["text"] == "First A"
    assert active_pages[1].editor_document["paragraphs"][0]["text"] == "First B"


@pytest.mark.django_db
def test_split_draft_render_descriptors_use_split_text_fields():
    owner = _make_user("draft_split_descriptor_owner")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="First A\n\nFirst B")

    response = _client(owner).post(
        f"/api/v1/projects/{project.id}/transcript/actions/",
        {
            "draft_only": True,
            "action": "split_page",
            "page_id": page.id,
            "parts": [{"narration_text": "First A"}, {"narration_text": "First B"}],
        },
        format="json",
    )
    assert response.status_code == 200

    slides = worker_tasks._build_render_slides_from_draft(
        project.id,
        [
            {
                "index": 0,
                "source_slide_index": 0,
                "image_path": "",
                "notes_text": "First A\n\nFirst B",
                "original_text": "First A\n\nFirst B",
                "display_text": "First A\n\nFirst B",
            }
        ],
    )

    assert [slide["page_key"] for slide in slides] == ["s1-p1", "s1-p2"]
    assert [slide["split_index"] for slide in slides] == [0, 1]
    assert [slide["narration_text"] for slide in slides] == ["First A", "First B"]
    assert [slide["original_text"] for slide in slides] == ["First A", "First B"]
    assert [slide["display_text"] for slide in slides] == ["First A", "First B"]
    assert [slide["notes_text"] for slide in slides] == ["First A", "First B"]
    assert [slide["subtitle_chunks"] for slide in slides] == [["First A"], ["First B"]]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("mode", "path_key", "expected_whiteboard"),
    [
        ("whiteboard", "", True),
        ("original", "original_background_path", False),
        ("custom", "custom_background_path", False),
        ("source_background", "source_background_path", False),
    ],
)
def test_split_draft_render_descriptors_inherit_visual_scene_settings(
    tmp_path,
    monkeypatch,
    mode,
    path_key,
    expected_whiteboard,
):
    owner = _make_user(f"draft_split_visual_descriptor_{mode}")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="First A\n\nFirst B")
    original_rel = f"{project.id}/images/slide-1.png"
    custom_rel = f"uploads/{project.id}/backgrounds/custom.png"
    source_rel = f"{project.id}/source_backgrounds/slide-1.png"
    for rel_path in (original_rel, custom_rel, source_rel):
        absolute = tmp_path / rel_path
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_bytes(b"image")

    scene = {
        "background_mode": mode,
        "background_fit": "cover",
        "text_scale": 1.45,
        "source_type": "pptx",
        "original_background_path": original_rel,
        "custom_background_path": custom_rel,
        "source_background_path": source_rel,
        "overlay_layout": {"padding": 52, "safe_area": {"left": 20, "right": 20}},
        "font": {"size": 36, "align": "center"},
    }
    page.editor_document = {
        "version": 1,
        "html": "First A<br><br>First B",
        "paragraphs": [{"index": 0, "text": "First A\n\nFirst B"}],
        "scene": scene,
    }
    page.whiteboard_mode = mode == "whiteboard"
    page.save(update_fields=["editor_document", "whiteboard_mode", "updated_at"])

    split_response = _client(owner).post(
        f"/api/v1/projects/{project.id}/transcript/actions/",
        {
            "draft_only": True,
            "action": "split_page",
            "page_id": page.id,
            "parts": [{"narration_text": "First A"}, {"narration_text": "First B"}],
        },
        format="json",
    )
    assert split_response.status_code == 200
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    slides = worker_tasks._build_render_slides_from_draft(
        project.id,
        [
            {
                "index": 0,
                "source_slide_index": 0,
                "source_type": "pptx",
                "image_path": str(tmp_path / original_rel),
                "notes_text": "First A\n\nFirst B",
                "original_text": "First A\n\nFirst B",
                "display_text": "First A\n\nFirst B",
            }
        ],
    )

    expected_mode = "whiteboard" if mode == "whiteboard" else mode
    if path_key:
        expected_image_path = str(tmp_path / scene[path_key])
    else:
        expected_image_path = ""
    assert [slide["page_key"] for slide in slides] == ["s1-p1", "s1-p2"]
    assert [slide["display_text"] for slide in slides] == ["First A", "First B"]
    assert [slide["scene_background_mode"] for slide in slides] == [expected_mode, expected_mode]
    assert [slide["whiteboard_mode"] for slide in slides] == [expected_whiteboard, expected_whiteboard]
    assert [slide["image_path"] for slide in slides] == [expected_image_path, expected_image_path]
    assert [slide["scene_background_fit"] for slide in slides] == ["cover", "cover"]
    assert [slide["scene_text_scale"] for slide in slides] == [1.45, 1.45]
    assert slides[1]["editor_document"]["scene"]["overlay_layout"] == scene["overlay_layout"]
    assert slides[1]["editor_document"]["scene"]["font"] == scene["font"]


@pytest.mark.django_db
def test_deleted_draft_pages_are_deactivated_on_promotion():
    owner = _make_user("draft_delete_promote_owner")
    project = _make_project(owner)
    first = _make_page(project, order=0, text="Keep")
    second = _make_page(project, order=1, text="Delete")

    response = _client(owner).post(
        f"/api/v1/projects/{project.id}/transcript/actions/",
        {"draft_only": True, "action": "delete_page", "page_id": second.id},
        format="json",
    )
    assert response.status_code == 200

    project.refresh_from_db()
    promote_project_draft(project)
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_active is True
    assert second.is_active is False
    assert second.deleted_at is not None


@pytest.mark.django_db
def test_draft_render_descriptor_uses_draft_text_and_tts_settings():
    owner = _make_user("draft_descriptor_owner")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="Active descriptor text")
    _client(owner).patch(
        f"/api/v1/projects/{project.id}/",
        {"draft_only": True, "tts_settings": {"provider_preference": "gtts", "speech_speed": 1.15}},
        format="json",
    )
    _save_draft_text(project, page, "Draft descriptor narration")

    slides = worker_tasks._build_render_slides_from_draft(
        project.id,
        [{"index": 0, "source_slide_index": 0, "image_path": "", "notes_text": "Exported text"}],
    )
    tts_settings = worker_tasks._draft_render_tts_settings(project.id, {})

    assert slides[0]["narration_text"] == "Draft descriptor narration"
    assert slides[0]["notes_text"] == "Draft descriptor narration"
    assert tts_settings["provider_preference"] == "gtts"
    assert tts_settings["speech_speed"] == 1.15


@pytest.mark.django_db
def test_draft_finalize_uses_unique_artifact_path_and_preserves_old_public_file(tmp_path, monkeypatch):
    from scripts import ffmpeg_helpers

    owner = _make_user("draft_finalize_owner")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="Old public text")
    old_job = project.jobs.filter(status="done").first()
    old_path = tmp_path / old_job.result_url
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(b"old-public-video")
    _save_draft_text(project, page, "New safe draft text")
    new_job = Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    part_path = tmp_path / "parts" / "part_001.mp4"
    part_path.parent.mkdir(parents=True)
    part_path.write_bytes(b"part")

    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_tasks, "DRM_STREAMING_ENABLED", False)
    monkeypatch.setattr(worker_tasks.time, "time_ns", lambda: 123456789)
    monkeypatch.setattr(worker_tasks, "_sync_lesson_segments", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker_tasks,
        "_run_auto_video_frame_audit_after_render",
        lambda *_args, **_kwargs: {"enabled": False, "status": "skipped"},
    )

    def fake_concat_videos(_part_paths, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"new-draft-video")

    def fake_generate_srt_from_cues(_cues, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("1\n00:00:00,000 --> 00:00:01,000\nNew safe draft text\n", encoding="utf-8")

    def fake_generate_vtt_from_cues(_cues, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nNew safe draft text\n", encoding="utf-8")

    monkeypatch.setattr(ffmpeg_helpers, "concat_videos", fake_concat_videos)
    monkeypatch.setattr(ffmpeg_helpers, "generate_srt_from_cues", fake_generate_srt_from_cues)
    monkeypatch.setattr(ffmpeg_helpers, "generate_vtt_from_cues", fake_generate_vtt_from_cues)

    render_result = {
        "index": 0,
        "slide_num": 1,
        "page_key": page.page_key,
        "source_slide_index": 0,
        "split_index": 0,
        "part_path": str(part_path),
        "duration": 1.0,
        "pause_seconds": 0.0,
        "text": "New safe draft text",
        "original_text": "New safe draft text",
        "display_text": "New safe draft text",
        "spoken_text": "New safe draft text",
        "subtitle_chunks": ["New safe draft text"],
        "slide_path": "",
        "tts_audio_path": "",
        "avatar_applied": False,
        "avatar_attempted": False,
        "avatar_skipped": False,
        "avatar_failed": False,
        "avatar_status": "none",
        "avatar_error": "",
        "avatar_failure_reason": "",
        "avatar_segment_rel_path": "",
        "avatar_engine_used": "none",
        "avatar_fallback_chain": [],
        "avatar_motion_validation": {},
    }

    result = worker_tasks.concat_and_finalize.run([render_result], str(project.id), True)

    old_job.refresh_from_db()
    new_job.refresh_from_db()
    project.refresh_from_db()
    page.refresh_from_db()
    expected_rel = f"{project.id}/draft_renders/draft-123456789/{project.id}.mp4"
    assert old_path.read_bytes() == b"old-public-video"
    assert old_job.result_url.endswith("/old.mp4")
    assert new_job.status == "done"
    assert new_job.result_url == expected_rel
    assert result["result_url"] == expected_rel
    assert (tmp_path / expected_rel).read_bytes() == b"new-draft-video"
    assert page.narration_text == "New safe draft text"
    assert project.draft_data == {}


@pytest.mark.django_db
def test_non_draft_trigger_rerender_path_still_updates_active_page(tmp_path, monkeypatch):
    owner = _make_user("active_rerender_owner")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="Active before direct rerender")
    upload_dir = tmp_path / "uploads" / str(project.id)
    upload_dir.mkdir(parents=True)
    (upload_dir / "lesson.txt").write_text("lesson", encoding="utf-8")
    sent_tasks = []
    monkeypatch.setattr(views, "_get_voice_id", lambda *_args, **_kwargs: "voice_test")
    monkeypatch.setattr(
        views,
        "_celery_app",
        SimpleNamespace(
            send_task=lambda *args, **kwargs: (
                sent_tasks.append((args, kwargs)) or SimpleNamespace(id="active-rerender-task")
            )
        ),
    )

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _client(owner).patch(
            f"/api/v1/projects/{project.id}/transcript/",
            {
                "trigger_rerender": True,
                "draft_only": False,
                "pages": [{"id": page.id, "narration_text": "Active direct rerender"}],
            },
            format="json",
        )

    page.refresh_from_db()
    assert response.status_code == 200
    assert page.narration_text == "Active direct rerender"
    assert sent_tasks[0][1].get("kwargs") == {"job_id": response.data["rerender_job"]["id"]}
