from __future__ import annotations

import os
import platform
from pathlib import Path
from threading import Event
from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from .actions import SafeActionExecutor
from .checks import CheckEngine
from .clone import (
    DEFAULT_REPOSITORY_REF,
    DEFAULT_REPOSITORY_URL,
    CloneManager,
    CloneRequest,
    sanitized_repository_url,
)
from .configuration import AttentionLevel, action_required_items, inspect_configuration
from .models import APP_NAME, VERSION, CheckResult, CheckRun, CheckStatus, Profile
from .reports import render_text, sanitize_text, write_report
from .repository import (
    RepositoryContext,
    RepositoryState,
    display_marker_list,
    forget_repository,
    initial_repository_context,
    load_repository_settings,
    repository_validation_details,
    save_repository_preference,
    select_system_only,
    validate_repository,
)
from .resources import asset_path
from .runner import CommandResult
from .runtime import RuntimeManager
from .services import (
    SERVICE_REGISTRY,
    ServiceController,
    ServiceDefinition,
    ServiceOperationResult,
    ServiceSnapshot,
    ServiceType,
    available_service_groups,
)
from .status import ServiceStatus, status_presentation, status_text

CHECK_STATUS_MAP = {
    CheckStatus.PASS: ServiceStatus.HEALTHY,
    CheckStatus.WARNING: ServiceStatus.DEGRADED,
    CheckStatus.FAILURE: ServiceStatus.FAILED,
    CheckStatus.SKIPPED: ServiceStatus.UNKNOWN,
    CheckStatus.RUNNING: ServiceStatus.CHECKING,
}


class CheckWorker(QtCore.QObject):
    progress = QtCore.Signal(str, int, int)
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, repository: str, profile: Profile, full: bool, system_only: bool = False) -> None:
        super().__init__()
        self.repository = repository
        self.profile = profile
        self.full = full
        self.system_only = system_only

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = CheckEngine().run(
                repository=self.repository or None,
                profile=self.profile,
                full=self.full,
                system_only=self.system_only,
                progress=self.progress.emit,
            )
            self.completed.emit(result)
        except Exception as exc:  # GUI boundary
            self.failed.emit(sanitize_text(str(exc)))


class TaskWorker(QtCore.QObject):
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, function: Callable[[], object]) -> None:
        super().__init__()
        self.function = function

    @QtCore.Slot()
    def run(self) -> None:
        try:
            self.completed.emit(self.function())
        except Exception as exc:  # GUI boundary
            self.failed.emit(sanitize_text(str(exc)))


class CloneWorker(QtCore.QObject):
    progress = QtCore.Signal(str)
    completed = QtCore.Signal(object)

    def __init__(self, request: CloneRequest) -> None:
        super().__init__()
        self.request = request
        self.cancel_event = Event()

    @QtCore.Slot()
    def run(self) -> None:
        manager = CloneManager()
        result = manager.execute(
            self.request,
            confirmed=True,
            cancel_event=self.cancel_event,
            progress=lambda channel, text: self.progress.emit(f"[{channel}] {text}"),
        )
        self.completed.emit(result)

    def cancel(self) -> None:
        self.cancel_event.set()


