from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import queue
import signal
import shutil
import sys
import tempfile
import time
import threading
import hashlib
import subprocess
from pathlib import Path

try:
    import numpy as np  # type: ignore
except Exception:
    np = None

try:
    import cv2  # type: ignore
except Exception:
    cv2 = None


def _apply_torch_legacy_load_guard() -> None:
    """Force legacy torch.load behavior for trusted local MuseTalk weights.

    PyTorch 2.6 changed torch.load default `weights_only` to True, which breaks
    older .pth/.tar checkpoints used by MuseTalk face parsing dependencies.
    """
    try:
        import torch
    except Exception:
        return

    original_load = torch.load

    def patched_load(*args, **kwargs):  # noqa: ANN002,ANN003
        kwargs.setdefault("weights_only", False)
        return original_load(*args, **kwargs)

    torch.load = patched_load


def _apply_mmengine_duplicate_registration_guard() -> None:
    # MuseTalk + mmpose + current transformers/torch stack can trigger
    # duplicate Adafactor registration in mmengine's optimizer registry.
    # Ignore only this known duplicate registration error.
    try:
        from mmengine.registry import Registry
    except Exception:
        return

    original = Registry._register_module

    def patched(self, module, module_name=None, force=False):
        try:
            return original(self, module, module_name=module_name, force=force)
        except KeyError as exc:
            message = str(exc)
            if "already registered in optimizer" in message and "Adafactor" in message:
                return None
            raise

    Registry._register_module = patched


def _assert_real_pose_backend() -> dict[str, str]:
    """Require real OpenMMLab backend; raise with actionable details on failure."""
    versions: dict[str, str] = {}
    try:
        import mmcv
        import mmengine
        import mmdet
        import mmpose

        versions["mmcv"] = str(getattr(mmcv, "__version__", "unknown"))
        versions["mmengine"] = str(getattr(mmengine, "__version__", "unknown"))
        versions["mmdet"] = str(getattr(mmdet, "__version__", "unknown"))
        versions["mmpose"] = str(getattr(mmpose, "__version__", "unknown"))
    except Exception as exc:
        raise RuntimeError(
            "Real pose backend import failed (mmcv/mmengine/mmdet/mmpose). "
            f"Original error: {exc}"
        ) from exc

    try:
        from mmcv.ops import nms  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "Real pose backend is unavailable because mmcv compiled ops failed to import. "
            "Install a compiled mmcv build compatible with the current torch/CUDA runtime. "
            f"Original error: {exc}"
        ) from exc

    try:
        from mmpose.apis import inference_topdown, init_model  # noqa: F401
        from mmpose.structures import merge_data_samples  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "mmpose APIs required by MuseTalk failed to import. "
            f"Original error: {exc}"
        ) from exc

    return versions


def _probe_duration_seconds(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return 0.0
    try:
        return max(float((proc.stdout or "0").strip() or "0"), 0.0)
    except Exception:
        return 0.0


def _probe_frame_count(path: Path) -> int:
    if not path.exists() or not path.is_file():
        return 0
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames,nb_frames",
            "-of",
            "json",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return 0
    try:
        payload = json.loads(proc.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        return int(stream.get("nb_read_frames") or stream.get("nb_frames") or 0)
    except Exception:
        return 0


def _assert_onnxruntime_cuda_provider() -> dict[str, object]:
    require_cuda = str(os.environ.get("MUSETALK_REQUIRE_CUDA_PROVIDER", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    try:
        import onnxruntime as ort  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "musetalk_provider_check_failed stage=provider_setup reason=onnxruntime_import_failed "
            f"error={exc}"
        ) from exc

    providers: list[str] = []
    try:
        providers = list(ort.get_available_providers())
    except Exception as exc:
        raise RuntimeError(
            "musetalk_provider_check_failed stage=provider_setup reason=provider_query_failed "
            f"error={exc}"
        ) from exc

    device_name = "unknown"
    try:
        device_name = str(getattr(ort, "get_device", lambda: "unknown")())
    except Exception:
        device_name = "unknown"

    has_cuda_provider = "CUDAExecutionProvider" in providers
    if require_cuda and not has_cuda_provider:
        raise RuntimeError(
            "musetalk_provider_check_failed stage=provider_setup reason=missing_cuda_execution_provider "
            f"available_providers={providers} device={device_name}"
        )

    return {
        "require_cuda_provider": bool(require_cuda),
        "available_providers": providers,
        "device": device_name,
        "cuda_provider_available": bool(has_cuda_provider),
    }


def _runtime_cleanup(*, stage_name: str, include_cuda: bool = True) -> dict[str, object]:
    gc_collected = 0
    try:
        gc_collected = int(gc.collect())
    except Exception:
        gc_collected = 0

    torch_cache_cleared = False
    torch_error = ""
    if include_cuda:
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch_cache_cleared = True
        except Exception as exc:
            torch_error = str(exc)

    snapshot = _gpu_memory_snapshot(None)
    payload = {
        "stage_name": str(stage_name),
        "gc_collected": int(gc_collected),
        "torch_cache_cleared": bool(torch_cache_cleared),
        "torch_error": str(torch_error),
        "memory_snapshot": snapshot,
    }
    logger.info(
        "MuseTalk runtime cleanup stage=%s gc_collected=%s torch_cache_cleared=%s snapshot=%s",
        str(stage_name),
        int(gc_collected),
        bool(torch_cache_cleared),
        snapshot,
    )
    return payload


def _run_ffmpeg_stage(
    *,
    stage_name: str,
    command: list[str],
    timeout_seconds: float,
    expected_output: Path | None = None,
) -> None:
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=max(float(timeout_seconds), 1.0),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "musetalk_stage_timeout "
            f"stage={stage_name} timeout_seconds={float(timeout_seconds):.1f} command={' '.join(command)}"
        ) from exc

    if proc.returncode != 0:
        raise RuntimeError(
            "musetalk_stage_failed "
            f"stage={stage_name} return_code={int(proc.returncode)} command={' '.join(command)} "
            f"stderr_tail={str(proc.stderr or '')[-400:]}"
        )
    if expected_output is not None:
        if not expected_output.exists() or expected_output.stat().st_size <= 0:
            raise RuntimeError(
                "musetalk_stage_failed "
                f"stage={stage_name} output_missing_or_empty output_path={expected_output} "
                f"command={' '.join(command)}"
            )


def _build_chunk_ranges(*, total_duration_seconds: float, max_chunk_seconds: float) -> list[tuple[float, float]]:
    total = max(float(total_duration_seconds), 0.0)
    chunk_max = max(float(max_chunk_seconds), 0.0)
    if total <= 0.0 or chunk_max <= 0.0 or total <= chunk_max:
        return [(0.0, total)] if total > 0.0 else []

    ranges: list[tuple[float, float]] = []
    cursor = 0.0
    while cursor < total - 1e-6:
        duration = min(chunk_max, total - cursor)
        ranges.append((round(cursor, 6), round(duration, 6)))
        cursor += duration
    return ranges


def _prepare_media_chunk(
    *,
    source_path: Path,
    source_kind: str,
    audio_path: Path,
    work_dir: Path,
    chunk_index: int,
    chunk_start_seconds: float,
    chunk_duration_seconds: float,
) -> tuple[Path, Path]:
    chunk_audio = work_dir / f"chunk_{chunk_index:04d}.wav"
    chunk_audio_cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{float(chunk_start_seconds):.6f}",
        "-t",
        f"{float(chunk_duration_seconds):.6f}",
        "-i",
        str(audio_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(chunk_audio),
    ]
    _run_ffmpeg_stage(
        stage_name=f"chunk_audio_prepare_{int(chunk_index)}",
        command=chunk_audio_cmd,
        timeout_seconds=max(float(chunk_duration_seconds) * 2.0, 120.0),
        expected_output=chunk_audio,
    )

    if source_kind != "video":
        return source_path, chunk_audio

    chunk_video = work_dir / f"chunk_{chunk_index:04d}.mp4"
    chunk_video_cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{float(chunk_start_seconds):.6f}",
        "-t",
        f"{float(chunk_duration_seconds):.6f}",
        "-i",
        str(source_path),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        str(chunk_video),
    ]
    _run_ffmpeg_stage(
        stage_name=f"chunk_video_prepare_{int(chunk_index)}",
        command=chunk_video_cmd,
        timeout_seconds=max(float(chunk_duration_seconds) * 3.0, 180.0),
        expected_output=chunk_video,
    )
    return chunk_video, chunk_audio


def _concat_chunk_outputs(*, chunk_outputs: list[Path], output_path: Path, work_dir: Path) -> None:
    playlist = work_dir / "chunk_outputs.txt"
    playlist.write_text(
        "\n".join(f"file '{path.as_posix()}'" for path in chunk_outputs),
        encoding="utf-8",
    )

    concat_copy_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(playlist),
        "-c",
        "copy",
        str(output_path),
    ]
    try:
        _run_ffmpeg_stage(
            stage_name="chunk_concat_copy",
            command=concat_copy_cmd,
            timeout_seconds=max(float(len(chunk_outputs)) * 180.0, 300.0),
            expected_output=output_path,
        )
        return
    except Exception as copy_exc:
        logger.warning("MuseTalk chunk concat copy failed, retrying reencode reason=%s", copy_exc)

    concat_reencode_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(playlist),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output_path),
    ]
    _run_ffmpeg_stage(
        stage_name="chunk_concat_reencode",
        command=concat_reencode_cmd,
        timeout_seconds=max(float(len(chunk_outputs)) * 240.0, 420.0),
        expected_output=output_path,
    )


