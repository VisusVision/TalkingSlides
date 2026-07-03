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


def test_all_six_sections_render(window: SetupAssistantWindow) -> None:
    assert window.pages.count() == 6
    assert [window.navigation.item(index).text() for index in range(6)] == [
        "Welcome",
        "Requirements",
        "Installation & Configuration",
        "System Diagnostics",
        "Runtime",
        "Report",
    ]


def test_status_group_and_details_expand(window: SetupAssistantWindow) -> None:
    window._render_results(sample_run())
    assert window.result_tree.topLevelItemCount() == 1
    assert "Failure" in window.result_tree.topLevelItem(0).text(0)
    assert "Start Docker" in window.details.toPlainText()


def test_secret_not_shown_in_ui_details(window: SetupAssistantWindow) -> None:
    window._render_results(sample_run())
    assert "must-not-appear" not in window.details.toPlainText()
    assert "<redacted>" in window.details.toPlainText()


def test_repository_selection_validation(window: SetupAssistantWindow, talking_slides_repo: Path) -> None:
    window.repository_edit.setText(str(talking_slides_repo))
    assert "markers found" in window.repository_state.text().lower()


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
