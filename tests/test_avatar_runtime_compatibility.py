from __future__ import annotations

from pathlib import Path

from avatar import canonical_pipeline, resource_manager
from avatar.digital_twin.domain import TwinStatus


def test_digital_twin_string_enums_keep_string_semantics() -> None:
    assert str(TwinStatus.DRAFT) == "draft"
    assert TwinStatus.DRAFT == "draft"


def test_stage_metrics_default_to_configured_storage_root(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AVATAR_ORCH_METRICS_FILE", raising=False)
    monkeypatch.delenv("AVATAR_STORAGE_ROOT", raising=False)
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))

    assert resource_manager._history_path() == tmp_path / "avatar_stage_metrics.json"
    assert canonical_pipeline._musetalk_history_file() == tmp_path / "avatar_stage_metrics.json"


def test_avatar_storage_root_precedes_general_storage_root(tmp_path, monkeypatch) -> None:
    avatar_root = tmp_path / "avatar"
    monkeypatch.delenv("AVATAR_ORCH_METRICS_FILE", raising=False)
    monkeypatch.setenv("AVATAR_STORAGE_ROOT", str(avatar_root))
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "general"))

    assert resource_manager._history_path() == avatar_root / "avatar_stage_metrics.json"
    assert canonical_pipeline._musetalk_history_file() == avatar_root / "avatar_stage_metrics.json"


def test_explicit_metrics_file_has_highest_priority(tmp_path, monkeypatch) -> None:
    metrics_path = tmp_path / "custom" / "metrics.json"
    monkeypatch.setenv("AVATAR_ORCH_METRICS_FILE", str(metrics_path))
    monkeypatch.setenv("AVATAR_STORAGE_ROOT", str(tmp_path / "avatar"))

    assert resource_manager._history_path() == Path(metrics_path)
    assert canonical_pipeline._musetalk_history_file() == Path(metrics_path)
