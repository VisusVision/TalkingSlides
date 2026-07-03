from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .resources import executable_directory

REPOSITORY_MARKERS = (
    "infra/docker-compose.yml",
    "services/api",
    "services/frontend",
    "scripts",
)


@dataclass(frozen=True)
class RepositoryValidation:
    path: Path
    valid: bool
    missing_markers: tuple[str, ...]


def validate_repository(path: str | os.PathLike[str]) -> RepositoryValidation:
    candidate = Path(path).expanduser().resolve()
    missing = tuple(marker for marker in REPOSITORY_MARKERS if not (candidate / marker).exists())
    return RepositoryValidation(candidate, not missing, missing)


def preference_path() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "TalkingSlides" / "setup-assistant.json"


def load_repository_preference(path: Path | None = None) -> Path | None:
    target = path or preference_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        value = data.get("repository")
        return Path(value).expanduser() if isinstance(value, str) and value else None
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None


def save_repository_preference(repository: Path, path: Path | None = None) -> Path:
    validation = validate_repository(repository)
    if not validation.valid:
        raise ValueError(f"Not a TalkingSlides repository: missing {', '.join(validation.missing_markers)}")
    target = path or preference_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"repository": os.fspath(validation.path)}, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def _parents(path: Path, limit: int = 5) -> Iterable[Path]:
    current = path
    for _ in range(limit):
        yield current
        if current.parent == current:
            break
        current = current.parent


def discover_repository(explicit: str | os.PathLike[str] | None = None) -> RepositoryValidation | None:
    if explicit:
        return validate_repository(explicit)
    candidates: list[Path] = []
    env_path = os.environ.get("TALKINGSLIDES_REPOSITORY")
    if env_path:
        candidates.append(Path(env_path))
    preferred = load_repository_preference()
    if preferred:
        candidates.append(preferred)
    for start in (executable_directory(), Path.cwd(), Path(__file__).resolve()):
        candidates.extend(_parents(start))

    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = os.path.normcase(os.fspath(resolved))
        if key in seen:
            continue
        seen.add(key)
        validation = validate_repository(resolved)
        if validation.valid:
            return validation
    return None
