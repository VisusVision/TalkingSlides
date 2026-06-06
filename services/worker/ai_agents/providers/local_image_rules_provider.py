from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..schemas import AgentFindingSchema, AgentResultSchema, FindingLocation, Modality
from .base import VisualModerationProvider


LOCAL_IMAGE_AGENT_SLUG = "visual_moderation_local_image_rules"
LOCAL_IMAGE_AGENT_VERSION = "local-image-rules:v1"

SUPPORTED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP"}
SUPPORTED_IMAGE_MODES = {"RGB", "RGBA", "L", "LA", "P", "CMYK"}


class LocalImageRulesProvider(VisualModerationProvider):
    provider_name = "local_image_rules"
    agent_slug = LOCAL_IMAGE_AGENT_SLUG
    agent_version = LOCAL_IMAGE_AGENT_VERSION

    def __init__(
        self,
        *,
        max_width: int = 12000,
        max_height: int = 12000,
        max_pixels: int = 80_000_000,
        min_width: int = 2,
        min_height: int = 2,
    ) -> None:
        self.max_width = int(max_width)
        self.max_height = int(max_height)
        self.max_pixels = int(max_pixels)
        self.min_width = int(min_width)
        self.min_height = int(min_height)

    def review_image(self, image_path: str | None, location: FindingLocation) -> AgentResultSchema:
        return self._review_asset(
            modality="image",
            asset_path=image_path,
            location=location,
        )

    def review_frame(self, frame_path: str | None, location: FindingLocation) -> AgentResultSchema:
        return self._review_asset(
            modality="video_frame",
            asset_path=frame_path,
            location=location,
        )

    def _review_asset(
        self,
        *,
        modality: Modality,
        asset_path: str | None,
        location: FindingLocation,
    ) -> AgentResultSchema:
        normalized_path = str(asset_path or "").strip()
        metadata: dict[str, Any] = {
            "provider": self.provider_name,
            "missing": False,
            "location": location.model_dump(exclude_none=True),
        }
        if not normalized_path or not Path(normalized_path).is_file():
            metadata["missing"] = True
            return self._result(
                modality=modality,
                decision="allow",
                confidence=0.0,
                findings=[],
                metadata=metadata,
            )

        metadata["file_size_bytes"] = _safe_file_size(normalized_path)
        try:
            with Image.open(normalized_path) as image:
                metadata.update(
                    {
                        "width": int(image.width),
                        "height": int(image.height),
                        "format": str(image.format or "unknown"),
                        "mode": str(image.mode or "unknown"),
                    }
                )
                findings = self._findings_for_image(image=image, location=location)
                if findings:
                    return self._result(
                        modality=modality,
                        decision=_decision_for_findings(findings),
                        confidence=max(finding.confidence for finding in findings),
                        findings=findings,
                        metadata=metadata,
                    )
                try:
                    image.verify()
                except Exception as exc:  # noqa: BLE001
                    finding = _finding(
                        location=location,
                        decision="needs_admin_review",
                        severity="medium",
                        confidence=0.65,
                        user_message="This image could not be fully validated and should be reviewed.",
                        admin_message=f"Pillow opened the image header but verify() failed: {exc.__class__.__name__}.",
                    )
                    return self._result(
                        modality=modality,
                        decision="needs_admin_review",
                        confidence=finding.confidence,
                        findings=[finding],
                        metadata=metadata,
                    )
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            metadata["error"] = exc.__class__.__name__
            finding = _finding(
                location=location,
                decision="needs_admin_review",
                severity="medium",
                confidence=0.7,
                user_message="This image could not be read and should be reviewed.",
                admin_message=f"Pillow could not identify or read the image: {exc.__class__.__name__}.",
            )
            return self._result(
                modality=modality,
                decision="needs_admin_review",
                confidence=finding.confidence,
                findings=[finding],
                metadata=metadata,
            )

        return self._result(
            modality=modality,
            decision="allow",
            confidence=0.0,
            findings=[],
            metadata=metadata,
        )

    def _findings_for_image(self, *, image: Image.Image, location: FindingLocation) -> list[AgentFindingSchema]:
        findings: list[AgentFindingSchema] = []
        width = int(image.width)
        height = int(image.height)
        pixels = width * height
        if width > self.max_width or height > self.max_height or pixels > self.max_pixels:
            findings.append(
                _finding(
                    location=location,
                    decision="needs_admin_review",
                    severity="medium",
                    confidence=0.75,
                    user_message="This image is unusually large and should be reviewed before publishing.",
                    admin_message=(
                        f"Image dimensions are {width}x{height} pixels, exceeding configured local safety limits."
                    ),
                )
            )
        if width < self.min_width or height < self.min_height:
            findings.append(
                _finding(
                    location=location,
                    decision="warn",
                    severity="low",
                    confidence=0.45,
                    user_message="This image is unusually small and may not display correctly.",
                    admin_message=f"Image dimensions are {width}x{height} pixels.",
                )
            )
        if str(image.format or "").upper() not in SUPPORTED_IMAGE_FORMATS:
            findings.append(
                _finding(
                    location=location,
                    decision="warn",
                    severity="low",
                    confidence=0.4,
                    user_message="This image format may not be supported everywhere.",
                    admin_message=f"Image format was {image.format or 'unknown'}.",
                )
            )
        if str(image.mode or "") not in SUPPORTED_IMAGE_MODES:
            findings.append(
                _finding(
                    location=location,
                    decision="warn",
                    severity="low",
                    confidence=0.4,
                    user_message="This image color mode may not be supported everywhere.",
                    admin_message=f"Image mode was {image.mode or 'unknown'}.",
                )
            )
        return findings

    def _result(
        self,
        *,
        modality: Modality,
        decision: str,
        confidence: float,
        findings: list[AgentFindingSchema],
        metadata: dict[str, Any],
    ) -> AgentResultSchema:
        return AgentResultSchema(
            agent_slug=LOCAL_IMAGE_AGENT_SLUG,
            agent_version=LOCAL_IMAGE_AGENT_VERSION,
            modality=modality,
            provider=self.provider_name,
            decision=decision,  # type: ignore[arg-type]
            confidence=confidence,
            findings=findings,
            metadata=metadata,
        )


def _finding(
    *,
    location: FindingLocation,
    decision: str,
    severity: str,
    confidence: float,
    user_message: str,
    admin_message: str,
) -> AgentFindingSchema:
    return AgentFindingSchema(
        category="graphic_content",
        severity=severity,  # type: ignore[arg-type]
        confidence=confidence,
        decision=decision,  # type: ignore[arg-type]
        location=location,
        user_message=user_message,
        admin_message=admin_message,
    )


def _decision_for_findings(findings: list[AgentFindingSchema]) -> str:
    if any(finding.decision == "needs_admin_review" for finding in findings):
        return "needs_admin_review"
    if any(finding.decision == "block" for finding in findings):
        return "block"
    if any(finding.decision == "warn" for finding in findings):
        return "warn"
    return "allow"


def _safe_file_size(path: str) -> int | None:
    try:
        return Path(path).stat().st_size
    except OSError:
        return None
