from __future__ import annotations

import argparse
import os
import re
import signal
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Motion composer — generates a scheduled driving video (blinks + gaze cycles)
try:
    import liveportrait_motion_composer as _motion_composer  # type: ignore
except ImportError:
    _motion_composer = None  # type: ignore

# Defaults for the composer code path
_TARGET_FPS      = int(os.environ.get("LP_MOTION_TARGET_FPS",    "25"))
_MIN_COMPOSE_DUR = float(os.environ.get("LP_MOTION_COMPOSE_MIN_S", "2.0"))
_MAX_COMPOSE_DUR = float(os.environ.get("LP_MOTION_COMPOSE_MAX_S", "0.0"))
_DRIVER_MIN_UNIQUE_FRAMES = int(os.environ.get("LP_DRIVER_MIN_UNIQUE_FRAMES", "6"))
_DRIVER_MIN_UNIQUE_RATIO = float(os.environ.get("LP_DRIVER_MIN_UNIQUE_RATIO", "0.16"))
_DRIVER_MIN_MAD = float(os.environ.get("LP_DRIVER_MIN_MAD", "0.35"))
_COMPOSER_MIN_UNIQUE_RATIO = float(os.environ.get("AVATAR_LIVEPORTRAIT_COMPOSER_MIN_UNIQUE_RATIO", "0.05"))
_COMPOSER_LONG_CLIP_MIN_UNIQUE_FRAMES = int(
    os.environ.get("AVATAR_LIVEPORTRAIT_COMPOSER_LONG_CLIP_MIN_UNIQUE_FRAMES", "20")
)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
_DEFAULT_MOTION_PRESET = "natural_conservative"
_ALLOWED_MOTION_PRESETS = {"natural_conservative", "natural_visible", "subtle_blink", "subtle_gaze", "expressive_debug"}
_BOOSTED_PROFILES = {"boosted", "boosted_strong", "stronger", "strong"}


def _truthy(value: str | None, default: str = "0") -> bool:
    raw = str(value if value is not None else default).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _resolve_motion_preset(raw_value: str | None = None) -> str:
    if _motion_composer is not None and hasattr(_motion_composer, "resolve_motion_preset"):
        try:
            return str(_motion_composer.resolve_motion_preset(raw_value))
        except Exception:
            pass
    raw = str(raw_value if raw_value is not None else os.environ.get("AVATAR_LIVEPORTRAIT_MOTION_PRESET", "")).strip().lower()
    return raw if raw in _ALLOWED_MOTION_PRESETS else _DEFAULT_MOTION_PRESET


def _boosted_retry_allowed(motion_preset: str) -> bool:
    if _motion_composer is not None and hasattr(_motion_composer, "boosted_retry_allowed"):
        try:
            return bool(
                _motion_composer.boosted_retry_allowed(
                    motion_preset=motion_preset,
                    env_value=os.environ.get("AVATAR_LIVEPORTRAIT_ALLOW_BOOSTED_RETRY"),
                )
            )
        except Exception:
            pass
    return motion_preset == "expressive_debug" or _truthy(os.environ.get("AVATAR_LIVEPORTRAIT_ALLOW_BOOSTED_RETRY"), "0")


def _composer_validation_env_mode(raw_value: str | None = None) -> str:
    raw = str(
        raw_value
        if raw_value is not None
        else os.environ.get("AVATAR_LIVEPORTRAIT_COMPOSER_VALIDATION_MODE", "localized")
    ).strip().lower()
    return "strict_global" if raw == "strict_global" else "localized"


def _compose_profiles_for_preset(motion_preset: str, allow_boosted_retry: bool) -> list[str]:
    if _motion_composer is not None and hasattr(_motion_composer, "profile_sequence_for_preset"):
        try:
            return list(
                _motion_composer.profile_sequence_for_preset(
                    motion_preset=motion_preset,
                    allow_boosted_retry=allow_boosted_retry,
                )
            )
        except Exception:
            pass
    profiles = ["default"]
    if allow_boosted_retry:
        profiles.extend(["boosted", "boosted_strong"])
    return profiles


def _derive_duration_contract(
    *,
    requested_fps: float,
    target_frame_count: int,
    audio_path: Path | None,
) -> dict[str, object]:
    requested_fps_value = max(float(requested_fps or 0.0), 0.0)
    target_frame_count_value = max(int(target_frame_count or 0), 0)
    target_duration_seconds = 0.0
    duration_source = "unknown"

    if target_frame_count_value > 0 and requested_fps_value > 0.0:
        target_duration_seconds = float(target_frame_count_value) / float(requested_fps_value)
        duration_source = "frame_count_and_requested_fps"
    elif audio_path is not None and audio_path.exists():
        target_duration_seconds = _probe_duration_seconds(audio_path, stream_selector="a:0")
        duration_source = "audio_duration"

    if target_duration_seconds <= 0.0:
        target_duration_seconds = max(float(_MIN_COMPOSE_DUR), 0.5)
        if float(_MAX_COMPOSE_DUR) > 0.0:
            target_duration_seconds = min(target_duration_seconds, float(_MAX_COMPOSE_DUR))
        duration_source = "compose_fallback"

    expected_duration_seconds = target_duration_seconds
    return {
        "requested_fps": round(float(requested_fps_value), 6),
        "internal_composer_fps": int(_TARGET_FPS),
        "target_frame_count": int(target_frame_count_value),
        "target_duration_seconds": round(float(target_duration_seconds), 6),
        "expected_duration_seconds": round(float(expected_duration_seconds), 6),
        "duration_source": str(duration_source),
    }


def _detect_input_kind(*, source_image: Path, explicit_source_video: Path | None) -> str:
    if explicit_source_video is None:
        return "image"
    source_video_ext = str(explicit_source_video.suffix or "").strip().lower()
    if source_video_ext in _IMAGE_EXTS:
        return "image"
    return "video"



