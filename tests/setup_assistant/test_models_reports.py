from __future__ import annotations

import json

from tools.setup_assistant.models import CheckResult, CheckRun, CheckStatus, Profile, SafeAction, Severity
from tools.setup_assistant.reports import render_json, render_markdown, render_text, sanitize_data, sanitize_text


def make_run(*statuses: CheckStatus) -> CheckRun:
    return CheckRun(
        Profile.CORE,
        "quick",
        "TestOS",
        [
            CheckResult(
                f"check.{index}",
                f"Check {index}",
                "Test",
                status,
                Severity.INFO,
                "summary",
                safe_action=SafeAction("fix", "Fix", "Safe fix") if index == 0 else None,
            )
            for index, status in enumerate(statuses)
        ],
        "2026-01-01T00:00:00+00:00",
        12,
    )


def test_status_aggregation_prioritizes_failure() -> None:
    run = make_run(CheckStatus.PASS, CheckStatus.WARNING, CheckStatus.FAILURE)
    assert run.status is CheckStatus.FAILURE
    assert run.counts["failure"] == 1


def test_status_aggregation_warning_without_failure() -> None:
    assert make_run(CheckStatus.PASS, CheckStatus.WARNING).status is CheckStatus.WARNING


def test_result_model_contains_safe_action_metadata() -> None:
    payload = make_run(CheckStatus.PASS).to_dict()
    assert payload["results"][0]["safe_action"]["confirmation_required"] is True
    assert payload["results"][0]["safe_action"]["destructive"] is False


def test_report_sanitization_redacts_secret_assignments_and_bearer_tokens() -> None:
    text = sanitize_text("SECRET_KEY=abc Bearer eyJ.bad token https://user:pass@example.test")
    assert "abc" not in text
    assert "eyJ.bad" not in text
    assert "user:pass" not in text


def test_report_sanitization_redacts_secret_keys_recursively() -> None:
    sanitized = sanitize_data({"nested": {"password": "bad", "safe": "ok"}})
    assert sanitized["nested"]["password"] == "<redacted>"
    assert sanitized["nested"]["safe"] == "ok"


def test_json_markdown_and_text_reports_share_sanitized_model() -> None:
    run = make_run(CheckStatus.PASS)
    payload = json.loads(render_json(run))
    assert payload["application"] == "TalkingSlides Setup Assistant"
    assert "| PASS | Test | Check 0 | summary |" in render_markdown(run)
    assert "[PASS" in render_text(run)
