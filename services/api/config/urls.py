"""Root URL configuration for AI_ACADEMY API."""

from django.contrib import admin
from django.http import HttpResponse
from django.urls import path, include


def health(request):
    """Lightweight health-check endpoint used by Docker / load balancers."""
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("api/v1/", include("core.urls")),
]

