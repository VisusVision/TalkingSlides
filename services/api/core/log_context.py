from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def set_log_context(*, request_id: str = "", trace_id: str = "") -> tuple[Any, Any]:
    request_token = _request_id_var.set(str(request_id or "").strip()[:120])
    trace_token = _trace_id_var.set(str(trace_id or "").strip()[:64])
    return request_token, trace_token


def reset_log_context(tokens: tuple[Any, Any]) -> None:
    request_token, trace_token = tokens
    _request_id_var.reset(request_token)
    _trace_id_var.reset(trace_token)


def get_request_id() -> str:
    return _request_id_var.get()


def get_trace_id() -> str:
    return _trace_id_var.get()


class RequestContextLogFilter:
    def filter(self, record: Any) -> bool:
        record.request_id = get_request_id() or "-"
        record.trace_id = get_trace_id() or "-"
        return True


class SlowQueryLogFilter:
    def __init__(self, threshold_ms: int = 750) -> None:
        self.threshold_ms = max(int(threshold_ms), 0)

    def filter(self, record: Any) -> bool:
        duration = getattr(record, "duration", None)
        if duration is None:
            return False
        try:
            elapsed_ms = float(duration) * 1000.0
        except (TypeError, ValueError):
            return False
        return elapsed_ms >= self.threshold_ms
