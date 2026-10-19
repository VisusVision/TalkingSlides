from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def talking_slides_repo(tmp_path: Path) -> Path:
    for directory in (
        "infra",
        "services/api",
        "services/frontend",
        "scripts",
        "tools/setup_assistant",
    ):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    (tmp_path / "infra" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# TalkingSlides\n", encoding="utf-8")
    (tmp_path / "infra" / ".env.example").write_text("SECRET_KEY=replace-me\n", encoding="utf-8")
    (tmp_path / "scripts" / "windows-runtime.ps1").write_text(
        '$ValidProfiles = @("core", "worker", "tts", "avatar", "translation", "full")\n',
        encoding="utf-8",
    )
    (tmp_path / "tools" / "setup_assistant" / "__init__.py").write_text("", encoding="utf-8")
    return tmp_path
