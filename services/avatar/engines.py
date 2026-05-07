from __future__ import annotations

from .canonical_adapters import (
    CANONICAL_ENGINE,
    SUPPORTED_ENGINES as SUPPORTED_REAL_ENGINES,
    EngineResult,
    get_avatar_engine_configuration_report,
    normalize_avatar_engine,
    run_liveportrait,
    run_musetalk,
    run_restoration,
)

SUPPORTED_REAL_ENGINE_SET = set(SUPPORTED_REAL_ENGINES)

__all__ = [
    "CANONICAL_ENGINE",
    "SUPPORTED_REAL_ENGINES",
    "SUPPORTED_REAL_ENGINE_SET",
    "EngineResult",
    "get_avatar_engine_configuration_report",
    "normalize_avatar_engine",
    "run_liveportrait",
    "run_musetalk",
    "run_restoration",
]
