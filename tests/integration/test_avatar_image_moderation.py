# pyright: reportMissingImports=false

import json
import os
import sys
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace

import django
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.core.management import call_command  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core import views  # noqa: E402
from core.avatar_image_moderation import (  # noqa: E402
    avatar_image_moderation_gate,
    run_avatar_image_moderation,
)
from core.models import Project, UserProfile  # noqa: E402
from ai_agents.management.commands.moderation_system_status import collect_moderation_system_status  # noqa: E402
from worker.ai_agents.providers.visual_safety_provider import AzureContentSafetyVisualProvider  # noqa: E402


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _image_upload(filename: str = "avatar.png", *, size: tuple[int, int] = (32, 32)) -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", size, color=(12, 90, 120)).save(buffer, format="PNG")
    return SimpleUploadedFile(filename, buffer.getvalue(), content_type="image/png")


def _save_image(path: Path, *, size: tuple[int, int] = (32, 32)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=(12, 90, 120)).save(path)
    return path


def _avatar_url(user: User) -> str:
    return f"/api/v1/users/{user.id}/avatar/"


def _enable_azure_visual_safety(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENABLE_AVATAR", True, raising=False)
    monkeypatch.setattr(settings, "AVATAR_IMAGE_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AVATAR_IMAGE_MODERATION_BLOCK_ON_REJECTION", True, raising=False)
    monkeypatch.setattr(settings, "AVATAR_IMAGE_MODERATION_REQUIRE_APPROVAL", False, raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "azure_content_safety", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_TIMEOUT_SECONDS", 20, raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_MAX_IMAGE_BYTES", 10485760, raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_ENDPOINT", "https://example.cognitiveservices.azure.com", raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_KEY", "test-secret-key", raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_API_VERSION", "2024-09-01", raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_CATEGORIES", "sexual,violence,self_harm,hate", raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_BLOCK_SEVERITY", 4, raising=False)


def _mock_avatar_processing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(views, "_composite_engine_configured", lambda: True)

    def fake_preprocess(*, storage_root, teacher_id, **_kwargs):
        processed = Path(storage_root) / "avatars" / str(teacher_id) / "processed" / "avatar_processed.png"
        _save_image(processed)
        return SimpleNamespace(
            processed_rel_path=str(processed.relative_to(Path(storage_root))).replace("\\", "/"),
            source_hash="avatar-source-hash",
            warnings=[],
        )

    def fake_validation(profile, *, storage_root, persist=True):
        profile.avatar_source_valid = True
        profile.avatar_source_validation_error = ""
        profile.avatar_source_hash = "avatar-source-hash"
        profile.avatar_source_image_hash = "avatar-image-hash"
        profile.avatar_source_reference_type = "image"
        if persist:
            profile.save(
                update_fields=[
                    "avatar_source_valid",
                    "avatar_source_validation_error",
                    "avatar_source_hash",
                    "avatar_source_image_hash",
                    "avatar_source_reference_type",
                    "updated_at",
                ]
            )
        return {
            "valid": True,
            "error": "",
            "source_hash": "avatar-source-hash",
            "image_hash": "avatar-image-hash",
            "reference_type": "image",
            "validation_current": True,
            "preview_stale": False,
        }

    monkeypatch.setattr(views, "preprocess_teacher_avatar_image", fake_preprocess)
    monkeypatch.setattr(views, "refresh_avatar_source_validation", fake_validation)


@pytest.mark.django_db
def test_avatar_image_moderation_disabled_does_not_block_upload(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "ENABLE_AVATAR", True, raising=False)
    monkeypatch.setattr(settings, "AVATAR_IMAGE_MODERATION_AUTO_ENABLED", False, raising=False)
    _mock_avatar_processing(monkeypatch, tmp_path)
    teacher = _make_teacher("avatar_disabled_teacher")

    response = _client(teacher).post(
        _avatar_url(teacher),
        {"avatar_file": _image_upload(), "avatar_consent_confirmed": "1"},
        format="multipart",
    )

    teacher.profile.refresh_from_db()
    assert response.status_code == 200
    assert teacher.profile.avatar_image_status == "ready"
    assert teacher.profile.avatar_moderation_status == "skipped"
    assert teacher.profile.avatar_moderation_summary["reason"] == "avatar_image_moderation_disabled"


@pytest.mark.django_db
def test_unsafe_avatar_image_blocks_preprocessing_when_block_flag_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path), raising=False)
    _enable_azure_visual_safety(monkeypatch)
    monkeypatch.setattr(
        AzureContentSafetyVisualProvider,
        "_submit_image",
        lambda self, *, endpoint, key, image_bytes: {"categoriesAnalysis": [{"category": "Sexual", "severity": 4}]},
    )
    monkeypatch.setattr(views, "_composite_engine_configured", lambda: True)

    def fail_preprocess(**_kwargs):
        raise AssertionError("Unsafe avatar image should not be preprocessed")

    monkeypatch.setattr(views, "preprocess_teacher_avatar_image", fail_preprocess)
    teacher = _make_teacher("avatar_unsafe_teacher")

    response = _client(teacher).post(
        _avatar_url(teacher),
        {"avatar_file": _image_upload(), "avatar_consent_confirmed": "1"},
        format="multipart",
    )

    teacher.profile.refresh_from_db()
    assert response.status_code == 400
    assert response.data["error_code"] == "avatar_image_moderation_blocked"
    assert teacher.profile.avatar_image_status == "rejected"
    assert teacher.profile.avatar_moderation_status == "rejected"
    assert teacher.profile.avatar_moderation_summary["finding_count"] == 1


