# pyright: reportMissingImports=false

import importlib
import os
import sys
from pathlib import Path

import django
import pytest
from django.db import connection
from django.utils import timezone

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
TTS_ROOT = REPO_ROOT / "services" / "tts_service"
for path in [API_ROOT, SERVICES_ROOT, TTS_ROOT]:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402

from core.models import Job, Project, TranscriptPage, UserProfile  # noqa: E402
from core.serializers import canonical_project_tts_settings  # noqa: E402


def _ensure_transcript_table() -> None:
    table_name = TranscriptPage._meta.db_table
    if table_name in connection.introspection.table_names():
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(cursor, table_name)
            }
        required_fields = [
            "rich_text_html",
            "editor_document",
            "subtitle_chunks",
            "chunk_timeline",
            "whiteboard_mode",
            "is_active",
            "deleted_at",
            "start_seconds",
            "end_seconds",
            "duration_seconds",
        ]
        missing_fields = [
            field_name
            for field_name in required_fields
            if TranscriptPage._meta.get_field(field_name).column not in columns
        ]
        if missing_fields:
            with connection.schema_editor() as schema_editor:
                for field_name in missing_fields:
                    schema_editor.add_field(TranscriptPage, TranscriptPage._meta.get_field(field_name))
        return
    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(TranscriptPage)


def _make_teacher(username: str):
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str, *, tts_settings=None) -> Project:
    teacher = _make_teacher(username)
    kwargs = {"title": username, "user": teacher}
    if tts_settings is not None:
        kwargs["tts_settings"] = tts_settings
    return Project.objects.create(**kwargs)


def _make_page(
    project: Project,
    *,
    order: int,
    page_key: str,
    text: str,
    subtitle_chunks=None,
    chunk_timeline=None,
    start=None,
    end=None,
    is_active=True,
    deleted_at=None,
    original_text=None,
    split_index=0,
    source_slide_index=None,
) -> TranscriptPage:
    duration = None
    if start is not None and end is not None:
        duration = float(end) - float(start)
    return TranscriptPage.objects.create(
        project=project,
        order=order,
        source_slide_index=order if source_slide_index is None else source_slide_index,
        split_index=split_index,
        page_key=page_key,
        original_text=original_text if original_text is not None else text,
        narration_text=text,
        editor_document={
            "version": 1,
            "paragraphs": [{"index": 0, "text": text}],
        },
        subtitle_chunks=subtitle_chunks or [],
        chunk_timeline=chunk_timeline or [],
        start_seconds=start,
        end_seconds=end,
        duration_seconds=duration,
        is_active=is_active,
        deleted_at=deleted_at,
    )


def _worker_tasks():
    from worker import tasks as worker_tasks

    return worker_tasks


def _assert_valid_monotonic(cues):
    previous_end = 0.0
    for cue in cues:
        assert cue["start"] >= 0
        assert cue["end"] > cue["start"]
        assert cue["start"] + 0.001 >= previous_end
        previous_end = cue["end"]


@pytest.mark.django_db
def test_cue_builder_uses_valid_explicit_chunk_timeline():
    _ensure_transcript_table()
    worker_tasks = _worker_tasks()
    project = _make_project("subtitle_valid_timeline")
    _make_page(
        project,
        order=0,
        page_key="s1-p1",
        text="Alpha Beta",
        subtitle_chunks=["Alpha", "Beta"],
        start=0.0,
        end=4.0,
        chunk_timeline=[
            {"start": 0.0, "end": 1.5, "text": "Alpha", "chunk_index": 0},
            {"start": 1.5, "end": 4.0, "text": "Beta", "chunk_index": 1},
        ],
    )

    cues = worker_tasks.build_subtitle_cues_from_transcript_pages(
        project.id,
        [{"page_key": "s1-p1", "duration": 4.0}],
        [4.0],
    )

    assert [cue["source"] for cue in cues] == ["chunk_timeline", "chunk_timeline"]
    assert [cue["text"] for cue in cues] == ["Alpha", "Beta"]
    assert [(cue["start"], cue["end"]) for cue in cues] == [(0.0, 1.5), (1.5, 4.0)]
    _assert_valid_monotonic(cues)


