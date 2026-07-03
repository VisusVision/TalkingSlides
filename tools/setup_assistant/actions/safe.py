from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..repository import validate_repository


@dataclass(frozen=True)
class ActionExecution:
    action_id: str
    executed: bool
    changed: bool
    summary: str


class SafeActionExecutor:
    """Small, local-only repair actions. Callers must show the preview first."""

    def preview(self, action_id: str, repository: Path) -> str:
        root = validate_repository(repository)
        if not root.valid:
            raise ValueError("A valid TalkingSlides repository is required.")
        previews = {
            "config.create_env": "Copy infra/.env.example to infra/.env. Existing infra/.env will not be overwritten.",
            "config.create_storage": "Create an empty storage_local directory. Existing files are not changed.",
            "linux.make_setup_executable": "Add the user execute bit to the selected local setup executable.",
        }
        if action_id not in previews:
            raise ValueError(f"Unsupported safe action: {action_id}")
        return previews[action_id]

    def execute(
        self,
        action_id: str,
        repository: Path,
        *,
        confirmed: bool,
        executable: Path | None = None,
    ) -> ActionExecution:
        preview = self.preview(action_id, repository)
        if not confirmed:
            return ActionExecution(action_id, False, False, f"Confirmation required: {preview}")
        repository = repository.resolve()
        if action_id == "config.create_env":
            source = repository / "infra" / ".env.example"
            target = repository / "infra" / ".env"
            if target.exists():
                return ActionExecution(action_id, True, False, "infra/.env already exists; nothing was changed.")
            if not source.is_file():
                raise FileNotFoundError(source)
            with source.open("rb") as source_file, target.open("xb") as target_file:
                shutil.copyfileobj(source_file, target_file)
            return ActionExecution(action_id, True, True, "Created infra/.env from the template without overwriting.")
        if action_id == "config.create_storage":
            target = repository / "storage_local"
            existed = target.exists()
            target.mkdir(parents=True, exist_ok=True)
            return ActionExecution(
                action_id,
                True,
                not existed,
                "storage_local already exists; nothing was changed." if existed else "Created storage_local.",
            )
        if action_id == "linux.make_setup_executable":
            if os.name == "nt":
                raise OSError("Executable permission repair is only supported on Linux.")
            if executable is None or not executable.is_file():
                raise FileNotFoundError(executable)
            original_mode = executable.stat().st_mode
            executable.chmod(original_mode | 0o100)
            return ActionExecution(action_id, True, executable.stat().st_mode != original_mode, "Updated the user execute bit.")
        raise ValueError(f"Unsupported safe action: {action_id}")
