"""Small JSON metadata writers routed through the storage adapter boundary."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from core.storage_adapter import get_storage_adapter


def write_json_metadata_file(
    *,
    storage_root: str | Path | None,
    relative_path: str | Path,
    payload: dict[str, Any],
) -> str:
    """Write a JSON metadata file while preserving filesystem temp-replace behavior."""
    adapter = get_storage_adapter(storage_root)
    target = adapter.resolve_path(relative_path)
    parent_rel_path = Path(str(relative_path).replace("\\", "/")).parent
    adapter.make_dirs("" if str(parent_rel_path) == "." else parent_rel_path.as_posix())
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(json.dumps(payload, ensure_ascii=True, indent=2))
    temp_path.replace(target)
    return str(target)
