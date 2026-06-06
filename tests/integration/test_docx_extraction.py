from __future__ import annotations

from pathlib import Path

import pptx_extract


class _FakeStyle:
    def __init__(self, name: str):
        self.name = name


class _FakeParagraph:
    def __init__(self, text: str, style_name: str = "Normal"):
        self.text = text
        self.style = _FakeStyle(style_name)


class _FakeCell:
    def __init__(self, text: str):
        self.text = text


class _FakeRow:
    def __init__(self, cells: list[_FakeCell]):
        self.cells = cells


class _FakeTable:
    def __init__(self, rows: list[_FakeRow]):
        self.rows = rows


class _FakeDoc:
    def __init__(self):
        self.paragraphs = [
            _FakeParagraph("Lesson 1: Intro", "Heading 1"),
            _FakeParagraph("Artificial intelligence is transforming learning."),
            _FakeParagraph("We will cover models, prompts, and safety."),
            _FakeParagraph("Lesson 2: Applications", "Heading 2"),
            _FakeParagraph("Healthcare and education are key examples."),
        ]
        self.tables = [
            _FakeTable(
                [
                    _FakeRow([_FakeCell("Topic"), _FakeCell("Duration")]),
                    _FakeRow([_FakeCell("Prompting"), _FakeCell("15 min")]),
                ]
            )
        ]


def test_docx_split_slides_extracts_real_content():
    slides = pptx_extract._docx_split_slides(_FakeDoc())

    assert slides
    joined = "\n".join(slides)
    assert "Lesson 1: Intro" in joined
    assert "Artificial intelligence is transforming learning." in joined
    assert "Lesson 2: Applications" in joined
    assert "Topic | Duration" in joined
    assert "Slide 1." not in joined


def test_extract_docx_text_uses_docx_content_not_placeholders(tmp_path, monkeypatch):
    # Prepare a dummy input path; fake Document loader ignores file contents.
    docx_path = tmp_path / "lesson.docx"
    docx_path.write_bytes(b"dummy")

    notes_root = tmp_path / "notes_root"
    notes_dir = notes_root / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(pptx_extract, "_HAVE_DOCX", True)
    monkeypatch.setattr(pptx_extract, "_DocxDocument", lambda _path: _FakeDoc())
    def fail_libreoffice_conversion(*_args, **_kwargs):
        raise RuntimeError("force python-docx fallback")

    monkeypatch.setattr(pptx_extract, "_convert_via_libreoffice_to_pdf", fail_libreoffice_conversion)

    paths = pptx_extract._extract_docx_text(str(docx_path), notes_dir)

    assert paths
    content = [Path(p).read_text(encoding="utf-8") for p in paths]
    merged = "\n".join(content)

    assert "Lesson 1: Intro" in merged
    assert "Artificial intelligence is transforming learning." in merged
    assert "Topic | Duration" in merged
    assert "Slide 1." not in merged
