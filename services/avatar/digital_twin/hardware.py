"""Resource-aware inference defaults for local digital-twin workers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import subprocess
from typing import MutableMapping


@dataclass(frozen=True)
class GpuSnapshot:
    name: str = ""
    total_mib: int = 0
    free_mib: int = 0
    driver_version: str = ""


@dataclass(frozen=True)
class InferenceProfile:
    name: str
    gpu: GpuSnapshot
    defaults: dict[str, str]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["warnings"] = list(self.warnings)
        return payload


def probe_nvidia_gpu() -> GpuSnapshot:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return GpuSnapshot()
    if result.returncode != 0:
        return GpuSnapshot()
    candidates: list[GpuSnapshot] = []
    for row in result.stdout.splitlines():
        parts = [part.strip() for part in row.split(",")]
        if len(parts) < 4:
            continue
        try:
            candidates.append(
                GpuSnapshot(
                    name=parts[0],
                    total_mib=int(parts[1]),
                    free_mib=int(parts[2]),
                    driver_version=parts[3],
                )
            )
        except ValueError:
            continue
    return max(candidates, key=lambda item: item.free_mib, default=GpuSnapshot())


def select_inference_profile(gpu: GpuSnapshot) -> InferenceProfile:
    common = {
        "MUSETALK_USE_FLOAT16": "1",
        "CELERY_AVATAR_WORKER_CONCURRENCY": "1",
        "AVATAR_MUSETALK_SERVICE_ENABLED": "1",
    }
    warnings: list[str] = []
    if gpu.total_mib <= 0:
        return InferenceProfile("gpu_unknown", gpu, common, ("nvidia_gpu_not_detected",))
    if gpu.total_mib <= 8704:
        defaults = {
            **common,
            "MUSETALK_BATCH_SIZE": "2",
            "AVATAR_PREVIEW_MUSETALK_BATCH_SIZE": "1",
            "AVATAR_LOW_VRAM_MUSETALK_BATCH_SIZE": "1",
            "MUSETALK_LOW_VRAM_BATCH_SIZE": "1",
            "MUSETALK_CHUNK_MAX_SECONDS": "8",
            "MUSETALK_SERVICE_CLEAR_CACHE_EACH_BATCH": "1",
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True,max_split_size_mb:128",
            "DIGITAL_TWIN_MAX_PARALLEL_GPU_JOBS": "1",
            "DIGITAL_TWIN_DELIVERY_MAX_HEIGHT": "720",
        }
        if gpu.free_mib < 5500:
            warnings.append("close_gpu_apps_before_render")
        if gpu.free_mib < 3000:
            warnings.append("high_oom_risk")
        return InferenceProfile("ada_laptop_8gb", gpu, defaults, tuple(warnings))
    if gpu.total_mib <= 16384:
        return InferenceProfile(
            "local_12_16gb",
            gpu,
            {
                **common,
                "MUSETALK_BATCH_SIZE": "4",
                "MUSETALK_CHUNK_MAX_SECONDS": "12",
                "DIGITAL_TWIN_MAX_PARALLEL_GPU_JOBS": "1",
                "DIGITAL_TWIN_DELIVERY_MAX_HEIGHT": "1080",
            },
        )
    return InferenceProfile(
        "workstation_24gb_plus",
        gpu,
        {
            **common,
            "MUSETALK_BATCH_SIZE": "8",
            "MUSETALK_CHUNK_MAX_SECONDS": "15",
            "DIGITAL_TWIN_MAX_PARALLEL_GPU_JOBS": "1",
            "DIGITAL_TWIN_DELIVERY_MAX_HEIGHT": "1080",
        },
    )


def apply_local_inference_profile(
    environ: MutableMapping[str, str] | None = None,
    *,
    gpu: GpuSnapshot | None = None,
) -> InferenceProfile:
    """Apply safe defaults without replacing explicit operator settings."""

    target = environ if environ is not None else os.environ
    profile = select_inference_profile(gpu or probe_nvidia_gpu())
    for key, value in profile.defaults.items():
        target.setdefault(key, value)
    return profile
