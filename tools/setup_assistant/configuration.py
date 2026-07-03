from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

from .repository import validate_repository
from .services import ServiceSnapshot
from .status import ServiceStatus


class AttentionLevel(str, Enum):
    BLOCKING = "blocking"
    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"
    INFORMATIONAL = "informational"


@dataclass(frozen=True)
class ConfigurationRequirement:
    variable: str
    feature: str
    required: bool
    secret: bool = False
    format_kind: str = "text"


@dataclass(frozen=True)
class ConfigurationStatus:
    variable: str
    feature: str
    present: bool
    valid: bool
    required: bool
    secret: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "variable": self.variable,
            "feature": self.feature,
            "present": self.present,
            "valid": self.valid,
            "required": self.required,
            "secret": self.secret,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ActionRequiredItem:
    level: AttentionLevel
    title: str
    reason: str
    affected_feature: str
    next_step: str
    automatic_action: str = ""
    documentation_reference: str = ""


CONFIGURATION_REQUIREMENTS: tuple[ConfigurationRequirement, ...] = (
    ConfigurationRequirement("DJANGO_SETTINGS_MODULE", "Core API", True),
    ConfigurationRequirement("SECRET_KEY", "Core API", True, secret=True),
    ConfigurationRequirement("MEDIA_TOKEN_SECRET", "Protected media", True, secret=True),
    ConfigurationRequirement("POSTGRES_DB", "PostgreSQL", True),
    ConfigurationRequirement("POSTGRES_USER", "PostgreSQL", True),
    ConfigurationRequirement("POSTGRES_PASSWORD", "PostgreSQL", True, secret=True),
    ConfigurationRequirement("REDIS_URL", "Redis / workers", True, format_kind="url"),
    ConfigurationRequirement("CELERY_BROKER_URL", "Render workers", True, format_kind="url"),
    ConfigurationRequirement("CELERY_RESULT_BACKEND", "Render workers", True, format_kind="url"),
    ConfigurationRequirement("STORAGE_BACKEND", "Media storage", True),
    ConfigurationRequirement("STORAGE_ROOT", "Media storage", True),
    ConfigurationRequirement("VITE_API_BASE_URL", "Frontend", True, format_kind="url"),
    ConfigurationRequirement("OPENAI_API_KEY", "OpenAI integration", False, secret=True),
    ConfigurationRequirement("OLLAMA_BASE_URL", "Local Ollama", False, format_kind="url"),
    ConfigurationRequirement("STRIPE_SECRET_KEY", "Payments", False, secret=True),
    ConfigurationRequirement("SUBTITLE_TRANSLATION_API_PROVIDER", "External translation", False),
    ConfigurationRequirement("SUBTITLE_TRANSLATION_API_BASE_URL", "External translation", False, format_kind="url"),
    ConfigurationRequirement("SUBTITLE_TRANSLATION_API_KEY", "External translation", False, secret=True),
    ConfigurationRequirement("SUBTITLE_TRANSLATION_API_MODEL", "External translation", False),
    ConfigurationRequirement("GOOGLE_CLIENT_ID", "Google OAuth", False),
    ConfigurationRequirement("GOOGLE_CLIENT_SECRET", "Google OAuth", False, secret=True),
)


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _valid_value(value: str, format_kind: str) -> tuple[bool, str]:
    if not value:
        return False, "Value is empty."
    lowered = value.lower()
    if any(marker in lowered for marker in ("replace-me", "change-me", "your-", "<required>")):
        return False, "Value is still a placeholder."
    if format_kind == "url":
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https", "redis", "rediss", "postgres", "postgresql"}:
            return False, "URL scheme is missing or unsupported."
    if format_kind == "boolean" and lowered not in {"1", "0", "true", "false", "yes", "no", "on", "off"}:
        return False, "Expected a boolean value."
    if format_kind == "integer" and not re.fullmatch(r"\d+", value):
        return False, "Expected a non-negative integer."
    return True, "Format accepted."