def _extend_video_to_duration(
    *,
    source_path: Path,
    target_duration_seconds: float,
    shortfall_seconds: float,
) -> tuple[bool, str, str, float]:
    if target_duration_seconds <= 0.0:
        return False, "", "target_duration_invalid", _probe_duration_seconds(source_path)

    loop_tmp = source_path.with_suffix(source_path.suffix + ".contract_loop.mp4")
    pad_tmp = source_path.with_suffix(source_path.suffix + ".contract_pad.mp4")
    attempts: list[tuple[str, list[str], Path, int]] = [
        (
            "loop_to_contract",
            [
                "ffmpeg",
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                str(source_path),
                "-t",
                f"{target_duration_seconds:.6f}",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                str(loop_tmp),
            ],
            loop_tmp,
            180,
        ),
        (
            "pad_last_frame",
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source_path),
                "-vf",
                f"tpad=stop_mode=clone:stop_duration={max(shortfall_seconds, 0.0):.6f}",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                str(pad_tmp),
            ],
            pad_tmp,
            150,
        ),
    ]

    errors: list[str] = []
    for strategy, cmd, tmp_path, timeout_seconds in attempts:
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=timeout_seconds)
        except Exception as exc:
            errors.append(f"{strategy}:exception={exc}")
            continue
        if result.returncode != 0:
            errors.append(f"{strategy}:return_code={int(result.returncode)} stderr={str(result.stderr or '')[-220:]}")
            continue
        if not tmp_path.exists() or tmp_path.stat().st_size <= 0:
            errors.append(f"{strategy}:empty_output")
            continue
        shutil.move(str(tmp_path), str(source_path))
        return True, strategy, "", _probe_duration_seconds(source_path)

    return False, "", ";".join(errors[-2:]), _probe_duration_seconds(source_path)


def _analyze_video_hashes(path: Path, *, max_hashes: int = 8) -> dict[str, object]:
    if cv2 is None or np is None:
        return {
            "frame_count": 0,
            "unique_frame_count": 0,
            "frame_hash_summary": [],
        }

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return {
            "frame_count": 0,
            "unique_frame_count": 0,
            "frame_hash_summary": [],
        }

    frame_hashes: list[str] = []
    first_small = None
    last_small = None
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        frame_hashes.append(hashlib.sha256(frame.tobytes()).hexdigest()[:16])
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (96, 96), interpolation=cv2.INTER_AREA)
        if first_small is None:
            first_small = small
        last_small = small
    cap.release()

    start_end_frame_diff = 0.0
    if first_small is not None and last_small is not None:
        start_end_frame_diff = float(np.mean(np.abs(first_small.astype("float32") - last_small.astype("float32"))))
    max_loop_similarity = float(os.environ.get("AVATAR_MAX_LOOP_START_END_DIFF", "1.1"))

    return {
        "frame_count": len(frame_hashes),
        "unique_frame_count": len(set(frame_hashes)),
        "frame_hash_summary": frame_hashes[:max_hashes],
        "start_end_frame_diff": round(start_end_frame_diff, 6),
        "semantic_loop_similarity": bool(len(frame_hashes) >= 16 and start_end_frame_diff <= max_loop_similarity),
    }


def _find_intermediate_frame_dir(search_root: Path) -> Path | None:
    best_dir: Path | None = None
    best_count = 0
    for candidate in search_root.rglob("*"):
        if not candidate.is_dir():
            continue
        try:
            image_count = sum(
                1
                for p in candidate.iterdir()
                if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            )
        except Exception:
            continue
        if image_count > best_count:
            best_count = image_count
            best_dir = candidate
    return best_dir


def _get_int_env(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)).strip() or str(default))
    except Exception:
        return int(default)


def _get_float_env(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, default)).strip() or str(default))
    except Exception:
        return float(default)


