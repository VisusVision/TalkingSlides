from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from django.conf import settings
import requests

from ..schemas import AgentFindingSchema, AgentResultSchema, FindingLocation, Modality
from .base import VisualModerationProvider
from .noop_visual_provider import NoopVisualProvider


AZURE_VISUAL_SAFETY_AGENT_SLUG = "visual_safety_azure_content_safety"
AZURE_VISUAL_SAFETY_AGENT_VERSION = "azure-content-safety:v1"

_AZURE_CATEGORY_NAMES = {
    "sexual": "Sexual",
    "violence": "Violence",
    "self_harm": "SelfHarm",
    "selfharm": "SelfHarm",
    "hate": "Hate",
}

_CATEGORY_MAP = {
    "sexual": "sexual",
    "violence": "violence",
    "selfharm": "self_harm",
    "self_harm": "self_harm",
    "hate": "hate_or_harassment",
}


class AzureContentSafetyVisualProvider(VisualModerationProvider):
    provider_name = "azure_content_safety"
    agent_slug = AZURE_VISUAL_SAFETY_AGENT_SLUG
    agent_version = AZURE_VISUAL_SAFETY_AGENT_VERSION

    def review_image(self, image_path: str | None, location: FindingLocation) -> AgentResultSchema:
        return self._review_asset(modality="image", asset_path=image_path, location=location)

    def review_frame(self, frame_path: str | None, location: FindingLocation) -> AgentResultSchema:
        return self._review_asset(modality="video_frame", asset_path=frame_path, location=location)

    def _review_asset(
        self,
        *,
        modality: Modality,
        asset_path: str | None,
        location: FindingLocation,
    ) -> AgentResultSchema:
        normalized_path = str(asset_path or "").strip()
        metadata = self._base_metadata(location=location)

        if not bool(getattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", False)):
            return self._allow_result(
                modality=modality,
                metadata={**metadata, "skipped": True, "reason": "visual_safety_classifier_disabled"},
            )
        if not bool(getattr(settings, "AZURE_CONTENT_SAFETY_ENABLED", False)):
            return self._allow_result(
                modality=modality,
                metadata={**metadata, "skipped": True, "reason": "azure_content_safety_disabled"},
            )

        endpoint = str(getattr(settings, "AZURE_CONTENT_SAFETY_ENDPOINT", "") or "").strip().rstrip("/")
        key = str(getattr(settings, "AZURE_CONTENT_SAFETY_KEY", "") or "").strip()
        if not endpoint or not key:
            return self._allow_result(
                modality=modality,
                metadata={**metadata, "skipped": True, "reason": "azure_content_safety_missing_config"},
            )

        try:
            image_bytes, file_metadata = self._read_image_bytes(normalized_path)
        except _VisualSafetySkip as exc:
            return self._allow_result(
                modality=modality,
                metadata={**metadata, "skipped": True, "reason": exc.reason, **exc.metadata},
            )

        try:
            response_json = self._submit_image(endpoint=endpoint, key=key, image_bytes=image_bytes)
            findings = _findings_from_azure_response(response_json, location=location)
            decision = _decision_for_findings(findings)
            confidence = max((finding.confidence for finding in findings), default=0.0)
            return AgentResultSchema(
                agent_slug=self.agent_slug,
                agent_version=self.agent_version,
                modality=modality,
                provider=self.provider_name,
                decision=decision,
                confidence=confidence,
                findings=findings,
                metadata={
                    **metadata,
                    **file_metadata,
                    "skipped": False,
                    "response_category_count": len(_category_rows(response_json)),
                },
            )
        except requests.Timeout:
            return self._provider_error_result(modality=modality, metadata=metadata, reason="azure_content_safety_timeout")
        except requests.RequestException as exc:
            return self._provider_error_result(
                modality=modality,
                metadata=metadata,
                reason="azure_content_safety_request_error",
                error=exc,
            )
        except (TypeError, ValueError, KeyError) as exc:
            return self._provider_error_result(
                modality=modality,
                metadata=metadata,
                reason="azure_content_safety_invalid_response",
                error=exc,
            )

    def _base_metadata(self, *, location: FindingLocation) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "classifier_enabled": bool(getattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", False)),
            "azure_enabled": bool(getattr(settings, "AZURE_CONTENT_SAFETY_ENABLED", False)),
            "endpoint_configured": bool(str(getattr(settings, "AZURE_CONTENT_SAFETY_ENDPOINT", "") or "").strip()),
            "key_configured": bool(str(getattr(settings, "AZURE_CONTENT_SAFETY_KEY", "") or "").strip()),
            "api_version": str(getattr(settings, "AZURE_CONTENT_SAFETY_API_VERSION", "2024-09-01") or "2024-09-01"),
            "categories": _configured_categories(),
            "block_severity": _block_severity(),
            "max_image_bytes": _max_image_bytes(),
            "location": location.model_dump(exclude_none=True),
        }

    def _read_image_bytes(self, image_path: str) -> tuple[bytes, dict[str, Any]]:
        if not image_path:
            raise _VisualSafetySkip("Image path is empty.", reason="missing_image_path")
        path = Path(image_path)
        if not path.is_file():
            raise _VisualSafetySkip("Image file was not found.", reason="missing_image_file")
        size = path.stat().st_size
        if size > _max_image_bytes():
            raise _VisualSafetySkip(
                "Image file is larger than the visual safety size limit.",
                reason="image_too_large",
                metadata={"file_size_bytes": size},
            )
        return path.read_bytes(), {"file_size_bytes": size}

    def _submit_image(self, *, endpoint: str, key: str, image_bytes: bytes) -> dict[str, Any]:
        api_version = str(getattr(settings, "AZURE_CONTENT_SAFETY_API_VERSION", "2024-09-01") or "2024-09-01")
        timeout_seconds = float(getattr(settings, "VISUAL_SAFETY_TIMEOUT_SECONDS", 20) or 20)
        url = f"{endpoint}/contentsafety/image:analyze"
        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/json",
        }
        payload = {
            "image": {"content": base64.b64encode(image_bytes).decode("ascii")},
            "categories": [_AZURE_CATEGORY_NAMES.get(category, category) for category in _configured_categories()],
            "outputType": "FourSeverityLevels",
        }
        response = requests.post(
            url,
            params={"api-version": api_version},
            headers=headers,
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        response_json = response.json()
        if not isinstance(response_json, dict):
            raise ValueError("Azure Content Safety response was not an object.")
        return response_json

    def _provider_error_result(
        self,
        *,
        modality: Modality,
        metadata: dict[str, Any],
        reason: str,
        error: BaseException | None = None,
    ) -> AgentResultSchema:
        error_metadata = {
            **metadata,
            "skipped": True,
            "reason": reason,
            "provider_error": True,
        }
        if error is not None:
            error_metadata["error"] = _short_error(error)
        return self._allow_result(modality=modality, metadata=error_metadata)

    def _allow_result(self, *, modality: Modality, metadata: dict[str, Any]) -> AgentResultSchema:
        return AgentResultSchema(
            agent_slug=self.agent_slug,
            agent_version=self.agent_version,
            modality=modality,
            provider=self.provider_name,
            decision="allow",
            confidence=0.0,
            findings=[],
            metadata=metadata,
        )


class _VisualSafetySkip(Exception):
    def __init__(self, message: str, *, reason: str, metadata: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.metadata = metadata or {}


def build_visual_safety_provider(provider_name: str | None = None) -> VisualModerationProvider:
    provider = str(provider_name or getattr(settings, "VISUAL_SAFETY_PROVIDER", "none") or "none").strip().lower()
    if provider == "azure_content_safety":
        return AzureContentSafetyVisualProvider()
    return NoopVisualProvider()


def visual_safety_classifier_should_run() -> bool:
    provider = str(getattr(settings, "VISUAL_SAFETY_PROVIDER", "none") or "none").strip().lower()
    return provider != "none" and bool(getattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", False))


def visual_safety_provider_status() -> dict[str, Any]:
    return {
        "visual_safety_provider": str(getattr(settings, "VISUAL_SAFETY_PROVIDER", "none") or "none").strip().lower(),
        "visual_safety_classifier_enabled": bool(getattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", False)),
        "visual_safety_timeout_seconds": getattr(settings, "VISUAL_SAFETY_TIMEOUT_SECONDS", None),
        "visual_safety_max_image_bytes": getattr(settings, "VISUAL_SAFETY_MAX_IMAGE_BYTES", None),
        "azure_content_safety_enabled": bool(getattr(settings, "AZURE_CONTENT_SAFETY_ENABLED", False)),
        "azure_content_safety_endpoint_configured": bool(
            str(getattr(settings, "AZURE_CONTENT_SAFETY_ENDPOINT", "") or "").strip()
        ),
        "azure_content_safety_key_configured": bool(
            str(getattr(settings, "AZURE_CONTENT_SAFETY_KEY", "") or "").strip()
        ),
        "azure_content_safety_api_version": str(
            getattr(settings, "AZURE_CONTENT_SAFETY_API_VERSION", "2024-09-01") or "2024-09-01"
        ),
        "azure_content_safety_categories": _configured_categories(),
        "azure_content_safety_block_severity": _block_severity(),
    }


def _findings_from_azure_response(payload: dict[str, Any], *, location: FindingLocation) -> list[AgentFindingSchema]:
    findings: list[AgentFindingSchema] = []
    for row in _category_rows(payload):
        raw_category = str(row.get("category") or "").strip()
        category_key = _category_key(raw_category)
        mapped_category = _CATEGORY_MAP.get(category_key, "unknown")
        severity_value = _severity_value(row)
        if severity_value <= 0:
            continue
        severity = _severity_label(severity_value)
        decision = "block" if severity_value >= _block_severity() else "warn"
        confidence = min(1.0, max(0.1, 0.55 + (severity_value / 10.0)))
        if decision == "block":
            confidence = max(confidence, 0.9)
        findings.append(
            AgentFindingSchema(
                category=mapped_category,  # type: ignore[arg-type]
                severity=severity,
                confidence=confidence,
                decision=decision,
                location=location,
                user_message="This image may contain unsafe visual content and should be reviewed.",
                admin_message=(
                    f"Azure Content Safety category={raw_category or 'unknown'} severity={severity_value}."
                ),
                evidence_excerpt=f"{raw_category or 'unknown'} severity {severity_value}",
            )
        )
    return findings


def _category_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("categoriesAnalysis") or payload.get("categories_analysis") or payload.get("categoryAnalysis")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    if isinstance(rows, dict):
        return [_row_from_mapping(category, value) for category, value in rows.items()]
    direct_rows = []
    for key in ("sexual", "violence", "selfHarm", "self_harm", "hate", "Sexual", "Violence", "SelfHarm", "Hate"):
        if key in payload:
            direct_rows.append(_row_from_mapping(key, payload[key]))
    return direct_rows


def _row_from_mapping(category: str, value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"category": category, **value}
    return {"category": category, "severity": value}


def _severity_value(row: dict[str, Any]) -> int:
    value = (
        row.get("severity")
        if row.get("severity") is not None
        else row.get("severityLevel")
        if row.get("severityLevel") is not None
        else row.get("severity_level")
    )
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _severity_label(severity_value: int):
    if severity_value >= 6:
        return "critical"
    if severity_value >= 4:
        return "high"
    if severity_value >= 2:
        return "medium"
    return "low"


def _decision_for_findings(findings: list[AgentFindingSchema]):
    if any(finding.decision == "block" for finding in findings):
        return "block"
    if any(finding.decision == "needs_admin_review" for finding in findings):
        return "needs_admin_review"
    if any(finding.decision == "warn" for finding in findings):
        return "warn"
    return "allow"


def _configured_categories() -> list[str]:
    raw = str(getattr(settings, "AZURE_CONTENT_SAFETY_CATEGORIES", "sexual,violence,self_harm,hate") or "")
    categories = [_category_key(item) for item in raw.split(",") if item.strip()]
    return categories or ["sexual", "violence", "self_harm", "hate"]


def _category_key(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _block_severity() -> int:
    try:
        return max(0, int(getattr(settings, "AZURE_CONTENT_SAFETY_BLOCK_SEVERITY", 4) or 4))
    except (TypeError, ValueError):
        return 4


def _max_image_bytes() -> int:
    try:
        return max(1, int(getattr(settings, "VISUAL_SAFETY_MAX_IMAGE_BYTES", 10485760) or 10485760))
    except (TypeError, ValueError):
        return 10485760


def _short_error(exc: BaseException, limit: int = 240) -> str:
    return f"{exc.__class__.__name__}: {exc}"[:limit]