@pytest.mark.django_db
def test_invalid_chunk_timeline_falls_back_to_distributed_chunks():
    _ensure_transcript_table()
    worker_tasks = _worker_tasks()
    project = _make_project("subtitle_invalid_timeline")
    _make_page(
        project,
        order=0,
        page_key="s1-p1",
        text="Alpha Beta",
        subtitle_chunks=["Alpha", "Beta"],
        start=0.0,
        end=4.0,
        chunk_timeline=[
            {"start": 0.0, "end": 5.0, "text": "Alpha", "chunk_index": 0},
        ],
    )

    cues = worker_tasks.build_subtitle_cues_from_transcript_pages(
        project.id,
        [{"page_key": "s1-p1", "duration": 4.0}],
        [4.0],
    )

    assert [cue["source"] for cue in cues] == ["distributed_chunks", "distributed_chunks"]
    assert [cue["text"] for cue in cues] == ["Alpha", "Beta"]
    _assert_valid_monotonic(cues)


@pytest.mark.django_db
def test_cue_builder_distributes_subtitle_chunks_when_timeline_missing():
    _ensure_transcript_table()
    worker_tasks = _worker_tasks()
    project = _make_project("subtitle_distributed_chunks")
    _make_page(
        project,
        order=0,
        page_key="s1-p1",
        text="First chunk Second chunk",
        subtitle_chunks=["First chunk", "Second chunk"],
        start=0.0,
        end=6.0,
    )

    cues = worker_tasks.build_subtitle_cues_from_transcript_pages(
        project.id,
        [{"page_key": "s1-p1", "duration": 6.0}],
        [6.0],
    )

    assert [cue["source"] for cue in cues] == ["distributed_chunks", "distributed_chunks"]
    assert [cue["chunk_index"] for cue in cues] == [0, 1]
    assert cues[-1]["end"] == 6.0
    _assert_valid_monotonic(cues)


@pytest.mark.django_db
def test_cue_builder_falls_back_to_narration_text_when_chunks_missing():
    _ensure_transcript_table()
    worker_tasks = _worker_tasks()
    project = _make_project("subtitle_page_fallback")
    _make_page(
        project,
        order=0,
        page_key="s1-p1",
        text="Fallback display transcript",
        subtitle_chunks=[],
        start=0.0,
        end=3.0,
    )

    cues = worker_tasks.build_subtitle_cues_from_transcript_pages(
        project.id,
        [{"page_key": "s1-p1", "duration": 3.0}],
        [3.0],
    )

    assert len(cues) == 1
    assert cues[0]["source"] == "page_fallback"
    assert cues[0]["text"] == "Fallback display transcript"
    _assert_valid_monotonic(cues)


def test_generate_vtt_from_cues_writes_valid_webvtt(tmp_path):
    ffmpeg_helpers = importlib.import_module("scripts.ffmpeg_helpers")
    vtt_path = tmp_path / "lesson.vtt"
    ffmpeg_helpers.generate_vtt_from_cues(
        [
            {"start": 0.0, "end": 1.25, "text": "First caption"},
            {"start": 1.25, "end": 1.25, "text": "Invalid timing"},
            {"start": 2.0, "end": 3.0, "text": "  "},
            {"start": 3.0, "end": 4.0, "text": "Second\ncaption"},
        ],
        str(vtt_path),
    )

    content = vtt_path.read_text(encoding="utf-8")
    assert content.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:01.250" in content
    assert "First caption" in content
    assert "Second\ncaption" in content
    assert "Invalid timing" not in content
    assert content.count(" --> ") == 2


def test_page_timeline_excludes_pause_from_caption_chunk_distribution():
    worker_tasks = _worker_tasks()

    timeline = worker_tasks._build_page_timeline_from_render_results(
        [
            {
                "index": 0,
                "page_key": "s1-p1",
                "duration": 4.0,
                "pause_seconds": 1.0,
                "text": "First chunk second chunk",
                "subtitle_chunks": ["First chunk", "second chunk"],
            }
        ]
    )

    assert len(timeline) == 1
    assert timeline[0]["start"] == 0.0
    assert timeline[0]["end"] == 4.0
    assert timeline[0]["duration"] == 4.0
    assert timeline[0]["chunk_timeline"][-1]["end"] == 3.0