def _prepare_patched_inference_script(source_path: Path, target_path: Path) -> None:
    def _replace_once(content: str, old: str, new: str, *, patch_name: str) -> str:
        if old not in content:
            raise RuntimeError(f"Unable to patch MuseTalk inference.py for {patch_name}")
        return content.replace(old, new, 1)

    def _replace_one_of(
        content: str,
        replacements: list[tuple[str, str]],
        *,
        patch_name: str,
    ) -> str:
        for old, new in replacements:
            if old in content:
                return content.replace(old, new, 1)
        raise RuntimeError(f"Unable to patch MuseTalk inference.py for {patch_name}")

    text = source_path.read_text(encoding="utf-8")

    text = _replace_once(
        text,
        (
            '            # Set bbox_shift based on version\n'
            '            if args.version == "v15":\n'
            '                bbox_shift = 0  # v15 uses fixed bbox_shift\n'
            '            else:\n'
            '                bbox_shift = inference_config[task_id].get("bbox_shift", args.bbox_shift)  # v1 uses config or default\n'
        ),
        (
            '            # Honor bbox_shift from config/args for preview tuning in this controlled runtime.\n'
            '            bbox_shift = inference_config[task_id].get("bbox_shift", args.bbox_shift)\n'
        ),
        patch_name="bbox_shift support in v15 runtime",
    )

    # Ensure cleanup paths are deterministic and initialized for every source type.
    text = _replace_once(
        text,
        (
            '            # Extract frames from source video\n'
            '            if get_file_type(video_path) == "video":\n'
            '                save_dir_full = os.path.join(temp_dir, input_basename)\n'
            '                os.makedirs(save_dir_full, exist_ok=True)\n'
        ),
        (
            '            # Extract frames from source video\n'
            '            save_dir_full = os.path.join(temp_dir, input_basename)\n'
            '            if get_file_type(video_path) == "video":\n'
            '                os.makedirs(save_dir_full, exist_ok=True)\n'
        ),
        patch_name="save_dir_full initialization",
    )

    # Make final encode/mux failures explicit and actionable.
    text = _replace_one_of(
        text,
        [
            (
                (
                    '            cmd_img2video = f"ffmpeg -y -v warning -r {fps} -f image2 -i {result_img_save_path}/%08d.png -vcodec libx264 -vf format=yuv420p -crf 18 {temp_vid_path}"\n'
                    '            print("Video generation command:", cmd_img2video)\n'
                    '            os.system(cmd_img2video)   \n'
                    '            \n'
                    '            cmd_combine_audio = f"ffmpeg -y -v warning -i {audio_path} -i {temp_vid_path} {output_vid_name}"\n'
                    '            print("Audio combination command:", cmd_combine_audio) \n'
                    '            os.system(cmd_combine_audio)\n'
                ),
                (
                    '            cmd_img2video = f"ffmpeg -y -v warning -r {fps} -f image2 -i {result_img_save_path}/%08d.png -vcodec libx264 -vf format=yuv420p -crf 18 {temp_vid_path}"\n'
                    '            print("Video generation command:", cmd_img2video)\n'
                    '            img2video_rc = int(os.system(cmd_img2video))\n'
                    '            if img2video_rc != 0 or (not os.path.exists(temp_vid_path)) or os.path.getsize(temp_vid_path) <= 0:\n'
                    '                raise RuntimeError(\n'
                    '                    f"mux_encode_stage=image_to_video_failed command={cmd_img2video} "\n'
                    '                    f"temp_video={temp_vid_path} return_code={img2video_rc}"\n'
                    '                )\n'
                    '            \n'
                    '            cmd_combine_audio = f"ffmpeg -y -v warning -i {audio_path} -i {temp_vid_path} {output_vid_name}"\n'
                    '            print("Audio combination command:", cmd_combine_audio) \n'
                    '            combine_audio_rc = int(os.system(cmd_combine_audio))\n'
                    '            if combine_audio_rc != 0 or (not os.path.exists(output_vid_name)) or os.path.getsize(output_vid_name) <= 0:\n'
                    '                raise RuntimeError(\n'
                    '                    f"mux_encode_stage=audio_mux_failed command={cmd_combine_audio} "\n'
                    '                    f"output_path={output_vid_name} return_code={combine_audio_rc}"\n'
                    '                )\n'
                ),
            ),
            (
                (
                    '            cmd_img2video = f"ffmpeg -y -v warning -r {fps} -f image2 -i {result_img_save_path}/%08d.png -vcodec libx264 -vf format=yuv420p -crf 18 {temp_vid_path}"\n'
                    '            print("Video generation command:", cmd_img2video)\n'
                    '            os.system(cmd_img2video)\n'
                    '\n'
                    '            cmd_combine_audio = f"ffmpeg -y -v warning -i {audio_path} -i {temp_vid_path} {output_vid_name}"\n'
                    '            print("Audio combination command:", cmd_combine_audio)\n'
                    '            os.system(cmd_combine_audio)\n'
                ),
                (
                    '            cmd_img2video = f"ffmpeg -y -v warning -r {fps} -f image2 -i {result_img_save_path}/%08d.png -vcodec libx264 -vf format=yuv420p -crf 18 {temp_vid_path}"\n'
                    '            print("Video generation command:", cmd_img2video)\n'
                    '            img2video_rc = int(os.system(cmd_img2video))\n'
                    '            if img2video_rc != 0 or (not os.path.exists(temp_vid_path)) or os.path.getsize(temp_vid_path) <= 0:\n'
                    '                raise RuntimeError(\n'
                    '                    f"mux_encode_stage=image_to_video_failed command={cmd_img2video} "\n'
                    '                    f"temp_video={temp_vid_path} return_code={img2video_rc}"\n'
                    '                )\n'
                    '\n'
                    '            cmd_combine_audio = f"ffmpeg -y -v warning -i {audio_path} -i {temp_vid_path} {output_vid_name}"\n'
                    '            print("Audio combination command:", cmd_combine_audio)\n'
                    '            combine_audio_rc = int(os.system(cmd_combine_audio))\n'
                    '            if combine_audio_rc != 0 or (not os.path.exists(output_vid_name)) or os.path.getsize(output_vid_name) <= 0:\n'
                    '                raise RuntimeError(\n'
                    '                    f"mux_encode_stage=audio_mux_failed command={cmd_combine_audio} "\n'
                    '                    f"output_path={output_vid_name} return_code={combine_audio_rc}"\n'
                    '                )\n'
                ),
            ),
        ],
        patch_name="final mux/encode checks",
    )

    text = _replace_one_of(
        text,
        [
            (
                (
                    '            # Clean up temporary files\n'
                    '            shutil.rmtree(result_img_save_path)\n'
                    '            os.remove(temp_vid_path)\n'
                    '            \n'
                    '            shutil.rmtree(save_dir_full)\n'
                    '            if not args.saved_coord:\n'
                    '                os.remove(crop_coord_save_path)\n'
                ),
                (
                    '            # Clean up temporary files\n'
                    '            if os.path.isdir(result_img_save_path):\n'
                    '                shutil.rmtree(result_img_save_path, ignore_errors=True)\n'
                    '            if os.path.isfile(temp_vid_path):\n'
                    '                os.remove(temp_vid_path)\n'
                    '            \n'
                    '            if save_dir_full and os.path.isdir(save_dir_full):\n'
                    '                shutil.rmtree(save_dir_full, ignore_errors=True)\n'
                    '            if not args.saved_coord and os.path.isfile(crop_coord_save_path):\n'
                    '                os.remove(crop_coord_save_path)\n'
                ),
            ),
            (
                (
                    '            # Clean up temporary files\n'
                    '            shutil.rmtree(result_img_save_path)\n'
                    '            os.remove(temp_vid_path)\n'
                    '\n'
                    '            shutil.rmtree(save_dir_full)\n'
                    '            if not args.saved_coord:\n'
                    '                os.remove(crop_coord_save_path)\n'
                ),
                (
                    '            # Clean up temporary files\n'
                    '            if os.path.isdir(result_img_save_path):\n'
                    '                shutil.rmtree(result_img_save_path, ignore_errors=True)\n'
                    '            if os.path.isfile(temp_vid_path):\n'
                    '                os.remove(temp_vid_path)\n'
                    '\n'
                    '            if save_dir_full and os.path.isdir(save_dir_full):\n'
                    '                shutil.rmtree(save_dir_full, ignore_errors=True)\n'
                    '            if not args.saved_coord and os.path.isfile(crop_coord_save_path):\n'
                    '                os.remove(crop_coord_save_path)\n'
                ),
            ),
        ],
        patch_name="safe cleanup of optional frame directory",
    )

    text = _replace_once(
        text,
        (
            '        except Exception as e:\n'
            '            print("Error occurred during processing:", e)\n'
        ),
        (
            '        except Exception as e:\n'
            '            print("Error occurred during processing:", e)\n'
            '            raise\n'
        ),
        patch_name="exception propagation",
    )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(text, encoding="utf-8")


def _copy_tree_best_effort(source_path: Path, target_path: Path) -> None:
    if not source_path.exists():
        return
    try:
        shutil.copytree(source_path, target_path, dirs_exist_ok=True)
    except Exception as exc:
        logger.warning(
            "MuseTalk debug artifact copy skipped source=%s target=%s reason=%s",
            source_path,
            target_path,
            exc,
        )


logger = logging.getLogger(__name__)


def _process_group_kwargs() -> dict[str, object]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _terminate_process_group(
    process: subprocess.Popen[str],
    *,
    reason: str,
    grace_seconds: float = 5.0,
) -> dict[str, object]:
    pid = int(getattr(process, "pid", 0) or 0)
    payload: dict[str, object] = {
        "pid": pid,
        "pgid": 0,
        "reason": str(reason or ""),
        "terminated": False,
        "killed": False,
        "error": "",
    }
    if pid <= 0 or process.poll() is not None:
        return payload
    try:
        if os.name == "nt":
            process.terminate()
            payload["terminated"] = True
        else:
            pgid = os.getpgid(pid)
            payload["pgid"] = int(pgid)
            os.killpg(pgid, signal.SIGTERM)
            payload["terminated"] = True
        logger.warning(
            "MuseTalk terminate process group reason=%s pid=%s pgid=%s",
            reason,
            pid,
            payload.get("pgid") or "",
        )
        process.wait(timeout=max(float(grace_seconds), 0.1))
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                process.kill()
            else:
                pgid = int(payload.get("pgid") or os.getpgid(pid))
                payload["pgid"] = int(pgid)
                os.killpg(pgid, signal.SIGKILL)
            payload["killed"] = True
            logger.warning(
                "MuseTalk kill process group reason=%s pid=%s pgid=%s",
                reason,
                pid,
                payload.get("pgid") or "",
            )
            process.wait(timeout=2.0)
        except Exception as exc:
            payload["error"] = str(exc)
    except ProcessLookupError:
        pass
    except Exception as exc:
        payload["error"] = str(exc)
        try:
            process.kill()
            payload["killed"] = True
        except Exception:
            pass
    return payload


def _progress_marker(root: Path) -> tuple[int, float]:
    file_count = 0
    newest_mtime = 0.0
    try:
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue
            file_count += 1
            newest_mtime = max(newest_mtime, float(candidate.stat().st_mtime))
    except Exception:
        return file_count, newest_mtime
    return file_count, newest_mtime


def _classify_progress_timeout(
    *,
    now: float,
    last_progress_at: float,
    idle_timeout_seconds: float,
    total_deadline: float | None,
    chunk_deadline: float | None,
) -> str:
    if total_deadline is not None and now > float(total_deadline):
        return "musetalk_total_timeout"
    if chunk_deadline is not None and now > float(chunk_deadline):
        return "musetalk_chunk_timeout"
    if idle_timeout_seconds > 0.0 and (now - float(last_progress_at)) > float(idle_timeout_seconds):
        return "musetalk_idle_timeout"
    return ""