def _conditionally_required(variable: str, values: dict[str, str]) -> bool:
    truthy = {"1", "true", "yes", "on"}
    if variable == "OPENAI_API_KEY":
        return (
            values.get("TTS_LLM_SUGGESTIONS_ENABLED", "").lower() in truthy
            and values.get("TTS_LLM_PROVIDER", "").lower() == "openai"
        )
    if variable == "OLLAMA_BASE_URL":
        if values.get("ENABLE_LOCAL_OLLAMA", "").lower() in truthy:
            return True
        chains = (
            values.get("LESSON_INTELLIGENCE_PROVIDER_CHAIN", ""),
            values.get("ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN", ""),
            values.get("SUBTITLE_TRANSLATION_PROVIDER_CHAIN", ""),
        )
        return any("ollama" in {part.strip().lower() for part in chain.split(",")} for chain in chains)
    if variable == "STRIPE_SECRET_KEY":
        return values.get("PAYMENT_PROVIDER", "").lower() == "stripe"
    return False


def inspect_configuration(repository: Path | None) -> tuple[ConfigurationStatus, ...]:
    if repository is None:
        return ()
    validation = validate_repository(repository)
    if not validation.valid:
        return ()
    env_file = validation.path / "infra" / ".env"
    values = _read_env(env_file)
    results: list[ConfigurationStatus] = []
    for requirement in CONFIGURATION_REQUIREMENTS:
        required = requirement.required or _conditionally_required(requirement.variable, values)
        present = requirement.variable in values and bool(values[requirement.variable])
        valid, reason = _valid_value(values.get(requirement.variable, ""), requirement.format_kind)
        if not present:
            reason = "Variable is missing."
        results.append(
            ConfigurationStatus(
                requirement.variable,
                requirement.feature,
                present,
                valid,
                required,
                requirement.secret,
                reason,
            )
        )
    return tuple(results)


def action_required_items(
    repository: Path | None,
    *,
    service_snapshots: tuple[ServiceSnapshot, ...] = (),
) -> tuple[ActionRequiredItem, ...]:
    items: list[ActionRequiredItem] = []
    validation = validate_repository(repository) if repository else None
    if not validation or not validation.valid:
        items.append(
            ActionRequiredItem(
                AttentionLevel.BLOCKING,
                "Repository not selected",
                "Repository-dependent checks and controls are unavailable.",
                "TalkingSlides services",
                "Choose an existing TalkingSlides checkout or clone the public repository.",
                documentation_reference="docs/SETUP_ASSISTANT.md#first-run-repository-onboarding",
            )
        )
    else:
        env_file = validation.path / "infra" / ".env"
        if not env_file.is_file():
            items.append(
                ActionRequiredItem(
                    AttentionLevel.REQUIRED,
                    "Environment file missing",
                    "Repository services need local configuration before they can start.",
                    "Core runtime",
                    "Review infra/.env.example, then create infra/.env. Existing files are never overwritten.",
                    automatic_action="config.create_env",
                    documentation_reference="docs/SETUP_ASSISTANT.md#configuration-and-privacy",
                )
            )
        else:
            for status in inspect_configuration(validation.path):
                if status.required and (not status.present or not status.valid):
                    items.append(
                        ActionRequiredItem(
                            AttentionLevel.REQUIRED,
                            f"{status.variable} needs attention",
                            status.reason,
                            status.feature,
                            f"Set {status.variable} in infra/.env using the environment documentation.",
                            documentation_reference="docs/ENVIRONMENT_VARIABLES.md",
                        )
                    )
    for snapshot in service_snapshots:
        if snapshot.status in {ServiceStatus.BLOCKED, ServiceStatus.FAILED}:
            items.append(
                ActionRequiredItem(
                    AttentionLevel.BLOCKING if not snapshot.definition.optional else AttentionLevel.OPTIONAL,
                    f"{snapshot.definition.display_name}: {snapshot.status.value.replace('_', ' ')}",
                    snapshot.explanation,
                    snapshot.definition.display_name,
                    snapshot.definition.manual_guidance or "Open service details and follow the reported remediation.",
                    documentation_reference=snapshot.definition.documentation_reference,
                )
            )
        elif snapshot.status in {ServiceStatus.DEGRADED, ServiceStatus.NOT_CONFIGURED}:
            items.append(
                ActionRequiredItem(
                    AttentionLevel.RECOMMENDED if not snapshot.definition.optional else AttentionLevel.OPTIONAL,
                    f"{snapshot.definition.display_name} needs attention",
                    snapshot.explanation,
                    snapshot.definition.display_name,
                    snapshot.definition.manual_guidance or "Review service details.",
                    documentation_reference=snapshot.definition.documentation_reference,
                )
            )
    return tuple(items)
