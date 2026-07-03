from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

APP_NAME = "TalkingSlides Setup Assistant"
VERSION = os.environ.get("TALKINGSLIDES_SETUP_ASSISTANT_VERSION", "0.1.0")


class CheckStatus(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    FAILURE = "failure"
    SKIPPED = "skipped"
    RUNNING = "running"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Profile(str, Enum):
    CORE = "core"
    TTS = "tts"
    AVATAR = "avatar"


@dataclass(frozen=True)
class SafeAction:
    action_id: str
    title: str
    description: str
    command: tuple[str, ...] = ()
    confirmation_required: bool = True
    reversible: bool = True
    destructive: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["command"] = list(self.command)
        return data


@dataclass
class CheckResult:
    check_id: str
    title: str
    category: str
    status: CheckStatus
    severity: Severity
    summary: str
    technical_details: str = ""
    remediation: str = ""
    safe_action: SafeAction | None = None
    documentation_reference: str = ""
    duration_ms: int = 0
    diagnostic_data: dict[str, Any] = field(default_factory=dict)
    profile: str = "all"
    expensive: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["severity"] = self.severity.value
        if self.safe_action:
            data["safe_action"] = self.safe_action.to_dict()
        return data


@dataclass
class CheckRun:
    profile: Profile
    mode: str
    platform: str
    results: list[CheckResult]
    started_at: str
    duration_ms: int
    repository: str | None = None
    application_version: str = VERSION

    @property
    def counts(self) -> dict[str, int]:
        return {
            status.value: sum(item.status is status for item in self.results)
            for status in CheckStatus
        }

    @property
    def status(self) -> CheckStatus:
        statuses = {item.status for item in self.results}
        if CheckStatus.FAILURE in statuses:
            return CheckStatus.FAILURE
        if CheckStatus.WARNING in statuses:
            return CheckStatus.WARNING
        if CheckStatus.RUNNING in statuses:
            return CheckStatus.RUNNING
        if CheckStatus.PASS in statuses:
            return CheckStatus.PASS
        return CheckStatus.SKIPPED

    def to_dict(self) -> dict[str, Any]:
        return {
            "application": APP_NAME,
            "application_version": self.application_version,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "platform": self.platform,
            "profile": self.profile.value,
            "mode": self.mode,
            "repository": self.repository,
            "status": self.status.value,
            "counts": self.counts,
            "results": [item.to_dict() for item in self.results],
        }