class _InferenceProcessError(RuntimeError):
    def __init__(self, message: str, *, trace_info: dict[str, object] | None = None):
        super().__init__(message)
        self.trace_info = dict(trace_info or {})


def _safe_float(value: object, default: float) -> float:
    try:
        return float(str(value).strip() or str(default))
    except Exception:
        return float(default)


def _stage_idle_timeout_map() -> dict[str, float]:
    default_timeout = _safe_float(
        os.environ.get("MUSETALK_IDLE_TIMEOUT_SECONDS", os.environ.get("MUSETALK_STAGE_TIMEOUT_DEFAULT_SECONDS", "900")),
        900.0,
    )
    return {
        "default": max(default_timeout, 30.0),
        "model_load": max(_safe_float(os.environ.get("MUSETALK_STAGE_TIMEOUT_MODEL_LOAD_SECONDS", default_timeout), default_timeout), 30.0),
        "face_landmark_extraction": max(_safe_float(os.environ.get("MUSETALK_STAGE_TIMEOUT_FACE_LANDMARK_SECONDS", default_timeout), default_timeout), 30.0),
        "inference_loop": max(_safe_float(os.environ.get("MUSETALK_STAGE_TIMEOUT_INFERENCE_LOOP_SECONDS", max(default_timeout, 3600.0)), max(default_timeout, 3600.0)), 60.0),
        "frame_blend": max(_safe_float(os.environ.get("MUSETALK_STAGE_TIMEOUT_FRAME_BLEND_SECONDS", default_timeout), default_timeout), 30.0),
        "mux_encode": max(_safe_float(os.environ.get("MUSETALK_STAGE_TIMEOUT_MUX_ENCODE_SECONDS", min(default_timeout, 600.0)), min(default_timeout, 600.0)), 30.0),
        "final_save": max(_safe_float(os.environ.get("MUSETALK_STAGE_TIMEOUT_FINAL_SAVE_SECONDS", min(default_timeout, 300.0)), min(default_timeout, 300.0)), 30.0),
    }


def _detect_inference_stage(line: str, current_stage: str) -> str:
    lowered = str(line or "").strip().lower()
    if not lowered:
        return current_stage
    if "cuda start" in lowered or "load unet model" in lowered or "loaded inference config" in lowered:
        return "model_load"
    if "extracting landmarks" in lowered or "get key_landmark" in lowered:
        return "face_landmark_extraction"
    if "starting inference" in lowered:
        return "inference_loop"
    if "padding generated images" in lowered:
        return "frame_blend"
    if "video generation command" in lowered:
        return "mux_encode"
    if "audio combination command" in lowered or "results saved to" in lowered:
        return "final_save"
    return current_stage


def _gpu_memory_snapshot(child_pid: int | None = None) -> dict[str, float]:
    snapshot: dict[str, float] = {}
    try:
        import torch

        if torch.cuda.is_available():
            snapshot["parent_cuda_allocated_mib"] = round(float(torch.cuda.memory_allocated(0)) / (1024.0 * 1024.0), 2)
            snapshot["parent_cuda_reserved_mib"] = round(float(torch.cuda.memory_reserved(0)) / (1024.0 * 1024.0), 2)
    except Exception:
        pass

    if child_pid is not None and child_pid > 0:
        try:
            proc = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-compute-apps=pid,used_memory",
                    "--format=csv,noheader,nounits",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=5,
            )
            if proc.returncode == 0:
                for raw in (proc.stdout or "").splitlines():
                    parts = [part.strip() for part in raw.split(",") if part.strip()]
                    if len(parts) < 2:
                        continue
                    try:
                        pid_value = int(parts[0])
                        memory_value = float(parts[1])
                    except Exception:
                        continue
                    if pid_value == int(child_pid):
                        snapshot["child_gpu_memory_mib"] = round(memory_value, 2)
                        break
        except Exception:
            pass

    return snapshot


def _run_patched_inference_subprocess(
    *,
    work_dir: Path,
    patched_inference_path: Path,
    config_path: Path,
    result_dir: Path,
    selected_params: dict[str, object],
    chunk_index: int = 0,
    total_deadline: float | None = None,
    chunk_deadline: float | None = None,
) -> dict[str, object]:
    command = [
        sys.executable,
        "-u",
        str(patched_inference_path),
        "--inference_config",
        str(config_path),
        "--result_dir",
        str(result_dir),
        "--bbox_shift",
        str(selected_params["bbox_shift"]),
        "--extra_margin",
        str(selected_params["extra_margin"]),
        "--fps",
        str(selected_params["fps"]),
        "--audio_padding_length_left",
        str(selected_params["audio_padding_length_left"]),
        "--audio_padding_length_right",
        str(selected_params["audio_padding_length_right"]),
        "--batch_size",
        str(selected_params["batch_size"]),
        "--parsing_mode",
        str(selected_params["parsing_mode"]),
        "--left_cheek_width",
        str(selected_params["left_cheek_width"]),
        "--right_cheek_width",
        str(selected_params["right_cheek_width"]),
        "--version",
        "v15",
    ]
    if bool(selected_params.get("use_float16")):
        command.append("--use_float16")

    stage_timeouts = _stage_idle_timeout_map()
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")

    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=str(work_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **_process_group_kwargs(),
    )

    line_queue: queue.Queue[str | None] = queue.Queue()

    def _reader() -> None:
        try:
            if process.stdout is None:
                return
            for raw in process.stdout:
                line_queue.put(str(raw).rstrip("\r\n"))
        finally:
            line_queue.put(None)

    reader_thread = threading.Thread(target=_reader, name="musetalk-inference-log-reader", daemon=True)
    reader_thread.start()

    current_stage = "model_load"
    stage_started_at = started
    stage_timings: dict[str, float] = {}
    stage_trace: list[dict[str, object]] = [
        {
            "event": "stage_start",
            "stage": current_stage,
            "elapsed_seconds": 0.0,
            "memory_snapshot": _gpu_memory_snapshot(process.pid),
        }
    ]
    last_output_at = started
    last_progress_at = started
    last_progress_marker = _progress_marker(result_dir)
    next_progress_probe_at = started + 5.0
    last_line = ""
    log_tail: list[str] = []
    stream_closed = False

    try:
        while True:
            now = time.monotonic()
            try:
                line = line_queue.get(timeout=1.0)
            except queue.Empty:
                line = None

            if line is None:
                if process.poll() is not None:
                    stream_closed = True
            else:
                last_output_at = now
                if line:
                    last_line = line
                    log_tail.append(line)
                    if len(log_tail) > 300:
                        log_tail = log_tail[-300:]
                    logger.info("MuseTalk inference log: %s", line)

                    next_stage = _detect_inference_stage(line, current_stage)
                    if next_stage != current_stage:
                        stage_timings[current_stage] = round(now - stage_started_at, 2)
                        stage_trace.append(
                            {
                                "event": "stage_end",
                                "stage": current_stage,
                                "elapsed_seconds": round(now - started, 2),
                                "duration_seconds": stage_timings[current_stage],
                                "memory_snapshot": _gpu_memory_snapshot(process.pid),
                            }
                        )
                        logger.info(
                            "MuseTalk stage_transition from=%s to=%s duration_seconds=%.2f",
                            current_stage,
                            next_stage,
                            stage_timings[current_stage],
                        )
                        current_stage = next_stage
                        stage_started_at = now
                        stage_trace.append(
                            {
                                "event": "stage_start",
                                "stage": current_stage,
                                "elapsed_seconds": round(now - started, 2),
                                "memory_snapshot": _gpu_memory_snapshot(process.pid),
                            }
                        )

            now = time.monotonic()
            idle_seconds = now - last_output_at
            timeout_seconds = float(stage_timeouts.get(current_stage, stage_timeouts.get("default", 900.0)))
            if now >= next_progress_probe_at:
                marker = _progress_marker(result_dir)
                if marker[0] > last_progress_marker[0] or marker[1] > last_progress_marker[1]:
                    last_progress_at = now
                    last_progress_marker = marker
                    logger.info(
                        "MuseTalk filesystem progress chunk_index=%s files=%s newest_mtime=%s stage=%s",
                        int(chunk_index),
                        int(marker[0]),
                        round(float(marker[1]), 6),
                        current_stage,
                    )
                next_progress_probe_at = now + 5.0
            if line is not None:
                last_progress_at = now

            timeout_label = _classify_progress_timeout(
                now=now,
                last_progress_at=last_progress_at,
                idle_timeout_seconds=timeout_seconds,
                total_deadline=total_deadline,
                chunk_deadline=chunk_deadline,
            )
            if timeout_label:
                cleanup = _terminate_process_group(process, reason=timeout_label)
                trace_info = {
                    "command": command,
                    "stage_timeouts": stage_timeouts,
                    "stage_timings": stage_timings,
                    "stage_trace": stage_trace,
                    "current_stage": current_stage,
                    "idle_seconds": round(idle_seconds, 2),
                    "no_progress_seconds": round(now - last_progress_at, 2),
                    "last_log_line": last_line,
                    "log_tail": log_tail,
                    "chunk_index": int(chunk_index),
                    "cleanup": cleanup,
                }
                raise _InferenceProcessError(
                    f"{timeout_label} "
                    f"stage={current_stage} chunk_index={int(chunk_index)} idle_seconds={idle_seconds:.1f} "
                    f"no_progress_seconds={now - last_progress_at:.1f} idle_timeout_seconds={timeout_seconds:.1f} "
                    f"command={' '.join(command)} last_log_line={last_line}",
                    trace_info=trace_info,
                )

            if stream_closed and process.poll() is not None:
                break

        finished = time.monotonic()
        stage_timings[current_stage] = round(finished - stage_started_at, 2)
        stage_trace.append(
            {
                "event": "stage_end",
                "stage": current_stage,
                "elapsed_seconds": round(finished - started, 2),
                "duration_seconds": stage_timings[current_stage],
                "memory_snapshot": _gpu_memory_snapshot(process.pid),
            }
        )
        total_seconds = round(finished - started, 2)
        return_code = int(process.returncode or 0)
        trace_info = {
            "command": command,
            "stage_timeouts": stage_timeouts,
            "stage_timings": stage_timings,
            "stage_trace": stage_trace,
            "current_stage": current_stage,
            "last_log_line": last_line,
            "log_tail": log_tail,
            "total_seconds": total_seconds,
            "return_code": return_code,
        }

        if return_code != 0:
            raise _InferenceProcessError(
                "musetalk_stage_failed "
                f"stage={current_stage} return_code={return_code} command={' '.join(command)} "
                f"last_log_line={last_line}",
                trace_info=trace_info,
            )

        logger.info("MuseTalk stage tracing summary timings=%s", stage_timings)
        return trace_info
    finally:
        try:
            if process.poll() is None:
                _terminate_process_group(process, reason="finally_cleanup")
        except Exception:
            pass
        try:
            if process.stdout is not None:
                process.stdout.close()
        except Exception:
            pass


