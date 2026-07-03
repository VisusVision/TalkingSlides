from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ServiceStatus(str, Enum):
    CHECKING = "checking"
    HEALTHY = "healthy"
    RUNNING = "running"
    STARTING = "starting"
    STOPPING = "stopping"
    DEGRADED = "degraded"
    STOPPED = "stopped"
    BLOCKED = "blocked"
    NOT_CONFIGURED = "not_configured"
    OPTIONAL = "optional"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StatusPresentation:
    label: str
    icon: str
    light_color: str
    dark_color: str
    description: str


STATUS_PRESENTATIONS: dict[ServiceStatus, StatusPresentation] = {
    ServiceStatus.CHECKING: StatusPresentation(
        "Checking", "…", "#075CBD", "#75B7FF", "A status check or operation is in progress."
    ),
    ServiceStatus.HEALTHY: StatusPresentation(
        "Healthy", "✓", "#087A4B", "#65D6A5", "The service responded successfully to its health check."
    ),
    ServiceStatus.RUNNING: StatusPresentation(
        "Running", "●", "#087A4B", "#65D6A5", "The service is running."
    ),
    ServiceStatus.STARTING: StatusPresentation(
        "Starting", "↗", "#9A5700", "#FFC06A", "The service is starting; wait for its health check."
    ),
    ServiceStatus.STOPPING: StatusPresentation(
        "Stopping", "↘", "#9A5700", "#FFC06A", "The service is stopping."
    ),
    ServiceStatus.DEGRADED: StatusPresentation(
        "Degraded", "⚠", "#9A5700", "#FFC06A", "The service is available but needs attention."
    ),
    ServiceStatus.STOPPED: StatusPresentation(
        "Stopped", "■", "#B4232D", "#FF8A92", "The service is installed or configured but is not running."
    ),
    ServiceStatus.BLOCKED: StatusPresentation(
        "Blocked", "⊘", "#B4232D", "#FF8A92", "A required prerequisite prevents this service from running."
    ),
    ServiceStatus.NOT_CONFIGURED: StatusPresentation(
        "Not configured", "○", "#596579", "#B4BECD", "Configuration required by this service is missing."
    ),
    ServiceStatus.OPTIONAL: StatusPresentation(
        "Optional", "◇", "#596579", "#B4BECD", "This service is optional for the selected feature set."
    ),
    ServiceStatus.FAILED: StatusPresentation(
        "Failed", "✕", "#B4232D", "#FF8A92", "The last check or operation failed."
    ),
    ServiceStatus.UNKNOWN: StatusPresentation(
        "Unknown", "?", "#596579", "#B4BECD", "The current status could not be determined."
    ),
}


def status_presentation(status: ServiceStatus, *, dark: bool = False) -> StatusPresentation:
    presentation = STATUS_PRESENTATIONS[status]
    if not dark:
        return presentation
    return StatusPresentation(
        presentation.label,
        presentation.icon,
        presentation.dark_color,
        presentation.dark_color,
        presentation.description,
    )


def status_text(status: ServiceStatus) -> str:
    presentation = STATUS_PRESENTATIONS[status]
    return f"{presentation.icon} {presentation.label}"