@pytest.mark.django_db
def test_safe_avatar_image_is_approved_and_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path), raising=False)
    _enable_azure_visual_safety(monkeypatch)
    _mock_avatar_processing(monkeypatch, tmp_path)
    monkeypatch.setattr(
        AzureContentSafetyVisualProvider,
        "_submit_image",
        lambda self, *, endpoint, key, image_bytes: {"categoriesAnalysis": [{"category": "Violence", "severity": 0}]},
    )
    teacher = _make_teacher("avatar_safe_teacher")

    response = _client(teacher).post(
        _avatar_url(teacher),
        {"avatar_file": _image_upload(), "avatar_consent_confirmed": "1"},
        format="multipart",
    )

    teacher.profile.refresh_from_db()
    assert response.status_code == 200
    assert teacher.profile.avatar_image_status == "ready"
    assert teacher.profile.avatar_moderation_status == "approved"
    assert avatar_image_moderation_gate(teacher.profile)["blocked"] is False


@pytest.mark.django_db
def test_missing_visual_provider_config_skips_fail_open_unless_approval_required(monkeypatch, tmp_path):
    image_path = _save_image(tmp_path / "avatar.png")
    teacher = _make_teacher("avatar_missing_config_teacher")
    profile = teacher.profile
    monkeypatch.setattr(settings, "ENABLE_AVATAR", True, raising=False)
    monkeypatch.setattr(settings, "AVATAR_IMAGE_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "azure_content_safety", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_ENDPOINT", "", raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_KEY", "", raising=False)

    summary = run_avatar_image_moderation(profile, image_path, persist=True)

    profile.refresh_from_db()
    assert summary["status"] == "skipped"
    assert profile.avatar_moderation_status == "skipped"
    assert avatar_image_moderation_gate(profile)["blocked"] is False
    monkeypatch.setattr(settings, "AVATAR_IMAGE_MODERATION_REQUIRE_APPROVAL", True, raising=False)
    assert avatar_image_moderation_gate(profile)["blocked"] is True


@pytest.mark.django_db
def test_avatar_image_moderation_does_not_mutate_project_moderation_status(monkeypatch, tmp_path):
    image_path = _save_image(tmp_path / "avatar.png")
    teacher = _make_teacher("avatar_project_status_teacher")
    project = Project.objects.create(title="Avatar project status", user=teacher, moderation_status="approved")
    _enable_azure_visual_safety(monkeypatch)
    monkeypatch.setattr(
        AzureContentSafetyVisualProvider,
        "_submit_image",
        lambda self, *, endpoint, key, image_bytes: {"categoriesAnalysis": [{"category": "Violence", "severity": 4}]},
    )

    run_avatar_image_moderation(teacher.profile, image_path, persist=True)

    project.refresh_from_db()
    assert project.moderation_status == "approved"


@pytest.mark.django_db
def test_avatar_moderation_diagnostics_do_not_print_secrets(settings):
    settings.ENABLE_AVATAR = True
    settings.AVATAR_IMAGE_MODERATION_AUTO_ENABLED = True
    settings.AVATAR_IMAGE_MODERATION_BLOCK_ON_REJECTION = True
    settings.AVATAR_IMAGE_MODERATION_REQUIRE_APPROVAL = False
    settings.VISUAL_SAFETY_PROVIDER = "azure_content_safety"
    settings.VISUAL_SAFETY_CLASSIFIER_ENABLED = True
    settings.AZURE_CONTENT_SAFETY_ENABLED = True
    settings.AZURE_CONTENT_SAFETY_ENDPOINT = "https://example.cognitiveservices.azure.com"
    settings.AZURE_CONTENT_SAFETY_KEY = "avatar-secret-key"

    status = collect_moderation_system_status()
    stdout = StringIO()
    call_command("moderation_system_status", stdout=stdout)
    rendered = json.dumps(status, sort_keys=True) + stdout.getvalue()

    assert status["visual_ocr_video_providers"]["avatar_image_moderation_auto_enabled"] is True
    assert "avatar-secret-key" not in rendered
