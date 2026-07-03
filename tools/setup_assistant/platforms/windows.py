from __future__ import annotations

import os
from pathlib import Path

from ..models import CheckResult, CheckStatus, Profile, Severity
from ..runner import CommandRunner, CommandSpec


def _command_result(
    runner: CommandRunner,
    *,
    check_id: str,
    title: str,
    argv: tuple[str, ...],
    missing_summary: str,
    remediation: str,
    warning_on_failure: bool = True,
) -> CheckResult:
    result = runner.run(CommandSpec.create(argv, timeout_seconds=8))
    if result.ok:
        summary = (result.stdout.strip() or result.stderr.strip() or "Available.").splitlines()[0]
        return CheckResult(
            check_id,
            title,
            "Windows",
            CheckStatus.PASS,
            Severity.INFO,
            summary,
            diagnostic_data={"exit_code": result.exit_code},
            duration_ms=result.duration_ms,
        )
    return CheckResult(
        check_id,
        title,
        "Windows",
        CheckStatus.WARNING if warning_on_failure else CheckStatus.FAILURE,
        Severity.MEDIUM if warning_on_failure else Severity.HIGH,
        missing_summary,
        technical_details=result.error or result.stderr.strip() or result.stdout.strip(),
        remediation=remediation,
        diagnostic_data={"exit_code": result.exit_code, "executable_found": result.exit_code is not None},
        duration_ms=result.duration_ms,
    )


def windows_checks(
    repository: Path | None,
    runner: CommandRunner,
    profile: Profile,
    full: bool,
    **_: object,
) -> list[CheckResult]:
    results = [
        _command_result(
            runner,
            check_id="windows.powershell",
            title="PowerShell",
            argv=(
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$PSVersionTable.PSVersion.ToString()",
            ),
            missing_summary="Windows PowerShell was not available.",
            remediation="Enable Windows PowerShell 5.1 or install PowerShell 7.",
            warning_on_failure=False,
        ),
        _command_result(
            runner,
            check_id="windows.wsl",
            title="WSL2",
            argv=("wsl.exe", "--status"),
            missing_summary="WSL2 was not available or is not configured.",
            remediation="Use `wsl --status` and enable WSL2 before configuring Docker Desktop integration.",
        ),
    ]
    powershell7 = _command_result(
        runner,
        check_id="windows.powershell7",
        title="PowerShell 7",
        argv=("pwsh.exe", "-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"),
        missing_summary="PowerShell 7 is not installed; Windows PowerShell 5.1 remains supported.",
        remediation="Install PowerShell 7 only if desired; it is not required for diagnostics.",
    )
    results.append(powershell7)
    docker_desktop_paths = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Docker" / "Docker Desktop.exe",
    )
    installed = any(path.is_file() for path in docker_desktop_paths)
    results.append(
        CheckResult(
            "windows.docker_desktop",
            "Docker Desktop installation",
            "Windows",
            CheckStatus.PASS if installed else CheckStatus.WARNING,
            Severity.HIGH if not installed else Severity.INFO,
            "Docker Desktop installation was found." if installed else "Docker Desktop executable was not found in standard locations.",
            remediation="Install Docker Desktop manually from its official installer." if not installed else "",
            diagnostic_data={"installed": installed},
        )
    )
    results.append(
        _command_result(
            runner,
            check_id="windows.virtualization",
            title="Virtualization",
            argv=(
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-CimInstance Win32_ComputerSystem).HypervisorPresent",
            ),
            missing_summary="Virtualization support could not be confirmed.",
            remediation="Check virtualization in Task Manager and firmware settings; the assistant will not change firmware.",
        )
    )
    long_path_risk = bool(repository and len(os.fspath(repository)) > 120)
    results.append(
        CheckResult(
            "windows.path_length",
            "Windows path length",
            "Windows",
            CheckStatus.WARNING if long_path_risk else CheckStatus.PASS,
            Severity.MEDIUM if long_path_risk else Severity.INFO,
            f"Repository path length is {len(os.fspath(repository)) if repository else 0} characters."
            if repository
            else "Repository is not selected.",
            remediation="Choose a shorter repository path if native tools report path-length failures." if long_path_risk else "",
            diagnostic_data={"path_length": len(os.fspath(repository)) if repository else None},
        )
    )
    if profile is Profile.AVATAR:
        gpu = _command_result(
            runner,
            check_id="windows.nvidia_smi",
            title="NVIDIA driver and GPU",
            argv=("nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"),
            missing_summary="NVIDIA tools were not available.",
            remediation="Install a compatible NVIDIA driver manually before using the avatar profile.",
        )
        gpu.profile = Profile.AVATAR.value
        gpu.expensive = False
        results.append(gpu)
    if full:
        policy = _command_result(
            runner,
            check_id="windows.execution_policy",
            title="Execution policy",
            argv=(
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-ExecutionPolicy -Scope Process).ToString()",
            ),
            missing_summary="Execution policy could not be read.",
            remediation="Use process-scoped `-ExecutionPolicy Bypass` only for trusted repository scripts.",
        )
        results.append(policy)
    else:
        results.append(
            CheckResult(
                "windows.execution_policy",
                "Execution policy guidance",
                "Windows",
                CheckStatus.SKIPPED,
                Severity.INFO,
                "Detailed execution-policy inspection runs during Full Check.",
                remediation="Use process-scoped `-ExecutionPolicy Bypass` for signed repository scripts when necessary.",
            )
        )
    return results