@pytest.mark.django_db
def test_inactive_and_deleted_pages_are_skipped():
    _ensure_transcript_table()
    worker_tasks = _worker_tasks()
    project = _make_project("subtitle_skip_deleted")
    _make_page(project, order=0, page_key="active", text="Active", subtitle_chunks=["Active"], start=0.0, end=1.0)
    _make_page(
        project,
        order=1,
        page_key="inactive",
        text="Inactive",
        subtitle_chunks=["Inactive"],
        start=1.0,
        end=2.0,
        is_active=False,
    )
    _make_page(
        project,
        order=2,
        page_key="deleted",
        text="Deleted",
        subtitle_chunks=["Deleted"],
        start=2.0,
        end=3.0,
        is_active=False,
        deleted_at=timezone.now(),
    )

    cues = worker_tasks.build_subtitle_cues_from_transcript_pages(
        project.id,
        [{"page_key": "active", "duration": 1.0}],
        [1.0],
    )

    assert [cue["page_key"] for cue in cues] == ["active"]
    assert [cue["text"] for cue in cues] == ["Active"]


@pytest.mark.django_db
def test_concat_finalize_writes_chunk_srt_and_updates_transcript_timing(tmp_path, monkeypatch):
    _ensure_transcript_table()
    worker_tasks = _worker_tasks()
    ffmpeg_helpers = importlib.import_module("scripts.ffmpeg_helpers")
    project = _make_project("subtitle_concat_final")
    job = Job.objects.create(project=project, job_type="video_export", status="running")
    page = _make_page(
        project,
        order=0,
        page_key="s1-p1",
        text="Edited ChatGPT display text",
        original_text="Original unedited text",
        subtitle_chunks=["Edited ChatGPT", "display text"],
    )

    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_tasks, "DRM_STREAMING_ENABLED", False)
    monkeypatch.setattr(worker_tasks, "_sync_lesson_segments", lambda *_args, **_kwargs: None)

    def fake_concat_videos(_part_paths, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"mp4")

    monkeypatch.setattr(ffmpeg_helpers, "concat_videos", fake_concat_videos)

    part_path = tmp_path / str(project.id) / "parts" / "part_001.mp4"
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(b"part")

    result = worker_tasks.concat_and_finalize.run(
        [
            {
                "index": 0,
                "slide_num": 1,
                "page_key": "s1-p1",
                "source_slide_index": 0,
                "split_index": 0,
                "part_path": str(part_path),
                "duration": 3.0,
                "text": "Edited ChatGPT display text",
                "original_text": "Original unedited text",
                "spoken_text": "edited chat g p t display text",
                "subtitle_chunks": ["Edited ChatGPT", "display text"],
                "slide_path": "",
                "tts_audio_path": "",
            }
        ],
        str(project.id),
    )

    content = Path(result["srt"]).read_text(encoding="utf-8")
    assert content.count(" --> ") == 2
    assert "Edited ChatGPT" in content
    assert "Original unedited text" not in content
    assert "chat g p t" not in content

    vtt_content = Path(result["vtt"]).read_text(encoding="utf-8")
    assert vtt_content.startswith("WEBVTT")
    assert vtt_content.count(" --> ") == 2
    assert "Edited ChatGPT" in vtt_content
    assert "Original unedited text" not in vtt_content
    assert "chat g p t" not in vtt_content

    page.refresh_from_db()
    job.refresh_from_db()
    assert page.original_text == "Original unedited text"
    assert page.start_seconds == 0.0
    assert page.end_seconds == 3.0
    assert page.duration_seconds == 3.0
    assert len(page.chunk_timeline) == 2
    assert job.srt_url == f"{project.id}/{project.id}.srt"
    assert result["vtt_url"] == f"{project.id}/{project.id}.vtt"
    assert result["playback_assets"]["vtt_rel_path"] == f"{project.id}/{project.id}.vtt"


