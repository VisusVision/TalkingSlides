from __future__ import annotations

import os
import platform
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .actions import SafeActionExecutor
from .checks import CheckEngine
from .models import APP_NAME, VERSION, CheckResult, CheckRun, CheckStatus, Profile
from .reports import render_text, sanitize_text, write_report
from .repository import discover_repository, save_repository_preference, validate_repository
from .resources import asset_path
from .runtime import RuntimeManager

STATUS_LABELS = {
    CheckStatus.PASS: "✓ Pass",
    CheckStatus.WARNING: "⚠ Warning",
    CheckStatus.FAILURE: "✕ Failure",
    CheckStatus.SKIPPED: "— Skipped",
    CheckStatus.RUNNING: "… Running",
}
STATUS_COLORS = {
    CheckStatus.PASS: "#16845b",
    CheckStatus.WARNING: "#b26a00",
    CheckStatus.FAILURE: "#c33c44",
    CheckStatus.SKIPPED: "#687386",
    CheckStatus.RUNNING: "#246bfd",
}


class CheckWorker(QtCore.QObject):
    progress = QtCore.Signal(str, int, int)
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, repository: str, profile: Profile, full: bool) -> None:
        super().__init__()
        self.repository = repository
        self.profile = profile
        self.full = full

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = CheckEngine().run(
                repository=self.repository or None,
                profile=self.profile,
                full=self.full,
                progress=self.progress.emit,
            )
            self.completed.emit(result)
        except Exception as exc:  # GUI boundary: present a controlled error instead of crashing.
            self.failed.emit(str(exc))


