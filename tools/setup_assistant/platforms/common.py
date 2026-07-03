from __future__ import annotations

import ctypes
import os
import platform
import shutil
from pathlib import Path

from ..models import CheckResult, CheckStatus, Severity


def _memory_gib() -> float | None:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return round(status.ullTotalPhys / (1024**3), 1)
        return None
    meminfo = Path("/proc/meminfo")
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / (1024**2), 1)
    except (OSError, ValueError, IndexError):
        return None
    return None


def common_platform_checks(repository: Path | None, **_: object) -> list[CheckResult]:
    results: list[CheckResult] = []
    system = platform.system()
    machine = platform.machine()
    supported = system in {"Windows", "Linux"} and machine.lower() in {
        "amd64",
        "x86_64",
        "arm64",
        "aarch64",
    }
    results.append(
        CheckResult(
            "system.platform",
            "Operating system and architecture",
            "Requirements",
            CheckStatus.PASS if supported else CheckStatus.FAILURE,
            Severity.CRITICAL if not supported else Severity.INFO,
            f"{system} {platform.release()} on {machine}.",
            remediation="Use a supported 64-bit Windows or Linux host." if not supported else "",
            diagnostic_data={"system": system, "release": platform.release(), "architecture": machine},
        )
    )
    cpus = os.cpu_count() or 0
    results.append(
        CheckResult(
            "system.cpu",
            "CPU",
            "Requirements",
            CheckStatus.PASS if cpus >= 4 else CheckStatus.WARNING,
            Severity.MEDIUM if cpus < 4 else Severity.INFO,
            f"{cpus or 'Unknown'} logical CPU(s) detected.",
            remediation="Four or more logical CPUs are recommended." if cpus < 4 else "",
            diagnostic_data={"logical_cpu_count": cpus},
        )
    )
    memory = _memory_gib()
    memory_status = CheckStatus.PASS if memory is not None and memory >= 8 else CheckStatus.WARNING
    results.append(
        CheckResult(
            "system.memory",
            "Memory",
            "Requirements",
            memory_status,
            Severity.MEDIUM if memory_status is CheckStatus.WARNING else Severity.INFO,
            f"{memory:.1f} GiB RAM detected." if memory is not None else "RAM size could not be determined.",
            remediation="At least 8 GiB RAM is recommended." if memory_status is CheckStatus.WARNING else "",
            diagnostic_data={"total_gib": memory},
        )
    )
    target = repository or Path.home()
    try:
        free_gib = round(shutil.disk_usage(target).free / (1024**3), 1)
    except OSError:
        free_gib = None
    disk_status = CheckStatus.PASS if free_gib is not None and free_gib >= 20 else CheckStatus.WARNING
    results.append(
        CheckResult(
            "system.disk",
            "Free disk space",
            "Requirements",
            disk_status,
            Severity.HIGH if disk_status is CheckStatus.WARNING else Severity.INFO,
            f"{free_gib:.1f} GiB free near the selected repository." if free_gib is not None else "Free space could not be measured.",
            remediation="Keep at least 20 GiB free; avatar models require substantially more." if disk_status is CheckStatus.WARNING else "",
            diagnostic_data={"free_gib": free_gib},
        )
    )
    return results
