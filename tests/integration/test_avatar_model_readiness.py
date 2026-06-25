from __future__ import annotations

import json
import os
import subprocess
import sys
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_avatar_models.py"
SPEC = importlib.util.spec_from_file_location("check_avatar_models", SCRIPT)
assert SPEC is not None
checker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["check_avatar_models"] = checker
SPEC.loader.exec_module(checker)

REQUIRED_FILES = checker.REQUIRED_FILES
check_model_bundle = checker.check_model_bundle


def _write_complete_bundle(root: Path) -> None:
    for rel in REQUIRED_FILES:
        path = root / Path(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fixture:{rel}".encode("utf-8"))

    config = root / "musetalk" / "musetalk.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('{"model": "musetalk"}\n', encoding="utf-8")


def _run_checker(root: Path | str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_complete_bundle_passes(tmp_path: Path) -> None:
    root = tmp_path / "models"
    _write_complete_bundle(root)

    result = check_model_bundle(root)

    assert result["complete"] is True
    assert result["root_exists"] is True
    assert result["missing_files"] == []
    assert result["empty_files"] == []
    assert result["musetalk_config"]["accepted_relative_path"] == "musetalk/musetalk.json"


def test_runtime_config_json_alone_does_not_pass(tmp_path: Path) -> None:
    root = tmp_path / "models"
    _write_complete_bundle(root)
    (root / "musetalk" / "config.json").write_text('{"model": "musetalk"}\n', encoding="utf-8")
    (root / "musetalk" / "musetalk.json").unlink()

    result = check_model_bundle(root)

    assert result["complete"] is False
    assert "musetalk/musetalk.json" in result["missing_files"]
    assert result["musetalk_config"]["present"] is False
    assert any(error["code"] == "musetalk_config_missing" for error in result["errors"])


def test_missing_root_fails(tmp_path: Path) -> None:
    root = tmp_path / "missing-models"

    completed = _run_checker(root)
    result = check_model_bundle(root)

    assert completed.returncode != 0
    assert "Model root does not exist" in completed.stdout
    assert result["complete"] is False
    assert {"code": "model_root_missing", "path": str(root.resolve())} in result["errors"]


def test_missing_required_file_fails(tmp_path: Path) -> None:
    root = tmp_path / "models"
    _write_complete_bundle(root)
    (root / "sd-vae" / "config.json").unlink()

    result = check_model_bundle(root)

    assert result["complete"] is False
    assert "sd-vae/config.json" in result["missing_files"]
    assert any(error["code"] == "required_files_missing" for error in result["errors"])


def test_zero_byte_required_file_fails(tmp_path: Path) -> None:
    root = tmp_path / "models"
    _write_complete_bundle(root)
    (root / "whisper" / "pytorch_model.bin").write_bytes(b"")

    result = check_model_bundle(root)

    assert result["complete"] is False
    assert "whisper/pytorch_model.bin" in result["empty_files"]
    assert any(error["code"] == "required_files_empty" for error in result["errors"])


def test_missing_musetalk_config_fails(tmp_path: Path) -> None:
    root = tmp_path / "models"
    _write_complete_bundle(root)
    (root / "musetalk" / "musetalk.json").unlink()

    result = check_model_bundle(root)

    assert result["complete"] is False
    assert result["musetalk_config"]["present"] is False
    assert any(error["code"] == "musetalk_config_missing" for error in result["errors"])


def test_json_output_shape(tmp_path: Path) -> None:
    root = tmp_path / "models"
    _write_complete_bundle(root)

    completed = _run_checker(root, "--json")
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["schema_version"] == 1
    assert payload["complete"] is True
    assert isinstance(payload["required_files"], list)
    assert isinstance(payload["optional_files"], list)
    assert isinstance(payload["warnings"], list)
    assert payload["musetalk_config"]["present"] is True


def test_windows_style_path_argument_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "models"
    _write_complete_bundle(root)
    path_text = str(root)
    if os.name == "nt" and "\\" not in path_text:
        path_text = path_text.replace("/", "\\")

    completed = _run_checker(path_text, "--json")
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["complete"] is True
