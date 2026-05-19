from __future__ import annotations

import re
from typing import Any

from django.utils import timezone


PROGRESSIVE_ENHANCEMENT_KEY = "progressive_enhancement"
PENDING_ENHANCEMENT_STATUSES = {"pending", "running"}
TERMINAL_ENHANCEMENT_STATUSES = {"done", "failed", "unavailable", "disabled", "stale"}


def provider_chain_contains_ollama(chain: list[str] | tuple[str, ...] | None) -> bool:
    return any(str(item or "").strip().lower() == "ollama" for item in (chain or []))


def first_provider_name(chain: list[str] | tuple[str, ...] | None) -> str:
    for item in chain or []:
        name = str(item or "").strip().lower()
        if name and name != "auto":
            return name
    return ""


def safe_enhancement_error(error: Exception | str | None, *, limit: int = 180) -> str:
    text = str(error or "").strip()
    if not text:
        return ""
    text = re.sub(r"\b[a-z][a-z0-9+.-]*://[^\s\"')]+", "[url]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)=\S+", r"\1=[redacted]", text)
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def enhancement_metadata(
    *,
    provider: str = "ollama",
    status: str = "pending",
    task_id: str = "",
    queue: str = "",
    error: Exception | str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    now = timezone.now().isoformat()
    normalized_status = str(status or "pending").strip().lower()
    payload = {
        "enabled": bool(enabled),
        "provider": str(provider or "ollama").strip().lower() or "ollama",
        "status": normalized_status,
        "queued_at": now if normalized_status == "pending" else "",
        "started_at": now if normalized_status == "running" else "",
        "finished_at": now if normalized_status in TERMINAL_ENHANCEMENT_STATUSES else "",
        "failed_at": now if normalized_status in {"failed", "stale"} else "",
        "task_id": str(task_id or ""),
        "queue": str(queue or "").strip(),
        "error": safe_enhancement_error(error),
    }
    return payload


def merge_enhancement_metadata(
    metadata: dict[str, Any] | None,
    *,
    provider: str | None = None,
    status: str | None = None,
    task_id: str | None = None,
    queue: str | None = None,
    error: Exception | str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    source = dict(metadata or {})
    current = source.get(PROGRESSIVE_ENHANCEMENT_KEY)
    enhancement = dict(current) if isinstance(current, dict) else enhancement_metadata()
    now = timezone.now().isoformat()

    if provider is not None:
        enhancement["provider"] = str(provider or "ollama").strip().lower() or "ollama"
    if enabled is not None:
        enhancement["enabled"] = bool(enabled)
    if task_id is not None:
        enhancement["task_id"] = str(task_id or "")
    if queue is not None:
        enhancement["queue"] = str(queue or "").strip()
    if status is not None:
        normalized_status = str(status or "").strip().lower() or "pending"
        previous_status = str(enhancement.get("status") or "").strip().lower()
        enhancement["status"] = normalized_status
        if normalized_status == "pending" and not enhancement.get("queued_at"):
            enhancement["queued_at"] = now
        if normalized_status == "running" and previous_status != "running":
            enhancement["started_at"] = now
        if normalized_status in TERMINAL_ENHANCEMENT_STATUSES:
            enhancement["finished_at"] = now
        if normalized_status in {"failed", "stale"}:
            enhancement["failed_at"] = now
    if error is not None:
        enhancement["error"] = safe_enhancement_error(error)
    elif status in {"done", "pending", "running"}:
        enhancement["error"] = ""

    source[PROGRESSIVE_ENHANCEMENT_KEY] = enhancement
    return source


def enhancement_from_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    source = metadata if isinstance(metadata, dict) else {}
    enhancement = source.get(PROGRESSIVE_ENHANCEMENT_KEY)
    return dict(enhancement) if isinstance(enhancement, dict) else {}


def enhancement_response_fields(
    metadata: dict[str, Any] | None,
    *,
    default_available: bool = False,
) -> dict[str, Any]:
    enhancement = enhancement_from_metadata(metadata)
    status = str(enhancement.get("status") or "").strip().lower()
    provider = str(enhancement.get("provider") or "").strip().lower()
    enabled = bool(enhancement.get("enabled", bool(provider or default_available)))
    available = bool(enabled and (provider or default_available))
    return {
        "enhancement_available": available,
        "enhancement_pending": bool(status in PENDING_ENHANCEMENT_STATUSES),
        "enhancement_status": status,
        "enhancement_provider": provider,
        "enhancement_error_safe": safe_enhancement_error(enhancement.get("error")),
    }


def provider_attempt(provider: str, status_value: str, error: Exception | str | None = None) -> dict[str, str]:
    payload = {"provider": str(provider or "").strip().lower(), "status": str(status_value or "").strip().lower()}
    if error:
        payload["error"] = safe_enhancement_error(error, limit=240)
    return payload
