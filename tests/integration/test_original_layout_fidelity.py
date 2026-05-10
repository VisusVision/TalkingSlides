# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path

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

from scripts import ffmpeg_helpers, pptx_extract, tts_client  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _base_slide_meta(tmp_path: Path, *, mode: str = "original", whiteboard_mode: bool = False) -> dict:
    image_path = tmp_path / f"{mode}-source.png"
    image_path.write_bytes(PNG_1X1)
    return {
        "index": 0,
        "slide_num": 1,
        "page_key": "s1-p1",
        "source_slide_index": 0,
        "split_index": 0,
        "image_path": str(image_path),
        "notes_text": "Spoken narration",
        "narration_text": "Spoken narration",
        "original_text": "Edited display text",
        "display_text": "Edited display text",
        "rich_text_html": "<p>Edited display text</p>",
        "subtitle_chunks": ["Spoken narration"],
        "whiteboard_mode": whiteboard_mode,
        "scene_background_mode": mode,
        "scene_background_fit": "cover",
        "scene_text_scale": 1.0,
        "audio_out": str(tmp_path / "audio" / f"{mode}.mp3"),
        "part_out": str(tmp_path / "parts" / f"{mode}.mp4"),
    }


def _patch_render_dependencies(monkeypatch, *, overlay_result: str | None = None, whiteboard_result: str | None = None):
    calls = {"overlay": [], "whiteboard": [], "create": []}

    def fake_synthesize_text_with_metadata(_voice_id, text, out_path, **kwargs):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"audio")
        return {
            "spoken_text": text,
            "provider": "test",
            "tts_normalization_language": kwargs.get("lang") or "auto",
        }

    def fake_overlay(base_image_path, display_text, rich_text_html, output_path, **kwargs):
        calls["overlay"].append(
            {
                "base_image_path": base_image_path,
                "display_text": display_text,
                "rich_text_html": rich_text_html,
                "output_path": output_path,
                **kwargs,
            }
        )
        target = overlay_result or output_path
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"overlay")
        return target

    def fake_whiteboard(display_text, output_path, **kwargs):
        calls["whiteboard"].append({"display_text": display_text, "output_path": output_path, **kwargs})
        target = whiteboard_result or output_path
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"whiteboard")
        return target

    def fake_create_slide_video(image_path, audio_path, out_video_path, **kwargs):
        calls["create"].append(
            {
                "image_path": image_path,
                "audio_path": audio_path,
                "out_video_path": out_video_path,
                **kwargs,
            }
        )
        Path(out_video_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_video_path).write_bytes(b"video")
        return out_video_path

    monkeypatch.setattr(worker_tasks.synthesize_and_render_slide, "update_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(tts_client, "synthesize_text_with_metadata", fake_synthesize_text_with_metadata)
    monkeypatch.setattr(ffmpeg_helpers, "create_slide_video", fake_create_slide_video)
    monkeypatch.setattr(ffmpeg_helpers, "get_audio_duration", lambda _path: 1.0)
    monkeypatch.setattr(ffmpeg_helpers, "trim_trailing_silence", lambda _path: None)
    monkeypatch.setattr(worker_tasks, "_render_transcript_overlay_image", fake_overlay)
    monkeypatch.setattr(worker_tasks, "_make_whiteboard_image", fake_whiteboard)
    return calls


def test_original_mode_does_not_overlay_rich_or_edited_text(tmp_path, monkeypatch):
    calls = _patch_render_dependencies(monkeypatch)
    slide_meta = _base_slide_meta(tmp_path, mode="original", whiteboard_mode=False)
    original_image_path = slide_meta["image_path"]

    result = worker_tasks.synthesize_and_render_slide.run(
        slide_meta,
        project_id="1",
        voice_id="test-voice",
        pause_sec=0.2,
        lang_hint="auto",
    )

    assert calls["overlay"] == []
    assert calls["whiteboard"] == []
    assert calls["create"][0]["image_path"] == original_image_path
    assert result["slide_path"] == original_image_path


def test_original_scene_mode_wins_over_legacy_whiteboard_flag(tmp_path, monkeypatch):
    calls = _patch_render_dependencies(monkeypatch)
    slide_meta = _base_slide_meta(tmp_path, mode="original", whiteboard_mode=True)
    original_image_path = slide_meta["image_path"]

    result = worker_tasks.synthesize_and_render_slide.run(
        slide_meta,
        project_id="1",
        voice_id="test-voice",
        pause_sec=0.2,
        lang_hint="auto",
    )

    assert calls["overlay"] == []
    assert calls["whiteboard"] == []
    assert calls["create"][0]["image_path"] == original_image_path
    assert result["slide_path"] == original_image_path
    assert result["whiteboard_mode"] is False


def test_custom_mode_still_renders_transcript_overlay(tmp_path, monkeypatch):
    overlay_path = str(tmp_path / "custom-overlay.png")
    calls = _patch_render_dependencies(monkeypatch, overlay_result=overlay_path)
    slide_meta = _base_slide_meta(tmp_path, mode="custom", whiteboard_mode=False)

    result = worker_tasks.synthesize_and_render_slide.run(
        slide_meta,
        project_id="1",
        voice_id="test-voice",
        pause_sec=0.2,
        lang_hint="auto",
    )

    assert len(calls["overlay"]) == 1
    assert calls["overlay"][0]["base_image_path"] == slide_meta["image_path"]
    assert calls["overlay"][0]["display_text"] == "Edited display text"
    assert calls["create"][0]["image_path"] == overlay_path
    assert result["slide_path"] == overlay_path


def test_source_background_mode_renders_transcript_overlay(tmp_path, monkeypatch):
    overlay_path = str(tmp_path / "source-background-overlay.png")
    calls = _patch_render_dependencies(monkeypatch, overlay_result=overlay_path)
    slide_meta = _base_slide_meta(tmp_path, mode="source_background", whiteboard_mode=False)

    result = worker_tasks.synthesize_and_render_slide.run(
        slide_meta,
        project_id="1",
        voice_id="test-voice",
        pause_sec=0.2,
        lang_hint="auto",
    )

    assert len(calls["overlay"]) == 1
    assert calls["overlay"][0]["base_image_path"] == slide_meta["image_path"]
    assert calls["overlay"][0]["display_text"] == "Edited display text"
    assert calls["whiteboard"] == []
    assert calls["create"][0]["image_path"] == overlay_path
    assert result["slide_path"] == overlay_path
    assert result["scene_background_mode"] == "source_background"


def test_source_background_missing_falls_back_to_whiteboard_with_warning(tmp_path, monkeypatch):
    whiteboard_path = str(tmp_path / "source-background-fallback.png")
    calls = _patch_render_dependencies(monkeypatch, whiteboard_result=whiteboard_path)
    slide_meta = _base_slide_meta(tmp_path, mode="source_background", whiteboard_mode=False)
    slide_meta["image_path"] = ""

    result = worker_tasks.synthesize_and_render_slide.run(
        slide_meta,
        project_id="1",
        voice_id="test-voice",
        pause_sec=0.2,
        lang_hint="auto",
    )

    assert calls["overlay"] == []
    assert len(calls["whiteboard"]) == 1
    assert calls["create"][0]["image_path"] == whiteboard_path
    assert result["slide_path"] == whiteboard_path
    assert result["whiteboard_mode"] is True
    assert "source_background_missing_fallback_whiteboard" in result["source_render_warnings"]


def test_source_background_records_text_overflow_warning(tmp_path, monkeypatch):
    overlay_path = str(tmp_path / "source-background-overflow.png")
    _patch_render_dependencies(monkeypatch, overlay_result=overlay_path)
    slide_meta = _base_slide_meta(tmp_path, mode="source_background", whiteboard_mode=False)
    long_text = " ".join(["Source background overlay text should remain on one visual slide."] * 900)
    slide_meta["original_text"] = long_text
    slide_meta["display_text"] = long_text
    slide_meta["rich_text_html"] = f"<p>{long_text}</p>"
    slide_meta["subtitle_chunks"] = [long_text]

    result = worker_tasks.synthesize_and_render_slide.run(
        slide_meta,
        project_id="1",
        voice_id="test-voice",
        pause_sec=0.2,
        lang_hint="auto",
    )

    assert result["scene_background_mode"] == "source_background"
    assert "source_background_text_overflow" in result["source_render_warnings"]


def test_whiteboard_mode_still_uses_whiteboard_renderer(tmp_path, monkeypatch):
    whiteboard_path = str(tmp_path / "whiteboard.png")
    calls = _patch_render_dependencies(monkeypatch, whiteboard_result=whiteboard_path)
    slide_meta = _base_slide_meta(tmp_path, mode="whiteboard", whiteboard_mode=True)

    result = worker_tasks.synthesize_and_render_slide.run(
        slide_meta,
        project_id="1",
        voice_id="test-voice",
        pause_sec=0.2,
        lang_hint="auto",
    )

    assert calls["overlay"] == []
    assert len(calls["whiteboard"]) == 1
    assert calls["whiteboard"][0]["display_text"] == "Edited display text"
    assert calls["create"][0]["image_path"] == whiteboard_path
    assert result["slide_path"] == whiteboard_path


def test_txt_exports_default_to_whiteboard_without_original_background(tmp_path, monkeypatch):
    source_path = tmp_path / "lesson.txt"
    source_path.write_text("text lesson", encoding="utf-8")
    slide_path = tmp_path / "source.png"
    slide_path.write_bytes(PNG_1X1)
    note_path = tmp_path / "note.txt"
    note_path.write_text("Plain text lesson narration", encoding="utf-8")

    monkeypatch.setattr(worker_tasks.export_project, "update_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setattr(
        pptx_extract,
        "export_slide_images_with_metadata",
        lambda *_args, **_kwargs: {
            "image_paths": [str(slide_path)],
            "source_render_method": "txt_whiteboard",
            "source_render_warnings": [],
        },
    )
    monkeypatch.setattr(pptx_extract, "extract_speaker_notes", lambda *_args, **_kwargs: [str(note_path)])

    slides = worker_tasks.export_project.run("42", str(source_path), whiteboard_mode_all=False)

    assert slides
    assert slides[0]["source_type"] == "txt"
    assert slides[0]["whiteboard_mode"] is True
    assert slides[0]["original_background_path"] == ""
