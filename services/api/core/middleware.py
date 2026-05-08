"""Security headers for lecture playback pages and media responses."""

from __future__ import annotations


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