class SetupAssistantWindow(QtWidgets.QMainWindow):
    def __init__(self, repository: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        icon = asset_path("talkingslides-setup.svg")
        if icon.exists():
            self.setWindowIcon(QtGui.QIcon(os.fspath(icon)))
        self.resize(1100, 720)
        self.setMinimumSize(820, 560)
        self._last_run: CheckRun | None = None
        self._thread: QtCore.QThread | None = None
        self._worker: CheckWorker | None = None
        self._action_executor = SafeActionExecutor()

        discovered = discover_repository(repository)
        initial_repository = discovered.path if discovered and discovered.valid else (repository or Path())
        self.repository_edit = QtWidgets.QLineEdit(os.fspath(initial_repository) if initial_repository else "")
        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.addItems([profile.value for profile in Profile])
        self.no_frontend = QtWidgets.QCheckBox("Run without frontend")

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.navigation = QtWidgets.QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(230)
        self.navigation.addItems(
            [
                "Welcome",
                "Requirements",
                "Installation & Configuration",
                "System Diagnostics",
                "Runtime",
                "Report",
            ]
        )
        self.navigation.setCurrentRow(0)
        self.pages = QtWidgets.QStackedWidget()
        layout.addWidget(self.navigation)
        layout.addWidget(self.pages, 1)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)

        self.pages.addWidget(self._welcome_page())
        self.pages.addWidget(self._requirements_page())
        self.pages.addWidget(self._installation_page())
        self.pages.addWidget(self._diagnostics_page())
        self.pages.addWidget(self._runtime_page())
        self.pages.addWidget(self._report_page())
        self._set_styles()
        self._validate_repository()

    @staticmethod
    def _page(title: str, subtitle: str) -> tuple[QtWidgets.QWidget, QtWidgets.QVBoxLayout]:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        heading = QtWidgets.QLabel(title)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        description = QtWidgets.QLabel(subtitle)
        description.setWordWrap(True)
        description.setObjectName("pageSubtitle")
        layout.addWidget(description)
        layout.addSpacing(12)
        return page, layout

    def _welcome_page(self) -> QtWidgets.QWidget:
        page, layout = self._page(APP_NAME, "Configure, diagnose, and manage a local TalkingSlides runtime from one cross-platform application.")
        product = QtWidgets.QLabel("TalkingSlides")
        product.setObjectName("hero")
        layout.addWidget(product)
        layout.addWidget(QtWidgets.QLabel(f"Detected operating system: {platform.system()} {platform.release()}"))
        layout.addWidget(QtWidgets.QLabel(f"Application version: {VERSION}"))
        layout.addSpacing(18)
        buttons = QtWidgets.QHBoxLayout()
        quick = QtWidgets.QPushButton("Quick Check")
        quick.setObjectName("primaryButton")
        quick.clicked.connect(lambda: self._start_check(False))
        full = QtWidgets.QPushButton("Full Check")
        full.clicked.connect(lambda: self._start_check(True))
        buttons.addWidget(quick)
        buttons.addWidget(full)
        buttons.addStretch()
        layout.addLayout(buttons)
        safety = QtWidgets.QLabel(
            "Checks are read-only by default. Internet access and runtime changes occur only when explicitly selected."
        )
        safety.setWordWrap(True)
        safety.setObjectName("infoCard")
        layout.addWidget(safety)
        layout.addStretch()
        return page

    def _requirements_page(self) -> QtWidgets.QWidget:
        page, layout = self._page("Requirements", "Host, architecture, memory, storage, Docker, Compose, virtualization, WSL, display, and GPU readiness.")
        self.requirements_summary = QtWidgets.QTextBrowser()
        self.requirements_summary.setOpenExternalLinks(False)
        self.requirements_summary.setHtml(
            "<p>Run Quick Check or Full Check to populate the system summary.</p>"
            "<p>Core and TTS profiles do not require avatar GPU/model readiness.</p>"
        )
        layout.addWidget(self.requirements_summary, 1)
        return page

    def _repository_row(self, parent: QtWidgets.QVBoxLayout) -> None:
        parent.addWidget(QtWidgets.QLabel("TalkingSlides repository"))
        row = QtWidgets.QHBoxLayout()
        self.repository_edit.setPlaceholderText("Select the TalkingSlides repository folder")
        self.repository_edit.textChanged.connect(self._validate_repository)
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse_repository)
        row.addWidget(self.repository_edit, 1)
        row.addWidget(browse)
        parent.addLayout(row)
        self.repository_state = QtWidgets.QLabel()
        self.repository_state.setWordWrap(True)
        parent.addWidget(self.repository_state)

    def _installation_page(self) -> QtWidgets.QWidget:
        page, layout = self._page(
            "Installation & Configuration",
            "Choose the repository, inspect configuration state, and run only narrow local repair actions.",
        )
        self._repository_row(layout)
        layout.addSpacing(12)
        actions = QtWidgets.QGroupBox("Safe setup actions")
        action_layout = QtWidgets.QVBoxLayout(actions)
        create_env = QtWidgets.QPushButton("Create infra/.env from template")
        create_env.clicked.connect(lambda: self._run_safe_action("config.create_env"))
        create_storage = QtWidgets.QPushButton("Create storage_local directory")
        create_storage.clicked.connect(lambda: self._run_safe_action("config.create_storage"))
        action_layout.addWidget(create_env)
        action_layout.addWidget(create_storage)
        action_layout.addWidget(
            QtWidgets.QLabel("Actions show the exact change and require confirmation. Existing .env files are never overwritten.")
        )
        layout.addWidget(actions)
        layout.addStretch()
        return page

    def _diagnostics_page(self) -> QtWidgets.QWidget:
        page, layout = self._page(
            "System Diagnostics",
            "Grouped results use native exit codes and keep stdout, stderr, timings, and safe remediation separate.",
        )
        controls = QtWidgets.QHBoxLayout()
        self.recheck_button = QtWidgets.QPushButton("Recheck")
        self.recheck_button.clicked.connect(lambda: self._start_check(False))
        self.full_recheck_button = QtWidgets.QPushButton("Full Check")
        self.full_recheck_button.clicked.connect(lambda: self._start_check(True))
        controls.addWidget(self.recheck_button)
        controls.addWidget(self.full_recheck_button)
        controls.addStretch()
        layout.addLayout(controls)
        self.progress_label = QtWidgets.QLabel("No check is running.")
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)
        self.result_tree = QtWidgets.QTreeWidget()
        self.result_tree.setHeaderLabels(["Status", "Category", "Check", "Summary"])
        self.result_tree.setColumnWidth(0, 110)
        self.result_tree.setColumnWidth(1, 190)
        self.result_tree.setColumnWidth(2, 230)
        self.result_tree.setAlternatingRowColors(True)
        self.result_tree.currentItemChanged.connect(self._show_result_details)
        layout.addWidget(self.result_tree, 2)
        detail_group = QtWidgets.QGroupBox("Technical details and suggested fix")
        detail_group.setCheckable(True)
        detail_group.setChecked(True)
        detail_layout = QtWidgets.QVBoxLayout(detail_group)
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(1000)
        self.copy_command_button = QtWidgets.QPushButton("Copy Command")
        self.copy_command_button.setEnabled(False)
        self.copy_command_button.clicked.connect(self._copy_selected_action)
        detail_layout.addWidget(self.details)
        detail_layout.addWidget(self.copy_command_button, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
        detail_group.toggled.connect(self.details.setVisible)
        layout.addWidget(detail_group, 1)
        return page

    def _runtime_page(self) -> QtWidgets.QWidget:
        page, layout = self._page(
            "Runtime",
            "Inspect, start, or stop supported profiles. Start and stop require confirmation and never build, pull, or delete data.",
        )
        form = QtWidgets.QFormLayout()
        form.addRow("Profile", self.profile_combo)
        form.addRow("", self.no_frontend)
        layout.addLayout(form)
        button_row = QtWidgets.QHBoxLayout()
        for label, action in (("Start", "start"), ("Stop", "stop"), ("Health Check", "health"), ("Status", "status")):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(lambda _checked=False, selected=action: self._runtime_action(selected))
            button_row.addWidget(button)
        button_row.addStretch()
        layout.addLayout(button_row)
        self.runtime_output = QtWidgets.QPlainTextEdit()
        self.runtime_output.setReadOnly(True)
        self.runtime_output.setPlaceholderText("Runtime command previews and sanitized output appear here.")
        layout.addWidget(self.runtime_output, 1)
        return page

    def _report_page(self) -> QtWidgets.QWidget:
        page, layout = self._page(
            "Report",
            "Export sanitized JSON, Markdown, or text. Secrets, authorization values, and private media contents are excluded.",
        )
        controls = QtWidgets.QHBoxLayout()
        for label, report_format in (("Export JSON", "json"), ("Export Markdown", "markdown"), ("Export Text", "text")):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(lambda _checked=False, selected=report_format: self._export_report(selected))
            controls.addWidget(button)
        copy_summary = QtWidgets.QPushButton("Copy Summary")
        copy_summary.clicked.connect(self._copy_summary)
        controls.addWidget(copy_summary)
        controls.addStretch()
        layout.addLayout(controls)
        self.report_preview = QtWidgets.QPlainTextEdit()
        self.report_preview.setReadOnly(True)
        self.report_preview.setPlaceholderText("Run a check to preview the sanitized report.")
        layout.addWidget(self.report_preview, 1)
        return page

    def _browse_repository(self) -> None:
        selected = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose TalkingSlides repository", self.repository_edit.text())
        if selected:
            self.repository_edit.setText(selected)
            validation = validate_repository(selected)
            if validation.valid:
                try:
                    save_repository_preference(validation.path)
                except OSError as exc:
                    self.statusBar().showMessage(f"Repository selected; preference could not be saved: {exc}", 6000)

    @QtCore.Slot()
    def _validate_repository(self) -> None:
        text = self.repository_edit.text().strip()
        if not text:
            self.repository_state.setText("No repository selected.")
            self.repository_state.setStyleSheet("color: #b26a00")
            return
        validation = validate_repository(text)
        if validation.valid:
            self.repository_state.setText("Repository markers found.")
            self.repository_state.setStyleSheet("color: #16845b")
        else:
            self.repository_state.setText(f"Not a TalkingSlides repository. Missing: {', '.join(validation.missing_markers)}")
            self.repository_state.setStyleSheet("color: #c33c44")

    def _selected_profile(self) -> Profile:
        return Profile(self.profile_combo.currentText())

    def _start_check(self, full: bool) -> None:
        if self._thread and self._thread.isRunning():
            return
        self.navigation.setCurrentRow(3)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 9)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Starting diagnostics…")
        self.recheck_button.setEnabled(False)
        self.full_recheck_button.setEnabled(False)
        self._thread = QtCore.QThread(self)
        self._worker = CheckWorker(self.repository_edit.text().strip(), self._selected_profile(), full)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.completed.connect(self._check_completed)
        self._worker.failed.connect(self._check_failed)
        self._worker.completed.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._check_thread_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    @QtCore.Slot()
    def _check_thread_finished(self) -> None:
        self._worker = None
        self._thread = None

    @QtCore.Slot(str, int, int)
    def _on_progress(self, title: str, current: int, total: int) -> None:
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current - 1)
        self.progress_label.setText(f"Running: {title} ({current}/{total})")

    @QtCore.Slot(object)
    def _check_completed(self, run: CheckRun) -> None:
        self._last_run = run
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.progress_label.setText(f"Completed in {run.duration_ms} ms — {run.status.value.upper()}")
        self.recheck_button.setEnabled(True)
        self.full_recheck_button.setEnabled(True)
        self._render_results(run)
        self.report_preview.setPlainText(render_text(run))
        requirement_lines = [
            f"<h3>{run.status.value.title()}</h3>",
            f"<p>{run.counts}</p>",
        ]
        for result in run.results:
            if result.category in {"Requirements", "Windows", "Linux", "Docker"}:
                requirement_lines.append(f"<p><b>{result.title}</b>: {result.summary}</p>")
        self.requirements_summary.setHtml("".join(requirement_lines))

    @QtCore.Slot(str)
    def _check_failed(self, message: str) -> None:
        self.progress_bar.setVisible(False)
        self.progress_label.setText("Diagnostics could not complete.")
        self.recheck_button.setEnabled(True)
        self.full_recheck_button.setEnabled(True)
        self.details.setPlainText(message)

    def _render_results(self, run: CheckRun) -> None:
        self.result_tree.clear()
        for result in run.results:
            item = QtWidgets.QTreeWidgetItem(
                [STATUS_LABELS[result.status], result.category, result.title, sanitize_text(result.summary)]
            )
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(STATUS_COLORS[result.status])))
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, result)
            self.result_tree.addTopLevelItem(item)
        self.result_tree.resizeColumnToContents(0)
        if self.result_tree.topLevelItemCount():
            self.result_tree.setCurrentItem(self.result_tree.topLevelItem(0))

    def _show_result_details(self, current: QtWidgets.QTreeWidgetItem | None, _previous=None) -> None:
        if not current:
            self.details.clear()
            self.copy_command_button.setEnabled(False)
            return
        result: CheckResult = current.data(0, QtCore.Qt.ItemDataRole.UserRole)
        lines = [sanitize_text(result.summary)]
        if result.technical_details:
            lines.extend(("", "Technical details:", sanitize_text(result.technical_details)))
        if result.remediation:
            lines.extend(("", "Suggested fix:", sanitize_text(result.remediation)))
        if result.documentation_reference:
            lines.extend(("", f"Documentation: {result.documentation_reference}"))
        if result.safe_action:
            lines.extend(("", "Safe action:", result.safe_action.description))
        lines.extend(("", f"Duration: {result.duration_ms} ms"))
        self.details.setPlainText("\n".join(lines))
        self.copy_command_button.setEnabled(bool(result.safe_action and result.safe_action.command))

    def _copy_selected_action(self) -> None:
        current = self.result_tree.currentItem()
        if not current:
            return
        result: CheckResult = current.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if result.safe_action and result.safe_action.command:
            QtWidgets.QApplication.clipboard().setText(" ".join(result.safe_action.command))

    def _run_safe_action(self, action_id: str) -> None:
        repository = Path(self.repository_edit.text().strip())
        try:
            preview = self._action_executor.preview(action_id, repository)
        except (ValueError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "Action unavailable", str(exc))
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Confirm safe action",
            preview,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            result = self._action_executor.execute(action_id, repository, confirmed=True)
        except (ValueError, OSError) as exc:
            QtWidgets.QMessageBox.critical(self, "Action failed", str(exc))
            return
        self.statusBar().showMessage(result.summary, 7000)

    def _runtime_action(self, action: str) -> None:
        validation = validate_repository(self.repository_edit.text().strip())
        if not validation.valid:
            QtWidgets.QMessageBox.warning(self, "Repository required", "Select a valid TalkingSlides repository.")
            return
        manager = RuntimeManager(validation.path)
        profile = self._selected_profile()
        preview = manager.preview(action, profile, self.no_frontend.isChecked())
        confirmed = action not in {"start", "stop"}
        avatar_ack = False
        if not confirmed:
            answer = QtWidgets.QMessageBox.question(
                self,
                f"Confirm runtime {action}",
                f"The assistant will run:\n\n{preview}\n\nContinue?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            confirmed = answer == QtWidgets.QMessageBox.StandardButton.Yes
        if action == "start" and profile is Profile.AVATAR and confirmed:
            answer = QtWidgets.QMessageBox.warning(
                self,
                "Avatar queue risk",
                "Starting the avatar profile can consume real queued avatar work. Continue only if that is intentional.",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            avatar_ack = answer == QtWidgets.QMessageBox.StandardButton.Yes
        result = manager.execute(
            action,
            profile,
            no_frontend=self.no_frontend.isChecked(),
            confirmed=confirmed,
            allow_avatar_queue_risk=avatar_ack,
        )
        output = [f"Command: {result.preview}"]
        if result.error:
            output.append(result.error)
        if result.result:
            if result.result.stdout:
                output.append(result.result.stdout)
            if result.result.stderr:
                output.append(result.result.stderr)
            output.append(f"Exit code: {result.result.exit_code}")
        self.runtime_output.setPlainText("\n".join(output))

    def _export_report(self, report_format: str) -> None:
        if not self._last_run:
            QtWidgets.QMessageBox.information(self, "No report", "Run Quick Check or Full Check first.")
            return
        extensions = {"json": "JSON (*.json)", "markdown": "Markdown (*.md)", "text": "Text (*.txt)"}
        target, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export sanitized report", "", extensions[report_format])
        if not target:
            return
        try:
            path = write_report(self._last_run, Path(target), report_format)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage(f"Saved sanitized report: {path}", 7000)

    def _copy_summary(self) -> None:
        if self._last_run:
            QtWidgets.QApplication.clipboard().setText(render_text(self._last_run))

    def _set_styles(self) -> None:
        dark = self.palette().color(QtGui.QPalette.ColorRole.Window).lightness() < 128
        surface = "#20242c" if dark else "#ffffff"
        navigation = "#151922" if dark else "#eef2f7"
        text = "#f4f6fa" if dark else "#172033"
        muted = "#aab3c2" if dark else "#5e697a"
        border = "#353c49" if dark else "#d7dee8"
        self.setStyleSheet(
            f"""
            QWidget {{ color: {text}; font-size: 14px; }}
            QMainWindow, QStackedWidget {{ background: {surface}; }}
            QListWidget#navigation {{ background: {navigation}; border: 0; padding: 18px 8px; }}
            QListWidget#navigation::item {{ padding: 12px 14px; margin: 2px 0; border-radius: 7px; }}
            QListWidget#navigation::item:selected {{ background: #246bfd; color: white; }}
            QLabel#pageTitle {{ font-size: 26px; font-weight: 700; }}
            QLabel#pageSubtitle {{ color: {muted}; font-size: 14px; }}
            QLabel#hero {{ font-size: 40px; font-weight: 700; margin-top: 22px; }}
            QLabel#infoCard {{ background: {navigation}; border: 1px solid {border}; border-radius: 8px; padding: 14px; }}
            QPushButton {{ min-height: 32px; padding: 3px 14px; border: 1px solid {border}; border-radius: 6px; }}
            QPushButton:hover {{ border-color: #246bfd; }}
            QPushButton:focus {{ border: 2px solid #246bfd; }}
            QPushButton#primaryButton {{ background: #246bfd; color: white; border-color: #246bfd; }}
            QLineEdit, QPlainTextEdit, QTextBrowser, QTreeWidget, QComboBox {{
                border: 1px solid {border}; border-radius: 6px; padding: 5px;
            }}
            QGroupBox {{ border: 1px solid {border}; border-radius: 8px; margin-top: 12px; padding-top: 12px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; }}
            """
        )


def launch_gui(repository: Path | None = None, *, smoke: bool = False) -> int:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    application.setApplicationName(APP_NAME)
    application.setApplicationVersion(VERSION)
    application.setOrganizationName("TalkingSlides")
    window = SetupAssistantWindow(repository)
    window.show()
    if smoke:
        QtCore.QTimer.singleShot(350, application.quit)
    return application.exec()
