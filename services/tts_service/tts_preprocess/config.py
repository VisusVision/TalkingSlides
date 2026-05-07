from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TTSPreprocessConfig:
    preprocessing_enabled: bool = True
    max_chars_per_chunk: int = 500
    target_chars_per_chunk: int = 280
    sentence_pause_ms: int = 250
    paragraph_pause_ms: int = 450
    slide_pause_ms: int = 700
    glossary_path: Path = Path(__file__).with_name("glossary.json")


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(int(raw), minimum)
    except (TypeError, ValueError):
        return default


def _resolve_glossary_path(raw: str | None) -> Path:
    package_default = Path(__file__).with_name("glossary.json")
    if not raw:
        return package_default

    configured = Path(raw)
    candidates = [configured] if configured.is_absolute() else [Path.cwd() / configured]
    candidates.append(package_default)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def get_preprocess_config() -> TTSPreprocessConfig:
    max_chars = _int_env("TTS_MAX_CHARS_PER_CHUNK", 500, minimum=80)
    target_chars = _int_env("TTS_TARGET_CHARS_PER_CHUNK", 280, minimum=60)
    target_chars = min(target_chars, max_chars)
    return TTSPreprocessConfig(
        preprocessing_enabled=_bool_env("TTS_PREPROCESSING_ENABLED", True),
        max_chars_per_chunk=max_chars,
        target_chars_per_chunk=target_chars,
        sentence_pause_ms=_int_env("TTS_SENTENCE_PAUSE_MS", 250, minimum=0),
        paragraph_pause_ms=_int_env("TTS_PARAGRAPH_PAUSE_MS", 450, minimum=0),
        slide_pause_ms=_int_env("TTS_SLIDE_PAUSE_MS", 700, minimum=0),
        glossary_path=_resolve_glossary_path(os.environ.get("TTS_GLOSSARY_PATH")),
    )
