#!/usr/bin/env python3
"""Validate the local MuseTalk avatar model bundle layout.

This checker is intentionally read-only. It does not download model files,
start workers, import MuseTalk, or touch the avatar queue.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1

REQUIRED_FILES: tuple[str, ...] = (
    "musetalk/musetalk.json",
    "sd-vae/config.json",
    "sd-vae/diffusion_pytorch_model.bin",
    "musetalkV15/unet.pth",
    "whisper/config.json",
    "whisper/pytorch_model.bin",
    "whisper/preprocessor_config.json",
    "dwpose/dw-ll_ucoco_384.pth",
    "face-parse-bisent/79999_iter.pth",
    "face-parse-bisent/resnet18-5c106cde.pth",
)

OPTIONAL_FILES: tuple[str, ...] = (
    "musetalk/config.json",
    "musetalk/pytorch_model.bin",
    "musetalkV15/musetalk.json",
    "syncnet/latentsync_syncnet.pt",
)


@dataclass(frozen=True)
class FileCheck:
    relative_path: str
    path: str
    exists: bool
    non_empty: bool
    size_bytes: int

    @property
    def ok(self) -> bool:
        return self.exists and self.non_empty

    def as_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "path": self.path,
            "exists": self.exists,
            "non_empty": self.non_empty,
            "size_bytes": self.size_bytes,
            "ok": self.ok,
        }


def _default_model_root() -> Path:
    return Path.cwd() / "storage_local" / "models"


def _normalize_rel_path(path: str) -> str:
    return str(path).replace("\\", "/").strip().strip("/")


def _check_file(root: Path, relative_path: str) -> FileCheck:
    rel = _normalize_rel_path(relative_path)
    path = root / Path(*rel.split("/"))
    exists = path.exists() and path.is_file()
    size = 0
    if exists:
        try:
            size = int(path.stat().st_size)
        except OSError:
            size = 0
    return FileCheck(
        relative_path=rel,
        path=str(path),
        exists=exists,
        non_empty=bool(exists and size > 0),
        size_bytes=size,
    )


def check_model_bundle(model_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(model_root) if model_root is not None else _default_model_root()
    root = root.expanduser()
    try:
        resolved_root = root.resolve()
    except OSError:
        resolved_root = root.absolute()

    root_exists = resolved_root.exists() and resolved_root.is_dir()

    required_checks = [_check_file(resolved_root, rel) for rel in REQUIRED_FILES]
    config_check = _check_file(resolved_root, "musetalk/musetalk.json")
    normalized_config_check = _check_file(resolved_root, "musetalk/config.json")
    optional_checks = [_check_file(resolved_root, rel) for rel in OPTIONAL_FILES]

    missing_files = [check.relative_path for check in required_checks if not check.exists]
    empty_files = [check.relative_path for check in required_checks if check.exists and not check.non_empty]

    config_present = config_check.ok
    config_missing = not config_check.exists
    config_empty = config_check.exists and not config_check.non_empty

    warnings: list[dict[str, str]] = []
    if not root_exists:
        warnings.append(
            {
                "code": "model_root_missing",
                "message": f"Model root does not exist: {resolved_root}",
            }
        )

    nested_root = resolved_root / "models"
    if nested_root.exists() and nested_root.is_dir():
        warnings.append(
            {
                "code": "nested_models_directory_detected",
                "message": (
                    "A nested models/ directory exists under the selected root. "
                    "The supported local operator layout is direct children under the model root."
                ),
            }
        )

    for check in optional_checks:
        if not check.exists:
            warnings.append(
                {
                    "code": "optional_file_missing",
                    "path": check.relative_path,
                    "message": f"Optional upstream file is not present: {check.relative_path}",
                }
            )
        elif not check.non_empty:
            warnings.append(
                {
                    "code": "optional_file_empty",
                    "path": check.relative_path,
                    "message": f"Optional upstream file exists but is empty: {check.relative_path}",
                }
            )
        else:
            warnings.append(
                {
                    "code": "optional_file_detected",
                    "path": check.relative_path,
                    "message": f"Optional upstream file detected: {check.relative_path}",
                }
            )

    errors: list[dict[str, Any]] = []
    if not root_exists:
        errors.append({"code": "model_root_missing", "path": str(resolved_root)})
    if missing_files:
        errors.append({"code": "required_files_missing", "files": missing_files})
    if empty_files:
        errors.append({"code": "required_files_empty", "files": empty_files})
    if config_missing:
        errors.append({"code": "musetalk_config_missing", "path": config_check.relative_path})
    if config_empty:
        errors.append({"code": "musetalk_config_empty", "path": config_check.relative_path})

    complete = bool(root_exists and not missing_files and not empty_files and config_present)

    return {
        "schema_version": SCHEMA_VERSION,
        "model_root": str(resolved_root),
        "root_exists": root_exists,
        "complete": complete,
        "required_files": [check.as_dict() for check in required_checks],
        "missing_files": missing_files,
        "empty_files": empty_files,
        "musetalk_config": {
            "required": config_check.as_dict(),
            "normalized_runtime_config": normalized_config_check.as_dict(),
            "present": config_present,
            "accepted_relative_path": config_check.relative_path if config_present else "",
        },
        "optional_files": [check.as_dict() for check in optional_checks],
        "warnings": warnings,
        "errors": errors,
    }


def _human_report(result: dict[str, Any]) -> str:
    lines: list[str] = []
    status = "PASS" if result.get("complete") else "FAIL"
    lines.append(f"Avatar MuseTalk model bundle readiness: {status}")
    lines.append(f"Model root: {result.get('model_root')}")
    lines.append(f"Root exists: {str(bool(result.get('root_exists'))).lower()}")

    if result.get("complete"):
        accepted = str((result.get("musetalk_config") or {}).get("accepted_relative_path") or "")
        lines.append("Required files: complete")
        lines.append(f"MuseTalk config: {accepted}")
    else:
        missing = list(result.get("missing_files") or [])
        empty = list(result.get("empty_files") or [])
        if missing:
            lines.append("Missing required files:")
            lines.extend(f"  - {item}" for item in missing)
        if empty:
            lines.append("Empty required files:")
            lines.extend(f"  - {item}" for item in empty)
        config = dict(result.get("musetalk_config") or {})
        if not config.get("present"):
            required = config.get("required") if isinstance(config.get("required"), dict) else {}
            path = str(required.get("relative_path") or "musetalk/musetalk.json")
            lines.append(f"MuseTalk config missing: provide a non-empty file at {path}")

    warnings = list(result.get("warnings") or [])
    if warnings:
        lines.append("Warnings:")
        for warning in warnings:
            if isinstance(warning, dict):
                lines.append(f"  - {warning.get('code')}: {warning.get('message')}")
            else:
                lines.append(f"  - {warning}")

    return "\n".join(lines)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the local MuseTalk avatar model bundle layout."
    )
    parser.add_argument(
        "model_root",
        nargs="?",
        default=None,
        help="Model root to check. Defaults to storage_local/models from the current directory.",
    )
    parser.add_argument(
        "--format",
        choices=("human", "json", "both"),
        default="human",
        help="Output format. Default: human.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Shortcut for --format json.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    output_format = "json" if args.json else args.format
    result = check_model_bundle(args.model_root)

    if output_format in {"human", "both"}:
        print(_human_report(result))
    if output_format == "both":
        print()
    if output_format in {"json", "both"}:
        print(json.dumps(result, indent=2, sort_keys=True))

    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
