from __future__ import annotations

from typing import Any

from django.conf import settings
import requests

from ..policy_engine import PolicyEngine
from ..schemas import AgentFindingSchema, AgentResultSchema, FindingLocation
from .base import TextModerationProvider


AZURE_TEXT_SAFETY_AGENT_SLUG = "text_moderation_azure_content_safety"
AZURE_TEXT_SAFETY_AGENT_VERSION = "azure-content-safety-text:v1"

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


class AzureContentSafetyTextProvider(TextModerationProvider):
    provider_name = "azure_content_safety"
    agent_slug = AZURE_TEXT_SAFETY_AGENT_SLUG
    agent_version = AZURE_TEXT_SAFETY_AGENT_VERSION

    def scan_text(self, text: str, location: FindingLocation) -> list[AgentFindingSchema]:
        return self.review_text(text, location).findings

    def review_text(self, text: str, location: FindingLocation) -> AgentResultSchema:
        clean_text = str(text or "").strip()
        metadata = self._base_metadata(location=location)
        if not clean_text:
            return self._allow_result(metadata={**metadata, "skipped": True, "reason": "empty_text"})
        if not bool(getattr(settings, "TEXT_SAFETY_CLASSIFIER_ENABLED", False)):
            return self._provider_unavailable_result(
                location=location,
                metadata={**metadata, "skipped": True},
                reason="text_safety_classifier_disabled",
            )
        if not bool(getattr(settings, "AZURE_CONTENT_SAFETY_ENABLED", False)):
            return self._provider_unavailable_result(
                location=location,
                metadata={**metadata, "skipped": True},
                reason="azure_content_safety_disabled",
            )

        endpoint = str(getattr(settings, "AZURE_CONTENT_SAFETY_ENDPOINT", "") or "").strip().rstrip("/")
        key = str(getattr(settings, "AZURE_CONTENT_SAFETY_KEY", "") or "").strip()
        if not endpoint or not key:
            return self._provider_unavailable_result(
                location=location,
                metadata={**metadata, "skipped": True},
                reason="azure_content_safety_missing_config",
            )

        try:
            response_json = self._submit_text(endpoint=endpoint, key=key, text=clean_text)
            rows = _category_rows(response_json)
            if not rows:
                raise ValueError("Azure Content Safety text response did not include category analysis.")
            findings = _findings_from_azure_response(rows, text=clean_text, location=location)
            decision = _decision_for_findings(findings)
            confidence = max((finding.confidence for finding in findings), default=0.0)
            return AgentResultSchema(
                agent_slug=self.agent_slug,
                agent_version=self.agent_version,
                modality="text",
                provider=self.provider_name,
                decision=decision,
                confidence=confidence,
                findings=findings,
                metadata={
                    **metadata,
                    "skipped": False,
                    "provider_error": False,
                    "response_category_count": len(rows),
                },
            )
        except requests.Timeout:
            return self._provider_unavailable_result(
                location=location,
                metadata=metadata,
                reason="azure_content_safety_timeout",
            )
        except requests.RequestException as exc:
            return self._provider_unavailable_result(
                location=location,
                metadata=metadata,
                reason="azure_content_safety_request_error",
                error=exc,
            )
        except (TypeError, ValueError, KeyError) as exc:
            return self._provider_unavailable_result(
                location=location,
                metadata=metadata,
                reason="azure_content_safety_invalid_response",
                error=exc,
            )

    def _submit_text(self, *, endpoint: str, key: str, text: str) -> dict[str, Any]:
        api_version = str(getattr(settings, "AZURE_CONTENT_SAFETY_API_VERSION", "2024-09-01") or "2024-09-01")
        timeout_seconds = _timeout_seconds()
        url = f"{endpoint}/contentsafety/text:analyze"
        response = requests.post(
            url,
            params={"api-version": api_version},
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "categories": [_AZURE_CATEGORY_NAMES.get(category, category) for category in _configured_categories()],
                "outputType": "FourSeverityLevels",
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        response_json = response.json()
        if not isinstance(response_json, dict):
            raise ValueError("Azure Content Safety text response was not an object.")
        return response_json

    def _base_metadata(self, *, location: FindingLocation) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "classifier_enabled": bool(getattr(settings, "TEXT_SAFETY_CLASSIFIER_ENABLED", False)),
            "azure_enabled": bool(getattr(settings, "AZURE_CONTENT_SAFETY_ENABLED", False)),
            "endpoint_configured": bool(str(getattr(settings, "AZURE_CONTENT_SAFETY_ENDPOINT", "") or "").strip()),
            "key_configured": bool(str(getattr(settings, "AZURE_CONTENT_SAFETY_KEY", "") or "").strip()),
            "api_version": str(getattr(settings, "AZURE_CONTENT_SAFETY_API_VERSION", "2024-09-01") or "2024-09-01"),
            "categories": _configured_categories(),
            "block_severity": _block_severity(),
            "location": location.model_dump(exclude_none=True),
        }

    def _provider_unavailable_result(
        self,
        *,
        location: FindingLocation,
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
        return AgentResultSchema(
            agent_slug=self.agent_slug,
            agent_version=self.agent_version,
            modality="text",
            provider=self.provider_name,
            decision="needs_admin_review",
            confidence=0.0,
            findings=[],
            metadata=error_metadata,
        )

    def _allow_result(self, *, metadata: dict[str, Any]) -> AgentResultSchema:
        return AgentResultSchema(
            agent_slug=self.agent_slug,
            agent_version=self.agent_version,
            modality="text",
            provider=self.provider_name,
            decision="allow",
            confidence=0.0,
            findings=[],
            metadata=metadata,
        )


def build_text_safety_provider(provider_name: str | None = None) -> AzureContentSafetyTextProvider | None:
    provider = str(provider_name or getattr(settings, "TEXT_SAFETY_PROVIDER", "local_rules") or "local_rules")
    provider = provider.strip().lower()
    if provider == "azure_content_safety" and bool(getattr(settings, "TEXT_SAFETY_CLASSIFIER_ENABLED", False)):
        return AzureContentSafetyTextProvider()
    return None


def text_safety_provider_status() -> dict[str, Any]:
    return {
        "text_safety_provider": str(getattr(settings, "TEXT_SAFETY_PROVIDER", "local_rules") or "local_rules")
        .strip()
        .lower(),
        "text_safety_classifier_enabled": bool(getattr(settings, "TEXT_SAFETY_CLASSIFIER_ENABLED", False)),
        "text_safety_timeout_seconds": getattr(settings, "TEXT_SAFETY_TIMEOUT_SECONDS", None),
        "text_safety_categories": _configured_categories(),
        "text_safety_block_severity": _block_severity(),
        "text_safety_fallback_provider": str(getattr(settings, "TEXT_SAFETY_FALLBACK_PROVIDER", "local_rules") or "")
        .strip()
        .lower(),
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
    }


def _findings_from_azure_response(
    rows: list[dict[str, Any]],
    *,
    text: str,
    location: FindingLocation,
) -> list[AgentFindingSchema]:
    findings: list[AgentFindingSchema] = []
    for row in rows:
        raw_category = str(row.get("category") or "").strip()
        category_key = _category_key(raw_category)
        mapped_category = _CATEGORY_MAP.get(category_key, "unknown")
        severity_value = _severity_value(row)
        if severity_value <= 0:
            continue
        severity = _severity_label(severity_value)
        decision = "block" if severity_value >= _block_severity() else "needs_admin_review"
        confidence = min(1.0, max(0.1, 0.55 + (severity_value / 10.0)))
        if decision == "block":
            confidence = max(confidence, PolicyEngine.block_confidence_threshold)
        findings.append(
            AgentFindingSchema(
                category=mapped_category,  # type: ignore[arg-type]
                severity=severity,
                confidence=confidence,
                decision=decision,
                location=location,
                user_message=(
                    "This text may need admin review before publishing."
                    if decision == "needs_admin_review"
                    else "This text contains unsafe content. Please revise it before rerendering or publishing."
                ),
                admin_message=f"Azure Content Safety text category={raw_category or 'unknown'} severity={severity_value}.",
                evidence_excerpt=_short_excerpt(text),
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
    raw = str(getattr(settings, "TEXT_SAFETY_CATEGORIES", "sexual,violence,self_harm,hate") or "")
    categories = [_category_key(item) for item in raw.split(",") if item.strip()]
    return categories or ["sexual", "violence", "self_harm", "hate"]


def _category_key(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _block_severity() -> int:
    try:
        return max(0, int(getattr(settings, "TEXT_SAFETY_BLOCK_SEVERITY", 4) or 4))
    except (TypeError, ValueError):
        return 4


def _timeout_seconds() -> float:
    try:
        return max(0.1, float(getattr(settings, "TEXT_SAFETY_TIMEOUT_SECONDS", 20) or 20))
    except (TypeError, ValueError):
        return 20.0


def _short_excerpt(text: str, limit: int = 220) -> str:
    return str(text or "").strip()[:limit]


def _short_error(exc: BaseException, limit: int = 240) -> str:
    return f"{exc.__class__.__name__}: {exc}"[:limit]
