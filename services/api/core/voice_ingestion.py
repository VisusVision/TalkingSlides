"""Canonicalize and validate teacher voice references for XTTS."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any


logger = logging.getLogger(__name__)

VOICE_REFERENCE_SAMPLE_RATE = 24000
VOICE_REFERENCE_CHANNELS = 1
VOICE_REFERENCE_MIN_DURATION_SEC = 10.0
VOICE_REFERENCE_MAX_DURATION_SEC = 60.0
VOICE_REFERENCE_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
VOICE_REFERENCE_PROCESS_TIMEOUT_SEC = 90


class VoiceReferenceIngestionError(ValueError):
    """Safe, structured failure raised for invalid voice uploads."""

    def __init__(self, code: str, message: str, *, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class VoiceReferenceMetadata:
    duration_seconds: float
    codec_name: str
    sample_rate: int
    channels: int
    path: Path


def _tool_path(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise VoiceReferenceIngestionError(
            "voice_ingestion_unavailable",
            f"Voice processing is unavailable because {name} is not installed.",
            http_status=503,
        )
    return path


def _run(command: list[str], *, code: str, failure_message: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=VOICE_REFERENCE_PROCESS_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise VoiceReferenceIngestionError(
            "voice_processing_timeout",
            "Voice sample processing timed out. Try a shorter audio sample.",
        ) from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = str(getattr(exc, "stderr", "") or "").strip()
        logger.info(
            "voice_reference_process_failed",
            extra={"voice_error_code": code, "voice_process_stderr": stderr[:500]},
        )
        raise VoiceReferenceIngestionError(code, failure_message) from exc


def _probe_voice_reference(path: Path) -> VoiceReferenceMetadata:
    ffprobe = _tool_path("ffprobe")
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,sample_rate,channels,duration",
            "-of",
            "json",
            str(path),
        ],
        code="invalid_voice_audio",
        failure_message="The uploaded voice sample is corrupt or is not readable audio.",
    )
    try:
        payload: dict[str, Any] = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise VoiceReferenceIngestionError(
            "invalid_voice_audio",
            "The uploaded voice sample could not be inspected.",
        ) from exc

    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    audio_stream = next(
        (stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "audio"),
        None,
    )
    if not audio_stream:
        raise VoiceReferenceIngestionError(
            "voice_audio_stream_missing",
            "The uploaded file does not contain an audio stream.",
        )

    format_payload = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    raw_duration = audio_stream.get("duration") or format_payload.get("duration") or 0
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise VoiceReferenceIngestionError(
            "voice_duration_invalid",
            "The voice sample has no measurable audio duration.",
        )
    if duration < VOICE_REFERENCE_MIN_DURATION_SEC:
        raise VoiceReferenceIngestionError(
            "voice_sample_too_short",
            f"Voice sample must be at least {int(VOICE_REFERENCE_MIN_DURATION_SEC)} seconds long.",
        )
    if duration > VOICE_REFERENCE_MAX_DURATION_SEC:
        raise VoiceReferenceIngestionError(
            "voice_sample_too_long",
            f"Voice sample must be no longer than {int(VOICE_REFERENCE_MAX_DURATION_SEC)} seconds.",
        )

    try:
        sample_rate = int(audio_stream.get("sample_rate") or 0)
        channels = int(audio_stream.get("channels") or 0)
    except (TypeError, ValueError) as exc:
        raise VoiceReferenceIngestionError(
            "invalid_voice_audio",
            "The processed voice sample has invalid audio metadata.",
        ) from exc

    codec_name = str(audio_stream.get("codec_name") or "")
    if codec_name != "pcm_s16le" or sample_rate != VOICE_REFERENCE_SAMPLE_RATE or channels != VOICE_REFERENCE_CHANNELS:
        raise VoiceReferenceIngestionError(
            "voice_canonicalization_failed",
            "The voice sample could not be converted to the required PCM WAV format.",
        )
    if not path.exists() or not path.is_file() or path.stat().st_size <= 44:
        raise VoiceReferenceIngestionError(
            "voice_output_empty",
            "The processed voice sample is empty.",
        )

    return VoiceReferenceMetadata(
        duration_seconds=duration,
        codec_name=codec_name,
        sample_rate=sample_rate,
        channels=channels,
        path=path,
    )


def ingest_voice_reference(upload, *, storage_root: Path, voice_id: str) -> VoiceReferenceMetadata:
    """Transcode an uploaded sample to the canonical XTTS reference WAV."""

    upload_size = int(getattr(upload, "size", 0) or 0)
    if upload_size <= 0:
        raise VoiceReferenceIngestionError(
            "voice_upload_empty",
            "The uploaded voice sample is empty.",
        )
    if upload_size > VOICE_REFERENCE_MAX_UPLOAD_BYTES:
        raise VoiceReferenceIngestionError(
            "voice_upload_too_large",
            "Voice sample exceeds the 25 MB upload limit.",
        )

    ffmpeg = _tool_path("ffmpeg")
    voices_dir = Path(storage_root) / "voices"
    voices_dir.mkdir(parents=True, exist_ok=True)
    final_path = voices_dir / f"{voice_id}.wav"
    original_suffix = Path(str(getattr(upload, "name", "") or "")).suffix.lower()
    safe_suffix = original_suffix if original_suffix and len(original_suffix) <= 10 else ".audio"

    with tempfile.TemporaryDirectory(prefix=".voice-ingest-", dir=voices_dir) as temp_dir:
        temp_root = Path(temp_dir)
        input_path = temp_root / f"upload{safe_suffix}"
        output_path = temp_root / "canonical.wav"
        with open(input_path, "wb") as handle:
            for chunk in upload.chunks():
                handle.write(chunk)

        if not input_path.exists() or input_path.stat().st_size <= 0:
            raise VoiceReferenceIngestionError(
                "voice_upload_empty",
                "The uploaded voice sample is empty.",
            )

        _run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(input_path),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                str(VOICE_REFERENCE_CHANNELS),
                "-ar",
                str(VOICE_REFERENCE_SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                str(output_path),
            ],
            code="voice_transcode_failed",
            failure_message="The uploaded file is not a supported or readable audio sample.",
        )
        metadata = _probe_voice_reference(output_path)
        os.replace(output_path, final_path)

    logger.info(
        "voice_reference_ingested",
        extra={
            "voice_id": voice_id,
            "voice_duration_seconds": round(metadata.duration_seconds, 3),
            "voice_sample_rate": metadata.sample_rate,
            "voice_channels": metadata.channels,
            "voice_codec": metadata.codec_name,
        },
    )
    return VoiceReferenceMetadata(
        duration_seconds=metadata.duration_seconds,
        codec_name=metadata.codec_name,
        sample_rate=metadata.sample_rate,
        channels=metadata.channels,
        path=final_path,
    )
