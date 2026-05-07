from __future__ import annotations

import re
import uuid
from typing import Any

_HEX_16_RE = re.compile(r"^[0-9a-f]{16}$")
_HEX_32_RE = re.compile(r"^[0-9a-f]{32}$")


def _safe_hex(value: str, expected_len: int) -> str:
    lowered = str(value or "").strip().lower()
    if len(lowered) != expected_len:
        return ""
    matcher = _HEX_16_RE if expected_len == 16 else _HEX_32_RE
    return lowered if matcher.match(lowered) else ""


def parse_traceparent(raw_header: str) -> dict[str, str]:
    raw = str(raw_header or "").strip()
    if not raw:
        return {"version": "", "trace_id": "", "parent_id": "", "trace_flags": ""}
    parts = raw.split("-")
    if len(parts) != 4:
        return {"version": "", "trace_id": "", "parent_id": "", "trace_flags": ""}
    version, trace_id, parent_id, trace_flags = [p.strip().lower() for p in parts]
    if len(version) != 2 or len(trace_flags) != 2:
        return {"version": "", "trace_id": "", "parent_id": "", "trace_flags": ""}
    trace_id = _safe_hex(trace_id, 32)
    parent_id = _safe_hex(parent_id, 16)
    if not trace_id or trace_id == ("0" * 32) or not parent_id or parent_id == ("0" * 16):
        return {"version": "", "trace_id": "", "parent_id": "", "trace_flags": ""}
    return {
        "version": version,
        "trace_id": trace_id,
        "parent_id": parent_id,
        "trace_flags": trace_flags,
    }


def generate_request_id(prefix: str = "req") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def generate_trace_id() -> str:
    return uuid.uuid4().hex


def build_traceparent(trace_id: str, parent_id: str | None = None, trace_flags: str = "01") -> str:
    normalized_trace_id = _safe_hex(trace_id, 32)
    if not normalized_trace_id:
        normalized_trace_id = generate_trace_id()
    normalized_parent = _safe_hex(parent_id or "", 16) or uuid.uuid4().hex[:16]
    normalized_flags = str(trace_flags or "01").strip().lower()
    if len(normalized_flags) != 2:
        normalized_flags = "01"
    return f"00-{normalized_trace_id}-{normalized_parent}-{normalized_flags}"


def extract_request_context(request: Any) -> dict[str, str]:
    request_id = str(getattr(request, "request_id", "") or "").strip()
    if not request_id:
        request_id = (
            str(request.headers.get("X-Request-ID") or request.headers.get("X-Request-Id") or request.headers.get("Idempotency-Key") or "")
            .strip()
        )
    if not request_id:
        request_id = generate_request_id()

    parsed = parse_traceparent(str(request.headers.get("traceparent") or ""))
    trace_id = parsed["trace_id"] or str(request.headers.get("X-Trace-Id") or request.headers.get("X-Trace-ID") or "").strip().lower()
    if not _safe_hex(trace_id, 32):
        trace_id = generate_trace_id()
    traceparent = build_traceparent(trace_id, parsed.get("parent_id"), parsed.get("trace_flags") or "01")
    return {
        "request_id": request_id[:120],
        "trace_id": trace_id,
        "traceparent": traceparent,
    }


def outbound_trace_headers(request: Any | None = None, *, trace_id: str | None = None, request_id: str | None = None) -> dict[str, str]:
    resolved_trace = str(trace_id or getattr(request, "trace_id", "") or "").strip().lower()
    if not _safe_hex(resolved_trace, 32):
        resolved_trace = generate_trace_id()
    resolved_request_id = str(request_id or getattr(request, "request_id", "") or generate_request_id()).strip()[:120]
    return {
        "traceparent": build_traceparent(resolved_trace),
        "X-Request-ID": resolved_request_id,
    }
