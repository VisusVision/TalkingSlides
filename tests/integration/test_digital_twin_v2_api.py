from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from core.models import DigitalTwin, DigitalTwinConsentSession, DigitalTwinRender, DigitalTwinTrainingRun


pytestmark = pytest.mark.django_db


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_create_is_owner_scoped_and_idempotent():
    owner = User.objects.create_user(username="owner")
    stranger = User.objects.create_user(username="stranger")
    client = _client(owner)
    first = client.post(
        "/api/v2/digital-twins/",
        {"display_name": "Engin Twin", "capabilities": ["portrait", "voice", "motion_style"]},
        format="json",
        HTTP_IDEMPOTENCY_KEY="create-1",
    )
    second = client.post(
        "/api/v2/digital-twins/",
        {"display_name": "ignored"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="create-1",
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.data["id"] == second.data["id"]
    assert _client(stranger).get(f"/api/v2/digital-twins/{first.data['id']}/").status_code == 404


@override_settings(STORAGE_ROOT="storage_local/test-digital-twin")
def test_training_waits_for_explicit_consent_and_admin_approval(tmp_path, settings):
    settings.STORAGE_ROOT = str(tmp_path)
    owner = User.objects.create_user(username="owner2")
    admin = User.objects.create_superuser(username="admin", email="a@example.com", password="x")
    twin = DigitalTwin.objects.create(owner=owner, display_name="Twin")
    owner_client = _client(owner)
    challenge = owner_client.post(f"/api/v2/digital-twins/{twin.id}/consent-sessions/", {}, format="json")
    assert challenge.status_code == 201
    assert "challenge_nonce_hash" not in challenge.data

    with patch("core.digital_twin_views._celery.send_task", return_value=SimpleNamespace(id="verify-task")) as verify_dispatch:
        upload = owner_client.post(
            f"/api/v2/digital-twins/{twin.id}/training-runs/",
            {
                "consent_session_id": challenge.data["id"],
                "performance_video": SimpleUploadedFile("performance.mp4", b"performance-video", content_type="video/mp4"),
                "consent_video": SimpleUploadedFile("consent.mp4", b"consent-video", content_type="video/mp4"),
            },
            format="multipart",
        )
    assert upload.status_code == 202
    run = DigitalTwinTrainingRun.objects.get(pk=upload.data["id"])
    assert run.status == "pending_consent"
    assert run.stage == "verification_queued"
    assert run.task_id == "verify-task"
    assert run.consent_session.status == "pending"
    verify_dispatch.assert_called_once()

    with patch("core.digital_twin_views._celery.send_task", return_value=SimpleNamespace(id="task-1")) as dispatch:
        decision = _client(admin).post(
            f"/api/v2/digital-twins/{twin.id}/consent-sessions/{challenge.data['id']}/decision/",
            {"decision": "approved", "reason": "identity and liveness reviewed"},
            format="json",
        )
    assert decision.status_code == 200
    run.refresh_from_db()
    twin.refresh_from_db()
    assert run.status == "queued"
    assert run.task_id == "task-1"
    assert twin.consent_status == "approved"
    dispatch.assert_called_once()


def test_non_admin_cannot_approve_consent():
    owner = User.objects.create_user(username="owner3")
    twin = DigitalTwin.objects.create(owner=owner, display_name="Twin", status="verifying_consent")
    session = DigitalTwinConsentSession.objects.create(
        twin=twin,
        challenge_text="challenge",
        challenge_nonce_hash="a" * 64,
        expires_at="2099-01-01T00:00:00Z",
    )
    response = _client(owner).post(
        f"/api/v2/digital-twins/{twin.id}/consent-sessions/{session.id}/decision/",
        {"decision": "approved"},
        format="json",
    )
    assert response.status_code == 403


def test_render_requires_ready_approved_twin_and_revoke_cancels_queued_render():
    owner = User.objects.create_user(username="owner4")
    twin = DigitalTwin.objects.create(owner=owner, display_name="Twin", status="draft")
    client = _client(owner)
    blocked = client.post(f"/api/v2/digital-twins/{twin.id}/renders/", {"script": "Merhaba"}, format="json")
    assert blocked.status_code == 409

    twin.status = "ready"
    twin.consent_status = "approved"
    twin.save(update_fields=["status", "consent_status"])
    with patch("core.digital_twin_views._celery.send_task", return_value=SimpleNamespace(id="render-task")):
        queued = client.post(f"/api/v2/digital-twins/{twin.id}/renders/", {"script": "Merhaba"}, format="json")
    assert queued.status_code == 202
    render = DigitalTwinRender.objects.get(pk=queued.data["id"])
    assert render.watermark_required is True

    revoked = client.post(f"/api/v2/digital-twins/{twin.id}/revoke/", {}, format="json")
    assert revoked.status_code == 200
    render.refresh_from_db()
    twin.refresh_from_db()
    assert twin.status == "revoked"
    assert render.status == "cancelled"
