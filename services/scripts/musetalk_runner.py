from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def _run(cmd: list[str], cwd: Path) -> tuple[bool, str]:
    env = os.environ.copy()
    existing = str(env.get("PYTHONPATH", "")).strip()
    env["PYTHONPATH"] = f"{cwd}:{existing}" if existing else str(cwd)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return True, ""
    err = (proc.stderr or proc.stdout or "command failed").strip()
    return False, err


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_marker_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".musetalk_run.json")


def _debug_sidecar_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".musetalk_debug.json")


def _prepare_current_run(
    *,
    output_path: Path,
    run_id: str,
    source_path: Path,
    audio_path: Path,
    started_epoch: float,
) -> dict[str, str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for stale_path in [output_path, _debug_sidecar_path(output_path)]:
        if stale_path.exists():
            logger.warning(
                "late_musetalk_output_detected reason=stale_before_run path=%s mtime=%s run_id=%s",
                str(stale_path),
                round(float(stale_path.stat().st_mtime), 6),
                run_id,
            )
            stale_path.unlink(missing_ok=True)

    marker = {
        "run_id": run_id,
        "started_epoch": f"{float(started_epoch):.6f}",
        "source_path": str(source_path),
        "audio_path": str(audio_path),
        "source_sha256": _sha256_file(source_path),
        "audio_sha256": _sha256_file(audio_path),
    }
    _run_marker_path(output_path).write_text(json.dumps(marker, ensure_ascii=True, indent=2), encoding="utf-8")
    return marker


def _validate_current_run_output(
    *,
    output_path: Path,
    run_id: str,
    started_epoch: float,
    expected_source_sha256: str,
    expected_audio_sha256: str,
) -> tuple[bool, str]:
    if not output_path.exists() or output_path.stat().st_size <= 0:
        return False, "missing_output"
    if float(output_path.stat().st_mtime) < float(started_epoch) - 0.5:
        logger.warning(
            "late_musetalk_output_detected reason=older_than_current_run path=%s mtime=%s started_epoch=%s run_id=%s",
            str(output_path),
            round(float(output_path.stat().st_mtime), 6),
            round(float(started_epoch), 6),
            run_id,
        )
        return False, "late_musetalk_output_detected:older_than_current_run"

    sidecar = _debug_sidecar_path(output_path)
    if sidecar.exists():
        try:
            debug_payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            debug_payload = {}
        sidecar_run_id = str(debug_payload.get("musetalk_run_id") or "").strip()
        if sidecar_run_id and sidecar_run_id != str(run_id):
            logger.warning(
                "late_musetalk_output_detected reason=run_id_mismatch path=%s sidecar_run_id=%s expected_run_id=%s",
                str(output_path),
                sidecar_run_id,
                run_id,
            )
            return False, "late_musetalk_output_detected:run_id_mismatch"
        source_sha = str(debug_payload.get("input_reference_video_sha256") or debug_payload.get("input_reference_image_sha256") or "").strip()
        audio_sha = str(debug_payload.get("input_audio_sha256") or "").strip()
        if source_sha and source_sha != expected_source_sha256:
            return False, "late_musetalk_output_detected:source_hash_mismatch"
        if audio_sha and audio_sha != expected_audio_sha256:
            return False, "late_musetalk_output_detected:audio_hash_mismatch"
    return True, ""


def _resolve_media(source_image: str, source_video: str) -> tuple[str, str]:
    if source_video and Path(source_video).exists():
        return source_video, "video"
    if source_image and Path(source_image).exists():
        return source_image, "image"
    raise RuntimeError("No valid avatar source found. Provide source image or source video.")


def _build_candidates(source_path: str, source_kind: str, audio_path: str, output_path: str) -> list[list[str]]:
    # Use controlled entrypoint first; it applies compatibility guards and
    # model-root workspace setup required by this runtime.
    candidates: list[list[str]] = []

    source_flag = "--source_video" if source_kind == "video" else "--source_image"

    candidates.append([
        "python",
        "/app/scripts/musetalk_entrypoint.py",
        "--musetalk_home",
        str(os.environ.get("MUSETALK_HOME", "/opt/musetalk")),
        source_flag,
        source_path,
        "--driven_audio",
        audio_path,
        "--result_path",
        output_path,
    ])

    custom = str(os.environ.get("MUSETALK_INFERENCE_CMD_TEMPLATE", "")).strip()
    if custom:
        rendered = custom.format(
            source_image=source_path if source_kind == "image" else "",
            source_video=source_path if source_kind == "video" else "",
            audio_path=audio_path,
            output_path=output_path,
        )
        candidates.append(shlex.split(rendered))

    return candidates


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Run MuseTalk inference with fail-fast validation.")
    parser.add_argument("--source_image", required=True)
    parser.add_argument("--source_video", default="")
    parser.add_argument("--audio_path", required=True)
    parser.add_argument("--output_path", required=True)
    args = parser.parse_args()

    musetalk_home = Path(str(os.environ.get("MUSETALK_HOME", "")).strip() or "/opt/musetalk")
    if not musetalk_home.exists():
        raise RuntimeError(f"MuseTalk home is missing: {musetalk_home}. Set MUSETALK_HOME and install MuseTalk.")

    model_path = str(os.environ.get("MUSETALK_MODEL_PATH", "")).strip()
    if model_path and not Path(model_path).exists():
        raise RuntimeError(f"MuseTalk model path is missing: {model_path}")

    source_path, source_kind = _resolve_media(args.source_image, args.source_video)
    if not Path(args.audio_path).exists():
        raise RuntimeError(f"Audio path does not exist: {args.audio_path}")

    output_path = Path(args.output_path)
    run_id = str(os.environ.get("AVATAR_MUSETALK_RUN_ID", "")).strip() or f"musetalk-{uuid.uuid4().hex}"
    started_epoch = time.time()
    run_marker = _prepare_current_run(
        output_path=output_path,
        run_id=run_id,
        source_path=Path(source_path),
        audio_path=Path(args.audio_path),
        started_epoch=started_epoch,
    )
    os.environ["AVATAR_MUSETALK_RUN_ID"] = run_id
    os.environ["AVATAR_MUSETALK_INPUT_SOURCE_SHA256"] = run_marker["source_sha256"]
    os.environ["AVATAR_MUSETALK_INPUT_AUDIO_SHA256"] = run_marker["audio_sha256"]

    failures: list[str] = []
    for cmd in _build_candidates(source_path, source_kind, args.audio_path, str(output_path)):
        ok, err = _run(cmd, cwd=musetalk_home)
        valid, validation_error = _validate_current_run_output(
            output_path=output_path,
            run_id=run_id,
            started_epoch=started_epoch,
            expected_source_sha256=run_marker["source_sha256"],
            expected_audio_sha256=run_marker["audio_sha256"],
        )
        if ok and valid:
            return 0
        failures.append(f"{' '.join(cmd)} => {err or validation_error or 'no output generated'}")

    raise RuntimeError(
        "MuseTalk inference failed. No candidate command produced a valid output. "
        f"Failures: {' | '.join(failures)}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
