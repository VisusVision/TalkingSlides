from __future__ import annotations

from pathlib import Path

from ..ocr_bridge import OCRTextResult
from ..schemas import FindingLocation
from .base import OCRProvider


class NoopOCRProvider(OCRProvider):
    provider_name = "noop_ocr"

    def extract(self, image_path: str | None, location: FindingLocation) -> OCRTextResult:
        normalized_path = str(image_path or "").strip()
        return OCRTextResult(
            text="",
            location=location,
            provider=self.provider_name,
            success=True,
            error_message="",
            image_path=normalized_path,
            asset_type=location.asset_type,
            slide_order=location.slide_order,
            metadata={
                "noop": True,
                "asset_missing": not bool(normalized_path) or not Path(normalized_path).is_file(),
                "location": location.model_dump(exclude_none=True),
            },
        )
