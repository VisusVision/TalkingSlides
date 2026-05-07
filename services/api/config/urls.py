"""Root URL configuration for AI_ACADEMY API."""

from django.contrib import admin
from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.urls import path, include


def health(request):
    """Backward-compatible simple health endpoint."""
    return HttpResponse("ok", content_type="text/plain")


def live(request):
    """Liveness probe: process is up and can serve requests."""
    return JsonResponse({"status": "live"})


def ready(request):
    """Readiness probe: checks critical dependencies used by API requests."""
    checks: dict[str, dict[str, str | bool]] = {
        "database": {"ok": True, "detail": "ok"},
        "cache": {"ok": True, "detail": "ok"},
    }
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001
        checks["database"] = {"ok": False, "detail": f"{exc.__class__.__name__}"}
    try:
        sentinel_key = "ready:probe"
        cache.set(sentinel_key, "1", timeout=15)
        if str(cache.get(sentinel_key) or "") != "1":
            checks["cache"] = {"ok": False, "detail": "cache_read_mismatch"}
    except Exception as exc:  # noqa: BLE001
        checks["cache"] = {"ok": False, "detail": f"{exc.__class__.__name__}"}

    all_ok = all(bool(item.get("ok")) for item in checks.values())
    status_code = 200 if all_ok else 503
    return JsonResponse({"status": "ready" if all_ok else "not_ready", "checks": checks}, status=status_code)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("live/", live, name="live"),
    path("ready/", ready, name="ready"),
    path("api/v1/", include("core.urls")),
]

