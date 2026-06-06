from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

try:
    from celery.exceptions import SoftTimeLimitExceeded
except Exception:  # pragma: no cover - celery is present in worker runtime.
    class SoftTimeLimitExceeded(Exception):  # type: ignore[no-redef]
        pass

from avatar.resource_manager import compute_adaptive_timeout, probe_runtime_resources, record_stage_timing, release_stage_resources
from worker.avatar_timeout_policy import resolve_preview_task_time_limits

logger = logging.getLogger(__name__)

DEFAULT_PREVIEW_SCRIPT = "Hello! This is a preview of your avatar. If you can see this, it means your avatar is ready to go! Feel free to test it out and make any adjustments to your source image or video if needed. Enjoy bringing your avatar to life! and have fun exploring the possibilities! if you can see this, it means your avatar is ready to go! Feel free to test it out and make any adjustments to your source image or video if needed. Enjoy bringing your avatar to life! and have fun exploring the possibilities!"


def _storage_root() -> Path:
    return Path(str(os.environ.get("STORAGE_ROOT", "storage_local")) or "storage_local")


def _safe_rel_path(storage_root: Path, absolute_path: Path) -> str:
    return str(absolute_path.resolve().relative_to(storage_root.resolve())).replace("\\", "/")


def _extract_motion_source_marker(raw_output: str) -> str:
    marker_token = "motion_source="
    text = str(raw_output or "")
    marker_index = text.find(marker_token)
    if marker_index < 0:
        return ""
    return text[marker_index:].splitlines()[0].strip()


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
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "ffprobe_failed")
    return float((proc.stdout or "0").strip() or "0")


def _probe_frame_count(path: Path) -> int:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return 0
        return int((proc.stdout or "0").strip() or 0)
    except Exception:
        return 0


def _check_near_static(path: Path, *, min_mad_threshold: float = 0.3) -> bool:
    """Return True if video appears near-static/frozen using ffmpeg tblend difference."""
    try:
        # Probe basic properties
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=duration,nb_frames",
                "-of", "default=nw=1",
                str(path),
            ],
            capture_output=True, text=True, check=False, timeout=10,
        )
        props: dict[str, str] = {}
        for line in (probe.stdout or "").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                props[k.strip()] = v.strip()
        duration = float(props.get("duration") or 0)
        nb_frames = int(props.get("nb_frames") or 0)
        if duration < 0.5 or nb_frames < 4:
            return False

        r = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostdin", "-i", str(path),
                "-vf", "tblend=all_mode=difference,signalstats,metadata=print",
                "-frames:v", "30",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, check=False, timeout=30,
        )
        means: list[float] = []
        for line in (r.stderr or "").splitlines():
            if (
                "lavfi.td.field0.mean" in line
                or "lavfi.td.mean" in line
                or "lavfi.signalstats.YAVG" in line
                or "lavfi.signalstats.YDIF" in line
            ):
                try:
                    val = float(line.strip().split("=")[-1])
                except Exception:
                    continue
                means.append(val)
        if not means:
            return False
        avg_mean = sum(means) / len(means)
        return avg_mean < min_mad_threshold
    except Exception:
        return False


def _preview_fps() -> int:
    raw = str(os.environ.get("AVATAR_PREVIEW_FPS", "16")).strip()
    allowed = [10, 16, 20, 25, 32, 40, 50]
    try:
        value = int(raw)
    except Exception:
        value = 16
    value = max(value, 1)
    if value in allowed:
        return value
    nearest = min(allowed, key=lambda candidate: abs(candidate - value))
    logger.warning(
        "Avatar preview fps=%s is incompatible with 16kHz contract framing; using nearest supported fps=%s",
        int(value),
        int(nearest),
    )
    return int(nearest)


def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except Exception:
        return float(default)
    return float(value)


