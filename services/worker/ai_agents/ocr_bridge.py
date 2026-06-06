from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from .providers.base import OCRProvider
from .schemas import FindingLocation, VisualAssetType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OCRTextResult:
    text: str
    location: FindingLocation
    provider: str = "noop_ocr"
    success: bool = True
    error_message: str = ""
    image_path: str = ""
    asset_type: VisualAssetType | None = None
    slide_order: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class OCRBridge:
    def __init__(self, provider: OCRProvider | None = None) -> None:
        if provider is None:
            provider = build_ocr_provider()
        self.provider = provider
        self.provider_name = provider.provider_name

    def extract_text(
        self,
        image_path: str | None = "",
        location: FindingLocation | None = None,
    ) -> str:
        return self.extract(image_path=image_path, location=location).text

    def extract(
        self,
        image_path: str | None = "",
        location: FindingLocation | None = None,
        *,
        asset_type: VisualAssetType = "ocr_text",
        slide_order: int | None = None,
        project_id: int | None = None,
        ui_anchor: str | None = None,
    ) -> OCRTextResult:
        resolved_location = location or FindingLocation(
            project_id=project_id,
            asset_type=asset_type,
            image_path=str(image_path or ""),
            slide_order=slide_order,
            ui_anchor=ui_anchor,
        )
        return self.provider.extract(image_path, resolved_location)


def build_ocr_provider(provider_name: str | None = None) -> OCRProvider:
    normalized = str(provider_name or _settings_value("OCR_MODERATION_PROVIDER", "noop") or "noop").strip().lower()
    if normalized in {"", "none", "noop"}:
        from .providers.noop_ocr_provider import NoopOCRProvider

        return NoopOCRProvider()
    if normalized == "azure":
        from .providers.azure_ocr_provider import AzureOCRProvider

        return AzureOCRProvider()

    logger.warning("Unknown OCR moderation provider=%s; falling back to noop", normalized)
    from .providers.noop_ocr_provider import NoopOCRProvider

    return NoopOCRProvider()


def _settings_value(name: str, default: Any) -> Any:
    try:
        from django.conf import settings

        return getattr(settings, name, default)
    except Exception:
        return default
