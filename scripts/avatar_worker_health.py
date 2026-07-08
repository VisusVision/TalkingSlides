#!/usr/bin/env python3
"""Container health check for the avatar worker.

The check is read-only and does not touch Celery queues. It verifies that CUDA
is visible to PyTorch, the MuseTalk model bundle is present, and the persistent
MuseTalk service has loaded models and is ready for inference.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any


def _fail(code: str, detail: Any) -> int:
    print(json.dumps({"ok": False, "code": code, "detail": detail}, sort_keys=True))
    return 1


def _ok(payload: dict[str, Any]) -> int:
    payload = {"ok": True, **payload}
    print(json.dumps(payload, sort_keys=True))
    return 0


def _check_cuda() -> dict[str, Any]:
    try:
        import torch  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on image runtime
        raise RuntimeError(f"torch import failed: {exc}") from exc

    available = bool(torch.cuda.is_available())
    count = int(torch.cuda.device_count()) if available else 0
    if not available or count <= 0:
        raise RuntimeError(f"CUDA is not visible to torch: available={available} count={count}")
    current = int(torch.cuda.current_device())
    return {
        "cuda_available": available,
        "cuda_device_count": count,
        "cuda_current_device": current,
        "cuda_device_name": str(torch.cuda.get_device_name(current)),
    }


def _check_model_bundle() -> dict[str, Any]:
    scripts_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(scripts_dir))
    from check_avatar_models import check_model_bundle  # type: ignore

    model_root = os.environ.get("MUSETALK_MODEL_PATH", "/app/storage_local/models")
    result = check_model_bundle(model_root)
    if not result.get("complete"):
        raise RuntimeError(
            "model bundle incomplete: "
            + json.dumps(
                {
                    "model_root": result.get("model_root"),
                    "missing_files": result.get("missing_files"),
                    "empty_files": result.get("empty_files"),
                    "errors": result.get("errors"),
                },
                sort_keys=True,
            )
        )
    return {
        "model_root": result.get("model_root"),
        "model_bundle_complete": True,
    }


def _check_musetalk_service() -> dict[str, Any]:
    port = int(os.environ.get("AVATAR_MUSETALK_SERVICE_PORT", "17860"))
    url = os.environ.get("AVATAR_WORKER_HEALTH_URL", f"http://127.0.0.1:{port}/health")
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") != "ready":
        raise RuntimeError(f"MuseTalk service is not ready: {payload}")
    if payload.get("cuda_available") is not True:
        raise RuntimeError(f"MuseTalk service does not report CUDA available: {payload}")
    if payload.get("models_loaded") is not True:
        raise RuntimeError(f"MuseTalk service has not loaded models: {payload}")
    if payload.get("ready_for_inference") is not True:
        raise RuntimeError(f"MuseTalk service is not ready for inference: {payload}")
    return {
        "musetalk_health_url": url,
        "musetalk_status": payload.get("status"),
        "musetalk_models_loaded": bool(payload.get("models_loaded")),
        "musetalk_ready_for_inference": bool(payload.get("ready_for_inference")),
        "musetalk_cuda_available": bool(payload.get("cuda_available")),
        "musetalk_cuda_device": payload.get("cuda_device", ""),
    }


def main() -> int:
    try:
        cuda = _check_cuda()
        models = _check_model_bundle()
        service = _check_musetalk_service()
    except Exception as exc:
        return _fail("avatar_worker_unhealthy", str(exc))
    return _ok({**cuda, **models, **service})


if __name__ == "__main__":
    raise SystemExit(main())
