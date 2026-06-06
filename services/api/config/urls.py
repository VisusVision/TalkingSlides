"""Root URL configuration for AI_ACADEMY API."""

from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.urls import path, include
from core.perf_metrics import prometheus_metrics_response


def health(request):
    """Lightweight health-check endpoint used by Docker / load balancers."""
    return HttpResponse("ok", content_type="text/plain")


def ready(request):
    """Lightweight API readiness endpoint for production load balancers."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("api/v1/ready/", ready, name="api-ready"),
    path("api/v1/system/metrics/prometheus/", prometheus_metrics_response, name="prometheus-metrics"),
    path("api/v1/", include("core.urls")),
]
