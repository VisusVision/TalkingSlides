from django.urls import path

from core.digital_twin_views import (
    DigitalTwinCollectionView,
    DigitalTwinConsentDecisionView,
    DigitalTwinConsentSessionView,
    DigitalTwinDetailView,
    DigitalTwinRenderCollectionView,
    DigitalTwinRenderDetailView,
    DigitalTwinRevokeView,
    DigitalTwinTrainingRunView,
)


urlpatterns = [
    path("", DigitalTwinCollectionView.as_view(), name="digital-twin-list"),
    path("<uuid:twin_id>/", DigitalTwinDetailView.as_view(), name="digital-twin-detail"),
    path("<uuid:twin_id>/consent-sessions/", DigitalTwinConsentSessionView.as_view(), name="digital-twin-consent-create"),
    path("<uuid:twin_id>/training-runs/", DigitalTwinTrainingRunView.as_view(), name="digital-twin-training-create"),
    path(
        "<uuid:twin_id>/consent-sessions/<uuid:session_id>/decision/",
        DigitalTwinConsentDecisionView.as_view(),
        name="digital-twin-consent-decision",
    ),
    path("<uuid:twin_id>/renders/", DigitalTwinRenderCollectionView.as_view(), name="digital-twin-render-create"),
    path(
        "<uuid:twin_id>/renders/<uuid:render_id>/",
        DigitalTwinRenderDetailView.as_view(),
        name="digital-twin-render-detail",
    ),
    path("<uuid:twin_id>/revoke/", DigitalTwinRevokeView.as_view(), name="digital-twin-revoke"),
]
