from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from django.conf import settings
from django.utils import timezone

from .models import UserProfile


AVATAR_BLOCKING_STATUSES = {"rejected", "needs_admin_review"}


def avatar_image_moderation_auto_enabled() -> bool:
    if not bool(getattr(settings, "VISUAL_MODERATION_SCAN_AVATAR_ASSETS", True)):
        return False
    return bool(getattr(settings, "AVATAR_IMAGE_MODERATION_AUTO_ENABLED", False))


def avatar_image_moderation_block_on_rejection() -> bool:
    return bool(getattr(settings, "AVATAR_IMAGE_MODERATION_BLOCK_ON_REJECTION", True))


def avatar_image_moderation_require_approval() -> bool:
    return bool(getattr(settings, "AVATAR_IMAGE_MODERATION_REQUIRE_APPROVAL", False))


def semantic_visual_provider_required() -> bool:
    return bool(getattr(settings, "VISUAL_MODERATION_REQUIRE_SEMANTIC_PROVIDER", True))


def weak_local_visual_approval_allowed() -> bool:
    return bool(getattr(settings, "ALLOW_WEAK_LOCAL_VISUAL_APPROVAL", False))


def run_avatar_image_moderation(
    profile: UserProfile,
    image_path: str | Path,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    if not avatar_image_moderation_auto_enabled():
        return mark_avatar_image_moderation_skipped(
            profile,
            reason="avatar_image_moderation_disabled",
            persist=persist,
        )

    resolved_path = _resolve_image_path(image_path)
    try:
        LocalImageRulesProvider, build_visual_safety_provider, visual_safety_classifier_should_run, FindingLocation, PolicyEngine = (
            _visual_moderation_dependencies()
        )
    except Exception as exc:  # noqa: BLE001
        return _save_summary(
            profile,
            {
                "status": "needs_admin_review",
                "final_decision": "needs_admin_review",
                "reason": "avatar_image_moderation_unavailable",
                "provider_error": _short_error(exc),
                "asset_kind": "profile_image",
                "asset_label": "Profile image",
                "decision": "needs_admin_review",
                "reason_title": "Visual safety scan unavailable",
                "reason_message": (
                    "The semantic visual safety provider did not return a completed result. "
                    "This visual cannot be automatically approved and requires manual admin review before publishing."
                ),
                "technical_reason": "avatar_image_moderation_unavailable",
                "message": "Profile image visual safety scan could not be completed; admin review is required.",
                "scanned_at": timezone.now().isoformat(),
            },
            persist=persist,
        )

    location = FindingLocation(
        project_id=None,
        asset_type="avatar_image",
        image_path=resolved_path,
        ui_anchor=f"user-{profile.user_id}-avatar-image",
    )
    providers = [LocalImageRulesProvider()]
    classifier_requested = bool(visual_safety_classifier_should_run())
    if classifier_requested:
        providers.append(build_visual_safety_provider())

    results = []
    for provider in providers:
        try:
            results.append(provider.review_image(resolved_path, location))
        except Exception as exc:  # noqa: BLE001
            results.append(_provider_error_result(provider, location, exc))

    final_decision = PolicyEngine().combine_results(results)
    findings = [finding for result in results for finding in result.findings]
    safety_results = [result for result in results if str(result.provider or "") != "local_image_rules"]
    safety_completed = any(not bool((result.metadata or {}).get("skipped")) for result in safety_results)
    skipped_reasons = [
        str((result.metadata or {}).get("reason") or "")
        for result in safety_results
        if bool((result.metadata or {}).get("skipped"))
    ]
    provider_errors = [
        str((result.metadata or {}).get("reason") or (result.metadata or {}).get("provider_error") or "")
        for result in results
        if bool((result.metadata or {}).get("provider_error"))
    ]

    semantic_missing = (
        semantic_visual_provider_required()
        and not weak_local_visual_approval_allowed()
        and not safety_completed
    )

    if final_decision == "block":
        avatar_status = "rejected"
    elif final_decision == "needs_admin_review":
        avatar_status = "needs_admin_review"
    elif semantic_missing:
        avatar_status = "needs_admin_review"
        final_decision = "needs_admin_review"
    elif not safety_completed and not findings:
        avatar_status = "skipped"
    else:
        avatar_status = "approved"

    summary = {
        "status": avatar_status,
        "final_decision": final_decision,
        "asset_kind": "profile_image",
        "asset_label": "Profile image",
        "decision": "blocked" if avatar_status == "rejected" else avatar_status,
        "reason_title": "Visual safety scan unavailable" if semantic_missing else (
            "Unsafe visual detected" if avatar_status == "rejected" else "Visual needs admin review" if avatar_status == "needs_admin_review" else "Visual approved"
        ),
        "reason_message": (
            "The semantic visual safety provider did not return a completed result. "
            "This visual cannot be automatically approved and requires manual admin review before publishing."
            if semantic_missing
            else _status_message(avatar_status)
        ),
        "publisher_reason_message": (
            "We could not complete the visual safety scan for this profile image. "
            "An admin must review it before it can become public."
            if semantic_missing
            else _status_message(avatar_status)
        ),
        "admin_reason_message": (
            "The semantic visual safety provider did not return a completed result. "
            "This visual cannot be automatically approved and requires manual admin review before publishing."
            if semantic_missing
            else _status_message(avatar_status)
        ),
        "technical_reason": "semantic_visual_provider_unavailable" if semantic_missing else "",
        "finding_count": len(findings),
        "categories": _count_values(findings, "category"),
        "severities": _count_values(findings, "severity"),
        "providers": [str(result.provider or "") for result in results if str(result.provider or "")],
        "classifier_requested": classifier_requested,
        "safety_completed": safety_completed,
        "skipped_reasons": [reason for reason in skipped_reasons if reason],
        "provider_errors": [reason for reason in provider_errors if reason],
        "semantic_provider_required": semantic_visual_provider_required(),
        "weak_local_visual_approval_allowed": weak_local_visual_approval_allowed(),
        "semantic_provider_missing": semantic_missing,
        "findings": [_finding_summary(finding) for finding in findings[:10]],
        "message": _status_message(avatar_status),
        "scanned_at": timezone.now().isoformat(),
    }
    return _save_summary(profile, summary, persist=persist)


def mark_avatar_image_moderation_skipped(
    profile: UserProfile,
    *,
    reason: str,
    persist: bool = True,
) -> dict[str, Any]:
    return _save_summary(
        profile,
        {
            "status": "skipped",
            "reason": reason,
            "message": "Avatar image moderation is disabled; existing avatar flow is not blocked.",
            "scanned_at": timezone.now().isoformat(),
        },
        persist=persist,
    )


def avatar_image_moderation_gate(profile: UserProfile) -> dict[str, Any]:
    status = str(getattr(profile, "avatar_moderation_status", "") or "not_scanned").strip().lower()
    summary = getattr(profile, "avatar_moderation_summary", None)
    if not isinstance(summary, dict):
        summary = {}

    if avatar_image_moderation_require_approval() and status != "approved":
        return {
            "blocked": True,
            "error_code": "avatar_image_moderation_approval_required",
            "message": "Avatar image moderation approval is required before avatar generation.",
            "status": status,
            "summary": summary,
        }
    if status in AVATAR_BLOCKING_STATUSES and avatar_image_moderation_block_on_rejection():
        return {
            "blocked": True,
            "error_code": "avatar_image_moderation_blocked",
            "message": str(summary.get("message") or "Avatar source image needs admin review before avatar generation."),
            "status": status,
            "summary": summary,
        }
    if status == "pending" and avatar_image_moderation_auto_enabled():
        return {
            "blocked": True,
            "error_code": "avatar_image_moderation_pending",
            "message": "Avatar image moderation is still pending.",
            "status": status,
            "summary": summary,
        }
    return {"blocked": False, "error_code": "", "message": "", "status": status, "summary": summary}


def _save_summary(profile: UserProfile, summary: dict[str, Any], *, persist: bool) -> dict[str, Any]:
    status = str(summary.get("status") or "skipped").strip().lower()
    profile.avatar_moderation_status = status
    profile.avatar_moderation_summary = dict(summary)
    profile.avatar_last_moderation_run_id = None
    if persist:
        profile.save(
            update_fields=[
                "avatar_moderation_status",
                "avatar_moderation_summary",
                "avatar_last_moderation_run_id",
                "updated_at",
            ]
        )
    return dict(summary)


def _resolve_image_path(image_path: str | Path) -> str:
    raw = str(image_path or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if path.is_file():
        return str(path)
    if path.is_absolute():
        return str(path)
    storage_path = Path(str(getattr(settings, "STORAGE_ROOT", "storage_local"))) / raw.lstrip("/\\")
    return str(storage_path)


def _visual_moderation_dependencies():
    services_root = Path(__file__).resolve().parents[2]
    if str(services_root) not in sys.path:
        sys.path.insert(0, str(services_root))

    from worker.ai_agents.policy_engine import PolicyEngine
    from worker.ai_agents.providers.local_image_rules_provider import LocalImageRulesProvider
    from worker.ai_agents.providers.visual_safety_provider import (
        build_visual_safety_provider,
        visual_safety_classifier_should_run,
    )
    from worker.ai_agents.schemas import FindingLocation

    return LocalImageRulesProvider, build_visual_safety_provider, visual_safety_classifier_should_run, FindingLocation, PolicyEngine


def _provider_error_result(provider, location, exc):
    from worker.ai_agents.schemas import AgentResultSchema

    return AgentResultSchema(
        agent_slug=str(getattr(provider, "agent_slug", "avatar_image_moderation_provider")),
        agent_version=str(getattr(provider, "agent_version", "v1")),
        modality="image",
        provider=str(getattr(provider, "provider_name", "visual_provider")),
        decision="allow",
        confidence=0.0,
        findings=[],
        metadata={
            "skipped": True,
            "provider_error": True,
            "reason": "avatar_image_provider_error",
            "error": _short_error(exc),
            "location": location.model_dump(exclude_none=True),
        },
    )


def _finding_summary(finding) -> dict[str, Any]:
    return {
        "category": str(finding.category or ""),
        "severity": str(finding.severity or ""),
        "decision": str(finding.decision or ""),
        "asset_kind": "profile_image",
        "asset_label": "Profile image",
        "confidence": float(getattr(finding, "confidence", 0.0) or 0.0),
        "user_message": str(getattr(finding, "user_message", "") or ""),
        "admin_message": str(getattr(finding, "admin_message", "") or ""),
        "evidence_excerpt": str(getattr(finding, "evidence_excerpt", "") or ""),
        "provider": str(getattr(finding, "provider", "") or ""),
    }


def _count_values(findings: list, attr_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        key = str(getattr(finding, attr_name, "") or "").strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _status_message(status: str) -> str:
    if status == "approved":
        return "Avatar source image passed configured visual moderation."
    if status == "rejected":
        return "Avatar source image was rejected by visual moderation."
    if status == "needs_admin_review":
        return "Avatar source image needs manual admin review before it can be used."
    if status == "skipped":
        return "Avatar image moderation was skipped; existing avatar flow is not blocked."
    return "Avatar image moderation status was updated."


def _short_error(exc: BaseException, limit: int = 240) -> str:
    return f"{exc.__class__.__name__}: {exc}"[:limit]
