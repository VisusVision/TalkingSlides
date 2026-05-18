"""Security headers for lecture playback pages and media responses."""

from __future__ import annotations

from core.log_context import reset_log_context, set_log_context
from core.trace import extract_request_context


class RequestObservabilityMiddleware:
    """Attach trace/request ids to request, response and logging context."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        context = extract_request_context(request)
        request.request_id = context["request_id"]
        request.trace_id = context["trace_id"]
        request.traceparent = context["traceparent"]
        tokens = set_log_context(request_id=request.request_id, trace_id=request.trace_id)
        try:
            response = self.get_response(request)
        finally:
            reset_log_context(tokens)
        response.setdefault("X-Request-ID", request.request_id)
        response.setdefault("X-Trace-ID", request.trace_id)
        response.setdefault("traceparent", request.traceparent)
        return response


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