def _build_soft_time_limit_diagnostics(context: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    monotonic_now = time.monotonic() if now is None else float(now)
    task_started_at = float(context.get("task_started_at") or monotonic_now)
    stage_started_at = float(context.get("stage_started_at") or task_started_at)
    return {
        "classification": "preview_task_soft_time_limit_exceeded",
        "current_stage": str(context.get("current_stage") or "unknown"),
        "elapsed_total_task_seconds": round(max(monotonic_now - task_started_at, 0.0), 4),
        "stage_elapsed_seconds": round(max(monotonic_now - stage_started_at, 0.0), 4),
        "stage_timeout_budget_seconds": round(float(context.get("stage_timeout_budget_seconds") or 0.0), 4),
        "task_soft_limit_seconds": int(context.get("task_soft_limit_seconds") or 0),
        "task_hard_limit_seconds": int(context.get("task_hard_limit_seconds") or 0),
        "liveportrait_completed": bool(context.get("liveportrait_completed")),
        "musetalk_started": bool(context.get("musetalk_started")),
    }


def _format_soft_time_limit_error(diagnostics: dict[str, Any]) -> str:
    return (
        "preview_task_soft_time_limit_exceeded:"
        f"stage={diagnostics.get('current_stage')}"
        f" total_elapsed={diagnostics.get('elapsed_total_task_seconds')}s"
        f" stage_elapsed={diagnostics.get('stage_elapsed_seconds')}s"
        f" stage_timeout={diagnostics.get('stage_timeout_budget_seconds')}s"
        f" task_soft={diagnostics.get('task_soft_limit_seconds')}s"
        f" task_hard={diagnostics.get('task_hard_limit_seconds')}s"
        f" lp_completed={str(bool(diagnostics.get('liveportrait_completed'))).lower()}"
        f" musetalk_started={str(bool(diagnostics.get('musetalk_started'))).lower()}"
    )


def _select_preview_script() -> tuple[str, dict[str, Any]]:
    override = " ".join(str(os.environ.get("AVATAR_PREVIEW_SCRIPT", "")).split()).strip()
    max_words_raw = str(os.environ.get("AVATAR_PREVIEW_MAX_SCRIPT_WORDS", "4")).strip()
    max_chars_raw = str(os.environ.get("AVATAR_PREVIEW_MAX_SCRIPT_CHARS", "28")).strip()
    try:
        max_words = max(int(max_words_raw), 1)
    except Exception:
        max_words = 4
    try:
        max_chars = max(int(max_chars_raw), 8)
    except Exception:
        max_chars = 28

    if not override:
        return DEFAULT_PREVIEW_SCRIPT, {
            "source": "default_short",
            "used_default": True,
            "reason": "empty_override",
        }

    word_count = len([token for token in override.split(" ") if token.strip()])
    if word_count > max_words or len(override) > max_chars:
        logger.warning(
            "Avatar preview script override rejected for duration budget override=%s word_count=%s max_words=%s chars=%s max_chars=%s",
            override,
            int(word_count),
            int(max_words),
            int(len(override)),
            int(max_chars),
        )
        return DEFAULT_PREVIEW_SCRIPT, {
            "source": "default_short",
            "used_default": True,
            "reason": "override_too_long",
            "override": override,
            "word_count": int(word_count),
            "max_words": int(max_words),
            "char_count": int(len(override)),
            "max_chars": int(max_chars),
        }

    return override, {
        "source": "env_override",
        "used_default": False,
        "reason": "within_preview_budget",
        "word_count": int(word_count),
        "char_count": int(len(override)),
    }


def _audio_is_effectively_silent(path: Path) -> bool:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return "max_volume: -inf db" in str(proc.stderr or "").lower()


def _verify_preview_audio(path: Path, *, expected_duration: float | None = None) -> float:
    if not path.exists():
        raise RuntimeError("tts_audio_invalid:missing_file")
    if path.stat().st_size <= 0:
        raise RuntimeError("tts_audio_invalid:empty_file")
    duration = _probe_duration_seconds(path)
    if duration < 0.3:
        raise RuntimeError(f"tts_audio_invalid:too_short:{duration:.3f}s")
    if expected_duration is not None and abs(duration - expected_duration) > 0.6:
        raise RuntimeError(
            f"tts_audio_invalid:duration_mismatch:generated={duration:.3f}s,expected={expected_duration:.3f}s"
        )
    if _audio_is_effectively_silent(path):
        raise RuntimeError("tts_audio_invalid:silent_audio")
    return duration


def _trim_preview_audio_silence(path: Path) -> None:
    trimmed = path.with_suffix(path.suffix + ".trim.mp3")
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-af",
            "silenceremove=start_periods=1:start_threshold=-42dB:start_silence=0.12:stop_periods=-1:stop_threshold=-42dB:stop_silence=0.18",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "3",
            str(trimmed),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not trimmed.exists() or trimmed.stat().st_size <= 0:
        trimmed.unlink(missing_ok=True)
        return
    try:
        trimmed_duration = _probe_duration_seconds(trimmed)
        original_duration = _probe_duration_seconds(path)
    except Exception:
        trimmed.unlink(missing_ok=True)
        return
    if trimmed_duration < 0.5 or trimmed_duration >= (original_duration - 0.03):
        trimmed.unlink(missing_ok=True)
        return
    shutil.move(str(trimmed), str(path))


def _convert_audio_to_wav(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not destination.exists() or destination.stat().st_size <= 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "preview_audio_conversion_failed")


def _build_preview_audio_contract(*, source_mp3: Path, target_wav: Path, fps: int) -> dict[str, Any]:
    if fps <= 0:
        raise RuntimeError(f"preview_audio_contract_invalid_fps:{fps}")

    with tempfile.TemporaryDirectory(prefix="preview-audio-contract-") as temp_dir:
        decoded_wav = Path(temp_dir) / "decoded.wav"
        _convert_audio_to_wav(source_mp3, decoded_wav)
        with wave.open(str(decoded_wav), "rb") as reader:
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            sample_rate = reader.getframerate()
            sample_count = reader.getnframes()
            pcm_bytes = reader.readframes(sample_count)

        samples_per_frame = sample_rate / float(fps)
        if abs(samples_per_frame - round(samples_per_frame)) > 1e-6:
            raise RuntimeError(
                f"preview_audio_contract_non_integer_samples_per_frame:sample_rate={sample_rate},fps={fps}"
            )

        frame_sample_count = int(round(samples_per_frame))
        target_frame_count = max(int(sample_count // frame_sample_count), 1)

        # Preview-only floor: avoid ultra-short contracts that collapse motion metrics.
        raw_min_preview_frames = str(os.environ.get("AVATAR_PREVIEW_MIN_FRAME_COUNT", "24")).strip()
        try:
            min_preview_frames = max(int(raw_min_preview_frames), 1)
        except Exception:
            min_preview_frames = 24
        if target_frame_count < min_preview_frames:
            target_frame_count = int(min_preview_frames)

        target_sample_count = target_frame_count * frame_sample_count
        target_byte_count = target_sample_count * channels * sample_width

        pcm_target = pcm_bytes[:target_byte_count]
        if len(pcm_target) < target_byte_count:
            pcm_target = pcm_target + (b"\x00" * (target_byte_count - len(pcm_target)))

        target_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(target_wav), "wb") as writer:
            writer.setnchannels(channels)
            writer.setsampwidth(sample_width)
            writer.setframerate(sample_rate)
            writer.writeframes(pcm_target)

    source_duration_seconds = sample_count / float(sample_rate)
    target_duration_seconds = target_frame_count / float(fps)
    return {
        "source_duration_seconds": round(float(source_duration_seconds), 4),
        "target_duration_seconds": round(float(target_duration_seconds), 4),
        "target_frame_count": int(target_frame_count),
        "source_sample_count": int(sample_count),
        "target_sample_count": int(target_sample_count),
        "sample_rate": int(sample_rate),
        "fps": int(fps),
    }


def _prepare_preview_audio(
    *,
    voice_id: str,
    language: str,
    preview_script: str,
    output_mp3: Path,
    output_wav: Path,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    from scripts.tts_client import synthesize_text_with_metadata

    def _run_tts() -> dict[str, Any]:
        return dict(
            synthesize_text_with_metadata(
                voice_id,
                preview_script,
                str(output_mp3),
                mode="service",
                lang=(language or "auto"),
            )
        )

    effective_timeout_seconds = (
        float(timeout_seconds)
        if timeout_seconds is not None and float(timeout_seconds) > 0.0
        else float(str(os.environ.get("AVATAR_PREVIEW_TTS_TIMEOUT_SECONDS", "240")).strip() or 240.0)
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run_tts)
        try:
            tts_meta = future.result(timeout=effective_timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            raise RuntimeError(f"tts_timeout:exceeded_{int(effective_timeout_seconds)}s") from exc

    provider = str(tts_meta.get("provider") or "").strip().lower()
    if provider == "fallback":
        raise RuntimeError("tts_audio_invalid:fallback_audio")

    expected_duration = None
    if tts_meta.get("duration") is not None:
        try:
            expected_duration = float(tts_meta["duration"])
        except Exception:
            expected_duration = None
    generated_duration = _verify_preview_audio(output_mp3, expected_duration=expected_duration)
    _trim_preview_audio_silence(output_mp3)
    generated_duration = _verify_preview_audio(output_mp3, expected_duration=None)
    contract = _build_preview_audio_contract(source_mp3=output_mp3, target_wav=output_wav, fps=_preview_fps())
    wav_duration = _verify_preview_audio(output_wav, expected_duration=None)
    return {
        "provider": provider or "service",
        "duration_seconds": round(float(generated_duration), 4),
        "wav_duration_seconds": round(float(wav_duration), 4),
        "source_mp3_path": str(output_mp3),
        "wav_path": str(output_wav),
        "contract": contract,
    }


def _clear_preview_run_artifacts(*, preview_dir: Path, output_mp4: Path, source_mp3: Path, audio_wav: Path) -> list[str]:
    removed: list[str] = []
    candidates = [
        source_mp3,
        audio_wav,
        output_mp4,
        output_mp4.with_suffix(output_mp4.suffix + ".meta.json"),
        output_mp4.with_suffix(output_mp4.suffix + ".liveportrait.mp4"),
        output_mp4.with_suffix(output_mp4.suffix + ".liveportrait.reconciled.mp4"),
        output_mp4.with_suffix(output_mp4.suffix + ".musetalk_handoff.mp4"),
        output_mp4.with_suffix(output_mp4.suffix + ".musetalk.mp4"),
        output_mp4.with_suffix(output_mp4.suffix + ".restored.mp4"),
        output_mp4.with_suffix(output_mp4.suffix + ".musetalk_debug.json"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            candidate.unlink(missing_ok=True)
            removed.append(str(candidate))
    for candidate in sorted(preview_dir.glob(output_mp4.name + ".canonical_*.png")):
        if candidate.exists() and candidate.is_file():
            candidate.unlink(missing_ok=True)
            removed.append(str(candidate))
    return removed


def render_avatar_preview_canonical(task: Any, *, teacher_id: int, job_id: int | None = None) -> dict[str, Any]:
    from avatar.canonical_adapters import normalize_avatar_engine
    from avatar.hashing import sha256_file
    from avatar.pipeline import AvatarRenderRequest, render_avatar_segment_local
    from core.avatar_readiness import avatar_preview_readiness  # type: ignore
    from core.avatar_source_validation import refresh_avatar_source_validation  # type: ignore
    from core.models import Job, UserProfile, VoiceProfile  # type: ignore

    storage_root = _storage_root()
    profile = UserProfile.objects.filter(user_id=int(teacher_id)).first()
    voice_profile = VoiceProfile.objects.filter(user_id=int(teacher_id)).first()
    if profile is None:
        raise RuntimeError("setup_not_prepared:missing_profile")

    def _set_profile_state(
        *,
        status: str,
        error: str = "",
        preview_rel_path: str | None = None,
        image_status: str,
        clear_preview: bool = False,
        preview_source_hash: str | None = None,
    ) -> None:
        update_fields = ["avatar_last_preview_status", "avatar_preview_error", "avatar_last_preview_job_id", "avatar_image_status", "updated_at"]
        profile.avatar_last_preview_status = status
        profile.avatar_preview_error = error
        profile.avatar_last_preview_job_id = str(job_id or getattr(getattr(task, "request", None), "id", "") or "")
        profile.avatar_image_status = image_status
        if clear_preview:
            profile.avatar_preview_video = ""
            profile.avatar_last_preview_path = ""
            profile.avatar_preview_source_hash = ""
            profile.avatar_preview_stale = False
            update_fields.extend(["avatar_preview_video", "avatar_last_preview_path", "avatar_preview_source_hash", "avatar_preview_stale"])
        if preview_rel_path is not None:
            profile.avatar_preview_video = preview_rel_path
            profile.avatar_last_preview_path = preview_rel_path
            update_fields.extend(["avatar_preview_video", "avatar_last_preview_path"])
        if preview_source_hash is not None:
            profile.avatar_preview_source_hash = str(preview_source_hash or "")
            profile.avatar_preview_stale = False
            update_fields.extend(["avatar_preview_source_hash", "avatar_preview_stale"])
        profile.save(update_fields=update_fields)

    def _set_job(*, status: str, progress: int, result_url: str = "", error_message: str = "") -> None:
        if not job_id:
            return
        Job.objects.filter(id=int(job_id)).update(
            status=status,
            progress=int(progress),
            result_url=result_url,
            error_message=error_message,
        )

    def _cleanup_summary(payload: dict[str, Any]) -> dict[str, Any]:
        before = dict(payload.get("before") or {})
        after = dict(payload.get("after") or {})
        before_gpu = dict((before.get("gpu") or {}).get("selected") or {})
        after_gpu = dict((after.get("gpu") or {}).get("selected") or {})
        before_system = dict(before.get("system") or {})
        after_system = dict(after.get("system") or {})
        torch_payload = dict(payload.get("torch") or {})
        return {
            "reason": str(payload.get("reason") or ""),
            "gc_collected": int(payload.get("gc_collected") or 0),
            "torch_cache_cleared": bool(torch_payload.get("cache_cleared")),
            "before_gpu_free_mib": int(before_gpu.get("free_mib") or 0),
            "after_gpu_free_mib": int(after_gpu.get("free_mib") or 0),
            "before_mem_available_mib": int(before_system.get("available_mib") or 0),
            "after_mem_available_mib": int(after_system.get("available_mib") or 0),
        }

    if profile.avatar_image_processed or profile.avatar_image_original or profile.avatar_video_original:
        refresh_avatar_source_validation(profile, storage_root=storage_root, persist=True)
    readiness = avatar_preview_readiness(profile, voice_profile, storage_root=storage_root)
    if not bool(readiness.get("ready")):
        missing = ",".join(sorted(set(readiness.get("missing_requirements") or [])))
        error_text = str(readiness.get("error") or "Avatar is not prepared for preview.")
        if missing:
            error_text = f"setup_not_prepared:{missing}:{error_text}"
        else:
            error_text = f"setup_not_prepared:{error_text}"
        _set_profile_state(status="failed", error=error_text, image_status="failed", clear_preview=True)
        _set_job(status="failed", progress=100, error_message=error_text)
        raise RuntimeError(error_text)

    preview_dir = storage_root / "avatars" / str(teacher_id) / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    source_mp3 = preview_dir / "preview_source.mp3"
    audio_wav = preview_dir / "preview.wav"
    output_mp4 = preview_dir / "preview.mp4"

    preview_script, preview_script_meta = _select_preview_script()
    preview_text_hash = hashlib.sha256(preview_script.encode("utf-8")).hexdigest()

    logger.info(
        "Avatar preview script selected teacher_id=%s job_id=%s script=%s text_hash=%s meta=%s",
        int(teacher_id),
        int(job_id or 0),
        preview_script,
        preview_text_hash,
        preview_script_meta,
    )

    removed_artifacts = _clear_preview_run_artifacts(
        preview_dir=preview_dir,
        output_mp4=output_mp4,
        source_mp3=source_mp3,
        audio_wav=audio_wav,
    )
    if removed_artifacts:
        logger.info(
            "Avatar preview current-run cleanup teacher_id=%s job_id=%s removed=%s",
            int(teacher_id),
            int(job_id or 0),
            removed_artifacts,
        )

    run_wall_start = time.time()
    task_monotonic_start = time.monotonic()
    preview_task_limits = resolve_preview_task_time_limits(logger=logger)
    preview_task_context: dict[str, Any] = {
        "task_started_at": task_monotonic_start,
        "stage_started_at": task_monotonic_start,
        "current_stage": "preview_setup",
        "stage_timeout_budget_seconds": 0.0,
        "task_soft_limit_seconds": int(preview_task_limits.soft_seconds),
        "task_hard_limit_seconds": int(preview_task_limits.hard_seconds),
        "liveportrait_completed": False,
        "musetalk_started": False,
    }

    _set_job(status="running", progress=10, result_url="")
    _set_profile_state(status="rendering", error="", image_status="processing", clear_preview=True)

    try:
        preview_resources_before_tts = probe_runtime_resources()
        estimated_tts_audio_seconds = max(float(len([token for token in preview_script.split(" ") if token.strip()])) * 0.45, 0.8)
        estimated_tts_frame_count = max(int(round(estimated_tts_audio_seconds * float(_preview_fps()))), 1)

        explicit_tts_timeout_raw = str(
            os.environ.get("AVATAR_ORCH_STAGE_TIMEOUT_TTS_SECONDS", "")
            or os.environ.get("AVATAR_PREVIEW_TTS_TIMEOUT_SECONDS", "")
        ).strip()
        try:
            explicit_tts_timeout = float(explicit_tts_timeout_raw) if explicit_tts_timeout_raw else 0.0
        except Exception:
            explicit_tts_timeout = 0.0

        tts_timeout_seconds, tts_timeout_reason = compute_adaptive_timeout(
            stage_name="tts",
            audio_duration_seconds=estimated_tts_audio_seconds,
            frame_count=estimated_tts_frame_count,
            base_seconds=_env_float("AVATAR_ORCH_TTS_TIMEOUT_BASE_SECONDS", 40.0),
            min_seconds=_env_float("AVATAR_ORCH_TTS_TIMEOUT_MIN_SECONDS", 45.0),
            max_seconds=_env_float("AVATAR_ORCH_TTS_TIMEOUT_MAX_SECONDS", 360.0),
            per_audio_second=_env_float("AVATAR_ORCH_TTS_TIMEOUT_PER_AUDIO_SECOND", 4.0),
            per_frame_second=_env_float("AVATAR_ORCH_TTS_TIMEOUT_PER_FRAME_SECOND", 0.08),
            explicit_timeout_seconds=explicit_tts_timeout,
            resources=preview_resources_before_tts,
        )

        tts_started_at = time.monotonic()
        preview_task_context.update(
            {
                "current_stage": "tts",
                "stage_started_at": tts_started_at,
                "stage_timeout_budget_seconds": round(float(tts_timeout_seconds), 4),
            }
        )
        tts_success = False
        tts_history_audio_seconds = estimated_tts_audio_seconds
        tts_history_frame_count = estimated_tts_frame_count
        try:
            audio_info = _prepare_preview_audio(
                voice_id=str(getattr(voice_profile, "voice_id", "") or ""),
                language=str(getattr(voice_profile, "language", "") or "auto"),
                preview_script=preview_script,
                output_mp3=source_mp3,
                output_wav=audio_wav,
                timeout_seconds=float(tts_timeout_seconds),
            )
            tts_success = True
            tts_history_audio_seconds = float(audio_info.get("duration_seconds") or estimated_tts_audio_seconds)
            tts_history_frame_count = int(((audio_info.get("contract") or {}).get("target_frame_count") or estimated_tts_frame_count) or estimated_tts_frame_count)
        finally:
            tts_elapsed_seconds = time.monotonic() - tts_started_at
            tts_resources_after = probe_runtime_resources()
            record_stage_timing(
                stage_name="tts",
                elapsed_seconds=float(tts_elapsed_seconds),
                success=bool(tts_success),
                audio_duration_seconds=float(tts_history_audio_seconds),
                frame_count=int(tts_history_frame_count),
                resources=tts_resources_after,
                context={
                    "preview_teacher_id": int(teacher_id),
                    "preview_job_id": int(job_id or 0),
                    "timeout_seconds": round(float(tts_timeout_seconds), 4),
                    "timeout_reason": dict(tts_timeout_reason or {}),
                },
            )

        cleanup_after_tts_payload = release_stage_resources(reason="after_tts_before_avatar_render")
        cleanup_after_tts = _cleanup_summary(cleanup_after_tts_payload)

        source_audio_hash = sha256_file(source_mp3) if source_mp3.exists() else ""
        contract_audio_hash = sha256_file(audio_wav) if audio_wav.exists() else ""
        logger.info(
            "Avatar preview audio contract teacher_id=%s job_id=%s preview_text_hash=%s source_audio_hash=%s contract_audio_hash=%s tts_audio_duration_seconds=%s contract_duration_seconds=%s target_frame_count=%s audio_path=%s tts_timeout_seconds=%s tts_timeout_reason=%s",
            int(teacher_id),
            int(job_id or 0),
            preview_text_hash,
            source_audio_hash,
            contract_audio_hash,
            audio_info.get("duration_seconds"),
            ((audio_info.get("contract") or {}).get("target_duration_seconds")),
            ((audio_info.get("contract") or {}).get("target_frame_count")),
            audio_info.get("wav_path"),
            round(float(tts_timeout_seconds), 4),
            tts_timeout_reason,
        )
        _set_job(status="running", progress=45)

        source_image_processed_rel = str(profile.avatar_image_processed or "").strip()
        source_image_original_rel = str(profile.avatar_image_original or "").strip()
        source_image_processed_abs = str((storage_root / source_image_processed_rel) if source_image_processed_rel else "")
        source_video_rel = str(profile.avatar_video_processed or profile.avatar_video_original or "").strip()
        source_image_original_abs = str((storage_root / source_image_original_rel) if source_image_original_rel else "")
        source_image_rel = source_image_processed_rel or source_image_original_rel
        source_image_abs = str((storage_root / source_image_rel) if source_image_rel else "")
        source_video_abs = str((storage_root / source_video_rel) if source_video_rel else "")
        reference_type = str(profile.avatar_reference_type or "image").strip().lower()
        if reference_type not in {"image", "video"}:
            reference_type = "image"
        source_key = reference_type
        preview_source_candidates: list[dict[str, str]] = []
        if reference_type == "image":
            seen_paths: set[str] = set()

            def _add_source_candidate(candidate_key: str, candidate_path: str, candidate_reason: str) -> None:
                path_value = str(candidate_path or "").strip()
                if not path_value or not Path(path_value).exists():
                    return
                dedupe_key = str(Path(path_value).resolve())
                if dedupe_key in seen_paths:
                    return
                seen_paths.add(dedupe_key)
                preview_source_candidates.append(
                    {
                        "source_key": str(candidate_key),
                        "path": str(path_value),
                        "reason": str(candidate_reason),
                    }
                )

            _add_source_candidate("image_original", source_image_original_abs, "default_image_original")
            _add_source_candidate("image_processed", source_image_processed_abs, "default_image_processed")
            _add_source_candidate("preview_normalized", source_image_abs, "default_preview_normalized")

            if preview_source_candidates:
                source_key = str(preview_source_candidates[0].get("source_key") or "image")

        target_frame_count = int(((audio_info.get("contract") or {}).get("target_frame_count") or 0) or 0)
        target_duration_seconds = float(
            ((audio_info.get("contract") or {}).get("target_duration_seconds"))
            or (audio_info.get("wav_duration_seconds") or 0.0)
            or 0.0
        )
        if target_duration_seconds <= 0.0:
            target_duration_seconds = float(audio_info.get("duration_seconds") or 0.0)

        logger.info(
            "Avatar preview source binding teacher_id=%s job_id=%s requested_source_key=%s reference_type=%s source_image_processed_path=%s source_image_original_path=%s source_video_path=%s source_candidates=%s preview_text_hash=%s contract_audio_hash=%s output_path=%s",
            int(teacher_id),
            int(job_id or 0),
            source_key,
            reference_type,
            source_image_processed_abs,
            source_image_original_abs,
            source_video_abs,
            preview_source_candidates,
            preview_text_hash,
            contract_audio_hash,
            str(output_mp4),
        )

        requested_engine_raw = str(getattr(profile, "avatar_lipsync_engine", "") or os.environ.get("AVATAR_ENGINE") or "").strip()
        requested_engine = normalize_avatar_engine(requested_engine_raw)
        request = AvatarRenderRequest(
            source_image_path=(source_image_abs or source_image_original_abs),
            source_image_original_path=(source_image_original_abs or source_image_abs),
            source_video_path=source_video_abs,
            avatar_reference_type=reference_type,
            audio_path=str(audio_wav),
            output_path=str(output_mp4),
            motion_preset=str(getattr(profile, "avatar_motion_preset", "") or "natural"),
            quality_preset=str(getattr(profile, "avatar_quality_preset", "") or "high"),
            lipsync_engine=requested_engine,
            cache_text_hash=preview_text_hash,
            enforce_exact_audio_duration=True,
            target_frame_count=int(target_frame_count),
            target_duration_seconds=float(target_duration_seconds),
            preview_teacher_id=int(teacher_id),
            preview_job_id=int(job_id or 0),
            preview_source_meta={
                "source_key": source_key,
                "reference_type": reference_type,
                "requested_engine_raw": requested_engine_raw,
                "normalized_engine": requested_engine,
                "source_candidates": list(preview_source_candidates),
                "current_run_normalized_source_path": str(source_image_abs or ""),
            },
        )
        setattr(request, "_requested_engine_raw", requested_engine_raw)
        setattr(request, "_preview_task_context", preview_task_context)

        _set_job(status="running", progress=70)
        render_result = render_avatar_segment_local(request)
        stage_paths = dict(render_result.get("stage_paths") or {})
        stage_paths["preview_audio_source_path"] = str(source_mp3)
        stage_paths["preview_audio_contract_path"] = str(audio_wav)
        stage_paths["preview_audio_source_hash"] = source_audio_hash
        stage_paths["preview_audio_contract_hash"] = contract_audio_hash
        stage_paths["tts_timeout_seconds"] = round(float(tts_timeout_seconds), 4)
        stage_paths["tts_timeout_reason"] = dict(tts_timeout_reason or {})
        stage_paths["tts_elapsed_seconds"] = round(float(tts_elapsed_seconds), 4)
        stage_paths["tts_resources_before"] = preview_resources_before_tts
        stage_paths["tts_resources_after"] = tts_resources_after
        stage_paths["cleanup_after_tts"] = cleanup_after_tts

        preview_rel_path = _safe_rel_path(storage_root, Path(str(output_mp4)))
        preview_status = str(render_result.get("preview_status") or "ready").strip().lower() or "ready"
        preview_warning = str(render_result.get("preview_warning") or "").strip()
        if preview_warning and preview_status != "warning":
            preview_status = "warning"
        if preview_status not in {"ready", "warning"}:
            preview_status = "warning" if preview_warning else "ready"
        ui_playable_file = preview_rel_path

        preview_file_exists = bool(output_mp4.exists() and output_mp4.is_file() and output_mp4.stat().st_size > 0)
        current_run_playable_returned = False
        if preview_file_exists:
            try:
                current_run_playable_returned = bool(output_mp4.stat().st_mtime >= run_wall_start - 2.0)
            except Exception:
                current_run_playable_returned = False

        if not preview_file_exists:
            raise RuntimeError("preview_output_missing_or_empty")
        if not current_run_playable_returned:
            raise RuntimeError("preview_output_not_current_run")

        preview_usable = bool(render_result.get("preview_usable", True)) and bool(current_run_playable_returned)

        _set_profile_state(
            status=preview_status,
            error=preview_warning,
            preview_rel_path=preview_rel_path,
            image_status="ready",
            preview_source_hash=str(profile.avatar_source_hash or ""),
        )
        _set_job(
            status="done",
            progress=100,
            result_url=ui_playable_file,
            error_message=(f"preview_warning:{preview_warning}" if preview_warning else ""),
        )

        logger.info(
            "Avatar preview player_binding teacher_id=%s job_id=%s player_file=%s preview_rel_path=%s preview_status=%s preview_file_exists=%s preview_usable=%s current_run_playable_returned=%s warning=%s",
            int(teacher_id),
            int(job_id or 0),
            ui_playable_file,
            preview_rel_path,
            preview_status,
            preview_file_exists,
            preview_usable,
            current_run_playable_returned,
            preview_warning or "",
        )

        return {
            "teacher_id": int(teacher_id),
            "job_id": int(job_id) if job_id else None,
            "preview_rel_path": preview_rel_path,
            "ui_returned_playable_file": ui_playable_file,
            "preview_file_exists": preview_file_exists,
            "preview_usable": preview_usable,
            "current_run_playable_returned": current_run_playable_returned,
            "preview_status": preview_status,
            "warning": preview_warning,
            "preview_audio_source_rel_path": _safe_rel_path(storage_root, source_mp3),
            "preview_audio_contract_rel_path": _safe_rel_path(storage_root, audio_wav),
            "motion_validation": dict(render_result.get("motion_validation") or {}),
            "stage_paths": stage_paths,
            "stage_outputs": list(render_result.get("stage_outputs") or []),
        }
    except SoftTimeLimitExceeded as exc:
        diagnostics = _build_soft_time_limit_diagnostics(preview_task_context)
        error_text = _format_soft_time_limit_error(diagnostics)
        output_mp4.unlink(missing_ok=True)
        output_mp4.with_suffix(output_mp4.suffix + ".meta.json").unlink(missing_ok=True)
        _set_profile_state(status="failed", error=error_text, image_status="ready", clear_preview=True)
        _set_job(status="failed", progress=100, result_url="", error_message=error_text)
        logger.error(
            "Avatar preview task soft time limit exceeded teacher_id=%s job_id=%s diagnostics=%s",
            int(teacher_id),
            int(job_id or 0),
            diagnostics,
        )
        raise RuntimeError(error_text) from exc
    except Exception as exc:
        error_text = str(exc or "avatar_preview_failed")
        output_mp4.unlink(missing_ok=True)
        output_mp4.with_suffix(output_mp4.suffix + ".meta.json").unlink(missing_ok=True)
        _set_profile_state(status="failed", error=error_text, image_status="ready", clear_preview=True)
        _set_job(status="failed", progress=100, result_url="", error_message=error_text)
        logger.error(
            "Avatar preview failed summary teacher_id=%s job_id=%s reason=%s",
            int(teacher_id),
            int(job_id or 0),
            error_text,
        )
        logger.exception("Avatar preview failed teacher_id=%s job_id=%s", int(teacher_id), int(job_id or 0))
        raise