def _run(cmd: list[str], *, timeout_seconds: int) -> tuple[bool, str, dict[str, str]]:
    details: dict[str, str] = {
        "cmd": " ".join(str(p) for p in cmd),
        "timeout_seconds": str(int(timeout_seconds)),
        "return_code": "",
        "stderr_summary": "",
    }
    process: subprocess.Popen[str] | None = None
    try:
        kwargs = (
            {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **kwargs,
        )
        stdout, stderr = process.communicate(timeout=max(int(timeout_seconds), 1))
    except subprocess.TimeoutExpired:
        if process is not None and process.poll() is None:
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
            try:
                process.wait(timeout=2)
            except Exception:
                pass
        details["return_code"] = "-1"
        details["stderr_summary"] = f"timeout after {int(timeout_seconds)}s"
        return False, f"liveportrait_stage_failed:timeout_after_{int(timeout_seconds)}s", details
    return_code = int(process.returncode if process is not None and process.returncode is not None else 0)
    details["return_code"] = str(return_code)
    details["stderr_summary"] = str((stderr or stdout or "").strip()[:500])
    if return_code != 0:
        return False, f"liveportrait_stage_failed:return_code_{return_code}", details
    return True, "", details


def _help_text(entrypoint: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(entrypoint), "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def _pick_flag(help_text: str, candidates: list[str]) -> str | None:
    text = str(help_text or "")
    for flag in candidates:
        pattern = re.compile(r"(^|\s)" + re.escape(flag) + r"(\s|=|\[|$)", re.MULTILINE)
        if pattern.search(text):
            return flag
    return None


def _resolve_output_from_dir(out_dir: Path) -> Path | None:
    if not out_dir.exists():
        return None
    mp4s = sorted(out_dir.rglob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return mp4s[0] if mp4s else None


def _find_candidate_output(output_path: Path, candidate_dirs: list[Path], min_mtime: float = 0.0) -> Path | None:
    preferred_name = output_path.name.lower()
    all_candidates: list[Path] = []
    for directory in candidate_dirs:
        if not directory.exists():
            continue
        for p in directory.rglob("*.mp4"):
            if not p.exists() or p.stat().st_size <= 0:
                continue
            if min_mtime > 0.0 and p.stat().st_mtime < min_mtime:
                continue
            all_candidates.append(p)
    if not all_candidates:
        return None
    exact = [p for p in all_candidates if p.name.lower() == preferred_name]
    if exact:
        exact.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return exact[0]
    all_candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return all_candidates[0]


def _probe_duration_seconds(path: Path, *, stream_selector: str = "") -> float:
    cmd = ["ffprobe", "-v", "error"]
    if stream_selector:
        cmd.extend(["-select_streams", stream_selector, "-show_entries", "stream=duration"])
    else:
        cmd.extend(["-show_entries", "format=duration"])
    cmd.extend(["-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=12)
    if proc.returncode == 0:
        try:
            return max(float(str(proc.stdout or "0").strip() or "0"), 0.0)
        except Exception:
            pass
    if stream_selector:
        fallback = subprocess.run(
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
            capture_output=True,
            text=True,
            check=False,
            timeout=12,
        )
        if fallback.returncode == 0:
            try:
                return max(float(str(fallback.stdout or "0").strip() or "0"), 0.0)
            except Exception:
                return 0.0
    return 0.0


def _parse_ffprobe_rate(raw_value: str) -> float:
    text = str(raw_value or "").strip()
    if not text or text in {"0/0", "N/A"}:
        return 0.0
    if "/" in text:
        numerator_text, denominator_text = text.split("/", 1)
        try:
            numerator = float(numerator_text)
            denominator = float(denominator_text)
        except Exception:
            return 0.0
        if abs(denominator) <= 1e-9:
            return 0.0
        return max(numerator / denominator, 0.0)
    try:
        return max(float(text), 0.0)
    except Exception:
        return 0.0


def _probe_video_fps(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate,r_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=12,
    )
    if proc.returncode != 0:
        return 0.0
    props: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        props[key.strip()] = value.strip()
    avg_rate = _parse_ffprobe_rate(str(props.get("avg_frame_rate") or ""))
    if avg_rate > 0.0:
        return avg_rate
    return _parse_ffprobe_rate(str(props.get("r_frame_rate") or ""))


def _resolve_existing_path(raw_path: str, *, base_dirs: list[Path]) -> Path | None:
    candidate_paths: list[Path] = []
    raw = str(raw_path or "").strip()
    if not raw:
        return None
    candidate_paths.append(Path(raw))
    if not Path(raw).is_absolute():
        for base_dir in base_dirs:
            candidate_paths.append(base_dir / raw)
    for candidate in candidate_paths:
        try:
            if candidate.exists():
                return candidate.resolve()
        except Exception:
            continue
    return None


def _discover_image_driving_templates(*, liveportrait_home: Path, repo_root: Path) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    seen: set[str] = set()

    def _append(origin: str, candidate: Path) -> None:
        suffix = str(candidate.suffix or "").strip().lower()
        if suffix not in _VIDEO_EXTS:
            return
        resolved = candidate.resolve()
        key = str(resolved).lower()
        if key in seen or not resolved.exists() or not resolved.is_file():
            return
        seen.add(key)
        candidates.append((str(origin), resolved))

    for env_key in ("AVATAR_LIVEPORTRAIT_IMAGE_DRIVING_TEMPLATE", "AVATAR_LIVEPORTRAIT_PREVIEW_DRIVING_TEMPLATE"):
        raw_value = str(os.environ.get(env_key, "")).strip()
        if not raw_value:
            continue
        resolved = _resolve_existing_path(raw_value, base_dirs=[repo_root, liveportrait_home])
        if resolved is None:
            print(
                "[LivePortrait] configured image driving template missing "
                f"env={env_key} value={raw_value}",
                file=sys.stderr,
            )
            continue
        _append(f"env:{env_key}", resolved)

    asset_dirs = [
        liveportrait_home / "assets" / "examples" / "driving",
        repo_root / "assets" / "liveportrait" / "driving",
        repo_root / "infra" / "assets" / "liveportrait" / "driving",
        repo_root / "services" / "assets" / "liveportrait" / "driving",
    ]
    for asset_dir in asset_dirs:
        if not asset_dir.exists() or not asset_dir.is_dir():
            continue
        for candidate in sorted(asset_dir.iterdir(), key=lambda item: item.name.lower()):
            if candidate.is_file():
                _append(f"asset:{asset_dir}", candidate)

    return candidates


def _format_driver_metrics(metrics: dict[str, object]) -> str:
    return (
        f"path={metrics.get('path')} "
        f"duration_seconds={float(metrics.get('duration_seconds') or 0.0):.4f} "
        f"expected_duration_seconds={float(metrics.get('expected_duration_seconds') or 0.0):.4f} "
        f"duration_delta_seconds={float(metrics.get('duration_delta_seconds') or 0.0):.4f} "
        f"fps={float(metrics.get('fps') or 0.0):.4f} "
        f"requested_fps={float(metrics.get('requested_fps') or 0.0):.4f} "
        f"frame_count={int(metrics.get('frame_count') or 0)} "
        f"target_frame_count={int(metrics.get('target_frame_count') or 0)} "
        f"frame_count_delta={int(metrics.get('frame_count_delta') or 0)} "
        f"unique_frames={int(metrics.get('unique_frames') or 0)} "
        f"unique_ratio={float(metrics.get('unique_ratio') or 0.0):.6f} "
        f"mean_mad={float(metrics.get('mean_mad') or 0.0):.6f} "
        f"liveportrait_driver_validation_mode={str(metrics.get('liveportrait_driver_validation_mode') or 'global')} "
        f"liveportrait_driver_localized_motion_passed={int(bool(metrics.get('liveportrait_driver_localized_motion_passed')))} "
        f"liveportrait_driver_near_static_threshold_profile={str(metrics.get('liveportrait_driver_near_static_threshold_profile') or 'global')} "
        f"composer_localized_motion_override={int(bool(metrics.get('composer_localized_motion_override')))} "
        f"liveportrait_driver_unique_ratio={float(metrics.get('liveportrait_driver_unique_ratio') or metrics.get('unique_ratio') or 0.0):.6f} "
        f"liveportrait_driver_unique_frames={int(metrics.get('liveportrait_driver_unique_frames') or metrics.get('unique_frames') or 0)} "
        f"liveportrait_driver_mean_mad={float(metrics.get('liveportrait_driver_mean_mad') or metrics.get('mean_mad') or 0.0):.6f} "
        f"liveportrait_driver_recipe_blink_events={int(metrics.get('liveportrait_driver_recipe_blink_events') or 0)} "
        f"liveportrait_driver_recipe_gaze_events={int(metrics.get('liveportrait_driver_recipe_gaze_events') or 0)} "
        f"near_static={bool(metrics.get('near_static'))} "
        f"valid={bool(metrics.get('valid'))} "
        f"failure_reason={str(metrics.get('failure_reason') or '')} "
        f"validation_failure_reason={str(metrics.get('validation_failure_reason') or '')}"
    )


def _safe_artifact_token(path: Path) -> str:
    roots = [
        os.environ.get("AVATAR_STORAGE_ROOT"),
        os.environ.get("STORAGE_ROOT"),
    ]
    for root_value in roots:
        root_text = str(root_value or "").strip()
        if not root_text:
            continue
        try:
            return path.resolve().relative_to(Path(root_text).resolve()).as_posix()
        except Exception:
            continue
    return path.name


def _cleanup_rejected_driver_debug_dir(debug_dir: Path, *, keep: int = 12) -> None:
    try:
        candidates = sorted(
            [path for path in debug_dir.glob("*.mp4") if path.is_file()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for stale_path in candidates[max(int(keep), 1):]:
            stale_path.unlink(missing_ok=True)
    except Exception:
        pass


def _preserve_rejected_driver_video(
    *,
    candidate_video: Path,
    output_path: Path,
    profile: str,
    driver_source_policy: str,
    metrics: dict[str, object],
    rejection_reason: str,
) -> str:
    try:
        if (
            not candidate_video.exists()
            or not candidate_video.is_file()
            or candidate_video.stat().st_size <= 0
        ):
            return ""
        safe_profile = (
            re.sub(r"[^A-Za-z0-9_.-]+", "_", str(profile or "default")).strip("._")
            or "default"
        )
        debug_dir = output_path.parent / "liveportrait_debug" / "rejected_drivers"
        debug_dir.mkdir(parents=True, exist_ok=True)
        target = debug_dir / f"{output_path.stem}.driver_{safe_profile}.rejected.mp4"
        shutil.copy2(candidate_video, target)
        _cleanup_rejected_driver_debug_dir(debug_dir)
        token = _safe_artifact_token(target)
        print(
            "[LivePortrait] rejected_driver_preserved "
            f"liveportrait_driver_source_policy={driver_source_policy} "
            "liveportrait_driver_source=composer "
            "liveportrait_composer_used=1 "
            f"liveportrait_motion_profile={safe_profile} "
            f"liveportrait_rejected_driver_video={token} "
            f"liveportrait_driver_rejection_reason={rejection_reason} "
            f"liveportrait_driver_rejection_unique_ratio={float(metrics.get('unique_ratio') or 0.0):.6f} "
            f"liveportrait_driver_rejection_mean_mad={float(metrics.get('mean_mad') or 0.0):.6f}",
            file=sys.stderr,
        )
        return token
    except Exception as exc:
        print(f"[LivePortrait] rejected_driver_preserve_failed reason={exc}", file=sys.stderr)
        return ""


def _should_preserve_selected_driver_video(output_path: Path) -> bool:
    raw = str(os.environ.get("AVATAR_LIVEPORTRAIT_PRESERVE_DRIVER_DEBUG", "")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return any(str(part).strip().lower() == "preview" for part in output_path.parts)


def _preserve_selected_driver_video(
    *,
    candidate_video: Path,
    output_path: Path,
    profile: str,
    driver_source_policy: str,
    metrics: dict[str, object],
) -> str:
    try:
        if not _should_preserve_selected_driver_video(output_path):
            return ""
        if (
            not candidate_video.exists()
            or not candidate_video.is_file()
            or candidate_video.stat().st_size <= 0
        ):
            return ""
        safe_profile = (
            re.sub(r"[^A-Za-z0-9_.-]+", "_", str(profile or "default")).strip("._")
            or "default"
        )
        debug_dir = output_path.parent / "liveportrait_debug" / "selected_drivers"
        debug_dir.mkdir(parents=True, exist_ok=True)
        target = debug_dir / f"{output_path.stem}.driver_{safe_profile}.selected.mp4"
        shutil.copy2(candidate_video, target)
        token = _safe_artifact_token(target)
        print(
            "[LivePortrait] selected_driver_preserved "
            f"liveportrait_driver_source_policy={driver_source_policy} "
            "liveportrait_driver_source=composer "
            "liveportrait_composer_used=1 "
            f"liveportrait_motion_profile={safe_profile} "
            f"liveportrait_selected_driver_video={token} "
            f"liveportrait_driver_unique_ratio={float(metrics.get('unique_ratio') or 0.0):.6f} "
            f"liveportrait_driver_mean_mad={float(metrics.get('mean_mad') or 0.0):.6f}",
            file=sys.stderr,
        )
        return token
    except Exception as exc:
        print(f"[LivePortrait] selected_driver_preserve_failed reason={exc}", file=sys.stderr)
        return ""


def _composer_recipe_motion_metadata(
    *,
    target_duration_s: float,
    seed: int,
    motion_preset: str,
    motion_profile: str,
) -> dict[str, object]:
    metadata = {
        "recipe_available": False,
        "blink_events": 0,
        "gaze_events": 0,
    }
    if _motion_composer is None or not hasattr(_motion_composer, "_build_motion_recipe"):
        return metadata
    try:
        recipe = _motion_composer._build_motion_recipe(  # type: ignore[attr-defined]
            float(target_duration_s),
            seed=int(seed),
            motion_profile=str(motion_profile or "default"),
            motion_preset=str(motion_preset or _DEFAULT_MOTION_PRESET),
        )
    except Exception:
        return metadata
    blink_events = list(recipe.get("blink_events_s") or [])
    gaze_events = list(recipe.get("gaze_events") or [])
    metadata.update(
        {
            "recipe_available": True,
            "blink_events": len(blink_events),
            "gaze_events": len(gaze_events),
        }
    )
    return metadata


def _apply_composer_localized_validation(
    metrics: dict[str, object],
    *,
    recipe_motion: dict[str, object],
    validation_mode: str,
) -> dict[str, object]:
    updated = dict(metrics)
    unique_frames = int(updated.get("unique_frames") or 0)
    unique_ratio = float(updated.get("unique_ratio") or 0.0)
    mean_mad = float(updated.get("mean_mad") or 0.0)
    duration_seconds = float(updated.get("duration_seconds") or 0.0)
    frame_count = int(updated.get("frame_count") or 0)
    blink_events = int(recipe_motion.get("blink_events") or 0)
    gaze_events = int(recipe_motion.get("gaze_events") or 0)
    technical_failure_reason = str(updated.get("technical_failure_reason") or "")
    technical_valid = bool(updated.get("technical_valid", not technical_failure_reason))
    env_mode = _composer_validation_env_mode(validation_mode)

    updated.update(
        {
            "liveportrait_driver_unique_ratio": unique_ratio,
            "liveportrait_driver_unique_frames": unique_frames,
            "liveportrait_driver_mean_mad": mean_mad,
            "liveportrait_driver_recipe_blink_events": blink_events,
            "liveportrait_driver_recipe_gaze_events": gaze_events,
            "liveportrait_driver_localized_motion_passed": False,
            "composer_localized_motion_override": False,
            "liveportrait_driver_validation_mode": (
                "composer_localized_motion" if env_mode == "localized" else "strict_global"
            ),
            "liveportrait_driver_near_static_threshold_profile": (
                "composer_localized_motion" if env_mode == "localized" else "global"
            ),
        }
    )
    if env_mode != "localized":
        return updated

    min_unique_frames = int(_DRIVER_MIN_UNIQUE_FRAMES)
    if duration_seconds >= 10.0 or frame_count >= 160:
        min_unique_frames = max(min_unique_frames, int(_COMPOSER_LONG_CLIP_MIN_UNIQUE_FRAMES))

    has_recipe_motion = (blink_events + gaze_events) > 0
    has_frame_variation = (
        unique_frames >= min_unique_frames
        and unique_ratio >= float(_COMPOSER_MIN_UNIQUE_RATIO)
    )
    localized_passed = bool(technical_valid and has_recipe_motion and has_frame_variation)
    updated["liveportrait_driver_localized_motion_passed"] = localized_passed
    if not localized_passed:
        return updated

    global_near_static = bool(updated.get("near_static"))
    global_invalid = not bool(updated.get("valid"))
    low_global_mad = mean_mad < float(_DRIVER_MIN_MAD)
    updated["global_near_static"] = global_near_static
    updated["global_validation_failure_reason"] = str(updated.get("validation_failure_reason") or "")
    updated["composer_localized_motion_override"] = bool(global_near_static or global_invalid or low_global_mad)
    updated["near_static"] = False
    updated["valid"] = True
    updated["failure_reason"] = ""
    updated["validation_failure_reason"] = ""
    return updated


def _probe_video_frame_count(path: Path) -> int:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=12,
    )
    if proc.returncode != 0:
        return 0
    try:
        return int(float(str(proc.stdout or "0").strip() or "0"))
    except Exception:
        return 0


def _probe_driving_clip_variation(path: Path) -> dict[str, object]:
    metrics: dict[str, object] = {
        "path": str(path),
        "duration_seconds": round(float(_probe_duration_seconds(path, stream_selector="v:0")), 4),
        "fps": round(float(_probe_video_fps(path)), 6),
        "frame_count": int(_probe_video_frame_count(path)),
        "unique_frames": 0,
        "unique_ratio": 0.0,
        "first_hash": "",
        "last_hash": "",
        "mean_mad": 0.0,
        "probe_errors": [],
    }

    hashes: list[str] = []
    framehash_proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-f",
            "framehash",
            "-hash",
            "md5",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if framehash_proc.returncode == 0:
        for line in (framehash_proc.stdout or "").splitlines():
            text = str(line or "").strip()
            if not text or text.startswith("#"):
                continue
            parts = [part.strip() for part in text.split(",")]
            if len(parts) >= 6 and parts[5]:
                hashes.append(parts[5])
    else:
        metrics["probe_errors"] = list(metrics.get("probe_errors") or []) + [
            f"framehash_return_code={int(framehash_proc.returncode)}"
        ]

    if hashes:
        metrics["unique_frames"] = int(len(set(hashes)))
        metrics["unique_ratio"] = round(float(len(set(hashes))) / float(len(hashes)), 6)
        metrics["first_hash"] = str(hashes[0])
        metrics["last_hash"] = str(hashes[-1])

    mad_values: list[float] = []
    mad_proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-vf",
            "tblend=all_mode=difference,signalstats,metadata=print",
            "-frames:v",
            "36",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=35,
    )
    if mad_proc.returncode == 0:
        mad_output = "\n".join(part for part in [mad_proc.stdout or "", mad_proc.stderr or ""] if part)
        for line in mad_output.splitlines():
            if (
                "lavfi.td.field0.mean" in line
                or "lavfi.td.mean" in line
                or "lavfi.signalstats.YAVG" in line
                or "lavfi.signalstats.YDIF" in line
            ):
                try:
                    mad_values.append(float(str(line).strip().split("=")[-1]))
                except Exception:
                    continue
    else:
        metrics["probe_errors"] = list(metrics.get("probe_errors") or []) + [
            f"mad_probe_return_code={int(mad_proc.returncode)}"
        ]

    if mad_values:
        metrics["mean_mad"] = round(sum(mad_values) / len(mad_values), 6)

    frame_count = int(metrics.get("frame_count") or 0)
    if frame_count <= 0 and hashes:
        frame_count = len(hashes)
        metrics["frame_count"] = int(frame_count)

    reasons: list[str] = []
    if not hashes:
        reasons.append("framehash_unavailable")
    unique_frames = int(metrics.get("unique_frames") or 0)
    unique_ratio = float(metrics.get("unique_ratio") or 0.0)
    mean_mad = float(metrics.get("mean_mad") or 0.0)
    if unique_frames < int(_DRIVER_MIN_UNIQUE_FRAMES):
        reasons.append(f"unique_frames={unique_frames}<min_{int(_DRIVER_MIN_UNIQUE_FRAMES)}")
    if unique_ratio < float(_DRIVER_MIN_UNIQUE_RATIO):
        reasons.append(f"unique_ratio={round(unique_ratio, 6)}<min_{round(float(_DRIVER_MIN_UNIQUE_RATIO), 6)}")
    if mean_mad < float(_DRIVER_MIN_MAD):
        reasons.append(f"mean_mad={round(mean_mad, 6)}<min_{round(float(_DRIVER_MIN_MAD), 6)}")
    if frame_count >= 8 and str(metrics.get("first_hash") or "") and str(metrics.get("first_hash") or "") == str(metrics.get("last_hash") or ""):
        reasons.append("first_last_frame_identical")

    metrics["near_static"] = bool(reasons)
    metrics["failure_reason"] = "driver_near_static:" + ";".join(reasons) if reasons else ""
    return metrics


def _validate_driving_clip(
    *,
    path: Path,
    expected_duration_seconds: float,
    requested_fps: float,
    target_frame_count: int,
    fps_validation_mode: str = "informational",
) -> dict[str, object]:
    metrics = _probe_driving_clip_variation(path)
    duration_seconds = float(metrics.get("duration_seconds") or 0.0)
    actual_fps = float(metrics.get("fps") or 0.0)
    frame_count = int(metrics.get("frame_count") or 0)
    expected_duration = max(float(expected_duration_seconds or 0.0), 0.0)
    requested_fps_value = max(float(requested_fps or 0.0), 0.0)
    target_frames = max(int(target_frame_count or 0), 0)

    reasons: list[str] = []
    if duration_seconds <= 0.0:
        reasons.append("duration_unavailable")
    if actual_fps <= 0.0:
        reasons.append("fps_unavailable")
    if frame_count <= 0:
        reasons.append("frame_count_unavailable")

    if expected_duration > 0.0 and duration_seconds > 0.0:
        duration_delta_seconds = duration_seconds - expected_duration
        metrics["duration_delta_seconds"] = round(float(duration_delta_seconds), 6)
        if abs(duration_delta_seconds) > 0.12:
            reasons.append(
                f"duration_delta={round(duration_delta_seconds, 6)}"
                f"(actual={round(duration_seconds, 6)} expected={round(expected_duration, 6)})"
            )
    else:
        metrics["duration_delta_seconds"] = 0.0

    metrics["requested_fps"] = round(float(requested_fps_value), 6)
    metrics["target_frame_count"] = int(target_frames)
    metrics["expected_duration_seconds"] = round(float(expected_duration), 6)
    metrics["frame_count_delta"] = int(frame_count - target_frames) if target_frames > 0 else 0
    metrics["fps_validation_mode"] = str(fps_validation_mode)

    if (
        fps_validation_mode == "must_match_requested"
        and requested_fps_value > 0.0
        and actual_fps > 0.0
        and abs(actual_fps - requested_fps_value) > 0.25
    ):
        reasons.append(
            f"fps_delta={round(actual_fps - requested_fps_value, 6)}"
            f"(actual={round(actual_fps, 6)} requested={round(requested_fps_value, 6)})"
        )
    if (
        fps_validation_mode == "must_match_requested"
        and target_frames > 0
        and frame_count > 0
        and abs(frame_count - target_frames) > 2
    ):
        reasons.append(
            f"frame_count_delta={int(frame_count - target_frames)}"
            f"(actual={int(frame_count)} requested={int(target_frames)})"
        )

    technical_reasons = list(reasons)
    metrics["technical_valid"] = not technical_reasons
    metrics["technical_failure_reason"] = ";".join(technical_reasons)

    if bool(metrics.get("near_static")):
        reasons.append(str(metrics.get("failure_reason") or "driver_near_static"))

    metrics["valid"] = not reasons
    metrics["validation_failure_reason"] = "driver_invalid:" + ";".join(reasons) if reasons else ""
    return metrics


def _ensure_driving_clip_contract(
    *,
    source_video: Path,
    target_duration_seconds: float,
    work_dir: Path,
    target_fps: float = 0.0,
    output_name: str = "driving_contract.mp4",
    always_materialize: bool = False,
) -> tuple[Path, str, float]:
    current_duration_seconds = _probe_duration_seconds(source_video, stream_selector="v:0")
    current_fps = _probe_video_fps(source_video)
    requested_fps = max(float(target_fps or 0.0), 0.0)

    if target_duration_seconds <= 0.0 or current_duration_seconds <= 0.0:
        if always_materialize:
            raise RuntimeError(
                "driving_contract_missing_duration "
                f"path={source_video} target_duration={target_duration_seconds:.6f}"
            )
        return source_video, "passed_through", current_duration_seconds

    duration_close = abs(current_duration_seconds - target_duration_seconds) <= 0.02
    fps_close = requested_fps <= 0.0 or (
        current_fps > 0.0 and abs(current_fps - requested_fps) <= 0.25
    )
    if not always_materialize and duration_close and fps_close:
        return source_video, "passed_through", current_duration_seconds

    output_path = work_dir / str(output_name)
    vf_filters: list[str] = []
    if requested_fps > 0.0:
        vf_filters.append(f"fps={requested_fps:.6f}")
    vf_filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    normalize_cmd = [
        "ffmpeg",
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(source_video),
    ]
    if vf_filters:
        normalize_cmd.extend(["-vf", ",".join(vf_filters)])
    normalize_cmd.extend(
        [
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
            str(output_path),
        ]
    )

    proc = subprocess.run(normalize_cmd, capture_output=True, text=True, check=False, timeout=150)
    if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
        resolved_duration_seconds = _probe_duration_seconds(output_path, stream_selector="v:0")
        action = "contract_materialized" if always_materialize else "contract_normalized"
        return output_path, action, resolved_duration_seconds

    print(
        "[LivePortrait] driving contract normalization fallback to passthrough "
        f"path={source_video} "
        f"target_duration={target_duration_seconds:.4f} current_duration={current_duration_seconds:.4f} "
        f"target_fps={requested_fps:.4f} current_fps={current_fps:.4f} "
        f"return_code={int(proc.returncode)} stderr={str(proc.stderr or '')[-220:]}",
        file=sys.stderr,
    )
    if always_materialize:
        raise RuntimeError(
            "driving_contract_materialization_failed "
            f"path={source_video} target_duration={target_duration_seconds:.6f} "
            f"target_fps={requested_fps:.6f} return_code={int(proc.returncode)} "
            f"stderr={str(proc.stderr or '')[-220:]}"
        )
    return source_video, "extension_failed_passthrough", current_duration_seconds


def _append_tuning_args(help_text: str, cmd: list[str], *, fps: float = 0.0) -> list[str]:
    tuned = list(cmd)

    if fps > 0.0:
        fps_flag = _pick_flag(help_text, ["--fps", "--frame_rate", "--frame-rate"])
        if fps_flag:
            tuned.extend([fps_flag, str(int(fps))])

    # driving-multiplier (motion strength for expression-friendly mode).
    # LP flag is --driving-multiplier (hyphenated), not --driving_multiplier.
    # Default to a subtle motion strength when not explicitly configured.
    motion_strength = str(os.environ.get("AVATAR_LIVEPORTRAIT_MOTION_STRENGTH", "0.35")).strip()
    if motion_strength and motion_strength.lower() not in ("0", "false"):
        motion_flag = _pick_flag(
            help_text,
            ["--driving-multiplier", "--driving_multiplier", "--motion_scale", "--driving_scale"],
        )
        if motion_flag:
            tuned.extend([motion_flag, motion_strength])

    # Temporal smoothing variance — LP flag: --driving-smooth-observation-variance.
    # Higher values = smoother but less accurate. 3e-6 is a good natural balance.
    # Temporal smoothing: default to a small positive variance for smoother motion
    temporal_smoothing = str(os.environ.get("AVATAR_LIVEPORTRAIT_TEMPORAL_SMOOTHING", "3e-6")).strip()
    if temporal_smoothing and temporal_smoothing.lower() not in ("0", "false"):
        temporal_flag = _pick_flag(
            help_text,
            [
                "--driving-smooth-observation-variance",
                "--driving_smooth_observation_variance",
                "--temporal_smoothing",
                "--driving_smooth_observation",
                "--smooth",
            ],
        )
        if temporal_flag:
            tuned.extend([temporal_flag, temporal_smoothing])

    # Animation region: exp, pose, lip, eyes, or all.
    # Default "all" gives natural head+eye+mouth motion.
    animation_region = str(os.environ.get("AVATAR_LIVEPORTRAIT_ANIMATION_REGION", "all")).strip()
    if animation_region:
        region_flag = _pick_flag(help_text, ["--animation-region", "--animation_region"])
        if region_flag:
            tuned.extend([region_flag, animation_region])

    # Driving option: expression-friendly (default) or pose-friendly.
    driving_option = str(os.environ.get("AVATAR_LIVEPORTRAIT_DRIVING_OPTION",
                                        "expression-friendly")).strip()
    if driving_option:
        option_flag = _pick_flag(help_text, ["--driving-option", "--driving_option"])
        if option_flag:
            tuned.extend([option_flag, driving_option])

    # Normalize lip: brings lip to neutral-closed state before animation.
    # Prevents open-mouth artifacts when source has mouth slightly open.
    normalize_lip = str(os.environ.get("AVATAR_LIVEPORTRAIT_NORMALIZE_LIP", "1")).strip()
    if normalize_lip not in ("", "0", "false", "False"):
        nl_flag = _pick_flag(help_text, ["--flag-normalize-lip", "--flag_normalize_lip"])
        if nl_flag:
            tuned.append(nl_flag)

    # Force fp16 (half-precision). Extremely important for 4GB VRAM!
    # Without this, it defaults to fp32, OOMs on 4GB, or falls back to soft-CPU taking 30+ mins.
    force_fp16 = str(os.environ.get("AVATAR_LIVEPORTRAIT_USE_HALF_PRECISION", "1")).strip()
    if force_fp16 not in ("", "0", "false", "False"):
        fp16_flag = _pick_flag(help_text, ["--flag-use-half-precision", "--flag_use_half_precision"])
        if fp16_flag:
            tuned.append(fp16_flag)

    extra_args = str(os.environ.get("AVATAR_LIVEPORTRAIT_EXTRA_ARGS", "")).strip()
    if extra_args:
        tuned.extend(shlex.split(extra_args))

    return tuned


def main() -> int:
    parser = argparse.ArgumentParser(description="LivePortrait runtime wrapper")
    parser.add_argument("--source_image", required=True)
    parser.add_argument("--source_video", required=False, default="")
    parser.add_argument("--audio_path", required=False, default="")
    parser.add_argument("--output_path", required=True)
    parser.add_argument("--liveportrait_home", required=True)
    parser.add_argument("--liveportrait_entrypoint", required=True)
    parser.add_argument("--liveportrait_model_path", required=True)
    parser.add_argument("--timeout_seconds", type=int, default=180)
    parser.add_argument("--fps", type=float, required=False, default=0.0)
    parser.add_argument("--target_frame_count", type=int, required=False, default=0)
    args = parser.parse_args()

    source_image = Path(args.source_image)
    explicit_source_video = Path(args.source_video) if str(args.source_video or "").strip() else None
    input_kind = _detect_input_kind(source_image=source_image, explicit_source_video=explicit_source_video)
    source_video: Path | None = explicit_source_video if input_kind == "video" else None
    output_path = Path(args.output_path)
    liveportrait_home = Path(args.liveportrait_home)
    liveportrait_entrypoint = Path(args.liveportrait_entrypoint)
    liveportrait_model_path = Path(args.liveportrait_model_path)
    repo_root = Path(__file__).resolve().parents[2]

    if not liveportrait_home.exists():
        raise RuntimeError(f"LivePortrait runtime home missing: {liveportrait_home}")
    if not liveportrait_entrypoint.exists():
        raise RuntimeError(f"LivePortrait entrypoint missing: {liveportrait_entrypoint}")
    if not liveportrait_model_path.exists():
        raise RuntimeError(f"LivePortrait model path missing: {liveportrait_model_path}")
    if not source_image.exists():
        raise RuntimeError(f"source_image missing: {source_image}")
    if input_kind == "video":
        if source_video is None:
            raise RuntimeError("liveportrait_input_kind_video_missing_source_video")
        if not source_video.exists():
            raise RuntimeError(f"driving input missing (source_video): {source_video}")

    # Record run start time for stale output detection (wall clock for mtime comparison).
    run_wall_start = time.time()

    # Pre-clear LivePortrait's shared outputs directory so any fallback candidate search
    # only finds files produced by this invocation, not a previous run's output.
    lp_outputs_dir = liveportrait_home / "outputs"
    if lp_outputs_dir.exists() and lp_outputs_dir.is_dir():
        for _stale_mp4 in lp_outputs_dir.rglob("*.mp4"):
            try:
                _stale_mp4.unlink()
            except Exception:
                pass

    help_text = _help_text(liveportrait_entrypoint)
    source_flag = _pick_flag(help_text, ["--source", "--source_image", "-s"])
    driving_flag = _pick_flag(help_text, ["--driving", "--driving_video", "--source_video", "-d"])
    audio_flag = _pick_flag(help_text, ["--audio_path", "--audio", "--driving_audio"])
    output_file_flag = _pick_flag(help_text, ["--output_path", "--output_video", "--result_path"])
    output_dir_flag = _pick_flag(help_text, ["--output-dir", "--output_dir", "--output_folder", "--output"])
    model_flag = _pick_flag(help_text, ["--model_path", "--checkpoint_dir", "--weights_dir", "--models_dir"])

    if not source_flag or not driving_flag:
        raise RuntimeError(
            "LivePortrait CLI signature is unsupported. "
            f"source_flag={source_flag} driving_flag={driving_flag} entrypoint={liveportrait_entrypoint}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="liveportrait-run-") as td:
        temp_dir = Path(td)

        # ── Motion source policy ─────────────────────────────────────────────
        # Image input: generate driving from image composition.
        # Video input: reuse the provided source video as real driving input.
        _driver_source_policy = str(
            os.environ.get("AVATAR_LIVEPORTRAIT_DRIVER_SOURCE_POLICY", "composer_for_image")
        ).strip().lower()
        _composer_validation_mode = _composer_validation_env_mode()
        _motion_source = "real_video" if input_kind == "video" else "image_pending"
        _source_mode = "video_reuse" if input_kind == "video" else "image_driven_composition"
        _motion_preset = _resolve_motion_preset(os.environ.get("AVATAR_LIVEPORTRAIT_MOTION_PRESET"))
        _allow_boosted_retry = _boosted_retry_allowed(_motion_preset)
        _compose_profiles = _compose_profiles_for_preset(_motion_preset, _allow_boosted_retry)
        _composer_used = False
        _boosted_retry_used = False
        _driver_source = "source_video" if input_kind == "video" else "pending"
        _selected_profile = ""
        _recenter_enabled = _motion_preset in {"natural_conservative", "natural_visible", "subtle_blink", "subtle_gaze", "expressive_debug"}
        _whole_frame_drift_guard = _motion_preset != "expressive_debug"
        _driving_action = "passed_through"
        _resolved_driving_duration_seconds = 0.0
        _driver_metrics: dict[str, object] = {}
        _driver_validation_mode = "informational"

        _audio_path = Path(str(args.audio_path)) if str(args.audio_path or "").strip() else None
        _duration_contract = _derive_duration_contract(
            requested_fps=float(args.fps or 0.0),
            target_frame_count=int(args.target_frame_count or 0),
            audio_path=_audio_path,
        )
        _requested_fps = float(_duration_contract.get("requested_fps") or 0.0)
        _internal_composer_fps = int(_duration_contract.get("internal_composer_fps") or _TARGET_FPS)
        _target_frame_count = int(_duration_contract.get("target_frame_count") or 0)
        _target_contract_duration_seconds = float(_duration_contract.get("target_duration_seconds") or 0.0)
        _expected_duration_seconds = float(_duration_contract.get("expected_duration_seconds") or 0.0)
        _duration_source = str(_duration_contract.get("duration_source") or "unknown")

        print(
            "[LivePortrait] driving_contract "
            f"requested_fps={_requested_fps:.4f} "
            f"internal_composer_fps={_internal_composer_fps} "
            f"target_frame_count={_target_frame_count} "
            f"target_duration_seconds={_target_contract_duration_seconds:.4f} "
            f"expected_duration_seconds={_expected_duration_seconds:.4f} "
            f"duration_source={_duration_source}",
            file=sys.stderr,
        )
        print(
            "[LivePortrait] motion_preset_policy "
            f"liveportrait_motion_preset={_motion_preset} "
            f"allow_boosted_retry={int(bool(_allow_boosted_retry))} "
            f"compose_profiles={','.join(_compose_profiles)} "
            f"liveportrait_recenter_enabled={int(bool(_recenter_enabled))} "
            f"liveportrait_whole_frame_drift_guard={int(bool(_whole_frame_drift_guard))}",
            file=sys.stderr,
        )

        if input_kind == "image":
            _target_dur = max(_target_contract_duration_seconds, 0.5)
            _composed_out = temp_dir / "composed_drive.mp4"
            _driver_rejections: list[str] = []
            _compose_succeeded_once = False
            _template_candidates = []
            if _driver_source_policy != "composer_for_image":
                _template_candidates = _discover_image_driving_templates(
                    liveportrait_home=liveportrait_home,
                    repo_root=repo_root,
                )

            _template_override_candidates = [
                (origin, candidate_path)
                for origin, candidate_path in _template_candidates
                if str(origin).startswith("env:")
            ]
            if _template_override_candidates:
                _template_candidates = _template_override_candidates
            _best_template_choice: tuple[
                tuple[float, float, int],
                Path,
                str,
                dict[str, object],
                str,
                float,
            ] | None = None

            for _template_index, (_template_origin, _template_path) in enumerate(_template_candidates):
                try:
                    _candidate_video, _driving_action, _resolved_driving_duration_seconds = _ensure_driving_clip_contract(
                        source_video=_template_path,
                        target_duration_seconds=float(_target_contract_duration_seconds),
                        work_dir=temp_dir,
                        target_fps=float(_requested_fps),
                        output_name=f"image_template_drive_{_template_index:02d}.mp4",
                        always_materialize=True,
                    )
                except Exception as _template_exc:
                    _driver_rejections.append(
                        f"{_template_origin}:materialize_failed:{_template_exc}"
                    )
                    print(
                        "[LivePortrait] driver candidate rejected "
                        f"candidate=image_template origin={_template_origin} "
                        f"path={_template_path} reason=materialize_failed:{_template_exc}",
                        file=sys.stderr,
                    )
                    continue

                _driver_validation_mode = "must_match_requested" if _requested_fps > 0.0 else "informational"
                _driver_metrics = _validate_driving_clip(
                    path=_candidate_video,
                    expected_duration_seconds=float(_expected_duration_seconds),
                    requested_fps=float(_requested_fps),
                    target_frame_count=int(_target_frame_count),
                    fps_validation_mode=_driver_validation_mode,
                )
                print(
                    "[LivePortrait] driver_variation "
                    f"candidate=image_template origin={_template_origin} "
                    + _format_driver_metrics(_driver_metrics),
                    file=sys.stderr,
                )
                if not bool(_driver_metrics.get("valid")):
                    _rejection_reason = str(
                        _driver_metrics.get("validation_failure_reason")
                        or _driver_metrics.get("failure_reason")
                        or "driver_invalid"
                    )
                    _driver_rejections.append(
                        f"{_template_origin}:{_rejection_reason}:{_format_driver_metrics(_driver_metrics)}"
                    )
                    print(
                        "[LivePortrait] driver candidate rejected "
                        f"candidate=image_template origin={_template_origin} "
                        f"reason={_rejection_reason} metrics={_format_driver_metrics(_driver_metrics)}",
                        file=sys.stderr,
                    )
                    continue

                if str(_template_origin).startswith("env:"):
                    source_video = _candidate_video
                    _motion_source = f"image_template:{_template_origin}"
                    _driver_source = "template"
                    break

                _template_score = (
                    float(_driver_metrics.get("mean_mad") or 0.0),
                    float(_driver_metrics.get("unique_ratio") or 0.0),
                    int(_driver_metrics.get("unique_frames") or 0),
                )
                if _best_template_choice is None or _template_score > _best_template_choice[0]:
                    _best_template_choice = (
                        _template_score,
                        _candidate_video,
                        str(_template_origin),
                        dict(_driver_metrics),
                        str(_driving_action),
                        float(_resolved_driving_duration_seconds),
                    )

            if source_video is None and _best_template_choice is not None:
                (
                    _template_score,
                    source_video,
                    _selected_template_origin,
                    _driver_metrics,
                    _driving_action,
                    _resolved_driving_duration_seconds,
                ) = _best_template_choice
                _motion_source = f"image_template:{_selected_template_origin}"
                _driver_source = "template"
                print(
                    "[LivePortrait] selected_template_candidate "
                    f"origin={_selected_template_origin} "
                    f"score_mean_mad={_template_score[0]:.6f} "
                    f"score_unique_ratio={_template_score[1]:.6f} "
                    f"score_unique_frames={_template_score[2]}",
                    file=sys.stderr,
                )

            for _profile in _compose_profiles:
                if source_video is not None:
                    break
                _composed_ok = False
                _composer_seed = int(os.environ.get("LP_MOTION_SEED", "42"))
                _recipe_motion = _composer_recipe_motion_metadata(
                    target_duration_s=float(_target_dur),
                    seed=int(_composer_seed),
                    motion_profile=str(_profile),
                    motion_preset=str(_motion_preset),
                )
                if _motion_composer is not None:
                    try:
                        _composed_ok = _motion_composer.compose(
                            _target_dur,
                            _composed_out,
                            seed=int(_composer_seed),
                            verbose=True,
                            source_kind="image",
                            source_image_path=source_image,
                            source_video_path=None,
                            motion_profile=str(_profile),
                            motion_preset=str(_motion_preset),
                            requested_fps=float(_requested_fps),
                            target_frame_count=int(_target_frame_count),
                            expected_duration_seconds=float(_expected_duration_seconds),
                            render_fps=int(_internal_composer_fps),
                        )
                    except Exception as _ce:
                        print(f"[LivePortrait] motion_composer error profile={_profile}: {_ce}", file=sys.stderr)
                if not _composed_ok or not _composed_out.exists() or _composed_out.stat().st_size <= 0:
                    _driver_rejections.append(f"{_profile}:compose_failed")
                    continue
                _compose_succeeded_once = True

                _candidate_video, _driving_action, _resolved_driving_duration_seconds = _ensure_driving_clip_contract(
                    source_video=_composed_out,
                    target_duration_seconds=float(_target_contract_duration_seconds),
                    work_dir=temp_dir,
                    target_fps=0.0,
                    output_name=f"image_composed_drive_{_profile}.mp4",
                )
                _driver_validation_mode = "allow_internal_fps"
                _driver_metrics = _validate_driving_clip(
                    path=_candidate_video,
                    expected_duration_seconds=float(_expected_duration_seconds),
                    requested_fps=float(_requested_fps),
                    target_frame_count=int(_target_frame_count),
                    fps_validation_mode=_driver_validation_mode,
                )
                _driver_metrics = _apply_composer_localized_validation(
                    _driver_metrics,
                    recipe_motion=_recipe_motion,
                    validation_mode=str(_composer_validation_mode),
                )
                print(
                    "[LivePortrait] driver_variation "
                    f"candidate=image_composed profile={_profile} "
                    + _format_driver_metrics(_driver_metrics),
                    file=sys.stderr,
                )
                if bool(_driver_metrics.get("composer_localized_motion_override")):
                    print(
                        "[LivePortrait] composer_localized_motion_override=true "
                        f"profile={_profile} "
                        + _format_driver_metrics(_driver_metrics),
                        file=sys.stderr,
                    )
                if not bool(_driver_metrics.get("valid")):
                    _rejection_reason = str(
                        _driver_metrics.get("validation_failure_reason")
                        or _driver_metrics.get("failure_reason")
                        or "driver_invalid"
                    )
                    if bool(_driver_metrics.get("near_static")):
                        _preserve_rejected_driver_video(
                            candidate_video=Path(_candidate_video),
                            output_path=output_path,
                            profile=str(_profile),
                            driver_source_policy=str(_driver_source_policy),
                            metrics=dict(_driver_metrics),
                            rejection_reason=_rejection_reason,
                        )
                    _driver_rejections.append(
                        f"{_profile}:{_rejection_reason}:{_format_driver_metrics(_driver_metrics)}"
                    )
                    print(
                        "[LivePortrait] driver candidate rejected "
                        f"candidate=image_composed profile={_profile} "
                        f"reason={_rejection_reason} metrics={_format_driver_metrics(_driver_metrics)}",
                        file=sys.stderr,
                    )
                    continue

                _selected_profile = str(_profile)
                _composer_used = True
                _boosted_retry_used = str(_profile).strip().lower() in _BOOSTED_PROFILES
                _driver_source = "composer"
                source_video = _candidate_video
                _motion_source = f"image_composed:{_selected_profile}"
                _preserve_selected_driver_video(
                    candidate_video=Path(_candidate_video),
                    output_path=output_path,
                    profile=str(_selected_profile),
                    driver_source_policy=str(_driver_source_policy),
                    metrics=dict(_driver_metrics),
                )
                break

            if source_video is None and not _compose_succeeded_once and not _template_candidates:
                raise RuntimeError(
                    "liveportrait_no_driving_source: no image driving template available and "
                    "motion_composer failed to produce image-driven composition."
                )
            if source_video is None:
                _details = "|".join(_driver_rejections[-3:]) if _driver_rejections else "driver_invalid"
                raise RuntimeError(f"liveportrait_invalid_driving_clip:{_details}")
            print(
                "[LivePortrait] final_driver_recipe "
                f"motion_source={_motion_source} "
                f"liveportrait_motion_preset={_motion_preset} "
                f"liveportrait_motion_profile={_selected_profile or 'template'} "
                f"liveportrait_driver_source_policy={_driver_source_policy} "
                f"liveportrait_driver_source={_driver_source} "
                f"liveportrait_composer_used={int(bool(_composer_used))} "
                f"liveportrait_boosted_retry_used={int(bool(_boosted_retry_used))} "
                f"liveportrait_recenter_enabled={int(bool(_recenter_enabled))} "
                f"liveportrait_whole_frame_drift_guard={int(bool(_whole_frame_drift_guard))} "
                f"profile={_selected_profile} "
                f"metrics={_format_driver_metrics(_driver_metrics)}",
                file=sys.stderr,
            )
        elif source_video is None:
            raise RuntimeError("liveportrait_input_kind_video_missing_source_video")

        if input_kind == "video":
            source_video, _driving_action, _resolved_driving_duration_seconds = _ensure_driving_clip_contract(
                source_video=source_video,
                target_duration_seconds=float(_target_contract_duration_seconds),
                work_dir=temp_dir,
                target_fps=0.0,
                output_name="video_input_drive_contract.mp4",
            )
            _motion_source = "real_video"
            _driver_source = "source_video"

        assert source_video is not None
        if input_kind == "image" and source_video.resolve() == source_image.resolve():
            raise RuntimeError("liveportrait_input_routing_bug:image_input_reused_source_image_as_driving_video")
        _resolved_source_path = source_image if input_kind == "image" else source_video

        if not _driver_metrics or str(_driver_metrics.get("path") or "") != str(source_video):
            if input_kind == "video":
                _driver_validation_mode = "informational"
            elif _motion_source.startswith("image_template"):
                _driver_validation_mode = "must_match_requested" if _requested_fps > 0.0 else "informational"
            else:
                _driver_validation_mode = "allow_internal_fps"
            _driver_metrics = _validate_driving_clip(
                path=source_video,
                expected_duration_seconds=float(_expected_duration_seconds),
                requested_fps=float(_requested_fps),
                target_frame_count=int(_target_frame_count),
                fps_validation_mode=_driver_validation_mode,
            )
        _resolved_driving_duration_seconds = float(
            _driver_metrics.get("duration_seconds") or _resolved_driving_duration_seconds
        )
        print(
            "[LivePortrait] driver_sanity "
            + _format_driver_metrics(_driver_metrics),
            file=sys.stderr,
        )
        if not bool(_driver_metrics.get("valid")):
            raise RuntimeError(f"liveportrait_invalid_driving_clip:{_format_driver_metrics(_driver_metrics)}")

        print(
            "[LivePortrait] "
            f"motion_source={_motion_source} "
            f"liveportrait_motion_preset={_motion_preset} "
            f"liveportrait_motion_profile={_selected_profile or ('source_video' if input_kind == 'video' else 'template')} "
            f"liveportrait_driver_source_policy={_driver_source_policy} "
            f"liveportrait_driver_source={_driver_source} "
            f"liveportrait_composer_used={int(bool(_composer_used))} "
            f"liveportrait_boosted_retry_used={int(bool(_boosted_retry_used))} "
            f"liveportrait_recenter_enabled={int(bool(_recenter_enabled))} "
            f"liveportrait_whole_frame_drift_guard={int(bool(_whole_frame_drift_guard))} "
            f"source_mode={_source_mode} "
            f"input_kind={input_kind} "
            f"driving_action={_driving_action} "
            f"requested_fps={_requested_fps:.4f} "
            f"internal_composer_fps={_internal_composer_fps} "
            f"target_frame_count={_target_frame_count} "
            f"target_duration_seconds={_target_contract_duration_seconds:.4f} "
            f"expected_duration_seconds={_expected_duration_seconds:.4f} "
            f"driving_duration_seconds={_resolved_driving_duration_seconds:.4f} "
            f"resolved_source_path={_resolved_source_path} "
            f"resolved_motion_source_path={source_video} "
            f"source_image={source_image} "
            f"driving_input={source_video} "
            f"final_output_path={output_path}",
            file=sys.stderr,
        )

        cmd = [sys.executable, str(liveportrait_entrypoint), source_flag, str(source_image), driving_flag, str(source_video)]

        if audio_flag and args.audio_path:
            cmd.extend([audio_flag, args.audio_path])
        if model_flag:
            cmd.extend([model_flag, str(liveportrait_model_path)])

        used_dir_output = False
        if output_file_flag:
            cmd.extend([output_file_flag, str(output_path)])
        elif output_dir_flag:
            used_dir_output = True
            cmd.extend([output_dir_flag, str(temp_dir)])
        else:
            used_dir_output = True
            cmd.extend(["--output-dir", str(temp_dir)])

        cmd = _append_tuning_args(help_text, cmd, fps=float(args.fps))

        ok, err, run_details = _run(cmd, timeout_seconds=int(args.timeout_seconds))
        if not ok:
            raise RuntimeError(
                f"{err} cmd={run_details.get('cmd')} return_code={run_details.get('return_code')} "
                f"stderr_summary={run_details.get('stderr_summary')} output_exists={output_path.exists()}"
            )

        # Reject an output file that predates this run — it is a stale artifact that
        # survived cleanup and must not be accepted as the current run's result.
        if output_path.exists() and output_path.stat().st_size > 0:
            try:
                if output_path.stat().st_mtime < run_wall_start - 2.0:
                    print(
                        f"[LivePortrait] stale output at {output_path} "
                        f"(mtime {output_path.stat().st_mtime:.0f} < run_start {run_wall_start:.0f}), removing",
                        file=sys.stderr,
                    )
                    output_path.unlink(missing_ok=True)
            except Exception:
                pass

        if output_path.exists() and output_path.stat().st_size > 0:
            # Post-process: resample frames to target frame count if specified
            if int(args.target_frame_count or 0) > 0 and float(args.fps or 0.0) > 0.0:
                try:
                    target_frames = int(args.target_frame_count)
                    target_fps = float(args.fps)
                    # Probe current video to get actual frame count
                    probe_cmd = [
                        "ffprobe",
                        "-v", "error",
                        "-select_streams", "v:0",
                        "-show_entries", "stream=nb_read_frames",
                        "-of", "default=noprint_wrappers=1:nokey=1:0",
                        str(output_path),
                    ]
                    probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, check=False, timeout=10)
                    current_frames = int(str(probe_result.stdout or "0").strip() or 0)
                    
                    if current_frames > 0 and current_frames != target_frames and abs(current_frames - target_frames) > 2:
                        # Resample: use fps filter to match frame count
                        # fps filter will drop/duplicate frames to match the target fps
                        tmp_resampled = output_path.parent / (output_path.name + ".resampled.mp4")
                        resample_cmd = [
                            "ffmpeg",
                            "-y",
                            "-i", str(output_path),
                            "-vf", f"fps={target_fps}",
                            "-c:a", "copy",
                            str(tmp_resampled),
                        ]
                        resample_proc = subprocess.run(resample_cmd, capture_output=True, text=True, check=False, timeout=120)
                        if tmp_resampled.exists() and tmp_resampled.stat().st_size > 0 and resample_proc.returncode == 0:
                            shutil.move(str(tmp_resampled), str(output_path))
                except Exception as resample_exc:
                    # Log but don't fail - resampling is optimization, not critical
                    print(f"[LivePortrait] frame resampling skipped: {resample_exc}", file=sys.stderr)
            print(
                "[LivePortrait] "
                f"motion_source={_motion_source} "
                f"liveportrait_motion_preset={_motion_preset} "
                f"liveportrait_motion_profile={_selected_profile or ('source_video' if input_kind == 'video' else 'template')} "
                f"liveportrait_driver_source_policy={_driver_source_policy} "
                f"liveportrait_driver_source={_driver_source} "
                f"liveportrait_composer_used={int(bool(_composer_used))} "
                f"liveportrait_boosted_retry_used={int(bool(_boosted_retry_used))} "
                f"liveportrait_recenter_enabled={int(bool(_recenter_enabled))} "
                f"liveportrait_whole_frame_drift_guard={int(bool(_whole_frame_drift_guard))} "
                f"source_mode={_source_mode} "
                f"input_kind={input_kind} "
                f"driving_action={_driving_action} "
                f"resolved_source_path={_resolved_source_path} "
                f"resolved_motion_source_path={source_video} "
                f"final_output_path={output_path}",
                file=sys.stderr,
            )
            return 0

        candidate = _find_candidate_output(
            output_path,
            [temp_dir, output_path.parent, liveportrait_home / "outputs", liveportrait_home],
            min_mtime=run_wall_start - 2.0,
        )
        if candidate and candidate.exists() and candidate.stat().st_size > 0:
            shutil.copy2(candidate, output_path)
            if output_path.exists() and output_path.stat().st_size > 0:
                return 0

        created_mp4s = [
            str(p)
            for p in sorted(temp_dir.rglob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)[:8]
        ]
        raise RuntimeError(
            "liveportrait_output_missing "
            f"output_path={output_path} cmd={run_details.get('cmd')} return_code={run_details.get('return_code')} "
            f"stderr_summary={run_details.get('stderr_summary')} used_dir_output={used_dir_output} "
            f"output_exists={output_path.exists()} created_outputs={created_mp4s}"
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
