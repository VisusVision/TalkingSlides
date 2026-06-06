from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

from ..schemas import AgentFindingSchema, AgentResultSchema, FindingLocation

logger = logging.getLogger(__name__)

OLLAMA_AGENT_SLUG = "text_moderation_ollama"
OLLAMA_AGENT_VERSION = "ollama:qwen-text-review:v1"
SAFE_ALLOW_RESULT = AgentResultSchema(
    agent_slug=OLLAMA_AGENT_SLUG,
    agent_version=OLLAMA_AGENT_VERSION,
    modality="text",
    provider="ollama",
    decision="allow",
    confidence=0.0,
    findings=[],
    metadata={"skipped": True},
)


class OllamaProvider:
    provider_name = "ollama"

    def is_enabled(self) -> bool:
        return _bool_setting("AI_AGENTS_LOCAL_LLM_ENABLED", False)

    def review_text(self, text: str, location: FindingLocation) -> AgentResultSchema:
        if not self.is_enabled():
            return SAFE_ALLOW_RESULT.model_copy(deep=True)

        clean_text = str(text or "").strip()
        if not clean_text:
            return _allow_result(metadata={"skipped": True, "reason": "empty_text"})

        try:
            response = requests.post(
                f"{_base_url()}/api/generate",
                json={
                    "model": _model_name(),
                    "prompt": _prompt(clean_text),
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0},
                },
                timeout=_timeout_seconds(),
            )
            response.raise_for_status()
            payload = response.json()
            raw_text = str(payload.get("response") or "").strip()
            parsed = json.loads(raw_text)
            return _result_from_payload(parsed, location, raw_text=raw_text)
        except Exception as exc:  # noqa: BLE001
            logger.info("Ollama moderation review skipped: %s", exc)
            return _allow_result(
                metadata={
                    "skipped": True,
                    "error": exc.__class__.__name__,
                    "message": str(exc)[:240],
                }
            )


def _result_from_payload(payload: Any, location: FindingLocation, *, raw_text: str) -> AgentResultSchema:
    if not isinstance(payload, dict):
        raise ValueError("Ollama response JSON must be an object.")

    finding_rows = payload.get("findings")
    if not isinstance(finding_rows, list):
        finding_rows = []

    normalized_findings = []
    for row in finding_rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item.setdefault("location", location.model_dump(exclude_none=True))
        normalized_findings.append(item)

    decision = _advisory_decision(str(payload.get("decision") or "allow"))
    if decision != "allow" and not normalized_findings:
        normalized_findings.append(
            {
                "category": "unknown",
                "severity": "medium",
                "confidence": _confidence(payload.get("confidence"), fallback=0.5),
                "decision": decision,
                "location": location.model_dump(exclude_none=True),
                "user_message": "This content may need admin review before publishing.",
                "admin_message": "Ollama returned a non-allow decision without specific findings.",
                "evidence_excerpt": "",
            }
        )

    result_payload = {
        "agent_slug": OLLAMA_AGENT_SLUG,
        "agent_version": OLLAMA_AGENT_VERSION,
        "modality": "text",
        "provider": "ollama",
        "decision": decision,
        "confidence": _confidence(payload.get("confidence"), fallback=0.0),
        "findings": normalized_findings,
        "metadata": {
            "model": _model_name(),
            "advisory_only": True,
            "raw_response_chars": len(raw_text),
        },
    }
    result = AgentResultSchema.model_validate(result_payload)
    capped_findings = [_cap_finding(finding, location) for finding in result.findings]
    return result.model_copy(update={"findings": capped_findings})


def _cap_finding(finding: AgentFindingSchema, location: FindingLocation) -> AgentFindingSchema:
    decision = _advisory_decision(finding.decision)
    admin_message = finding.admin_message
    if finding.decision == "block":
        admin_message = (admin_message + " " if admin_message else "") + "Ollama block decision capped to admin review."
    return finding.model_copy(
        update={
            "decision": decision,
            "location": finding.location or location,
            "admin_message": admin_message,
        }
    )


def _advisory_decision(value: str) -> str:
    cleaned = str(value or "allow").strip()
    if cleaned == "block":
        return "needs_admin_review"
    if cleaned in {"allow", "warn", "needs_admin_review"}:
        return cleaned
    return "needs_admin_review"


def _allow_result(*, metadata: dict[str, Any] | None = None) -> AgentResultSchema:
    return AgentResultSchema(
        agent_slug=OLLAMA_AGENT_SLUG,
        agent_version=OLLAMA_AGENT_VERSION,
        modality="text",
        provider="ollama",
        decision="allow",
        confidence=0.0,
        findings=[],
        metadata=metadata or {},
    )


def _prompt(text: str) -> str:
    return (
        "You are a conservative educational content moderation reviewer. "
        "Return JSON only, with no markdown and no commentary. "
        "You are advisory only: uncertain or unsafe-looking educational content should be needs_admin_review, not block. "
        "Use only these decisions: allow, warn, needs_admin_review, block. "
        "Use only these severities: low, medium, high, critical. "
        "Use only these categories: profanity, sexual, violence, illegal_activity, self_harm, hate_or_harassment, "
        "political_or_targeted_abuse, dangerous_instruction, graphic_content, privacy_or_personal_data, unknown. "
        "Output shape: {\"decision\":\"allow|warn|needs_admin_review|block\",\"confidence\":0.0,"
        "\"findings\":[{\"category\":\"unknown\",\"severity\":\"medium\",\"confidence\":0.0,"
        "\"decision\":\"needs_admin_review\",\"user_message\":\"short safe publisher message\","
        "\"admin_message\":\"short admin rationale\",\"evidence_excerpt\":\"short excerpt\"}]}. "
        "Review this text:\n"
        f"{text[:6000]}"
    )


def _base_url() -> str:
    return _str_setting("AI_AGENTS_OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def _model_name() -> str:
    return _str_setting("AI_AGENTS_TEXT_MODEL", "qwen2.5:7b-instruct")


def _timeout_seconds() -> float:
    try:
        return max(0.1, float(_str_setting("AI_AGENTS_LLM_TIMEOUT_SECONDS", "8")))
    except ValueError:
        return 8.0


def _bool_setting(name: str, default: bool) -> bool:
    env_value = os.environ.get(name)
    if env_value is not None:
        return str(env_value).strip().lower() in {"1", "true", "yes", "on"}
    return bool(_django_setting(name, default))


def _str_setting(name: str, default: str) -> str:
    env_value = os.environ.get(name)
    if env_value is not None:
        return str(env_value).strip() or default
    return str(_django_setting(name, default) or default).strip()


def _django_setting(name: str, default: Any) -> Any:
    try:
        from django.conf import settings

        return getattr(settings, name, default)
    except Exception:
        return default


def _confidence(value: Any, *, fallback: float) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return fallback