class ServiceLogDialog(QtWidgets.QDialog):
    def __init__(
        self,
        service_name: str,
        initial_text: str,
        follow_spec,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{service_name} — sanitized logs")
        self.resize(850, 560)
        self._follow_spec = follow_spec
        self._process: QtCore.QProcess | None = None
        layout = QtWidgets.QVBoxLayout(self)
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(500)
        self.output.setPlainText(sanitize_text(initial_text))
        layout.addWidget(self.output, 1)
        controls = QtWidgets.QHBoxLayout()
        copy_button = QtWidgets.QPushButton("Copy")
        copy_button.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.output.toPlainText()))
        save_button = QtWidgets.QPushButton("Save sanitized logs")
        save_button.clicked.connect(self._save)
        clear_button = QtWidgets.QPushButton("Clear display")
        clear_button.setToolTip("Clears only this display. Docker logs are not deleted.")
        clear_button.clicked.connect(self.output.clear)
        self.follow_button = QtWidgets.QPushButton("Start follow")
        self.follow_button.setEnabled(follow_spec is not None)
        self.follow_button.clicked.connect(self._toggle_follow)
        controls.addWidget(copy_button)
        controls.addWidget(save_button)
        controls.addWidget(clear_button)
        controls.addWidget(self.follow_button)
        controls.addStretch()
        layout.addLayout(controls)

    def _save(self) -> None:
        target, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save sanitized logs", "", "Text (*.txt)")
        if not target:
            return
        try:
            Path(target).write_text(sanitize_text(self.output.toPlainText()), encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Save failed", sanitize_text(str(exc)))

    def _toggle_follow(self) -> None:
        if self._process and self._process.state() != QtCore.QProcess.ProcessState.NotRunning:
            self._process.terminate()
            self.follow_button.setText("Start follow")
            return
        if self._follow_spec is None:
            return
        self._process = QtCore.QProcess(self)
        if self._follow_spec.cwd:
            self._process.setWorkingDirectory(os.fspath(self._follow_spec.cwd))
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.finished.connect(lambda *_: self.follow_button.setText("Start follow"))
        self._process.start(self._follow_spec.argv[0], list(self._follow_spec.argv[1:]))
        self.follow_button.setText("Stop follow")

    def _append(self, value: bytes) -> None:
        text = sanitize_text(bytes(value).decode("utf-8", errors="replace"))
        if text:
            self.output.appendPlainText(text.rstrip())

    def _read_stdout(self) -> None:
        if self._process:
            self._append(self._process.readAllStandardOutput().data())

    def _read_stderr(self) -> None:
        if self._process:
            self._append(self._process.readAllStandardError().data())

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._process and self._process.state() != QtCore.QProcess.ProcessState.NotRunning:
            self._process.terminate()
        super().closeEvent(event)


class SetupAssistantWindow(QtWidgets.QMainWindow):
    def __init__(self, repository: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        icon = asset_path("talkingslides-setup.svg")
        if icon.exists():
            self.setWindowIcon(QtGui.QIcon(os.fspath(icon)))
        self.resize(1180, 780)
        self.setMinimumSize(900, 620)
        self._last_run: CheckRun | None = None
        self._last_service_snapshots: tuple[ServiceSnapshot, ...] = ()
        self._thread: QtCore.QThread | None = None
        self._worker: CheckWorker | None = None
        self._task_thread: QtCore.QThread | None = None
        self._task_worker: TaskWorker | None = None
        self._task_callback: Callable[[object], None] | None = None
        self._clone_thread: QtCore.QThread | None = None
        self._clone_worker: CloneWorker | None = None
        self._action_executor = SafeActionExecutor()
        self._context = initial_repository_context(repository)
        self._active_repository = self._context.repository if self._context.state is RepositoryState.VALID_SELECTED else None
        self._system_only = self._context.state is RepositoryState.SYSTEM_ONLY
        self.service_cards: dict[str, dict[str, object]] = {}
        self._operation_buttons: list[QtWidgets.QPushButton] = []

        initial_path = self._context.validation.path if self._context.validation else None
        self.repository_edit = QtWidgets.QLineEdit(os.fspath(initial_path) if initial_path is not None else "")
        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.addItems([profile.value for profile in Profile])
        self.no_frontend = QtWidgets.QCheckBox("Run without frontend")

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header())
        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)

        self.navigation = QtWidgets.QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(230)
        self.navigation.addItems(
            [
                "Overview",
                "Repository",
                "Requirements",
                "System Diagnostics",
                "Services",
                "Configuration",
                "Report",
            ]
        )
        self.pages = QtWidgets.QStackedWidget()
        body.addWidget(self.navigation)
        body.addWidget(self.pages, 1)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)

        self.pages.addWidget(self._welcome_page())
        self.pages.addWidget(self._repository_page())
        self.pages.addWidget(self._requirements_page())
        self.pages.addWidget(self._diagnostics_page())
        self.pages.addWidget(self._services_page())
        self.pages.addWidget(self._configuration_page())
        self.pages.addWidget(self._report_page())
        self._set_styles()
        self._refresh_repository_header()
        self._validate_repository()
        self._refresh_configuration()
        self._initialize_service_cards()
        self._update_action_required()
        self.navigation.setCurrentRow(1 if self._active_repository is None and not self._system_only else 0)

    def _header(self) -> QtWidgets.QWidget:
        header = QtWidgets.QFrame()
        header.setObjectName("appHeader")
        layout = QtWidgets.QHBoxLayout(header)
        layout.setContentsMargins(18, 10, 18, 10)
        title = QtWidgets.QLabel(APP_NAME)
        title.setObjectName("headerTitle")
        layout.addWidget(title)
        layout.addStretch()
        self.header_mode = QtWidgets.QLabel()
        self.header_mode.setAccessibleName("Operating mode")
        layout.addWidget(self.header_mode)
        self.repository_selector = QtWidgets.QComboBox()
        self.repository_selector.setMinimumWidth(320)
        self.repository_selector.setAccessibleName("Active repository selector")
        self.repository_selector.activated.connect(self._select_recent_repository)
        layout.addWidget(self.repository_selector)
        change = QtWidgets.QPushButton("Change")
        change.clicked.connect(lambda: self.navigation.setCurrentRow(1))
        layout.addWidget(change)
        return header

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
        page, layout = self._page(
            APP_NAME,
            "Repository-aware diagnostics and guarded local service controls for TalkingSlides.",
        )
        product = QtWidgets.QLabel("TalkingSlides")
        product.setObjectName("hero")
        layout.addWidget(product)
        self.overview_mode = QtWidgets.QLabel()
        self.overview_mode.setObjectName("infoCard")
        self.overview_mode.setWordWrap(True)
        layout.addWidget(self.overview_mode)
        layout.addWidget(QtWidgets.QLabel(f"Detected host: {platform.system()} {platform.release()}"))
        layout.addWidget(QtWidgets.QLabel(f"Application version: {VERSION}"))
        buttons = QtWidgets.QHBoxLayout()
        quick = QtWidgets.QPushButton("Quick Check")
        quick.setObjectName("primaryButton")
        quick.clicked.connect(lambda: self._start_check(False))
        full = QtWidgets.QPushButton("Full Check")
        full.clicked.connect(lambda: self._start_check(True))
        services = QtWidgets.QPushButton("Open Services")
        services.clicked.connect(lambda: self.navigation.setCurrentRow(4))
        buttons.addWidget(quick)
        buttons.addWidget(full)
        buttons.addWidget(services)
        buttons.addStretch()
        layout.addLayout(buttons)
        action_group = QtWidgets.QGroupBox("Action Required")
        action_layout = QtWidgets.QVBoxLayout(action_group)
        self.action_required_list = QtWidgets.QTreeWidget()
        self.action_required_list.setHeaderLabels(["Priority", "Item", "Affected feature", "Next step"])
        self.action_required_list.setRootIsDecorated(False)
        action_layout.addWidget(self.action_required_list)
        layout.addWidget(action_group, 1)
        safety = QtWidgets.QLabel(
            "Checks are read-only by default. Starts, stops, clones, pulls, builds, and file creation always require an explicit action."
        )
        safety.setObjectName("infoCard")
        safety.setWordWrap(True)
        layout.addWidget(safety)
        return page

    def _repository_page(self) -> QtWidgets.QWidget:
        page, layout = self._page(
            "Repository onboarding",
            "Use a detected checkout, choose an existing folder, clone the public repository, or continue with system checks only.",
        )
        self.detected_button = QtWidgets.QPushButton("Use detected repository")
        self.detected_button.clicked.connect(self._use_detected_repository)
        layout.addWidget(self.detected_button, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(QtWidgets.QLabel("Selected or detected path"))
        row = QtWidgets.QHBoxLayout()
        self.repository_edit.setPlaceholderText("Choose the TalkingSlides repository root")
        self.repository_edit.setAccessibleName("TalkingSlides repository path")
        self.repository_edit.textChanged.connect(self._validate_repository)
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse_repository)
        use_selected = QtWidgets.QPushButton("Use selected folder")
        use_selected.clicked.connect(self._activate_selected_repository)
        forget_selected = QtWidgets.QPushButton("Forget entry")
        forget_selected.setToolTip("Remove this path from recent repositories. Repository files are not changed.")
        forget_selected.clicked.connect(self._forget_selected_repository)
        row.addWidget(self.repository_edit, 1)
        row.addWidget(browse)
        row.addWidget(use_selected)
        row.addWidget(forget_selected)
        layout.addLayout(row)
        self.repository_state = QtWidgets.QLabel()
        self.repository_state.setWordWrap(True)
        self.repository_state.setAccessibleName("Repository validation status")
        layout.addWidget(self.repository_state)
        self.repository_details = QtWidgets.QPlainTextEdit()
        self.repository_details.setReadOnly(True)
        self.repository_details.setMaximumHeight(120)
        self.repository_details.setAccessibleName("Repository identity and capabilities")
        self.repository_details.setPlaceholderText("Repository identity and capability details appear here.")
        layout.addWidget(self.repository_details)

        clone_group = QtWidgets.QGroupBox("Clone TalkingSlides")
        clone_form = QtWidgets.QFormLayout(clone_group)
        self.clone_url = QtWidgets.QLineEdit(DEFAULT_REPOSITORY_URL)
        self.clone_url.setAccessibleName("Clone repository URL")
        self.clone_ref = QtWidgets.QLineEdit(DEFAULT_REPOSITORY_REF)
        self.clone_ref.setAccessibleName("Clone branch or ref")
        self.clone_ref.setPlaceholderText("Default repository branch")
        destination_row = QtWidgets.QHBoxLayout()
        self.clone_destination = QtWidgets.QLineEdit(
            os.fspath(Path.home() / "TalkingSlides")
        )
        self.clone_destination.setAccessibleName("Clone destination")
        destination_browse = QtWidgets.QPushButton("Browse…")
        destination_browse.clicked.connect(self._browse_clone_destination)
        destination_row.addWidget(self.clone_destination, 1)
        destination_row.addWidget(destination_browse)
        clone_form.addRow("Repository URL", self.clone_url)
        clone_form.addRow("Branch / ref", self.clone_ref)
        clone_form.addRow("Destination", destination_row)
        clone_actions = QtWidgets.QHBoxLayout()
        self.clone_button = QtWidgets.QPushButton("Clone")
        self.clone_button.clicked.connect(self._start_clone)
        self.clone_cancel = QtWidgets.QPushButton("Cancel clone")
        self.clone_cancel.setEnabled(False)
        self.clone_cancel.clicked.connect(self._cancel_clone)
        clone_actions.addWidget(self.clone_button)
        clone_actions.addWidget(self.clone_cancel)
        clone_actions.addStretch()
        clone_form.addRow("", clone_actions)
        self.clone_progress = QtWidgets.QProgressBar()
        self.clone_progress.setRange(0, 0)
        self.clone_progress.setVisible(False)
        clone_form.addRow(self.clone_progress)
        self.clone_output = QtWidgets.QPlainTextEdit()
        self.clone_output.setReadOnly(True)
        self.clone_output.setMaximumBlockCount(500)
        self.clone_output.setMaximumHeight(120)
        self.clone_output.setPlaceholderText("Sanitized Git progress appears here.")
        clone_form.addRow(self.clone_output)
        layout.addWidget(clone_group)
        system_only = QtWidgets.QPushButton("Continue with system checks only")
        system_only.clicked.connect(self._enable_system_only)
        layout.addWidget(system_only, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        return page

    def _requirements_page(self) -> QtWidgets.QWidget:
        page, layout = self._page(
            "Requirements",
            "Host, architecture, memory, storage, Docker, Compose, virtualization, display, and GPU readiness.",
        )
        self.requirements_summary = QtWidgets.QTextBrowser()
        self.requirements_summary.setHtml(
            "<p>Run Quick Check or Full Check to populate the system summary.</p>"
            "<p>System-only mode remains available without a repository.</p>"
        )
        layout.addWidget(self.requirements_summary, 1)
        return page

    def _diagnostics_page(self) -> QtWidgets.QWidget:
        page, layout = self._page(
            "System Diagnostics",
            "Grouped results use native exit codes and keep sanitized stdout, stderr, timings, and remediation separate.",
        )
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel("Check profile"))
        controls.addWidget(self.profile_combo)
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
        self.result_tree.setAlternatingRowColors(True)
        self.result_tree.currentItemChanged.connect(self._show_result_details)
        layout.addWidget(self.result_tree, 2)
        detail_group = QtWidgets.QGroupBox("Technical details and suggested fix")
        detail_layout = QtWidgets.QVBoxLayout(detail_group)
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(1000)
        self.copy_command_button = QtWidgets.QPushButton("Copy Command")
        self.copy_command_button.setEnabled(False)
        self.copy_command_button.clicked.connect(self._copy_selected_action)
        detail_layout.addWidget(self.details)
        detail_layout.addWidget(self.copy_command_button, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(detail_group, 1)
        return page

    def _services_page(self) -> QtWidgets.QWidget:
        page, layout = self._page(
            "Services Control Center",
            "Inspect and control only declared services. Commands are repository-scoped and never build, pull, prune, or delete data implicitly.",
        )
        controls = QtWidgets.QHBoxLayout()
        self.refresh_services_button = QtWidgets.QPushButton("Refresh All")
        self.refresh_services_button.clicked.connect(self._refresh_services)
        self.service_filter = QtWidgets.QComboBox()
        categories = ["All", *sorted({item.category for item in SERVICE_REGISTRY})]
        self.service_filter.addItems(categories)
        self.service_filter.currentTextChanged.connect(self._filter_services)
        self.auto_refresh = QtWidgets.QCheckBox("Auto refresh every 30 seconds")
        self.auto_refresh.toggled.connect(self._toggle_auto_refresh)
        controls.addWidget(self.refresh_services_button)
        controls.addWidget(QtWidgets.QLabel("Category"))
        controls.addWidget(self.service_filter)
        controls.addWidget(self.auto_refresh)
        controls.addStretch()
        layout.addLayout(controls)

        group_row = QtWidgets.QHBoxLayout()
        self.group_combo = QtWidgets.QComboBox()
        group_row.addWidget(QtWidgets.QLabel("Service group"))
        group_row.addWidget(self.group_combo, 1)
        self.start_group_button = QtWidgets.QPushButton("Start group")
        self.start_group_button.clicked.connect(lambda: self._service_group_action("start"))
        self.stop_group_button = QtWidgets.QPushButton("Stop group")
        self.stop_group_button.clicked.connect(lambda: self._service_group_action("stop"))
        self._operation_buttons.extend((self.start_group_button, self.stop_group_button))
        group_row.addWidget(self.start_group_button)
        group_row.addWidget(self.stop_group_button)
        layout.addLayout(group_row)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        container = QtWidgets.QWidget()
        self.service_cards_layout = QtWidgets.QVBoxLayout(container)
        self.service_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.service_cards_layout.setSpacing(10)
        for definition in SERVICE_REGISTRY:
            self.service_cards_layout.addWidget(self._service_card(definition))
        self.service_cards_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        self.service_operation_output = QtWidgets.QPlainTextEdit()
        self.service_operation_output.setReadOnly(True)
        self.service_operation_output.setMaximumBlockCount(500)
        self.service_operation_output.setMaximumHeight(120)
        self.service_operation_output.setPlaceholderText("Sanitized command previews and operation results appear here.")
        layout.addWidget(self.service_operation_output)
        self.service_refresh_timer = QtCore.QTimer(self)
        self.service_refresh_timer.setInterval(30_000)
        self.service_refresh_timer.timeout.connect(self._refresh_services)
        return page

    def _service_card(self, definition: ServiceDefinition) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setObjectName("serviceCard")
        card.setProperty("category", definition.category)
        card.setAccessibleName(f"{definition.display_name} service")
        layout = QtWidgets.QVBoxLayout(card)
        top = QtWidgets.QHBoxLayout()
        name = QtWidgets.QLabel(definition.display_name)
        name.setObjectName("serviceName")
        badge = QtWidgets.QLabel()
        badge.setAccessibleName(f"{definition.display_name} status")
        top.addWidget(name)
        top.addStretch()
        top.addWidget(badge)
        layout.addLayout(top)
        explanation = QtWidgets.QLabel()
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        metadata = QtWidgets.QLabel()
        metadata.setObjectName("pageSubtitle")
        metadata.setText(
            f"{definition.category}"
            + (f" • {definition.public_url}" if definition.public_url else "")
            + (f" • ports {', '.join(str(port) for port in definition.expected_ports)}" if definition.expected_ports else "")
        )
        metadata.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(metadata)
        checked = QtWidgets.QLabel("Last checked: never")
        checked.setObjectName("pageSubtitle")
        layout.addWidget(checked)
        actions = QtWidgets.QHBoxLayout()
        buttons: dict[str, QtWidgets.QPushButton] = {}
        for action in definition.supported_actions:
            label = {
                "start": "Start",
                "stop": "Stop",
                "restart": "Restart",
                "logs": "Logs",
                "pull": "Pull image",
                "build": "Build image",
            }[action]
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, service_id=definition.service_id, selected=action: self._service_action(
                    service_id, selected
                )
            )
            buttons[action] = button
            self._operation_buttons.append(button)
            actions.addWidget(button)
        details = QtWidgets.QPushButton("Details")
        details.clicked.connect(lambda _checked=False, service_id=definition.service_id: self._service_details(service_id))
        actions.addWidget(details)
        if definition.public_url:
            open_url = QtWidgets.QPushButton("Open")
            open_url.setToolTip(definition.public_url)
            open_url.clicked.connect(
                lambda _checked=False, url=definition.public_url: QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))
            )
            actions.addWidget(open_url)
        if definition.manual_guidance:
            copy_guidance = QtWidgets.QPushButton("Copy guidance")
            copy_guidance.clicked.connect(
                lambda _checked=False, text=definition.manual_guidance: QtWidgets.QApplication.clipboard().setText(text)
            )
            actions.addWidget(copy_guidance)
        actions.addStretch()
        layout.addLayout(actions)
        self.service_cards[definition.service_id] = {
            "widget": card,
            "badge": badge,
            "explanation": explanation,
            "checked": checked,
            "buttons": buttons,
            "snapshot": None,
        }
        return card

    def _configuration_page(self) -> QtWidgets.QWidget:
        page, layout = self._page(
            "Configuration and manual attention",
            "Only variable names, presence, format validity, requirement level, and affected features are shown. Values are never displayed.",
        )
        controls = QtWidgets.QHBoxLayout()
        self.create_env_button = QtWidgets.QPushButton("Create infra/.env from template")
        self.create_env_button.clicked.connect(lambda: self._run_safe_action("config.create_env"))
        open_docs = QtWidgets.QPushButton("Open environment documentation location")
        open_docs.clicked.connect(self._open_environment_docs)
        controls.addWidget(self.create_env_button)
        controls.addWidget(open_docs)
        controls.addStretch()
        layout.addLayout(controls)
        self.configuration_tree = QtWidgets.QTreeWidget()
        self.configuration_tree.setHeaderLabels(["Variable", "Presence", "Format", "Requirement", "Feature"])
        self.configuration_tree.setRootIsDecorated(False)
        layout.addWidget(self.configuration_tree, 1)
        note = QtWidgets.QLabel(
            "The assistant never invents credentials and never overwrites an existing infra/.env."
        )
        note.setObjectName("infoCard")
        note.setWordWrap(True)
        layout.addWidget(note)
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

    def _refresh_repository_header(self) -> None:
        blocker = QtCore.QSignalBlocker(self.repository_selector)
        self.repository_selector.clear()
        if self._active_repository:
            self.repository_selector.addItem(os.fspath(self._active_repository), os.fspath(self._active_repository))
        settings = load_repository_settings()
        for recent in settings.recent_repositories:
            text = os.fspath(recent)
            if self._active_repository and recent == self._active_repository:
                continue
            self.repository_selector.addItem(text, text)
        self.repository_selector.addItem("System checks only", "__system_only__")
        del blocker
        if self._active_repository:
            mode = f"Repository mode • {self._active_repository}"
            self.header_mode.setText("Repository mode")
        else:
            mode = "System-only mode • Repository required for service actions"
            self.header_mode.setText("System-only mode")
        self.header_mode.setToolTip(mode)
        self.overview_mode.setText(mode)
        self.detected_button.setEnabled(
            self._context.state is RepositoryState.CANDIDATE_DETECTED
            and bool(self._context.validation and self._context.validation.valid)
        )
        self._refresh_groups()

    def _select_recent_repository(self, index: int) -> None:
        if self._busy():
            return
        value = self.repository_selector.itemData(index)
        if value == "__system_only__":
            self._enable_system_only()
            return
        if value:
            self.repository_edit.setText(str(value))
            self._activate_selected_repository()

    def _refresh_groups(self) -> None:
        if not hasattr(self, "group_combo"):
            return
        blocker = QtCore.QSignalBlocker(self.group_combo)
        self.group_combo.clear()
        if self._active_repository:
            for group in available_service_groups(self._active_repository):
                self.group_combo.addItem(
                    f"{group.display_name} — {group.resource_usage}",
                    group.group_id,
                )
                self.group_combo.setItemData(
                    self.group_combo.count() - 1,
                    f"Includes: {', '.join(group.service_ids)}\nRequirements: {group.requirements}",
                    QtCore.Qt.ItemDataRole.ToolTipRole,
                )
        del blocker
        enabled = self.group_combo.count() > 0 and not self._busy()
        self.start_group_button.setEnabled(enabled)
        self.stop_group_button.setEnabled(enabled)
        if enabled:
            tooltip = ""
        elif not self._active_repository:
            tooltip = "Repository required"
        else:
            validation = validate_repository(self._active_repository)
            tooltip = (
                "Modern runtime groups require scripts/windows-runtime.ps1."
                if validation.valid and not validation.capabilities.modern_windows_runtime
                else "No compatible runtime groups are available in this checkout."
            )
        self.start_group_button.setToolTip(tooltip)
        self.stop_group_button.setToolTip(tooltip)

    def _browse_repository(self) -> None:
        if self._busy():
            return
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Choose TalkingSlides repository",
            self.repository_edit.text() or os.fspath(Path.home()),
        )
        if selected:
            self.repository_edit.setText(selected)

    def _browse_clone_destination(self) -> None:
        parent = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Choose clone destination parent",
            os.fspath(Path(self.clone_destination.text()).parent),
        )
        if parent:
            self.clone_destination.setText(os.fspath(Path(parent) / "TalkingSlides"))

    @QtCore.Slot()
    def _validate_repository(self) -> None:
        text = self.repository_edit.text().strip()
        dark = self.palette().color(QtGui.QPalette.ColorRole.Window).lightness() < 128
        if not text:
            presentation = status_presentation(ServiceStatus.NOT_CONFIGURED, dark=dark)
            self.repository_state.setText(f"{status_text(ServiceStatus.NOT_CONFIGURED)} — No repository selected.")
            self.repository_state.setToolTip(presentation.description)
            self.repository_state.setStyleSheet(f"color: {presentation.light_color}")
            if hasattr(self, "repository_details"):
                self.repository_details.clear()
            return
        validation = validate_repository(text)
        if validation.valid:
            limited = validation.compatibility_level != "modern"
            presentation = status_presentation(ServiceStatus.DEGRADED if limited else ServiceStatus.HEALTHY, dark=dark)
            git_note = " Git metadata found." if validation.git_metadata else " Git metadata is not required for portable source."
            if limited:
                self.repository_state.setText(
                    f"{presentation.icon} Attention — TalkingSlides repository detected, "
                    f"but some modern runtime controls are unavailable.{git_note}"
                )
            else:
                self.repository_state.setText(f"{status_text(ServiceStatus.HEALTHY)} — Compatible TalkingSlides repository.{git_note}")
            self.repository_state.setToolTip(repository_validation_details(validation))
        else:
            presentation = status_presentation(ServiceStatus.BLOCKED, dark=dark)
            self.repository_state.setText(
                f"{status_text(ServiceStatus.BLOCKED)} — Not a TalkingSlides repository. "
                f"Missing: {', '.join(display_marker_list(validation.missing_identity_markers))}"
            )
            self.repository_state.setToolTip(repository_validation_details(validation))
        if hasattr(self, "repository_details"):
            self.repository_details.setPlainText(repository_validation_details(validation))
        self.repository_state.setStyleSheet(f"color: {presentation.light_color}")

    def _use_detected_repository(self) -> None:
        if self._context.validation and self._context.validation.valid:
            self.repository_edit.setText(os.fspath(self._context.validation.path))
            self._activate_selected_repository()

    def _activate_selected_repository(self) -> None:
        if self._busy():
            return
        validation = validate_repository(self.repository_edit.text().strip())
        if not validation.valid:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid repository",
                f"Missing identity markers:\n{chr(10).join(display_marker_list(validation.missing_identity_markers))}",
            )
            return
        self._activate_repository_validation(validation, "Repository selected.")

    def _activate_repository_validation(self, validation, message: str) -> None:
        try:
            save_repository_preference(validation.path)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Preference not saved", sanitize_text(str(exc)))
        self._active_repository = validation.path
        self._system_only = False
        self._context = RepositoryContext(RepositoryState.VALID_SELECTED, validation, message)
        self.repository_edit.setText(os.fspath(validation.path))
        self._validate_repository()
        self._refresh_repository_header()
        self._refresh_configuration()
        self._initialize_service_cards()
        self._update_action_required()
        self.navigation.setCurrentRow(0)
        if validation.warnings:
            self.statusBar().showMessage(f"Active repository with compatibility warnings: {validation.path}", 9000)
        else:
            self.statusBar().showMessage(f"Active repository: {validation.path}", 7000)

    def _forget_selected_repository(self) -> None:
        if self._busy() or not self.repository_edit.text().strip():
            return
        candidate = Path(self.repository_edit.text().strip())
        try:
            forget_repository(candidate)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Preference not updated", sanitize_text(str(exc)))
            return
        if self._active_repository and validate_repository(candidate).path == self._active_repository:
            self._active_repository = None
            self._system_only = False
        self.repository_edit.clear()
        self._refresh_repository_header()
        self._initialize_service_cards()
        self._refresh_configuration()
        self._update_action_required()
        self.statusBar().showMessage("Forgot the recent path. Repository files were not changed.", 7000)

    def _enable_system_only(self) -> None:
        if self._busy():
            return
        try:
            select_system_only()
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Preference not saved", sanitize_text(str(exc)))
        self._active_repository = None
        self._system_only = True
        self._context = RepositoryContext(RepositoryState.SYSTEM_ONLY, message="System-only diagnostics mode.")
        self._refresh_repository_header()
        self._refresh_configuration()
        self._initialize_service_cards()
        self._update_action_required()
        self.navigation.setCurrentRow(0)

    def _start_clone(self) -> None:
        if self._busy():
            return
        request = CloneRequest(
            Path(self.clone_destination.text().strip()),
            self.clone_url.text().strip(),
            self.clone_ref.text().strip(),
        )
        manager = CloneManager()
        error = manager.preflight(request)
        if error:
            QtWidgets.QMessageBox.warning(self, "Clone unavailable", error)
            return
        existing = validate_repository(request.destination)
        if request.destination.exists() and existing.valid:
            self.repository_edit.setText(os.fspath(existing.path))
            self._activate_repository_validation(existing, "Existing TalkingSlides checkout selected.")
            self.clone_output.appendPlainText("Existing TalkingSlides checkout selected.")
            return
        command = manager.command(request)
        ref_label = request.ref.strip() or "Default repository branch"
        preview = "\n".join(
            (
                f"Repository: {sanitized_repository_url(request.repository_url)}",
                f"Destination: {request.destination.resolve(strict=False)}",
                f"Branch/ref: {ref_label}",
                "",
                "Git will clone the repository default branch unless an explicit ref is supplied. "
                "Duration and network usage depend on repository size.",
                "",
                "Command arguments:",
                "\n".join(f"  {index}: {sanitize_text(value)}" for index, value in enumerate(command)),
            )
        )
        answer = QtWidgets.QMessageBox.question(
            self,
            "Confirm repository clone",
            preview,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.clone_output.clear()
        self.clone_progress.setVisible(True)
        self.clone_button.setEnabled(False)
        self.clone_cancel.setEnabled(True)
        self._set_busy(True)
        self._clone_thread = QtCore.QThread(self)
        self._clone_worker = CloneWorker(request)
        self._clone_worker.moveToThread(self._clone_thread)
        self._clone_thread.started.connect(self._clone_worker.run)
        self._clone_worker.progress.connect(self._clone_progress_text)
        self._clone_worker.completed.connect(self._clone_completed)
        self._clone_worker.completed.connect(self._clone_thread.quit)
        self._clone_thread.finished.connect(self._clone_worker.deleteLater)
        self._clone_thread.finished.connect(self._clone_finished)
        self._clone_thread.finished.connect(self._clone_thread.deleteLater)
        self._clone_thread.start()

    def _clone_progress_text(self, text: str) -> None:
        self.clone_output.appendPlainText(sanitize_text(text).rstrip())

    def _cancel_clone(self) -> None:
        if self._clone_worker:
            self._clone_worker.cancel()
            self.clone_cancel.setEnabled(False)
            self.clone_output.appendPlainText("Cancellation requested…")

    @QtCore.Slot(object)
    def _clone_completed(self, result) -> None:
        self.clone_progress.setVisible(False)
        if result.ok and result.validation:
            if result.validation.warnings:
                self.clone_output.appendPlainText("Clone completed. TalkingSlides repository selected with compatibility warnings.")
                for warning in result.validation.warnings:
                    self.clone_output.appendPlainText(f"Warning: {sanitize_text(warning)}")
            else:
                self.clone_output.appendPlainText("Clone completed and compatible TalkingSlides repository selected.")
            if result.checked_out_branch:
                self.clone_output.appendPlainText(f"Checked out branch: {sanitize_text(result.checked_out_branch)}")
            if result.head_commit:
                self.clone_output.appendPlainText(f"HEAD commit: {sanitize_text(result.head_commit)}")
            if result.origin_url:
                self.clone_output.appendPlainText(f"Origin: {sanitize_text(result.origin_url)}")
            self._activate_repository_validation(result.validation, "Clone completed.")
            return
        self._context = RepositoryContext(RepositoryState.CLONE_FAILED, result.validation, result.error)
        label = "Clone failed"
        if result.outcome == "cloned_but_not_talkingslides":
            label = "Clone completed"
        self.clone_output.appendPlainText(f"{label}: {sanitize_text(result.error)}")
        if result.cleaned_incomplete_destination:
            self.clone_output.appendPlainText("Removed only the incomplete destination created by this clone attempt.")
        elif result.cleanup_error:
            self.clone_output.appendPlainText(sanitize_text(result.cleanup_error))

    @QtCore.Slot()
    def _clone_finished(self) -> None:
        self._clone_worker = None
        self._clone_thread = None
        self.clone_button.setEnabled(True)
        self.clone_cancel.setEnabled(False)
        self._set_busy(False)

    def _selected_profile(self) -> Profile:
        return Profile(self.profile_combo.currentText())

    def _start_check(self, full: bool) -> None:
        if self._thread and self._thread.isRunning():
            return
        self.navigation.setCurrentRow(3)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 10)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Starting diagnostics…")
        self.recheck_button.setEnabled(False)
        self.full_recheck_button.setEnabled(False)
        self._thread = QtCore.QThread(self)
        self._worker = CheckWorker(
            os.fspath(self._active_repository) if self._active_repository else "",
            self._selected_profile(),
            full,
            self._system_only or self._active_repository is None,
        )
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
        lines = [f"<h3>{run.status.value.title()}</h3>", f"<p>{run.counts}</p>"]
        for result in run.results:
            if result.category in {"Requirements", "Windows", "Linux", "Docker", "Optional integrations"}:
                lines.append(f"<p><b>{result.title}</b>: {sanitize_text(result.summary)}</p>")
        self.requirements_summary.setHtml("".join(lines))
        self._update_action_required()

    @QtCore.Slot(str)
    def _check_failed(self, message: str) -> None:
        self.progress_bar.setVisible(False)
        self.progress_label.setText("Diagnostics could not complete.")
        self.recheck_button.setEnabled(True)
        self.full_recheck_button.setEnabled(True)
        self.details.setPlainText(sanitize_text(message))

    def _render_results(self, run: CheckRun) -> None:
        self.result_tree.clear()
        dark = self.palette().color(QtGui.QPalette.ColorRole.Window).lightness() < 128
        for result in run.results:
            status = CHECK_STATUS_MAP[result.status]
            presentation = status_presentation(status, dark=dark)
            item = QtWidgets.QTreeWidgetItem(
                [status_text(status), result.category, result.title, sanitize_text(result.summary)]
            )
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(presentation.light_color)))
            item.setToolTip(0, presentation.description)
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

    def _initialize_service_cards(self) -> None:
        if not self.service_cards:
            return
        for definition in SERVICE_REGISTRY:
            if definition.repository_required and not self._active_repository:
                snapshot = ServiceSnapshot(definition, ServiceStatus.BLOCKED, "Repository required")
            else:
                snapshot = ServiceSnapshot(
                    definition,
                    ServiceStatus.OPTIONAL if definition.optional else ServiceStatus.UNKNOWN,
                    "Refresh to check current status.",
                )
            self._update_service_card(snapshot)

    def _update_service_card(self, snapshot: ServiceSnapshot) -> None:
        card = self.service_cards[snapshot.definition.service_id]
        dark = self.palette().color(QtGui.QPalette.ColorRole.Window).lightness() < 128
        presentation = status_presentation(snapshot.status, dark=dark)
        badge: QtWidgets.QLabel = card["badge"]  # type: ignore[assignment]
        badge.setText(status_text(snapshot.status))
        badge.setStyleSheet(f"color: {presentation.light_color}; font-weight: 700")
        badge.setToolTip(presentation.description)
        explanation: QtWidgets.QLabel = card["explanation"]  # type: ignore[assignment]
        explanation.setText(snapshot.explanation)
        checked: QtWidgets.QLabel = card["checked"]  # type: ignore[assignment]
        checked.setText(f"Last checked: {snapshot.last_checked}")
        card["snapshot"] = snapshot
        buttons: dict[str, QtWidgets.QPushButton] = card["buttons"]  # type: ignore[assignment]
        repository_blocked = snapshot.definition.repository_required and not self._active_repository
        controller = ServiceController(self._active_repository)
        for action, button in buttons.items():
            button.setToolTip("")
            enabled = not repository_blocked and not self._busy()
            unavailable_reason = controller.action_unavailable_reason(snapshot.definition.service_id, action)
            if unavailable_reason:
                enabled = False
                button.setToolTip(unavailable_reason)
            if snapshot.definition.service_id == "ollama" and action == "stop":
                enabled = enabled and snapshot.assistant_owned
                if not snapshot.assistant_owned:
                    button.setToolTip("Stop unavailable because this assistant does not own the Ollama process.")
            if snapshot.definition.service_id == "ollama" and action == "start":
                enabled = enabled and snapshot.status is ServiceStatus.STOPPED
                if snapshot.status is not ServiceStatus.STOPPED:
                    button.setToolTip("Start is available only when Ollama is installed but stopped.")
            button.setEnabled(enabled)
            if repository_blocked:
                button.setToolTip("Repository required")
            elif action == "pull":
                button.setToolTip("Separate explicit network/download action; never runs during Start.")
            elif action == "build":
                button.setToolTip("Separate explicit local build action; never runs during Start.")

    def _filter_services(self, category: str) -> None:
        for definition in SERVICE_REGISTRY:
            widget: QtWidgets.QWidget = self.service_cards[definition.service_id]["widget"]  # type: ignore[assignment]
            widget.setVisible(category == "All" or category == definition.category)

    def _toggle_auto_refresh(self, enabled: bool) -> None:
        if enabled:
            self.service_refresh_timer.start()
            self._refresh_services()
        else:
            self.service_refresh_timer.stop()

    def _refresh_services(self) -> None:
        if self._busy():
            return
        for definition in SERVICE_REGISTRY:
            self._update_service_card(ServiceSnapshot(definition, ServiceStatus.CHECKING, "Checking…"))
        repository = self._active_repository
        self._start_task(
            "Refreshing service status",
            lambda: ServiceController(repository).inspect_all(),
            self._services_refreshed,
        )

    def _services_refreshed(self, payload: object) -> None:
        snapshots = tuple(payload)  # type: ignore[arg-type]
        self._last_service_snapshots = snapshots
        by_id = {snapshot.definition.service_id: snapshot for snapshot in snapshots}
        for definition in SERVICE_REGISTRY:
            snapshot = by_id.get(definition.service_id)
            if snapshot:
                self._update_service_card(snapshot)
            elif definition.repository_required and not self._active_repository:
                self._update_service_card(ServiceSnapshot(definition, ServiceStatus.BLOCKED, "Repository required"))
        self._update_action_required()

    def _service_action(self, service_id: str, action: str) -> None:
        if self._busy():
            return
        definition = ServiceController.definition(service_id)
        if definition.repository_required and not self._active_repository:
            QtWidgets.QMessageBox.warning(self, "Repository required", "Select a valid TalkingSlides repository.")
            return
        controller = ServiceController(self._active_repository)
        reason = controller.action_unavailable_reason(service_id, action)
        if reason:
            QtWidgets.QMessageBox.warning(self, "Action unavailable", reason)
            return
        if action == "logs":
            self._start_task(
                f"Loading {definition.display_name} logs",
                lambda: controller.logs(service_id),
                lambda result: self._show_logs(definition, controller, result),
            )
            return
        try:
            preview = controller.preview(service_id, action)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Action unavailable", str(exc))
            return
        warning = ""
        if action == "pull":
            warning = "\n\nThis can download a large image and use network bandwidth."
        elif action == "build":
            warning = "\n\nThis can consume substantial CPU, disk, network, and time."
        elif service_id == "worker-avatar" and action in {"start", "restart"}:
            warning = "\n\nThis can consume real queued avatar work and substantial GPU resources."
        answer = QtWidgets.QMessageBox.question(
            self,
            f"Confirm {action}",
            f"Affected service: {definition.display_name}\n\n"
            + "\n".join(preview)
            + warning
            + "\n\nContinue?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        busy_status = ServiceStatus.STOPPING if action == "stop" else ServiceStatus.STARTING
        self._update_service_card(ServiceSnapshot(definition, busy_status, f"{action.title()} in progress…"))
        self.service_operation_output.setPlainText("\n".join(preview))
        self._start_task(
            f"{action.title()} {definition.display_name}",
            lambda: ServiceController(self._active_repository).execute(service_id, action, confirmed=True),
            self._service_operation_completed,
        )

    def _service_operation_completed(self, payload: object) -> None:
        result: ServiceOperationResult = payload  # type: ignore[assignment]
        lines = [f"{result.service_id}: {result.outcome or result.error}"]
        for command in result.command_results:
            lines.extend(
                (
                    f"Command: {command.display_command}",
                    f"Exit code: {command.exit_code}",
                )
            )
            if command.stdout:
                lines.append(command.stdout)
            if command.stderr:
                lines.append(command.stderr)
        self.service_operation_output.setPlainText(sanitize_text("\n".join(lines)))
        if not result.ok:
            QtWidgets.QMessageBox.warning(self, "Service operation", result.outcome or result.error)
        else:
            self.statusBar().showMessage(result.outcome, 7000)
        QtCore.QTimer.singleShot(150, self._refresh_services)

    def _show_logs(
        self,
        definition: ServiceDefinition,
        controller: ServiceController,
        payload: object,
    ) -> None:
        result: ServiceOperationResult = payload  # type: ignore[assignment]
        text = result.error
        if result.command_results:
            command = result.command_results[-1]
            text = command.stdout or command.stderr or command.error
        follow_spec = None
        if definition.service_type is ServiceType.COMPOSE:
            try:
                follow_spec = controller.follow_logs_spec(definition.service_id)
            except ValueError:
                follow_spec = None
        dialog = ServiceLogDialog(definition.display_name, text, follow_spec, self)
        dialog.exec()

    def _service_details(self, service_id: str) -> None:
        definition = ServiceController.definition(service_id)
        card = self.service_cards[service_id]
        snapshot: ServiceSnapshot | None = card["snapshot"]  # type: ignore[assignment]
        lines = [
            f"Service ID: {definition.service_id}",
            f"Type: {definition.service_type.value}",
            f"Category: {definition.category}",
            f"Optional: {definition.optional}",
            f"Repository required: {definition.repository_required}",
            f"Compose service: {definition.compose_service or 'n/a'}",
            f"Compose profile: {definition.compose_profile or 'default'}",
            f"Ports: {', '.join(str(item) for item in definition.expected_ports) or 'n/a'}",
            f"Health: {definition.health_url or 'n/a'}",
            f"Actions: {', '.join(definition.supported_actions) or 'none'}",
            f"Configuration variables: {', '.join(definition.configuration_requirements) or 'none'}",
            f"Documentation: {definition.documentation_reference or 'n/a'}",
        ]
        if snapshot:
            lines.extend(("", f"Current status: {status_text(snapshot.status)}", snapshot.explanation, snapshot.details))
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(f"{definition.display_name} details")
        box.setText("\n".join(lines))
        box.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        box.exec()

    def _service_group_action(self, action: str) -> None:
        if self._busy():
            return
        if not self._active_repository:
            QtWidgets.QMessageBox.warning(self, "Repository required", "Select a valid TalkingSlides repository.")
            return
        group_id = self.group_combo.currentData()
        if not group_id:
            return
        controller = ServiceController(self._active_repository)
        try:
            specs = controller.group_specs(group_id, action)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Group unavailable", str(exc))
            return
        previews = [
            CommandResult(
                spec.argv,
                os.fspath(spec.cwd) if spec.cwd else None,
                None,
                "",
                "",
                0,
            ).display_command
            for spec in specs
        ]
        group = next(item for item in available_service_groups(self._active_repository) if item.group_id == group_id)
        warning = f"\n\nResource use: {group.resource_usage}\nRequirements: {group.requirements}"
        if group.optional and action == "start":
            warning += "\nThis group includes optional resource-intensive services."
        answer = QtWidgets.QMessageBox.question(
            self,
            f"Confirm group {action}",
            f"Included services: {', '.join(group.service_ids)}\n\n"
            + "\n".join(previews)
            + warning,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._start_task(
            f"{action.title()} {group.display_name}",
            lambda: ServiceController(self._active_repository).execute_group(group_id, action, confirmed=True),
            self._service_operation_completed,
        )

    def _start_task(
        self,
        label: str,
        function: Callable[[], object],
        callback: Callable[[object], None],
    ) -> None:
        if self._task_thread is not None:
            return
        self.statusBar().showMessage(label)
        self._set_busy(True)
        self._task_callback = callback
        self._task_thread = QtCore.QThread(self)
        self._task_worker = TaskWorker(function)
        self._task_worker.moveToThread(self._task_thread)
        self._task_thread.started.connect(self._task_worker.run)
        self._task_worker.completed.connect(self._task_completed)
        self._task_worker.failed.connect(self._task_failed)
        self._task_worker.completed.connect(self._task_thread.quit)
        self._task_worker.failed.connect(self._task_thread.quit)
        self._task_thread.finished.connect(self._task_worker.deleteLater)
        self._task_thread.finished.connect(self._task_finished)
        self._task_thread.finished.connect(self._task_thread.deleteLater)
        self._task_thread.start()

    @QtCore.Slot(object)
    def _task_completed(self, result: object) -> None:
        if self._task_callback:
            self._task_callback(result)

    @QtCore.Slot(str)
    def _task_failed(self, message: str) -> None:
        QtWidgets.QMessageBox.critical(self, "Operation failed", sanitize_text(message))

    @QtCore.Slot()
    def _task_finished(self) -> None:
        self._task_worker = None
        self._task_thread = None
        self._task_callback = None
        self._set_busy(False)
        self.statusBar().clearMessage()

    def _busy(self) -> bool:
        return bool(
            (self._thread and self._thread.isRunning())
            or (self._task_thread and self._task_thread.isRunning())
            or (self._clone_thread and self._clone_thread.isRunning())
        )

    def _set_busy(self, busy: bool) -> None:
        if hasattr(self, "repository_selector"):
            self.repository_selector.setEnabled(not busy)
        if hasattr(self, "repository_edit"):
            self.repository_edit.setEnabled(not busy)
        for name in ("clone_url", "clone_ref", "clone_destination"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(not busy)
        if hasattr(self, "clone_button") and self._clone_thread is None:
            self.clone_button.setEnabled(not busy)
        if hasattr(self, "refresh_services_button"):
            self.refresh_services_button.setEnabled(not busy)
        for button in self._operation_buttons:
            button.setEnabled(not busy)
        if not busy and self.service_cards:
            for card in self.service_cards.values():
                snapshot = card.get("snapshot")
                if isinstance(snapshot, ServiceSnapshot):
                    self._update_service_card(snapshot)
        if hasattr(self, "group_combo"):
            group_enabled = not busy and bool(self._active_repository) and self.group_combo.count() > 0
            self.start_group_button.setEnabled(group_enabled)
            self.stop_group_button.setEnabled(group_enabled)

    def _refresh_configuration(self) -> None:
        if not hasattr(self, "configuration_tree"):
            return
        self.configuration_tree.clear()
        self.create_env_button.setEnabled(bool(self._active_repository) and not self._busy())
        self.create_env_button.setToolTip("" if self._active_repository else "Repository required")
        if not self._active_repository:
            item = QtWidgets.QTreeWidgetItem(["Repository required", "—", "—", "—", "Repository configuration"])
            self.configuration_tree.addTopLevelItem(item)
            return
        for status in inspect_configuration(self._active_repository):
            item = QtWidgets.QTreeWidgetItem(
                [
                    status.variable,
                    "Present" if status.present else "Missing",
                    "Valid" if status.valid else "Invalid",
                    "Required" if status.required else "Optional",
                    status.feature,
                ]
            )
            item.setToolTip(2, status.reason)
            self.configuration_tree.addTopLevelItem(item)
        self.configuration_tree.resizeColumnToContents(0)

    def _update_action_required(self) -> None:
        if not hasattr(self, "action_required_list"):
            return
        self.action_required_list.clear()
        items = action_required_items(
            self._active_repository,
            service_snapshots=self._last_service_snapshots,
        )
        for finding in items:
            item = QtWidgets.QTreeWidgetItem(
                [
                    finding.level.value.title(),
                    finding.title,
                    finding.affected_feature,
                    finding.next_step,
                ]
            )
            item.setToolTip(1, finding.reason)
            item.setToolTip(3, finding.documentation_reference)
            if finding.level is AttentionLevel.BLOCKING:
                status = ServiceStatus.BLOCKED
            elif finding.level in {AttentionLevel.REQUIRED, AttentionLevel.RECOMMENDED}:
                status = ServiceStatus.DEGRADED
            else:
                status = ServiceStatus.OPTIONAL
            dark = self.palette().color(QtGui.QPalette.ColorRole.Window).lightness() < 128
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(status_presentation(status, dark=dark).light_color)))
            self.action_required_list.addTopLevelItem(item)

    def _run_safe_action(self, action_id: str) -> None:
        if not self._active_repository:
            QtWidgets.QMessageBox.warning(self, "Repository required", "Select a valid TalkingSlides repository.")
            return
        try:
            preview = self._action_executor.preview(action_id, self._active_repository)
        except (ValueError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "Action unavailable", sanitize_text(str(exc)))
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
            result = self._action_executor.execute(action_id, self._active_repository, confirmed=True)
        except (ValueError, OSError) as exc:
            QtWidgets.QMessageBox.critical(self, "Action failed", sanitize_text(str(exc)))
            return
        self.statusBar().showMessage(result.summary, 7000)
        self._refresh_configuration()
        self._update_action_required()

    def _open_environment_docs(self) -> None:
        if not self._active_repository:
            QtWidgets.QMessageBox.warning(self, "Repository required", "Select a valid TalkingSlides repository.")
            return
        path = self._active_repository / "docs" / "ENVIRONMENT_VARIABLES.md"
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.fspath(path)))

    def _runtime_action(self, action: str) -> None:
        if not self._active_repository:
            QtWidgets.QMessageBox.warning(self, "Repository required", "Select a valid TalkingSlides repository.")
            return
        manager = RuntimeManager(self._active_repository)
        profile = self._selected_profile()
        preview = manager.preview(action, profile, self.no_frontend.isChecked())
        confirmed = action not in {"start", "stop"}
        if not confirmed:
            confirmed = (
                QtWidgets.QMessageBox.question(
                    self,
                    f"Confirm runtime {action}",
                    f"The assistant will run:\n\n{preview}\n\nContinue?",
                    QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.No,
                )
                == QtWidgets.QMessageBox.StandardButton.Yes
            )
        avatar_ack = action != "start" or profile is not Profile.AVATAR
        if action == "start" and profile is Profile.AVATAR and confirmed:
            avatar_ack = (
                QtWidgets.QMessageBox.warning(
                    self,
                    "Avatar queue risk",
                    "Starting avatar services can consume real queued avatar work.",
                    QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.No,
                )
                == QtWidgets.QMessageBox.StandardButton.Yes
            )
        result = manager.execute(
            action,
            profile,
            no_frontend=self.no_frontend.isChecked(),
            confirmed=confirmed,
            allow_avatar_queue_risk=avatar_ack,
        )
        lines = [f"Command: {result.preview}", result.error]
        if result.result:
            lines.extend((result.result.stdout, result.result.stderr, f"Exit code: {result.result.exit_code}"))
        self.service_operation_output.setPlainText(sanitize_text("\n".join(filter(None, lines))))

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
            QtWidgets.QMessageBox.critical(self, "Export failed", sanitize_text(str(exc)))
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
        border = "#454d5c" if dark else "#c7d0dd"
        self.setStyleSheet(
            f"""
            QWidget {{ color: {text}; font-size: 14px; }}
            QMainWindow, QStackedWidget {{ background: {surface}; }}
            QFrame#appHeader {{ background: {navigation}; border-bottom: 1px solid {border}; }}
            QLabel#headerTitle {{ font-size: 17px; font-weight: 700; }}
            QListWidget#navigation {{ background: {navigation}; border: 0; padding: 18px 8px; }}
            QListWidget#navigation::item {{ padding: 12px 14px; margin: 2px 0; border-radius: 7px; }}
            QListWidget#navigation::item:selected {{ background: #246bfd; color: white; }}
            QLabel#pageTitle {{ font-size: 26px; font-weight: 700; }}
            QLabel#pageSubtitle {{ color: {muted}; font-size: 13px; }}
            QLabel#hero {{ font-size: 38px; font-weight: 700; margin-top: 10px; }}
            QLabel#infoCard {{ background: {navigation}; border: 1px solid {border}; border-radius: 8px; padding: 12px; }}
            QFrame#serviceCard {{ border: 1px solid {border}; border-radius: 8px; padding: 8px; }}
            QLabel#serviceName {{ font-size: 17px; font-weight: 700; }}
            QPushButton {{ min-height: 32px; padding: 3px 12px; border: 1px solid {border}; border-radius: 6px; }}
            QPushButton:hover {{ border-color: #246bfd; }}
            QPushButton:focus {{ border: 2px solid #246bfd; }}
            QPushButton:disabled {{ color: {muted}; }}
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