@pytest.mark.django_db
def test_tts_override_isolation_keeps_caption_text_original(tmp_path):
    _ensure_transcript_table()
    worker_tasks = _worker_tasks()
    ffmpeg_helpers = importlib.import_module("scripts.ffmpeg_helpers")
    tts_client = importlib.import_module("tts_client")
    tts_settings = canonical_project_tts_settings(
        {"overrides": {"mixed_word": {"ChatGPT": "chat gpt"}}}
    )
    project = _make_project("subtitle_tts_override_isolation", tts_settings=tts_settings)
    _make_page(
        project,
        order=0,
        page_key="s1-p1",
        text="ChatGPT explains the pipeline",
        subtitle_chunks=["ChatGPT explains the pipeline"],
        start=0.0,
        end=2.0,
    )

    spoken = tts_client._prepare_text_with_settings(
        "ChatGPT explains the pipeline",
        "en",
        tts_settings,
    )["spoken_text"]
    assert "chat gpt" in spoken.lower()

    cues = worker_tasks.build_subtitle_cues_from_transcript_pages(
        project.id,
        [{"page_key": "s1-p1", "duration": 2.0}],
        [2.0],
    )
    srt_path = tmp_path / "lesson.srt"
    ffmpeg_helpers.generate_srt_from_cues(cues, str(srt_path))
    vtt_path = tmp_path / "lesson.vtt"
    ffmpeg_helpers.generate_vtt_from_cues(cues, str(vtt_path))
    content = srt_path.read_text(encoding="utf-8")
    vtt_content = vtt_path.read_text(encoding="utf-8")

    assert "ChatGPT" in content
    assert "chat gpt" not in content.lower()
    assert "ChatGPT" in vtt_content
    assert "chat gpt" not in vtt_content.lower()
    assert project.tts_settings["overrides"]["mixed_word"] == {"ChatGPT": "chat gpt"}


@pytest.mark.django_db
def test_multi_page_split_merge_reorder_ordering_and_srt(tmp_path):
    _ensure_transcript_table()
    worker_tasks = _worker_tasks()
    ffmpeg_helpers = importlib.import_module("scripts.ffmpeg_helpers")
    project = _make_project("subtitle_multi_page_order")
    _make_page(
        project,
        order=2,
        page_key="s1-p1",
        text="Third page",
        subtitle_chunks=["Third page"],
        split_index=0,
        source_slide_index=0,
    )
    _make_page(
        project,
        order=0,
        page_key="s1-p2",
        text="First A First B",
        subtitle_chunks=["First A", "First B"],
        split_index=1,
        source_slide_index=0,
    )
    _make_page(
        project,
        order=1,
        page_key="s2-p1",
        text="Second page",
        subtitle_chunks=["Second page"],
        split_index=0,
        source_slide_index=1,
    )
    _make_page(
        project,
        order=0,
        page_key="deleted",
        text="Deleted page",
        subtitle_chunks=["Deleted page"],
        is_active=False,
        deleted_at=timezone.now(),
    )

    ordered_slides = [
        {"page_key": "s1-p1", "duration": 3.0},
        {"page_key": "s2-p1", "duration": 2.0},
        {"page_key": "s1-p2", "duration": 4.0},
    ]
    cues = worker_tasks.build_subtitle_cues_from_transcript_pages(
        project.id,
        ordered_slides,
        [3.0, 2.0, 4.0],
    )

    assert [cue["text"] for cue in cues] == ["First A", "First B", "Second page", "Third page"]
    assert [cue["page_key"] for cue in cues] == ["s1-p2", "s1-p2", "s2-p1", "s1-p1"]
    assert [cue["chunk_index"] for cue in cues if cue["page_key"] == "s1-p2"] == [0, 1]
    _assert_valid_monotonic(cues)

    srt_path = tmp_path / "ordered.srt"
    ffmpeg_helpers.generate_srt_from_cues(cues, str(srt_path))
    content = srt_path.read_text(encoding="utf-8")
    assert content.find("First A") < content.find("First B") < content.find("Second page") < content.find("Third page")
    assert "Deleted page" not in content
