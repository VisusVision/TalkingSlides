import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from worker import avatar_preview_flow  # noqa: E402
from worker.avatar_timeout_policy import (  # noqa: E402
    PreviewTaskTimeLimitConfigError,
    resolve_preview_task_time_limits,
)


def test_render_avatar_preview_task_limit_exceeds_musetalk_adaptive_timeout() -> None:
    limits = resolve_preview_task_time_limits(
        {
            "AVATAR_ORCH_TTS_TIMEOUT_MAX_SECONDS": "360",
            "AVATAR_ORCH_LIVEPORTRAIT_TIMEOUT_MAX_SECONDS": "900",
            "AVATAR_PREVIEW_MUSETALK_TIMEOUT_MAX_SECONDS": "1495",
            "AVATAR_LOW_VRAM_MUSETALK_TIMEOUT_MULTIPLIER": "1.0",
            "AVATAR_PREVIEW_USE_RESTORATION": "0",
        }
    )

    assert limits.soft_seconds >= 3600
    assert limits.soft_seconds > 1495
    assert limits.soft_seconds > limits.stage_maxima_seconds["musetalk"]
    assert limits.hard_seconds > limits.soft_seconds


def test_stage_timeout_cannot_exceed_parent_soft_limit_without_config_error() -> None:
    with pytest.raises(PreviewTaskTimeLimitConfigError) as excinfo:
        resolve_preview_task_time_limits(
            {
                "AVATAR_PREVIEW_TASK_SOFT_TIME_LIMIT_SECONDS": "900",
                "AVATAR_PREVIEW_TASK_HARD_TIME_LIMIT_SECONDS": "960",
                "AVATAR_ORCH_TTS_TIMEOUT_MAX_SECONDS": "360",
                "AVATAR_ORCH_LIVEPORTRAIT_TIMEOUT_MAX_SECONDS": "900",
                "AVATAR_PREVIEW_MUSETALK_TIMEOUT_MAX_SECONDS": "1495",
                "AVATAR_LOW_VRAM_MUSETALK_TIMEOUT_MULTIPLIER": "1.0",
                "AVATAR_PREVIEW_USE_RESTORATION": "0",
            }
        )

    message = str(excinfo.value)
    assert "soft time limit must be greater than the largest stage timeout" in message
    assert "soft=900s" in message


def test_soft_time_limit_diagnostics_are_preview_task_classification() -> None:
    diagnostics = avatar_preview_flow._build_soft_time_limit_diagnostics(
        {
            "task_started_at": 10.0,
            "stage_started_at": 520.0,
            "current_stage": "preview_musetalk",
            "stage_timeout_budget_seconds": 1495.0,
            "task_soft_limit_seconds": 900,
            "task_hard_limit_seconds": 960,
            "liveportrait_completed": True,
            "musetalk_started": True,
        },
        now=899.5,
    )
    message = avatar_preview_flow._format_soft_time_limit_error(diagnostics)

    assert diagnostics["classification"] == "preview_task_soft_time_limit_exceeded"
    assert diagnostics["current_stage"] == "preview_musetalk"
    assert diagnostics["elapsed_total_task_seconds"] == pytest.approx(889.5)
    assert diagnostics["stage_elapsed_seconds"] == pytest.approx(379.5)
    assert diagnostics["stage_timeout_budget_seconds"] == pytest.approx(1495.0)
    assert diagnostics["task_soft_limit_seconds"] == 900
    assert diagnostics["task_hard_limit_seconds"] == 960
    assert diagnostics["liveportrait_completed"] is True
    assert diagnostics["musetalk_started"] is True
    assert message.startswith("preview_task_soft_time_limit_exceeded:")
    assert "musetalk_failed" not in message


def test_avatar_gpu_serialization_and_concurrency_config_are_enforced_or_documented() -> None:
    tasks_py = (REPO_ROOT / "services" / "worker" / "tasks.py").read_text(encoding="utf-8")
    dockerfile = (REPO_ROOT / "infra" / "dockerfiles" / "Dockerfile.worker").read_text(encoding="utf-8")
    compose = (REPO_ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    env_example = (REPO_ROOT / "infra" / ".env.example").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "services" / "worker" / "README_parallel.md").read_text(encoding="utf-8")

    assert "AVATAR_GPU_SERIAL_LOCK_ENABLED" in tasks_py
    assert "_avatar_gpu_serial_section" in tasks_py
    assert "--concurrency=${CELERY_WORKER_CONCURRENCY:-${CELERY_CONCURRENCY:-1}}" in dockerfile
    assert "--concurrency=$${CELERY_WORKER_CONCURRENCY:-$${CELERY_CONCURRENCY:-1}}" in compose
    assert "AVATAR_GPU_SERIAL_LOCK_ENABLED" in env_example
    assert "MUSETALK_CHUNK_MAX_SECONDS" in env_example
    assert "AVATAR_GPU_SERIAL_LOCK_ENABLED" in readme
