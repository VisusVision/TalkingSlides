from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TTSPreparedText:
    raw_text: str
    normalized_text: str
    spoken_text: str
    chunks: list[str]
    warnings: list[str]
    chunk_pause_ms: list[int] = field(default_factory=list)
    original_text: str = ""
    tts_normalization_language: str = ""
    tts_normalization_rules_applied: list[dict[str, Any]] = field(default_factory=list)
    unknown_terms: list[str] = field(default_factory=list)
    ambiguous_terms: list[str] = field(default_factory=list)
