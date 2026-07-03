from __future__ import annotations

import os
import shutil
from pathlib import Path

try:
    import grp
except ImportError:  # pragma: no cover - allows Linux adapter unit tests on Windows.
    grp = None

from ..models import CheckResult, CheckStatus, Profile, Severity
from ..runner import CommandRunner, CommandSpec


def _os_release() -> tuple[str, str]:
    values: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')
    except OSError:
        pass
    return values.get("NAME", "Linux"), values.get("VERSION_ID", "unknown")


def linux_checks(
    repository: Path | None,
    runner: CommandRunner,
    profile: Profile,
    full: bool,
    **_: object,
) -> list[CheckResult]:
    distribution, version = _os_release()
    results = [
        CheckResult(
            "linux.distribution",
            "Linux distribution",
            "Linux",
            CheckStatus.PASS,
            Severity.INFO,
            f"{distribution} {version}.",
            diagnostic_data={"distribution": distribution, "version": version},
        )
    ]
    socket_path = Path(os.environ.get("DOCKER_HOST", "").removeprefix("unix://") or "/var/run/docker.sock")
    if socket_path.exists():
        socket_access = os.access(socket_path, os.R_OK | os.W_OK)
        summary = "Current user can access the Docker socket." if socket_access else "Docker socket exists but is not readable/writable by the current user."
        results.append(
            CheckResult(
                "linux.docker_socket",
                "Docker socket permission",
                "Linux",
                CheckStatus.PASS if socket_access else CheckStatus.FAILURE,
                Severity.HIGH if not socket_access else Severity.INFO,
                summary,
                remediation="Review rootless Docker or docker-group setup; no group membership will be changed automatically." if not socket_access else "",
                diagnostic_data={"socket_exists": True, "accessible": socket_access},
            )
        )
    else:
        results.append(
            CheckResult(
                "linux.docker_socket",
                "Docker socket permission",
                "Linux",
                CheckStatus.WARNING,
                Severity.MEDIUM,
                "The default Docker socket was not found; rootless Docker may use a different socket.",
                remediation="Check `DOCKER_HOST` and the rootless Docker service.",
                diagnostic_data={"socket_exists": False, "rootless_hint": bool(os.environ.get("DOCKER_HOST"))},
            )
        )
    try:
        groups = {grp.getgrgid(group_id).gr_name for group_id in os.getgroups()} if grp else set()
    except (KeyError, OSError):
        groups = set()
    results.append(
        CheckResult(
            "linux.docker_group",
            "Docker group membership",
            "Linux",
            CheckStatus.PASS if "docker" in groups else CheckStatus.WARNING,
            Severity.LOW,
            "Current user belongs to the docker group." if "docker" in groups else "Current user is not in the docker group; rootless Docker may still work.",
            remediation="If required, review the distribution's Docker post-install guidance and explicitly choose whether to change group membership.",
            diagnostic_data={"docker_group_member": "docker" in groups},
        )
    )
    systemd = shutil.which("systemctl") is not None and Path("/run/systemd/system").exists()
    results.append(
        CheckResult(
            "linux.systemd",
            "Service manager",
            "Linux",
            CheckStatus.PASS if systemd else CheckStatus.SKIPPED,
            Severity.INFO,
            "systemd is available." if systemd else "systemd is not active; service checks will use Docker directly.",
            diagnostic_data={"systemd_available": systemd},
        )
    )
    display = "wayland" if os.environ.get("WAYLAND_DISPLAY") else ("x11" if os.environ.get("DISPLAY") else "")
    results.append(
        CheckResult(
            "linux.display",
            "Desktop display",
            "Linux",
            CheckStatus.PASS if display else CheckStatus.WARNING,
            Severity.MEDIUM if not display else Severity.INFO,
            f"{display.upper()} display detected." if display else "No X11 or Wayland display variable was detected.",
            remediation="Use CLI mode on headless systems or launch the GUI from a desktop session." if not display else "",
            diagnostic_data={"display": display or None},
        )
    )
    if repository:
        executable = os.access(repository, os.X_OK)
        results.append(
            CheckResult(
                "linux.repository_permissions",
                "Repository directory permissions",
                "Linux",
                CheckStatus.PASS if executable else CheckStatus.FAILURE,
                Severity.HIGH if not executable else Severity.INFO,
                "Repository directory is traversable." if executable else "Repository directory is not traversable.",
                remediation="Repair local directory execute permissions after reviewing the exact path." if not executable else "",
                diagnostic_data={"traversable": executable},
            )
        )
    if profile is Profile.AVATAR:
        nvidia = runner.run(CommandSpec.create(("nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"), timeout_seconds=8))
        results.append(
            CheckResult(
                "linux.nvidia_smi",
                "NVIDIA driver and GPU",
                "Linux",
                CheckStatus.PASS if nvidia.ok else CheckStatus.WARNING,
                Severity.HIGH if not nvidia.ok else Severity.INFO,
                (nvidia.stdout.strip().splitlines() or ["NVIDIA tools were not available."])[0],
                technical_details=nvidia.error or nvidia.stderr.strip(),
                remediation="Install a compatible NVIDIA driver manually before using the avatar profile." if not nvidia.ok else "",
                diagnostic_data={"exit_code": nvidia.exit_code},
                profile=Profile.AVATAR.value,
                duration_ms=nvidia.duration_ms,
            )
        )
        toolkit = runner.run(CommandSpec.create(("nvidia-container-cli", "--version"), timeout_seconds=8))
        results.append(
            CheckResult(
                "linux.nvidia_container_toolkit",
                "NVIDIA Container Toolkit",
                "Linux",
                CheckStatus.PASS if toolkit.ok else CheckStatus.WARNING,
                Severity.HIGH if not toolkit.ok else Severity.INFO,
                "NVIDIA Container Toolkit is available." if toolkit.ok else "NVIDIA Container Toolkit was not found.",
                technical_details=toolkit.error or toolkit.stderr.strip(),
                remediation="Install/configure the toolkit manually before starting avatar containers." if not toolkit.ok else "",
                diagnostic_data={"exit_code": toolkit.exit_code},
                profile=Profile.AVATAR.value,
                duration_ms=toolkit.duration_ms,
            )
        )
    return results
