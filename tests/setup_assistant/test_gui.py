from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PySide6 = pytest.importorskip("PySide6")
from PySide6 import QtWidgets

from tools.setup_assistant.gui import SetupAssistantWindow
from tools.setup_assistant.models import CheckResult, CheckRun, CheckStatus, Profile, Severity


@pytest.fixture(scope="module")
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(application, talking_slides_repo: Path):
    value = SetupAssistantWindow(talking_slides_repo)
    yield value
    value.close()


def sample_run() -> CheckRun:
    return CheckRun(
        Profile.CORE,
        "quick",
        "TestOS",
        [
            CheckResult(
                "test.failure",
                "Docker daemon",
                "Docker",
                CheckStatus.FAILURE,
                Severity.HIGH,
                "Unavailable.",
                technical_details="SECRET_KEY=must-not-appear",
                remediation="Start Docker.",
            )
        ],
        "2026-01-01T00:00:00+00:00",
        10,
    )


def test_all_sections_render(window: SetupAssistantWindow) -> None:
    assert window.pages.count() == 7
    assert [window.navigation.item(index).text() for index in range(7)] == [
        "Overview",
        "Repository",
        "Requirements",
        "System Diagnostics",
        "Services",
        "Configuration",
        "Report",
    ]


def test_status_group_and_details_expand(window: SetupAssistantWindow) -> None:
    window._render_results(sample_run())
    assert window.result_tree.topLevelItemCount() == 1
    assert "Failed" in window.result_tree.topLevelItem(0).text(0)
    assert "Start Docker" in window.details.toPlainText()


def test_secret_not_shown_in_ui_details(window: SetupAssistantWindow) -> None:
    window._render_results(sample_run())
    assert "must-not-appear" not in window.details.toPlainText()
    assert "<redacted>" in window.details.toPlainText()


def test_repository_selection_validation(window: SetupAssistantWindow, talking_slides_repo: Path) -> None:
    window.repository_edit.setText(str(talking_slides_repo))
    assert "markers found" in window.repository_state.text().lower()


def test_services_and_action_required_render(window: SetupAssistantWindow) -> None:
    assert "api" in window.service_cards
    assert window.action_required_list.topLevelItemCount() > 0


def test_onboarding_page_loads_and_repository_actions_are_gated(
    application,
    tmp_path: Path,
) -> None:
    value = SetupAssistantWindow(tmp_path / "missing")
    try:
        assert value.navigation.currentRow() == 1
        assert "Missing:" in value.repository_state.text()
        buttons = value.service_cards["api"]["buttons"]
        assert not buttons["start"].isEnabled()
        assert buttons["start"].toolTip() == "Repository required"
    finally:
        value.close()


def test_browse_flow_updates_selected_path(
    monkeypatch,
    window: SetupAssistantWindow,
    talking_slides_repo: Path,
) -> None:
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(talking_slides_repo),
    )
    window.repository_edit.clear()
    window._browse_repository()
    assert window.repository_edit.text() == str(talking_slides_repo)
    assert "markers found" in window.repository_state.text().lower()


def test_system_only_continuation(
    monkeypatch,
    application,
    talking_slides_repo: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "tools.setup_assistant.repository.preference_path",
        lambda: tmp_path / "settings.json",
    )
    value = SetupAssistantWindow(talking_slides_repo)
    try:
        value._enable_system_only()
        assert value._system_only
        assert value._active_repository is None
        assert "System-only mode" in value.overview_mode.text()
        assert value.navigation.currentRow() == 0
    finally:
        value.close()


def test_valid_repository_service_actions_are_enabled(window: SetupAssistantWindow) -> None:
    buttons = window.service_cards["api"]["buttons"]
    assert buttons["start"].isEnabled()
    assert buttons["stop"].isEnabled()
    assert buttons["restart"].isEnabled()


def test_runtime_profile_selection(window: SetupAssistantWindow) -> None:
    window.profile_combo.setCurrentText("avatar")
    assert window._selected_profile() is Profile.AVATAR


def test_report_export(monkeypatch, window: SetupAssistantWindow, tmp_path: Path) -> None:
    target = tmp_path / "report.json"
    window._last_run = sample_run()
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(target), "JSON (*.json)"),
    )
    window._export_report("json")
    assert target.is_file()


def test_quick_check_and_recheck_complete(window: SetupAssistantWindow, application) -> None:
    for _ in range(2):
        window._start_check(False)
        deadline = time.monotonic() + 20
        while window._thread is not None and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(0.01)
        assert window._thread is None
        assert window._last_run is not None
        assert window.result_tree.topLevelItemCount() > 0
