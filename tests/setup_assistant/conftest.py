from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def talking_slides_repo(tmp_path: Path) -> Path:
    for directory in ("infra", "services/api", "services/frontend", "scripts"):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    (tmp_path / "infra" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "infra" / ".env.example").write_text("SECRET_KEY=replace-me\n", encoding="utf-8")
    return tmp_path
