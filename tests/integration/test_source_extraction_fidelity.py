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

from django.contrib.auth.models import User  # noqa: E402

from core.models import Project, TranscriptPage, UserProfile  # noqa: E402
from core.serializers import TranscriptPageSerializer  # noqa: E402
from scripts import pptx_extract  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str) -> Project:
    return Project.objects.create(title=f"Extraction fidelity {username}", user=_make_teacher(username))


def _write_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_1X1)
    return path


def _sync_one_page(project: Project, tmp_path: Path, monkeypatch, *, source_type: str, page_key: str = "s1-p1") -> TranscriptPage:
    storage_root = tmp_path / "storage"
    image_path = _write_png(storage_root / str(project.id) / "images" / f"{source_type}-slide-1.png")
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(storage_root))

    worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            {
                "index": 0,
                "source_slide_index": 0,
                "split_index": 0,
                "page_key": page_key,
                "source_type": source_type,
                "image_path": str(image_path),
                "original_background_path": str(image_path),
                "original_text": "Visible page text",
                "narration_text": "Narrated page text",
                "subtitle_chunks": ["Narrated page text"],
                "whiteboard_mode": False,
            }
        ],
    )
    return TranscriptPage.objects.get(project=project, page_key=page_key)


def test_docx_text_extraction_prefers_pdf_page_text(tmp_path, monkeypatch):
    docx_path = tmp_path / "lesson.docx"
    docx_path.write_bytes(b"dummy docx")
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()

    def fake_convert(_source_path: str, out_dir: Path) -> Path:
        pdf_path = out_dir / "lesson.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")
        return pdf_path

    def fake_extract_pdf_text(pdf_path: str, target_notes_dir: Path) -> list[str]:
        assert Path(pdf_path).name == "lesson.pdf"
        assert target_notes_dir == notes_dir
        out_paths = []
        for idx, text in enumerate(["Page one text", "Page two text"], start=1):
            path = target_notes_dir / f"slide-{idx}.txt"
            path.write_text(text, encoding="utf-8")
            out_paths.append(str(path))
        return out_paths

    monkeypatch.setattr(pptx_extract, "_convert_via_libreoffice_to_pdf", fake_convert)
    monkeypatch.setattr(pptx_extract, "_extract_pdf_text", fake_extract_pdf_text)

    paths = pptx_extract._extract_docx_text(str(docx_path), notes_dir)

    assert len(paths) == 2
    assert [Path(path).read_text(encoding="utf-8") for path in paths] == ["Page one text", "Page two text"]


@pytest.mark.django_db
def test_docx_export_uses_raster_page_count_and_handles_text_mismatch(tmp_path, monkeypatch):
    project = _make_project("docx_page_count")
    storage_root = tmp_path / "storage"
    source_path = tmp_path / "source.docx"
    source_path.write_bytes(b"fake docx")
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(storage_root))

    def fake_export_slide_images(_source_path: str, out_dir: str) -> list[str]:
        return [
            str(_write_png(Path(out_dir) / "slide-1.png")),
            str(_write_png(Path(out_dir) / "slide-2.png")),
            str(_write_png(Path(out_dir) / "slide-3.png")),
        ]

    def fake_extract_speaker_notes(_source_path: str, out_dir: str) -> list[str]:
        note_path = Path(out_dir) / "notes" / "slide-1.txt"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("Only the first physical page has extracted text.", encoding="utf-8")
        return [str(note_path)]

    monkeypatch.setattr(pptx_extract, "export_slide_images", fake_export_slide_images)
    monkeypatch.setattr(pptx_extract, "extract_speaker_notes", fake_extract_speaker_notes)
    monkeypatch.setattr(worker_tasks.export_project, "update_state", lambda *args, **kwargs: None)

    slides = worker_tasks.export_project.run(str(project.id), str(source_path), False)

    assert len(slides) == 3
    assert [slide["source_type"] for slide in slides] == ["docx", "docx", "docx"]
    assert slides[0]["original_text"] == "Only the first physical page has extracted text."
    assert slides[1]["original_text"] == ""
    assert slides[2]["narration_text"] == ""
    assert all(slide["original_background_path"] == slide["image_path"] for slide in slides)


