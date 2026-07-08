#!/usr/bin/env python3
"""Cloud-safe avatar deployment preflight.

This script is read-only except for a temporary write/delete probe under
STORAGE_ROOT. It does not start workers and does not consume Celery queues.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_avatar_models import check_model_bundle  # noqa: E402


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout,
    )


def _check_nvidia_smi() -> CheckResult:
    if shutil.which("nvidia-smi") is None:
        return CheckResult("nvidia_smi", False, {"error": "nvidia-smi not found in PATH"})
    try:
        proc = _run(["nvidia-smi", "-L"], timeout=15)
    except Exception as exc:
        return CheckResult("nvidia_smi", False, {"error": str(exc)})
    return CheckResult(
        "nvidia_smi",
        proc.returncode == 0,
        {"returncode": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()},
    )


def _normalize_gpus_arg(value: str) -> str:
    text = str(value or "all").strip()
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1]
    return text or "all"


def _check_docker_gpu(cuda_image: str, gpu_device: str) -> CheckResult:
    if shutil.which("docker") is None:
        return CheckResult("docker_gpu", False, {"error": "docker not found in PATH"})
    gpus_arg = _normalize_gpus_arg(gpu_device)
    try:
        proc = _run(
            ["docker", "run", "--rm", "--gpus", gpus_arg, cuda_image, "nvidia-smi", "-L"],
            timeout=120,
        )
    except Exception as exc:
        return CheckResult("docker_gpu", False, {"error": str(exc), "gpus": gpus_arg})
    return CheckResult(
        "docker_gpu",
        proc.returncode == 0,
        {
            "returncode": proc.returncode,
            "gpus": gpus_arg,
            "image": cuda_image,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        },
    )


def _check_models(model_path: Path) -> CheckResult:
    result = check_model_bundle(model_path)
    return CheckResult(
        "model_bundle",
        bool(result.get("complete")),
        {
            "model_root": result.get("model_root"),
            "root_exists": result.get("root_exists"),
            "complete": result.get("complete"),
            "missing_files": result.get("missing_files"),
            "empty_files": result.get("empty_files"),
            "errors": result.get("errors"),
        },
    )


def _check_storage_writable(storage_root: Path) -> CheckResult:
    try:
        storage_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".avatar-preflight-", dir=storage_root, delete=True) as handle:
            handle.write(b"ok")
            handle.flush()
        return CheckResult("storage_writable", True, {"storage_root": str(storage_root)})
    except Exception as exc:
        return CheckResult("storage_writable", False, {"storage_root": str(storage_root), "error": str(exc)})


def _redis_host_port(redis_url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(redis_url)
    if parsed.scheme not in {"redis", "rediss"}:
        raise ValueError(f"unsupported Redis URL scheme: {parsed.scheme}")
    return parsed.hostname or "localhost", int(parsed.port or 6379)


def _check_redis(redis_url: str) -> CheckResult:
    try:
        host, port = _redis_host_port(redis_url)
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(b"*1\r\n$4\r\nPING\r\n")
            data = sock.recv(64)
        ok = data.startswith(b"+PONG") or b"PONG" in data
        return CheckResult("redis_reachable", ok, {"redis_url": redis_url, "response": data.decode("utf-8", "replace")})
    except Exception as exc:
        return CheckResult("redis_reachable", False, {"redis_url": redis_url, "error": str(exc)})


def _check_worker_health(url: str) -> CheckResult:
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        ok = bool(
            payload.get("status") == "ready"
            and payload.get("cuda_available") is True
            and payload.get("models_loaded") is True
            and payload.get("ready_for_inference") is True
        )
        return CheckResult("worker_avatar_health", ok, {"url": url, "payload": payload})
    except Exception as exc:
        return CheckResult("worker_avatar_health", False, {"url": url, "error": str(exc)})


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run cloud avatar deployment preflight checks.")
    parser.add_argument("--model-path", default=os.environ.get("MUSETALK_MODEL_PATH", "/app/storage_local/models"))
    parser.add_argument("--storage-root", default=os.environ.get("STORAGE_ROOT", "/app/storage_local"))
    parser.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    parser.add_argument("--worker-health-url", default=os.environ.get("AVATAR_WORKER_HEALTH_URL", "http://127.0.0.1:17860/health"))
    parser.add_argument("--gpu-device", default=os.environ.get("AVATAR_GPU_DEVICE", os.environ.get("NVIDIA_VISIBLE_DEVICES", "all")))
    parser.add_argument("--cuda-image", default=os.environ.get("AVATAR_PREFLIGHT_CUDA_IMAGE", "nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04"))
    parser.add_argument("--skip-docker-gpu", action="store_true", help="Skip docker run --gpus validation.")
    parser.add_argument("--skip-worker-health", action="store_true", help="Skip the worker-avatar /health check.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    checks: list[CheckResult] = [
        _check_nvidia_smi(),
    ]
    if not args.skip_docker_gpu:
        checks.append(_check_docker_gpu(args.cuda_image, args.gpu_device))
    checks.extend(
        [
            _check_models(Path(args.model_path)),
            _check_storage_writable(Path(args.storage_root)),
            _check_redis(args.redis_url),
        ]
    )
    if not args.skip_worker_health:
        checks.append(_check_worker_health(args.worker_health_url))

    payload = {
        "ok": all(check.ok for check in checks),
        "checks": [check.as_dict() for check in checks],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for check in checks:
            status = "PASS" if check.ok else "FAIL"
            print(f"{status} {check.name}: {json.dumps(check.detail, sort_keys=True)}")
        print(f"Overall: {'PASS' if payload['ok'] else 'FAIL'}")
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