def _prepare_preview_fast_source(*, source_path: Path, source_kind: str, work_dir: Path) -> Path:
    fast_mode = str(os.environ.get("MUSETALK_PREVIEW_FAST_MODE", "0")).strip().lower() in {"1", "true", "yes", "on"}
    if not fast_mode:
        return source_path

    raw_max_width = str(os.environ.get("MUSETALK_PREVIEW_MAX_WIDTH", "512")).strip()
    try:
        max_width = max(int(raw_max_width), 256)
    except Exception:
        max_width = 512

    scaled_path = work_dir / ("preview_source_fast.mp4" if source_kind == "video" else "preview_source_fast.png")
    vf = f"scale='min({max_width},iw)':'-2':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-vf",
        vf,
    ]
    if source_kind == "video":
        cmd.extend(["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"])
    cmd.append(str(scaled_path))

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if proc.returncode != 0 or not scaled_path.exists() or scaled_path.stat().st_size <= 0:
        logger.warning(
            "MuseTalk preview fast mode downscaling failed source_kind=%s max_width=%s return_code=%s reason=fallback_to_original",
            source_kind,
            max_width,
            proc.returncode,
        )
        return source_path
    
    logger.info(
        "MuseTalk preview fast mode downscaling complete source_kind=%s max_width=%s original_path=%s scaled_path=%s",
        source_kind,
        max_width,
        str(source_path),
        str(scaled_path),
    )
    return scaled_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--musetalk_home", required=True)
    parser.add_argument("--source_image", default="")
    parser.add_argument("--source_video", default="")
    parser.add_argument("--driven_audio", required=True)
    parser.add_argument("--result_path", required=True)
    args = parser.parse_args()

    home = Path(args.musetalk_home)
    if not home.exists():
        raise RuntimeError(f"MuseTalk home does not exist: {home}")

    source_video = str(args.source_video or "").strip()
    source_image = str(args.source_image or "").strip()
    source_value = source_video or source_image
    if not source_value:
        raise RuntimeError("MuseTalk entrypoint requires source_image or source_video")

    source_path = Path(source_value)
    if not source_path.exists():
        raise RuntimeError(f"MuseTalk source does not exist: {source_path}")

    audio_path = Path(str(args.driven_audio))
    if not audio_path.exists():
        raise RuntimeError(f"MuseTalk driven audio does not exist: {audio_path}")

    output_path = Path(str(args.result_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    model_root_env = Path(str(os.environ.get("MUSETALK_MODEL_PATH", "")).strip() or "/app/storage_local/models")
    model_root_candidates = [model_root_env, model_root_env / "models"]
    model_root = model_root_candidates[0]
    if model_root_candidates[1].exists():
        model_root = model_root_candidates[1]

    required_files = [
        "sd-vae/config.json",
        "sd-vae/diffusion_pytorch_model.bin",
        "musetalkV15/unet.pth",
        "whisper/config.json",
        "whisper/pytorch_model.bin",
        "whisper/preprocessor_config.json",
        "dwpose/dw-ll_ucoco_384.pth",
        "face-parse-bisent/79999_iter.pth",
        "face-parse-bisent/resnet18-5c106cde.pth",
    ]
    missing_files = [rel for rel in required_files if not (model_root / rel).exists()]
    if missing_files:
        raise RuntimeError(
            "MuseTalk model files are missing. "
            f"Checked root={model_root}. Missing: {missing_files}. "
            "Download MuseTalk weights and place them under MUSETALK_MODEL_PATH."
        )

    os.environ.setdefault("PYTHONPATH", str(home))
    if str(home) not in os.environ["PYTHONPATH"].split(":"):
        os.environ["PYTHONPATH"] = f"{home}:{os.environ['PYTHONPATH']}"
    if str(home) not in sys.path:
        sys.path.insert(0, str(home))
    for app_path in [Path("/app"), Path("/app/api")]:
        if app_path.exists() and str(app_path) not in sys.path:
            sys.path.insert(0, str(app_path))

    _entrypoint_start = time.monotonic()
    entrypoint_stage_timings: dict[str, float] = {}

    provider_started = time.monotonic()
    provider_diagnostics = _assert_onnxruntime_cuda_provider()
    entrypoint_stage_timings["provider_setup_seconds"] = round(time.monotonic() - provider_started, 4)

    pose_started = time.monotonic()
    pose_backend_versions = _assert_real_pose_backend()
    _pose_backend_ready = time.monotonic()
    entrypoint_stage_timings["pose_backend_setup_seconds"] = round(_pose_backend_ready - pose_started, 4)
    logger.info(
        "MuseTalk entrypoint pose_backend_ready elapsed_seconds=%s source_image=%s source_video=%s audio=%s output=%s provider=%s",
        round(_pose_backend_ready - _entrypoint_start, 2),
        str(source_image),
        str(source_video),
        str(audio_path),
        str(output_path),
        provider_diagnostics,
    )
    selected_params = {
        "bbox_shift": _get_int_env("MUSETALK_BBOX_SHIFT", 0),
        "extra_margin": _get_int_env("MUSETALK_EXTRA_MARGIN", 10),
        "parsing_mode": str(os.environ.get("MUSETALK_PARSING_MODE", "jaw")).strip() or "jaw",
        "left_cheek_width": _get_int_env("MUSETALK_LEFT_CHEEK_WIDTH", 90),
        "right_cheek_width": _get_int_env("MUSETALK_RIGHT_CHEEK_WIDTH", 90),
        "fps": _get_int_env("MUSETALK_FPS", 25),
        "audio_padding_length_left": _get_int_env("MUSETALK_AUDIO_PADDING_LEFT", 2),
        "audio_padding_length_right": _get_int_env("MUSETALK_AUDIO_PADDING_RIGHT", 2),
        "batch_size": max(_get_int_env("MUSETALK_BATCH_SIZE", 8), 1),
        "use_float16": str(os.environ.get("MUSETALK_USE_FLOAT16", "1")).strip().lower() in {"1", "true", "yes", "on"},
    }
    target_frame_count = _get_int_env("MUSETALK_TARGET_FRAME_COUNT", 0)
    target_duration_seconds = _get_float_env("MUSETALK_TARGET_DURATION_SECONDS", 0.0)

    _apply_mmengine_duplicate_registration_guard()
    _apply_torch_legacy_load_guard()

    persist_debug = str(os.environ.get("AVATAR_PREVIEW_DIAGNOSTIC_MODE", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    with tempfile.TemporaryDirectory(prefix="musetalk-run-") as td:
        work = Path(td)
        musetalk_link = work / "musetalk"
        try:
            musetalk_link.symlink_to(home / "musetalk", target_is_directory=True)
        except Exception:
            shutil.copytree(home / "musetalk", musetalk_link, dirs_exist_ok=True)

        patched_inference_path = work / "scripts" / "inference.py"
        _prepare_patched_inference_script(home / "scripts" / "inference.py", patched_inference_path)

        models_target = work / "models"
        try:
            models_target.symlink_to(model_root, target_is_directory=True)
        except Exception:
            shutil.copytree(model_root, models_target, dirs_exist_ok=True)

        # Some MuseTalk snapshots provide musetalk.json while inference expects config.json.
        musetalk_dir = models_target / "musetalk"
        config_json = musetalk_dir / "config.json"
        legacy_json = musetalk_dir / "musetalk.json"
        if not config_json.exists() and legacy_json.exists():
            shutil.copy2(legacy_json, config_json)

        result_dir = work / "results"
        result_dir.mkdir(parents=True, exist_ok=True)
        result_name = output_path.name

        source_kind = "video" if source_video else "image"
        source_for_inference = _prepare_preview_fast_source(source_path=source_path, source_kind=source_kind, work_dir=work)

        source_duration_seconds = _probe_duration_seconds(source_for_inference) if source_kind == "video" else 0.0
        audio_duration_seconds = _probe_duration_seconds(audio_path)
        effective_duration_seconds = max(source_duration_seconds, audio_duration_seconds)
        chunk_max_seconds = max(_get_float_env("MUSETALK_CHUNK_MAX_SECONDS", 0.0), 0.0)
        chunk_ranges = _build_chunk_ranges(
            total_duration_seconds=effective_duration_seconds,
            max_chunk_seconds=chunk_max_seconds,
        )
        chunking_used = len(chunk_ranges) > 1
        total_timeout_seconds = max(_get_float_env("MUSETALK_TOTAL_TIMEOUT_SECONDS", 0.0), 0.0)
        chunk_timeout_seconds = max(_get_float_env("MUSETALK_CHUNK_TIMEOUT_SECONDS", 0.0), 0.0)
        idle_timeout_seconds = max(_get_float_env("MUSETALK_IDLE_TIMEOUT_SECONDS", 0.0), 0.0)
        total_deadline = (_entrypoint_start + total_timeout_seconds) if total_timeout_seconds > 0.0 else None
        metric_project_id = str(os.environ.get("AVATAR_PROJECT_ID", "")).strip()
        metric_job_id = str(os.environ.get("AVATAR_JOB_ID", "")).strip()
        metric_segment_index = str(os.environ.get("AVATAR_SEGMENT_INDEX", "")).strip()
        metric_preview_job_id = str(os.environ.get("AVATAR_PREVIEW_JOB_ID", "")).strip()

        stage_timeouts = _stage_idle_timeout_map()
        logger.info(
            "MuseTalk entrypoint runtime command=%s timeout_budget=%s total_timeout_seconds=%s "
            "chunk_timeout_seconds=%s idle_timeout_seconds=%s chunking_used=%s chunk_count=%s chunk_max_seconds=%s output_path=%s",
            str(patched_inference_path),
            stage_timeouts,
            round(float(total_timeout_seconds), 4),
            round(float(chunk_timeout_seconds), 4),
            round(float(idle_timeout_seconds), 4),
            bool(chunking_used),
            int(len(chunk_ranges)),
            round(float(chunk_max_seconds), 4),
            str(output_path),
        )

        _model_load_start = time.monotonic()
        logger.info(
            "MuseTalk entrypoint inference_start cold_start_seconds=%s params=%s",
            round(_model_load_start - _entrypoint_start, 2),
            selected_params,
        )

        inference_traces: list[dict[str, object]] = []
        chunk_metadata: list[dict[str, object]] = []
        chunk_timing_metrics: list[dict[str, object]] = []
        chunk_outputs: list[Path] = []
        config_paths: list[Path] = []

        try:
            if chunking_used:
                for chunk_index, (chunk_start_seconds, chunk_duration_seconds) in enumerate(chunk_ranges):
                    chunk_source_path, chunk_audio_path = _prepare_media_chunk(
                        source_path=source_for_inference,
                        source_kind=source_kind,
                        audio_path=audio_path,
                        work_dir=work,
                        chunk_index=int(chunk_index),
                        chunk_start_seconds=float(chunk_start_seconds),
                        chunk_duration_seconds=float(chunk_duration_seconds),
                    )

                    chunk_result_name = f"chunk_{chunk_index:04d}_{result_name}"
                    chunk_result_dir = result_dir / f"chunk_{chunk_index:04d}"
                    chunk_result_dir.mkdir(parents=True, exist_ok=True)

                    chunk_config_path = work / f"inference_chunk_{chunk_index:04d}.json"
                    chunk_config_path.write_text(
                        json.dumps(
                            {
                                "task_0": {
                                    "video_path": str(chunk_source_path),
                                    "audio_path": str(chunk_audio_path),
                                    "result_name": chunk_result_name,
                                    "bbox_shift": int(selected_params["bbox_shift"]),
                                }
                            }
                        ),
                        encoding="utf-8",
                    )
                    config_paths.append(chunk_config_path)

                    logger.info(
                        "MuseTalk chunk_start index=%s start_seconds=%s duration_seconds=%s source_path=%s audio_path=%s chunk_timeout_seconds=%s",
                        int(chunk_index),
                        round(float(chunk_start_seconds), 4),
                        round(float(chunk_duration_seconds), 4),
                        str(chunk_source_path),
                        str(chunk_audio_path),
                        round(float(chunk_timeout_seconds), 4),
                    )
                    chunk_started_at = time.monotonic()
                    try:
                        chunk_trace = _run_patched_inference_subprocess(
                            work_dir=work,
                            patched_inference_path=patched_inference_path,
                            config_path=chunk_config_path,
                            result_dir=chunk_result_dir,
                            selected_params=selected_params,
                            chunk_index=int(chunk_index),
                            total_deadline=total_deadline,
                            chunk_deadline=(chunk_started_at + chunk_timeout_seconds) if chunk_timeout_seconds > 0.0 else None,
                        )
                    except Exception:
                        elapsed = time.monotonic() - chunk_started_at
                        logger.info(
                            "MuseTalk chunk_timing project_id=%s job_id=%s segment_index=%s preview_job_id=%s "
                            "chunk_index=%s audio_duration_seconds=%s frame_count=%s elapsed_seconds=%s success=%s",
                            metric_project_id,
                            metric_job_id,
                            metric_segment_index,
                            metric_preview_job_id,
                            int(chunk_index),
                            round(float(chunk_duration_seconds), 4),
                            0,
                            round(float(elapsed), 4),
                            False,
                        )
                        raise
                    inference_traces.append(chunk_trace)

                    chunk_output = chunk_result_dir / "v15" / chunk_result_name
                    if not chunk_output.exists() or chunk_output.stat().st_size <= 0:
                        elapsed = time.monotonic() - chunk_started_at
                        logger.info(
                            "MuseTalk chunk_timing project_id=%s job_id=%s segment_index=%s preview_job_id=%s "
                            "chunk_index=%s audio_duration_seconds=%s frame_count=%s elapsed_seconds=%s success=%s",
                            metric_project_id,
                            metric_job_id,
                            metric_segment_index,
                            metric_preview_job_id,
                            int(chunk_index),
                            round(float(chunk_duration_seconds), 4),
                            0,
                            round(float(elapsed), 4),
                            False,
                        )
                        raise RuntimeError(
                            "musetalk_stage_failed stage=chunk_output_missing "
                            f"chunk_index={int(chunk_index)} output_path={chunk_output}"
                        )
                    chunk_outputs.append(chunk_output)
                    chunk_elapsed_seconds = time.monotonic() - chunk_started_at
                    chunk_frame_count = _probe_frame_count(chunk_output)
                    chunk_timing = {
                        "chunk_index": int(chunk_index),
                        "audio_duration_seconds": round(float(chunk_duration_seconds), 4),
                        "frame_count": int(chunk_frame_count),
                        "elapsed_seconds": round(float(chunk_elapsed_seconds), 4),
                        "success": True,
                    }
                    chunk_timing_metrics.append(chunk_timing)
                    chunk_metadata.append(
                        {
                            "index": int(chunk_index),
                            "chunk_index": int(chunk_index),
                            "start_seconds": round(float(chunk_start_seconds), 4),
                            "duration_seconds": round(float(chunk_duration_seconds), 4),
                            "audio_duration_seconds": round(float(chunk_duration_seconds), 4),
                            "frame_count": int(chunk_frame_count),
                            "elapsed_seconds": round(float(chunk_elapsed_seconds), 4),
                            "output_path": str(chunk_output),
                        }
                    )
                    logger.info(
                        "MuseTalk chunk_timing project_id=%s job_id=%s segment_index=%s preview_job_id=%s "
                        "chunk_index=%s audio_duration_seconds=%s frame_count=%s elapsed_seconds=%s success=%s",
                        metric_project_id,
                        metric_job_id,
                        metric_segment_index,
                        metric_preview_job_id,
                        int(chunk_index),
                        chunk_timing["audio_duration_seconds"],
                        int(chunk_timing["frame_count"]),
                        chunk_timing["elapsed_seconds"],
                        True,
                    )

                    _runtime_cleanup(stage_name=f"post_chunk_{chunk_index}")

                produced = result_dir / "v15" / result_name
                produced.parent.mkdir(parents=True, exist_ok=True)
                _concat_chunk_outputs(chunk_outputs=chunk_outputs, output_path=produced, work_dir=work)
            else:
                config_path = work / "inference.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "task_0": {
                                "video_path": str(source_for_inference),
                                "audio_path": str(audio_path),
                                "result_name": result_name,
                                "bbox_shift": int(selected_params["bbox_shift"]),
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                config_paths.append(config_path)

                chunk_started_at = time.monotonic()
                try:
                    trace = _run_patched_inference_subprocess(
                        work_dir=work,
                        patched_inference_path=patched_inference_path,
                        config_path=config_path,
                        result_dir=result_dir,
                        selected_params=selected_params,
                        chunk_index=0,
                        total_deadline=total_deadline,
                        chunk_deadline=(chunk_started_at + chunk_timeout_seconds) if chunk_timeout_seconds > 0.0 else None,
                    )
                except Exception:
                    elapsed = time.monotonic() - chunk_started_at
                    logger.info(
                        "MuseTalk chunk_timing project_id=%s job_id=%s segment_index=%s preview_job_id=%s "
                        "chunk_index=%s audio_duration_seconds=%s frame_count=%s elapsed_seconds=%s success=%s",
                        metric_project_id,
                        metric_job_id,
                        metric_segment_index,
                        metric_preview_job_id,
                        0,
                        round(float(audio_duration_seconds), 4),
                        0,
                        round(float(elapsed), 4),
                        False,
                    )
                    raise
                inference_traces.append(trace)
                produced = result_dir / "v15" / result_name
                chunk_elapsed_seconds = time.monotonic() - chunk_started_at
                chunk_output_ready = bool(produced.exists() and produced.stat().st_size > 0)
                chunk_frame_count = _probe_frame_count(produced)
                chunk_timing = {
                    "chunk_index": 0,
                    "audio_duration_seconds": round(float(audio_duration_seconds), 4),
                    "frame_count": int(chunk_frame_count),
                    "elapsed_seconds": round(float(chunk_elapsed_seconds), 4),
                    "success": bool(chunk_output_ready),
                }
                chunk_timing_metrics.append(chunk_timing)
                chunk_metadata.append(
                    {
                        "index": 0,
                        "chunk_index": 0,
                        "start_seconds": 0.0,
                        "duration_seconds": round(float(audio_duration_seconds), 4),
                        "audio_duration_seconds": round(float(audio_duration_seconds), 4),
                        "frame_count": int(chunk_frame_count),
                        "elapsed_seconds": round(float(chunk_elapsed_seconds), 4),
                        "output_path": str(produced),
                        "success": bool(chunk_output_ready),
                    }
                )
                logger.info(
                    "MuseTalk chunk_timing project_id=%s job_id=%s segment_index=%s preview_job_id=%s "
                    "chunk_index=%s audio_duration_seconds=%s frame_count=%s elapsed_seconds=%s success=%s",
                    metric_project_id,
                    metric_job_id,
                    metric_segment_index,
                    metric_preview_job_id,
                    0,
                    chunk_timing["audio_duration_seconds"],
                    int(chunk_timing["frame_count"]),
                    chunk_timing["elapsed_seconds"],
                    bool(chunk_timing["success"]),
                )

            _inference_done = time.monotonic()
            entrypoint_stage_timings["inference_total_seconds"] = round(_inference_done - _model_load_start, 4)
            logger.info(
                "MuseTalk entrypoint inference_done inference_seconds=%s total_seconds=%s chunking_used=%s chunk_count=%s",
                round(_inference_done - _model_load_start, 2),
                round(_inference_done - _entrypoint_start, 2),
                bool(chunking_used),
                int(len(chunk_ranges)),
            )
            _runtime_cleanup(stage_name="post_inference")
        except Exception as exc:
            trace_info = dict(getattr(exc, "trace_info", {}) or {})
            if persist_debug:
                failure_root = output_path.parent / "musetalk_runtime" / output_path.stem / "failed_run"
                failure_root.mkdir(parents=True, exist_ok=True)
                _copy_tree_best_effort(result_dir, failure_root / "results")
                for cfg_path in config_paths:
                    if cfg_path.exists():
                        shutil.copy2(cfg_path, failure_root / cfg_path.name)
                if patched_inference_path.exists():
                    shutil.copy2(patched_inference_path, failure_root / "patched_inference.py")
                failure_meta = {
                    "failure_reason": "musetalk_inference_failed",
                    "error": str(exc),
                    "source_image": str(source_image),
                    "source_video": str(source_video),
                    "audio_path": str(audio_path),
                    "result_path": str(output_path),
                    "result_dir": str(result_dir),
                    "patched_inference_path": str(patched_inference_path),
                    "inference_stage_trace": trace_info.get("stage_trace", []),
                    "inference_stage_timings": trace_info.get("stage_timings", {}),
                    "inference_last_stage": str(trace_info.get("current_stage", "")),
                    "inference_last_log_line": str(trace_info.get("last_log_line", "")),
                    "inference_log_tail": trace_info.get("log_tail", []),
                    "chunking_used": bool(chunking_used),
                    "chunk_metadata": chunk_metadata,
                    "chunk_timing_metrics": chunk_timing_metrics,
                }
                (failure_root / "failure_meta.json").write_text(
                    json.dumps(failure_meta, ensure_ascii=True, indent=2),
                    encoding="utf-8",
                )
                logger.warning(
                    "MuseTalk failure artifacts preserved path=%s",
                    str(failure_root),
                )
            raise RuntimeError(
                "musetalk_stage=inference_or_encode_failed "
                f"patched_script={patched_inference_path} result_dir={result_dir} "
                f"output_path={output_path} chunking_used={bool(chunking_used)} error={exc}"
            ) from exc

        if not produced.exists() or produced.stat().st_size == 0:
            raise RuntimeError(f"MuseTalk did not produce output at expected path: {produced}")
        raw_before = _analyze_video_hashes(produced)
        duration_before = _probe_duration_seconds(produced)
        raw_validation: dict[str, object] = {}
        try:
            from avatar.pipeline import validate_avatar_render_with_audio

            raw_validation = validate_avatar_render_with_audio(str(produced), str(audio_path))
        except Exception as exc:
            raw_validation = {
                "validation_error": str(exc),
            }

        runtime_debug: dict[str, object] = {
            "input_reference_image_path": str(source_image),
            "input_reference_video_path": str(source_video),
            "input_audio_path": str(audio_path),
            "input_reference_image_sha256": str(os.environ.get("AVATAR_MUSETALK_INPUT_SOURCE_SHA256", "") if source_image else ""),
            "input_reference_video_sha256": str(os.environ.get("AVATAR_MUSETALK_INPUT_SOURCE_SHA256", "") if source_video else ""),
            "input_audio_sha256": str(os.environ.get("AVATAR_MUSETALK_INPUT_AUDIO_SHA256", "")),
            "musetalk_run_id": str(os.environ.get("AVATAR_MUSETALK_RUN_ID", "")),
            "selected_musetalk_params": selected_params,
            "provider_diagnostics": provider_diagnostics,
            "entrypoint_stage_timings": entrypoint_stage_timings,
            "total_timeout_seconds": round(float(total_timeout_seconds), 4),
            "chunk_timeout_seconds": round(float(chunk_timeout_seconds), 4),
            "idle_timeout_seconds": round(float(idle_timeout_seconds), 4),
            "target_frame_count": int(target_frame_count),
            "target_duration_seconds": round(target_duration_seconds, 4),
            "raw_musetalk_output_path": str(produced),
            "final_encoded_mp4_path": str(output_path),
            "chunking_used": bool(chunking_used),
            "chunk_ranges": [
                {
                    "start_seconds": round(float(start_seconds), 4),
                    "duration_seconds": round(float(duration_seconds), 4),
                }
                for start_seconds, duration_seconds in chunk_ranges
            ],
            "chunk_metadata": chunk_metadata,
            "chunk_timing_metrics": chunk_timing_metrics,
            "timing_context": {
                "project_id": metric_project_id,
                "job_id": metric_job_id,
                "segment_index": metric_segment_index,
                "preview_job_id": metric_preview_job_id,
            },
            "frame_count_before_encoding": raw_before.get("frame_count", 0),
            "unique_frame_count_before_encoding": raw_before.get("unique_frame_count", 0),
            "frame_hash_summary_before_encoding": raw_before.get("frame_hash_summary", []),
            "raw_output_trace": raw_before,
            "raw_validation": raw_validation,
            "duration_before_encoding_seconds": round(duration_before, 4),
            "duration_delta_before_contract_seconds": (
                round(abs(duration_before - target_duration_seconds), 4) if target_duration_seconds > 0 else 0.0
            ),
            "pose_backend_type": "real_mmpose",
            "pose_backend_versions": pose_backend_versions,
            "stage_trace": [
                {
                    "chunk_index": int(chunk_index),
                    "trace": trace.get("stage_trace", []),
                }
                for chunk_index, trace in enumerate(inference_traces)
            ],
            "stage_timings": [
                {
                    "chunk_index": int(chunk_index),
                    "timings": trace.get("stage_timings", {}),
                }
                for chunk_index, trace in enumerate(inference_traces)
            ],
            "stage_timeouts": inference_traces[0].get("stage_timeouts", {}) if inference_traces else {},
            "inference_total_seconds": round(
                sum(float(trace.get("total_seconds") or 0.0) for trace in inference_traces),
                4,
            ),
            "intermediate_frame_directory": "",
            "raw_output_path": "",
        }

        def _write_runtime_debug() -> None:
            sidecar = output_path.with_suffix(output_path.suffix + ".musetalk_debug.json")
            sidecar.write_text(json.dumps(runtime_debug, ensure_ascii=True, indent=2), encoding="utf-8")

        frame_dir = _find_intermediate_frame_dir(result_dir / "v15")
        if frame_dir is not None:
            runtime_debug["intermediate_frame_directory"] = str(frame_dir)

        if persist_debug:
            diag_root = output_path.parent / "musetalk_runtime" / output_path.stem
            diag_root.mkdir(parents=True, exist_ok=True)
            raw_copy = diag_root / "raw_musetalk_output.mp4"
            shutil.copy2(produced, raw_copy)
            runtime_debug["raw_output_path"] = str(raw_copy)
            if frame_dir is not None and frame_dir.exists():
                sample_dir = diag_root / "intermediate_frames"
                sample_dir.mkdir(parents=True, exist_ok=True)
                copied = 0
                for img in sorted(frame_dir.iterdir()):
                    if not img.is_file() or img.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                        continue
                    shutil.copy2(img, sample_dir / img.name)
                    copied += 1
                    if copied >= int(os.environ.get("AVATAR_PREVIEW_DIAG_EXPORT_FRAMES", "32")):
                        break

        actual_raw_frames = int(raw_before.get("frame_count") or 0)
        if target_frame_count > 0 and actual_raw_frames < target_frame_count:
            runtime_debug["failure_reason"] = "raw_output_shorter_than_duration_contract"
            runtime_debug["contract_shortfall_frames"] = int(target_frame_count - actual_raw_frames)
            runtime_debug["contract_shortfall_seconds"] = round(
                max(target_duration_seconds - duration_before, 0.0),
                4,
            )
            extension_ok, extension_strategy, extension_error, extended_duration = _extend_video_to_duration(
                source_path=produced,
                target_duration_seconds=float(target_duration_seconds),
                shortfall_seconds=max(target_duration_seconds - duration_before, 0.0),
            )
            runtime_debug["contract_reconciliation_applied"] = bool(extension_ok)
            runtime_debug["contract_reconciliation_strategy"] = str(extension_strategy)
            runtime_debug["contract_reconciliation_error"] = str(extension_error)

            if extension_ok:
                raw_before = _analyze_video_hashes(produced)
                duration_before = _probe_duration_seconds(produced)
                actual_raw_frames = int(raw_before.get("frame_count") or 0)
                runtime_debug["raw_output_trace"] = raw_before
                runtime_debug["frame_count_before_encoding"] = raw_before.get("frame_count", 0)
                runtime_debug["unique_frame_count_before_encoding"] = raw_before.get("unique_frame_count", 0)
                runtime_debug["frame_hash_summary_before_encoding"] = raw_before.get("frame_hash_summary", [])
                runtime_debug["duration_before_encoding_seconds"] = round(duration_before, 4)
                runtime_debug["duration_delta_before_contract_seconds"] = (
                    round(abs(duration_before - target_duration_seconds), 4) if target_duration_seconds > 0 else 0.0
                )
                logger.info(
                    "MuseTalk raw contract extension applied strategy=%s duration_after=%s target_duration=%s frames_after=%s",
                    extension_strategy,
                    round(extended_duration, 4),
                    round(target_duration_seconds, 4),
                    int(actual_raw_frames),
                )
            else:
                logger.warning(
                    "MuseTalk raw contract extension unavailable target_frames=%s actual_frames=%s target_duration=%s actual_duration=%s reason=%s",
                    int(target_frame_count),
                    int(actual_raw_frames),
                    round(target_duration_seconds, 4),
                    round(duration_before, 4),
                    extension_error or "extension_failed",
                )

        shutil.copy2(produced, output_path)
        after = _analyze_video_hashes(output_path)
        duration_after = _probe_duration_seconds(output_path)
        final_validation: dict[str, object] = {}
        try:
            from avatar.pipeline import validate_avatar_render_with_audio

            final_validation = validate_avatar_render_with_audio(str(output_path), str(audio_path))
        except Exception as exc:
            final_validation = {
                "validation_error": str(exc),
            }
        runtime_debug.update(
            {
                "frame_count_after_encoding": after.get("frame_count", 0),
                "unique_frame_count_after_encoding": after.get("unique_frame_count", 0),
                "frame_hash_summary_after_encoding": after.get("frame_hash_summary", []),
                "final_output_trace": after,
                "duration_after_encoding_seconds": round(duration_after, 4),
                "duration_delta_after_contract_seconds": (
                    round(abs(duration_after - target_duration_seconds), 4) if target_duration_seconds > 0 else 0.0
                ),
                "final_validation": final_validation,
                "failure_reason": str(
                    (final_validation or {}).get("failure_reason")
                    or (raw_validation or {}).get("failure_reason")
                    or ""
                ),
            }
        )
        _runtime_cleanup(stage_name="post_final_save")
        _write_runtime_debug()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