@pytest.mark.django_db
@pytest.mark.parametrize("source_type", ["docx", "pdf", "pptx"])
def test_visual_source_pages_default_to_original_background(tmp_path, monkeypatch, source_type):
    project = _make_project(f"{source_type}_original_background")

    page = _sync_one_page(project, tmp_path, monkeypatch, source_type=source_type)

    scene = page.editor_document["scene"]
    assert scene["background_mode"] == "original"
    assert scene["original_background_path"].endswith(f"{source_type}-slide-1.png")
    serialized_scene = TranscriptPageSerializer(page).data["editor_document"]["scene"]
    assert serialized_scene["has_original_background"] is True
    assert serialized_scene["original_background_url"]
    assert "original_background_path" not in serialized_scene


@pytest.mark.django_db
def test_transcript_resync_preserves_existing_custom_background(tmp_path, monkeypatch):
    project = _make_project("preserve_custom")
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Existing text",
        narration_text="Existing narration",
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "custom",
                "custom_background_path": f"uploads/{project.id}/backgrounds/custom.png",
                "background_fit": "cover",
                "text_scale": 1.25,
            },
        },
    )
    storage_root = tmp_path / "storage"
    image_path = _write_png(storage_root / str(project.id) / "images" / "docx-slide-1.png")
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(storage_root))

    worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [{"index": 0, "page_key": page.page_key, "source_type": "docx", "image_path": str(image_path)}],
    )

    page.refresh_from_db()
    scene = page.editor_document["scene"]
    assert scene["background_mode"] == "custom"
    assert scene["custom_background_path"] == f"uploads/{project.id}/backgrounds/custom.png"
    assert scene["background_fit"] == "cover"
    assert scene["text_scale"] == 1.25
    assert scene["original_background_path"] == f"{project.id}/images/docx-slide-1.png"


@pytest.mark.django_db
def test_transcript_resync_preserves_existing_whiteboard_mode(tmp_path, monkeypatch):
    project = _make_project("preserve_whiteboard")
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Existing text",
        narration_text="Existing narration",
        whiteboard_mode=True,
        editor_document={"version": 1, "scene": {"background_mode": "whiteboard"}},
    )
    storage_root = tmp_path / "storage"
    image_path = _write_png(storage_root / str(project.id) / "images" / "pdf-slide-1.png")
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(storage_root))

    worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [{"index": 0, "page_key": page.page_key, "source_type": "pdf", "image_path": str(image_path)}],
    )

    page.refresh_from_db()
    assert page.whiteboard_mode is True
    assert page.editor_document["scene"]["background_mode"] == "whiteboard"
    assert page.editor_document["scene"]["original_background_path"] == f"{project.id}/images/pdf-slide-1.png"


@pytest.mark.django_db
def test_txt_source_defaults_to_whiteboard_without_original_background(tmp_path, monkeypatch):
    project = _make_project("txt_whiteboard")
    storage_root = tmp_path / "storage"
    image_path = _write_png(storage_root / str(project.id) / "images" / "slide-1.png")
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(storage_root))

    worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            {
                "index": 0,
                "source_slide_index": 0,
                "page_key": "s1-p1",
                "source_type": "txt",
                "image_path": str(image_path),
                "original_text": "Plain text source",
                "narration_text": "Plain text source",
                "whiteboard_mode": True,
            }
        ],
    )

    page = TranscriptPage.objects.get(project=project, page_key="s1-p1")
    scene = page.editor_document["scene"]
    assert scene["background_mode"] == "whiteboard"
    assert "original_background_path" not in scene
    serialized_scene = TranscriptPageSerializer(page).data["editor_document"]["scene"]
    assert serialized_scene["has_original_background"] is False
    assert serialized_scene["original_background_url"] == ""
    assert "original_background_path" not in serialized_scene
