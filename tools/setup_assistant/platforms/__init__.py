from __future__ import annotations

import platform

from .common import common_platform_checks


def platform_checks(repository, runner, profile, full):
    results = common_platform_checks(repository=repository)
    system = platform.system()
    if system == "Windows":
        from .windows import windows_checks

        results.extend(windows_checks(repository=repository, runner=runner, profile=profile, full=full))
    elif system == "Linux":
        from .linux import linux_checks

        results.extend(linux_checks(repository=repository, runner=runner, profile=profile, full=full))
    return results


__all__ = ["platform_checks"]
