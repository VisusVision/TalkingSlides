"""Security headers for lecture playback pages and media responses."""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseNotAllowed

from core.log_context import reset_log_context, set_log_context
from core.trace import extract_request_context

logger = logging.getLogger(__name__)

CSP_REPORT_ONLY_HEADER = "Content-Security-Policy-Report-Only"
CSP_REPORT_PATH = "/api/v1/security/csp-report/"


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
        if request.path == CSP_REPORT_PATH:
            return self._with_security_headers(self._handle_csp_report(request), request)

        response = self.get_response(request)
        return self._with_security_headers(response, request)

    def _with_security_headers(self, response, request):
        is_stream_response = request.path.startswith("/api/v1/stream/")
        response.setdefault(
            "Permissions-Policy",
            "display-capture=(), camera=(), microphone=(self), geolocation=(), payment=(), usb=(), picture-in-picture=()",
        )
        response.setdefault("X-Frame-Options", "DENY")
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("Referrer-Policy", "same-origin")
        response.setdefault("Cross-Origin-Resource-Policy", "cross-origin" if is_stream_response else "same-site")
        if getattr(settings, "CSP_REPORT_ONLY_ENABLED", False):
            policy = str(getattr(settings, "CSP_REPORT_ONLY_POLICY", "") or "").strip()
            if policy:
                response.setdefault(CSP_REPORT_ONLY_HEADER, policy)
        return response

    def _handle_csp_report(self, request):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        max_bytes = int(getattr(settings, "CSP_REPORT_BODY_MAX_BYTES", 16384) or 16384)
        try:
            content_length = int(request.META.get("CONTENT_LENGTH") or "0")
        except (TypeError, ValueError):
            content_length = 0
        if content_length > max_bytes:
            return HttpResponse(status=413)

        try:
            raw_body = request.body
        except Exception:
            raw_body = b""
        if len(raw_body) > max_bytes:
            return HttpResponse(status=413)

        if raw_body:
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict):
                report = payload.get("csp-report") if isinstance(payload.get("csp-report"), dict) else payload
                logger.info(
                    "CSP report received: keys=%s",
                    sorted(str(key)[:64] for key in report.keys()) if isinstance(report, dict) else [],
                )
            else:
                logger.info("CSP report received: invalid_json=true")

        return HttpResponse(status=204)
