from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from .resources import executable_directory

REPOSITORY_MARKERS = (
    "infra/docker-compose.yml",
    "scripts/windows-runtime.ps1",
    "tools/setup_assistant/__init__.py",
    "services/api",
    "services/frontend",
)
RECENT_REPOSITORY_LIMIT = 5
SETTINGS_SCHEMA = 2


class RepositoryState(str, Enum):
    NONE_SELECTED = "no_repository_selected"
    CANDIDATE_DETECTED = "repository_candidate_detected"
    VALID_SELECTED = "valid_repository_selected"
    INVALID_SELECTED = "invalid_repository_selected"
    CLONING = "cloning"
    CLONE_FAILED = "clone_failed"
    SYSTEM_ONLY = "system_only"


@dataclass(frozen=True)
class RepositoryValidation:
    path: Path
    valid: bool
    missing_markers: tuple[str, ...]
    git_metadata: bool = False


@dataclass(frozen=True)
class RepositorySettings:
    repository: Path | None = None
    recent_repositories: tuple[Path, ...] = ()
    system_only: bool = False


@dataclass(frozen=True)
class RepositoryContext:
    state: RepositoryState
    validation: RepositoryValidation | None = None
    message: str = ""

    @property
    def repository(self) -> Path | None:
        if self.validation and self.validation.valid:
            return self.validation.path
        return None


def canonicalize_path(path: str | os.PathLike[str]) -> Path:
    value = os.fspath(path).strip()
    if not value:
        raise ValueError("Repository path is empty.")
    if any(character in value for character in "\0\n\r\t"):
        raise ValueError("Repository path contains unsupported control characters.")
    return Path(value).expanduser().resolve(strict=False)


def validate_repository(path: str | os.PathLike[str]) -> RepositoryValidation:
    try:
        candidate = canonicalize_path(path)
    except (OSError, ValueError):
        candidate = Path(os.fspath(path) or ".").expanduser().absolute()
        return RepositoryValidation(candidate, False, REPOSITORY_MARKERS)
    missing: list[str] = []
    for marker in REPOSITORY_MARKERS:
        target = candidate / marker
        if marker in {"services/api", "services/frontend"}:
            present = target.is_dir()
        else:
            present = target.is_file()
        if not present:
            missing.append(marker)
    return RepositoryValidation(
        candidate,
        not missing,
        tuple(missing),
        (candidate / ".git").exists(),
    )


def preference_path() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "TalkingSlides" / "setup-assistant.json"


def load_repository_settings(path: Path | None = None) -> RepositorySettings:
    target = path or preference_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return RepositorySettings()
    if not isinstance(data, dict):
        return RepositorySettings()

    def read_path(value: object) -> Path | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return canonicalize_path(value)
        except (OSError, ValueError):
            return None

    selected = read_path(data.get("repository"))
    recent_values = data.get("recent_repositories", [])
    recent: list[Path] = []
    if isinstance(recent_values, list):
        for value in recent_values:
            candidate = read_path(value)
            if candidate and candidate not in recent:
                recent.append(candidate)
    if selected and selected not in recent:
        recent.insert(0, selected)
    return RepositorySettings(
        repository=selected,
        recent_repositories=tuple(recent[:RECENT_REPOSITORY_LIMIT]),
        system_only=bool(data.get("system_only", False)),
    )


def save_repository_settings(settings: RepositorySettings, path: Path | None = None) -> Path:
    target = path or preference_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SETTINGS_SCHEMA,
        "repository": os.fspath(settings.repository) if settings.repository else None,
        "recent_repositories": [os.fspath(item) for item in settings.recent_repositories],
        "system_only": settings.system_only,
    }
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    return target


def load_repository_preference(path: Path | None = None) -> Path | None:
    return load_repository_settings(path).repository


def save_repository_preference(repository: Path, path: Path | None = None) -> Path:
    validation = validate_repository(repository)
    if not validation.valid:
        raise ValueError(f"Not a TalkingSlides repository: missing {', '.join(validation.missing_markers)}")
    current = load_repository_settings(path)
    recent = [validation.path, *current.recent_repositories]
    unique: list[Path] = []
    for item in recent:
        if item not in unique:
            unique.append(item)
    return save_repository_settings(
        RepositorySettings(validation.path, tuple(unique[:RECENT_REPOSITORY_LIMIT]), False),
        path,
    )


def select_system_only(path: Path | None = None) -> Path:
    current = load_repository_settings(path)
    return save_repository_settings(
        RepositorySettings(current.repository, current.recent_repositories, True),
        path,
    )


def forget_repository(repository: Path, path: Path | None = None) -> Path:
    current = load_repository_settings(path)
    try:
        canonical = canonicalize_path(repository)
    except (OSError, ValueError):
        canonical = repository
    recent = tuple(item for item in current.recent_repositories if item != canonical)
    selected = None if current.repository == canonical else current.repository
    return save_repository_settings(RepositorySettings(selected, recent, current.system_only), path)


def _parents(path: Path, limit: int = 5) -> Iterable[Path]:
    current = path
    for _ in range(limit):
        yield current
        if current.parent == current:
            break
        current = current.parent


def _repository_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get("TALKINGSLIDES_REPOSITORY")
    if env_path:
        candidates.append(Path(env_path))
    settings = load_repository_settings()
    if settings.repository:
        candidates.append(settings.repository)
    candidates.extend(settings.recent_repositories)
    for start in (executable_directory(), Path.cwd(), Path(__file__).resolve()):
        candidates.extend(_parents(start))
    return candidates


def discover_repository(explicit: str | os.PathLike[str] | None = None) -> RepositoryValidation | None:
    if explicit:
        return validate_repository(explicit)
    seen: set[str] = set()
    for candidate in _repository_candidates():
        try:
            resolved = canonicalize_path(candidate)
        except (OSError, ValueError):
            continue
        key = os.path.normcase(os.fspath(resolved))
        if key in seen:
            continue
        seen.add(key)
        validation = validate_repository(resolved)
        if validation.valid:
            return validation
    return None


def initial_repository_context(explicit: str | os.PathLike[str] | None = None) -> RepositoryContext:
    if explicit:
        validation = validate_repository(explicit)
        return RepositoryContext(
            RepositoryState.VALID_SELECTED if validation.valid else RepositoryState.INVALID_SELECTED,
            validation,
            "Repository selected." if validation.valid else "The selected folder is not compatible.",
        )
    settings = load_repository_settings()
    if settings.system_only:
        return RepositoryContext(RepositoryState.SYSTEM_ONLY, message="System-only diagnostics mode.")
    if settings.repository:
        validation = validate_repository(settings.repository)
        if validation.valid:
            return RepositoryContext(RepositoryState.VALID_SELECTED, validation, "Saved repository reopened.")
    discovered = discover_repository()
    if discovered:
        return RepositoryContext(RepositoryState.CANDIDATE_DETECTED, discovered, "Compatible repository detected.")
    return RepositoryContext(RepositoryState.NONE_SELECTED, message="Choose, clone, or continue without a repository.")
