from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen

from .trace import outbound_trace_headers


def build_internal_request_headers(request=None, headers: dict[str, str] | None = None) -> dict[str, str]:
    merged = dict(headers or {})
    if request is not None:
        trace_headers = outbound_trace_headers(request)
        merged.setdefault("traceparent", trace_headers["traceparent"])
        merged.setdefault("X-Request-ID", trace_headers["X-Request-ID"])
    return merged


def open_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
    request=None,
) -> Any:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = build_internal_request_headers(
        request=request,
        headers={"Accept": "application/json", "Content-Type": "application/json", **dict(headers or {})},
    )
    req = Request(url, data=payload, headers=request_headers, method=method)
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def open_bytes(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
    max_bytes: int | None = None,
    request=None,
) -> tuple[bytes, dict[str, str]]:
    req = Request(url, headers=build_internal_request_headers(request=request, headers=headers), method=method)
    with urlopen(req, timeout=timeout) as response:
        payload = response.read(max_bytes + 1) if max_bytes is not None else response.read()
        response_headers = {key: value for key, value in response.headers.items()}
    return payload, response_headers
