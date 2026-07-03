from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable

from .resources import executable_directory


@dataclass(frozen=True)
class RepositoryMarker:
    path: str
    display_name: str
    expected_type: str

    def present(self, root: Path) -> bool:
        target = root / self.path
        if self.expected_type == "directory":
            return target.is_dir()
        return target.is_file()


IDENTITY_MARKERS = (
    RepositoryMarker("infra/docker-compose.yml", "Docker Compose file", "file"),
    RepositoryMarker("services/api", "API service directory", "directory"),
    RepositoryMarker("services/frontend", "frontend service directory", "directory"),
    RepositoryMarker("scripts", "scripts directory", "directory"),
    RepositoryMarker("README.md", "project README", "file"),
)
CAPABILITY_MARKERS = (
    RepositoryMarker("scripts/windows-runtime.ps1", "modern Windows runtime", "file"),
    RepositoryMarker("scripts/windows-dev-start.ps1", "legacy Windows start script", "file"),
    RepositoryMarker("scripts/windows-dev-setup.ps1", "legacy Windows setup script", "file"),
    RepositoryMarker("tools/setup_assistant/__init__.py", "embedded Setup Assistant source", "file"),
    RepositoryMarker("services/tts_service", "TTS service directory", "directory"),
    RepositoryMarker("services/worker", "worker service directory", "directory"),
    RepositoryMarker("services/avatar", "avatar service directory", "directory"),
)
REPOSITORY_MARKERS = tuple(marker.path for marker in IDENTITY_MARKERS)
MARKER_DISPLAY_NAMES = {
    marker.path: marker.display_name
    for marker in (*IDENTITY_MARKERS, *CAPABILITY_MARKERS)
}
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
class RepositoryCapabilities:
    compose: bool = False
    api: bool = False
    frontend: bool = False
    scripts_directory: bool = False
    modern_windows_runtime: bool = False
    legacy_windows_runtime: bool = False
    embedded_setup_assistant: bool = False
    tts: bool = False
    worker: bool = False
    avatar: bool = False

    @property
    def service_controls(self) -> bool:
        return self.compose and self.api and self.frontend

    @property
    def any_windows_runtime(self) -> bool:
        return self.modern_windows_runtime or self.legacy_windows_runtime


@dataclass(frozen=True)
class RepositoryValidation:
    path: Path
    valid: bool
    missing_identity_markers: tuple[str, ...]
    capabilities: RepositoryCapabilities = field(default_factory=RepositoryCapabilities)
    missing_capabilities: tuple[str, ...] = ()
    compatibility_level: str = "invalid"
    warnings: tuple[str, ...] = ()
    git_metadata: bool = False

    @property
    def missing_markers(self) -> tuple[str, ...]:
        return self.missing_identity_markers

    @property
    def compatible(self) -> bool:
        return self.valid and self.compatibility_level == "modern"


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


def marker_display_name(marker_path: str) -> str:
    return MARKER_DISPLAY_NAMES.get(marker_path, marker_path)


def display_marker_list(markers: Iterable[str]) -> tuple[str, ...]:
    return tuple(f"{marker_display_name(marker)} ({marker})" for marker in markers)


def _missing_markers(candidate: Path, markers: Iterable[RepositoryMarker]) -> tuple[str, ...]:
    return tuple(marker.path for marker in markers if not marker.present(candidate))


def _repository_capabilities(candidate: Path) -> RepositoryCapabilities:
    compose = (candidate / "infra" / "docker-compose.yml").is_file()
    api = (candidate / "services" / "api").is_dir()
    frontend = (candidate / "services" / "frontend").is_dir()
    scripts = (candidate / "scripts").is_dir()
    modern_runtime = (candidate / "scripts" / "windows-runtime.ps1").is_file()
    legacy_runtime = (
        (candidate / "scripts" / "windows-dev-start.ps1").is_file()
        and (candidate / "scripts" / "windows-dev-setup.ps1").is_file()
    )
    return RepositoryCapabilities(
        compose=compose,
        api=api,
        frontend=frontend,
        scripts_directory=scripts,
        modern_windows_runtime=modern_runtime,
        legacy_windows_runtime=legacy_runtime,
        embedded_setup_assistant=(candidate / "tools" / "setup_assistant" / "__init__.py").is_file(),
        tts=(candidate / "services" / "tts_service").is_dir(),
        worker=(candidate / "services" / "worker").is_dir(),
        avatar=(candidate / "services" / "avatar").is_dir(),
    )


