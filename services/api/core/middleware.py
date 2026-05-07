"""Security and observability middleware for API requests."""

from __future__ import annotations

import json
import logging
import time

from .trace import extract_request_context

logger = logging.getLogger("core.request")


class PlaybackSecurityHeadersMiddleware:
    """Apply protection-oriented headers without breaking secure playback."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        is_stream_response = request.path.startswith("/api/v1/stream/")
        response.setdefault(
            "Permissions-Policy",
            "display-capture=(), camera=(), microphone=(), geolocation=(), payment=(), usb=(), picture-in-picture=()",
        )
        response.setdefault("X-Frame-Options", "DENY")
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("Referrer-Policy", "same-origin")
        response.setdefault("Cross-Origin-Resource-Policy", "cross-origin" if is_stream_response else "same-site")
        return response


class StructuredRequestLoggingMiddleware:
    """Global request lifecycle logs with request and trace context."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started_at = time.perf_counter()
        context = extract_request_context(request)
        request.request_id = context["request_id"]
        request.trace_id = context["trace_id"]
        request.traceparent = context["traceparent"]

        self._log(
            "request_started",
            request=request,
            status_code=0,
            duration_ms=0,
            error_code="",
        )
        try:
            response = self.get_response(request)
        except Exception as exc:
            self._log(
                "request_exception",
                request=request,
                status_code=500,
                duration_ms=self._duration_ms(started_at),
                error_code=getattr(exc, "default_code", "") or exc.__class__.__name__,
                level="error",
            )
            raise

        response["X-Request-ID"] = context["request_id"]
        self._log(
            "request_finished",
            request=request,
            status_code=int(getattr(response, "status_code", 0) or 0),
            duration_ms=self._duration_ms(started_at),
            error_code="",
        )
        return response

    @staticmethod
    def _duration_ms(started_at: float) -> int:
        return int(max(0.0, (time.perf_counter() - started_at) * 1000))

    @staticmethod
    def _user_id(request):
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return None
        try:
            return int(user.id)
        except Exception:
            return None

    def _log(self, message: str, *, request, status_code: int, duration_ms: int, error_code: str, level: str = "info"):
        payload = {
            "request_id": str(getattr(request, "request_id", "") or ""),
            "trace_id": str(getattr(request, "trace_id", "") or ""),
            "method": str(getattr(request, "method", "") or ""),
            "path": str(getattr(request, "path", "") or ""),
            "status_code": int(status_code or 0),
            "user_id": self._user_id(request),
            "duration_ms": int(duration_ms or 0),
            "error_code": str(error_code or ""),
        }
        method = getattr(logger, str(level).lower(), logger.info)
        method("%s | %s", message, json.dumps(payload, ensure_ascii=True, sort_keys=True))
