from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.setup_assistant import repository, resources


def test_repository_validation(talking_slides_repo: Path) -> None:
    validation = repository.validate_repository(talking_slides_repo)
    assert validation.valid
    assert not validation.missing_markers


def test_invalid_repository_lists_markers(tmp_path: Path) -> None:
    validation = repository.validate_repository(tmp_path)
    assert not validation.valid
    assert "infra/docker-compose.yml" in validation.missing_markers


def test_preference_round_trip(talking_slides_repo: Path, tmp_path: Path) -> None:
    target = tmp_path / "preferences.json"
    repository.save_repository_preference(talking_slides_repo, target)
    assert repository.load_repository_preference(target).resolve() == talking_slides_repo.resolve()
    assert set(json.loads(target.read_text(encoding="utf-8"))) == {"repository"}


def test_discovery_uses_explicit_path(talking_slides_repo: Path) -> None:
    assert repository.discover_repository(talking_slides_repo).path == talking_slides_repo.resolve()


def test_discovery_rejects_invalid_explicit_path(tmp_path: Path) -> None:
    result = repository.discover_repository(tmp_path)
    assert result is not None
    assert not result.valid


def test_frozen_resource_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert resources.is_frozen()
    assert resources.resource_root() == tmp_path.resolve()


def test_appimage_resource_root(monkeypatch, tmp_path: Path) -> None:
    candidate = tmp_path / "usr" / "share" / "talkingslides-setup"
    candidate.mkdir(parents=True)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setenv("APPIMAGE", "/tmp/TalkingSlides.AppImage")
    monkeypatch.setenv("APPDIR", str(tmp_path))
    assert resources.resource_root() == candidate.resolve()