def _missing_capabilities(capabilities: RepositoryCapabilities) -> tuple[str, ...]:
    missing: list[str] = []
    if not capabilities.modern_windows_runtime:
        missing.append("scripts/windows-runtime.ps1")
    if not capabilities.legacy_windows_runtime and not capabilities.modern_windows_runtime:
        missing.extend(("scripts/windows-dev-start.ps1", "scripts/windows-dev-setup.ps1"))
    if not capabilities.embedded_setup_assistant:
        missing.append("tools/setup_assistant/__init__.py")
    if not capabilities.tts:
        missing.append("services/tts_service")
    if not capabilities.worker:
        missing.append("services/worker")
    if not capabilities.avatar:
        missing.append("services/avatar")
    return tuple(missing)


def _compatibility_level(valid: bool, capabilities: RepositoryCapabilities) -> str:
    if not valid:
        return "invalid"
    if capabilities.modern_windows_runtime and capabilities.service_controls:
        return "modern"
    if capabilities.legacy_windows_runtime:
        return "legacy"
    return "limited"


def _compatibility_warnings(valid: bool, capabilities: RepositoryCapabilities, level: str) -> tuple[str, ...]:
    if not valid:
        return ()
    warnings: list[str] = []
    if level == "legacy":
        warnings.append(
            "TalkingSlides repository detected, but some modern runtime controls are unavailable. "
            "Use available legacy controls or switch to a branch with scripts/windows-runtime.ps1."
        )
    elif level == "limited":
        warnings.append(
            "TalkingSlides repository detected, but no supported Windows runtime script was found. "
            "Diagnostics remain available; update the checkout or switch to a supported branch for runtime controls."
        )
    if not capabilities.embedded_setup_assistant:
        warnings.append(
            "Embedded Setup Assistant source is absent in this checkout. The packaged assistant can still select the repository."
        )
    return tuple(warnings)


def repository_capability_summary(validation: RepositoryValidation) -> tuple[str, ...]:
    capabilities = validation.capabilities
    return (
        f"Modern runtime: {'available' if capabilities.modern_windows_runtime else 'unavailable'}",
        f"Legacy runtime: {'available' if capabilities.legacy_windows_runtime else 'unavailable'}",
        f"Compose: {'available' if capabilities.compose else 'unavailable'}",
        f"API: {'available' if capabilities.api else 'unavailable'}",
        f"Frontend: {'available' if capabilities.frontend else 'unavailable'}",
        f"Service controls: {'available' if capabilities.service_controls else 'unavailable'}",
        f"Embedded Setup Assistant source: {'available' if capabilities.embedded_setup_assistant else 'not required for packaged assistant'}",
    )


def repository_validation_details(validation: RepositoryValidation) -> str:
    identity = "valid" if validation.valid else "invalid"
    identity_lines = [f"Repository identity: {identity}"]
    if validation.missing_identity_markers:
        identity_lines.append("Missing identity markers:")
        identity_lines.extend(f"- {item}" for item in display_marker_list(validation.missing_identity_markers))
    else:
        identity_lines.append("- all stable identity markers are present")
    capability_lines = ["", "Capabilities:"]
    capability_lines.extend(f"- {line}" for line in repository_capability_summary(validation))
    if validation.missing_capabilities:
        capability_lines.append("Missing optional capabilities:")
        capability_lines.extend(f"- {item}" for item in display_marker_list(validation.missing_capabilities))
    if validation.warnings:
        capability_lines.append("Guidance:")
        capability_lines.extend(f"- {warning}" for warning in validation.warnings)
    return "\n".join(identity_lines + capability_lines)


def validate_repository(path: str | os.PathLike[str]) -> RepositoryValidation:
    try:
        candidate = canonicalize_path(path)
    except (OSError, ValueError):
        candidate = Path(os.fspath(path) or ".").expanduser().absolute()
        return RepositoryValidation(candidate, False, REPOSITORY_MARKERS)
    missing_identity = _missing_markers(candidate, IDENTITY_MARKERS)
    capabilities = _repository_capabilities(candidate)
    level = _compatibility_level(not missing_identity, capabilities)
    return RepositoryValidation(
        candidate,
        not missing_identity,
        missing_identity,
        capabilities,
        _missing_capabilities(capabilities),
        level,
        _compatibility_warnings(not missing_identity, capabilities, level),
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
        missing = ", ".join(display_marker_list(validation.missing_identity_markers))
        raise ValueError(f"Not a TalkingSlides repository: missing {missing}")
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
            "Repository selected." if validation.valid else "The selected folder is not a TalkingSlides repository.",
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
        return RepositoryContext(RepositoryState.CANDIDATE_DETECTED, discovered, "TalkingSlides repository detected.")
    return RepositoryContext(RepositoryState.NONE_SELECTED, message="Choose, clone, or continue without a repository.")
