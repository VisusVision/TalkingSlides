"""Worker highlight engine compatibility wrapper.

Canonical implementation lives in `core.highlight_engine` so API preview and
worker render paths use the same code.
"""

try:
    from core.highlight_engine import ENGINE_VERSION, apply_highlight
except ModuleNotFoundError:  # pragma: no cover - import-path compatibility fallback
    from api.core.highlight_engine import ENGINE_VERSION, apply_highlight

__all__ = ["ENGINE_VERSION", "apply_highlight"]
