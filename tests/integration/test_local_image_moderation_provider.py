# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path

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

from django.contrib.auth.models import User  # noqa: E402

from core.models import Project, UserProfile  # noqa: E402
from worker.ai_agents.providers.local_image_rules_provider import LocalImageRulesProvider  # noqa: E402
from worker.ai_agents.providers.noop_visual_provider import NoopVisualProvider  # noqa: E402
from worker.ai_agents.schemas import FindingLocation  # noqa: E402
from worker.ai_agents.visual_moderation import SlideImageAsset, VisualModerationAgent  # noqa: E402


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str) -> Project:
    return Project.objects.create(
        title="Local image moderation lesson",
        user=_make_teacher(username),
        status="ready",
    )


def _save_image(path: Path, *, size: tuple[int, int] = (16, 16), mode: str = "RGB") -> Path:
    Image.new(mode, size, color=(20, 90, 140)).save(path)
    return path


def test_missing_image_path_returns_allow_without_crash(tmp_path):
    missing_path = tmp_path / "missing.png"

    result = LocalImageRulesProvider().review_image(
        str(missing_path),
        FindingLocation(project_id=1, asset_type="cover", image_path=str(missing_path)),
    )

    assert result.decision == "allow"
    assert result.findings == []
    assert result.metadata["missing"] is True


def test_corrupt_image_returns_review_needed_without_crash(tmp_path):
    corrupt_path = tmp_path / "corrupt.png"
    corrupt_path.write_bytes(b"not a real image")

    result = LocalImageRulesProvider().review_image(
        str(corrupt_path),
        FindingLocation(project_id=1, asset_type="cover", image_path=str(corrupt_path)),
    )

    assert result.decision in {"needs_admin_review", "warn"}
    assert result.findings
    assert result.findings[0].category == "graphic_content"
    assert result.metadata["error"] in {"UnidentifiedImageError", "OSError"}


def test_valid_small_generated_image_returns_allow(tmp_path):
    image_path = _save_image(tmp_path / "valid.png")

    result = LocalImageRulesProvider().review_image(
        str(image_path),
        FindingLocation(project_id=1, asset_type="cover", image_path=str(image_path)),
    )

    assert result.decision == "allow"
    assert result.findings == []
    assert result.metadata["width"] == 16
    assert result.metadata["height"] == 16
    assert result.metadata["format"] == "PNG"


def test_large_dimension_image_is_flagged_without_large_allocation(tmp_path):
    image_path = _save_image(tmp_path / "too-large.png", size=(20, 20))
    provider = LocalImageRulesProvider(max_width=10, max_height=10, max_pixels=100)

    result = provider.review_image(
        str(image_path),
        FindingLocation(project_id=1, asset_type="slide_image", image_path=str(image_path)),
    )

    assert result.decision == "needs_admin_review"
    assert result.findings[0].severity == "medium"
    assert result.metadata["width"] == 20
    assert result.metadata["height"] == 20


def test_slide_scan_preserves_slide_order(tmp_path):
    image_path = _save_image(tmp_path / "slide.png")

    result = VisualModerationAgent(provider=LocalImageRulesProvider()).scan_slide_image(
        project_id=123,
        image_path=str(image_path),
        slide_order=4,
        page_key="slide-5",
        ui_anchor="manual-slide-4-image",
    )

    location = result.metadata["location"]
    assert result.decision == "allow"
    assert location["asset_type"] == "slide_image"
    assert location["slide_order"] == 4
    assert location["page_key"] == "slide-5"
    assert location["ui_anchor"] == "manual-slide-4-image"


@pytest.mark.django_db
def test_cover_scan_sets_asset_type_cover(tmp_path):
    project = _make_project("local_image_cover_teacher")
    image_path = _save_image(tmp_path / "cover.png")

    result = VisualModerationAgent(provider=LocalImageRulesProvider()).scan_cover_image(
        project,
        image_path=str(image_path),
    )

    assert result.decision == "allow"
    assert result.metadata["location"]["project_id"] == project.id
    assert result.metadata["location"]["asset_type"] == "cover"


@pytest.mark.django_db
def test_slide_images_scan_accepts_explicit_assets(tmp_path):
    project = _make_project("local_image_slide_assets_teacher")
    image_path = _save_image(tmp_path / "asset-slide.png")

    result = VisualModerationAgent(provider=LocalImageRulesProvider()).scan_slide_images(
        project,
        slide_assets=[
            SlideImageAsset(
                image_path=str(image_path),
                slide_order=2,
                page_key="asset-slide-3",
                ui_anchor="asset-slide-2-image",
            )
        ],
    )

    assert result.decision == "allow"
    assert result.metadata["noop"] is False
    assert result.metadata["scanned_asset_count"] == 1


def test_noop_provider_behavior_remains_unchanged():
    result = NoopVisualProvider().review_image(
        "",
        FindingLocation(project_id=456, asset_type="cover", image_path=""),
    )

    assert result.decision == "allow"
    assert result.findings == []
    assert result.metadata["noop"] is True
    assert result.metadata["asset_missing"] is True
