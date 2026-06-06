"""
services/scripts/ffmpeg_helpers.py
====================================
Thin wrappers around ffmpeg / ffprobe for the AI_ACADEMY video pipeline.

Requirements:
  - ffmpeg and ffprobe must be on PATH (installed in the worker Docker image).
  - No Python audio libs required — everything is shelled out to ffmpeg.

All functions raise RuntimeError on subprocess failure with the captured
stderr included so errors are easy to diagnose in Celery logs.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    """Execute *cmd*; raise RuntimeError with stderr on non-zero exit."""
    logger.debug("ffmpeg cmd: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {result.returncode}):\n"
            f"  cmd : {' '.join(cmd)}\n"
            f"  stderr: {result.stderr.strip()}"
        )
    return result


# ---------------------------------------------------------------------------
# Audio duration
# ---------------------------------------------------------------------------

def get_audio_duration(path: str) -> float:
    """
    Return the duration of an audio (or video) file in seconds using ffprobe.

    Example::

        dur = get_audio_duration("/tmp/slide_001.mp3")
        # 4.83
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(path),
    ]
    result = _run(cmd)
    data = json.loads(result.stdout)
    duration = float(data["format"]["duration"])
    logger.debug("Duration of %s: %.3f s", path, duration)
    return duration


def trim_trailing_silence(
    path: str,
    *,
    silence_threshold_db: int = -42,
    min_silence_duration: float = 0.35,
) -> str:
    """
    Remove excess trailing silence from an audio file in place.

    This keeps slide timing aligned with audible speech when TTS providers
    emit long silent tails at the end of generated clips.
    """
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(f"trim_trailing_silence: input file not found: {path}")

    suffix = source_path.suffix or ".tmp"
    with tempfile.NamedTemporaryFile(
        suffix=suffix,
        delete=False,
        dir=str(source_path.parent),
    ) as tmp_file:
        temp_path = Path(tmp_file.name)

    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(source_path),
            "-af",
            (
                "areverse,"
                f"silenceremove=start_periods=1:start_duration={max(min_silence_duration, 0.05):.2f}:"
                f"start_threshold={silence_threshold_db}dB,"
                "areverse"
            ),
        ]
        if source_path.suffix.lower() == ".mp3":
            cmd.extend(["-codec:a", "libmp3lame", "-q:a", "4"])
        cmd.append(str(temp_path))
        _run(cmd)

        if temp_path.stat().st_size == 0:
            raise RuntimeError("trim_trailing_silence produced an empty file")

        original_duration = get_audio_duration(str(source_path))
        trimmed_duration = get_audio_duration(str(temp_path))
        if trimmed_duration <= 0:
            raise RuntimeError("trim_trailing_silence produced invalid audio duration")

        temp_path.replace(source_path)
        logger.info(
            "Trimmed trailing silence: %s %.3fs -> %.3fs",
            source_path,
            original_duration,
            trimmed_duration,
        )
        return str(source_path)
    finally:
        with suppress(FileNotFoundError):
            temp_path.unlink()


# ---------------------------------------------------------------------------
# Per-slide video
# ---------------------------------------------------------------------------

def create_slide_video(
    image_path: str,
    audio_path: str,
    out_video_path: str,
    resolution: tuple[int, int] = (1920, 1080),
    duration_sec: float | None = None,
) -> str:
    """
    Combine a static PNG image and an audio file into an MP4 video clip.

    The clip length is determined by the audio duration by default. When
    ``duration_sec`` is provided the clip is stretched to that exact length,
    padding the audio with silence when needed.
    Output is H.264 + AAC in an MP4 container, suitable for concatenation.

    Args:
        image_path:     Path to the slide PNG.
        audio_path:     Path to the narration audio (mp3/wav/aac).
        out_video_path: Destination MP4 path.
        resolution:     Output resolution, default 1920×1080.
        duration_sec:   Optional exact clip duration in seconds.

    Returns:
        *out_video_path* on success.

    Example::

        create_slide_video("slides/01.png", "audio/01.mp3", "parts/01.mp4")
    """
    Path(out_video_path).parent.mkdir(parents=True, exist_ok=True)
    w, h = resolution
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-i", str(audio_path),
    ]

    if duration_sec is not None:
        target_duration = max(float(duration_sec), 0.1)
        audio_duration = get_audio_duration(audio_path)
        pad_duration = max(target_duration - audio_duration, 0.0)
        if pad_duration > 0.01:
            cmd.extend(["-af", f"apad=pad_dur={pad_duration:.3f}"])
        cmd.extend(["-t", f"{target_duration:.3f}"])

    cmd.extend([
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
    ])

    if duration_sec is None:
        cmd.append("-shortest")

    cmd.append(
        str(out_video_path),
    )
    _run(cmd)
    logger.info("Slide video → %s", out_video_path)
    return out_video_path


def compose_slide_with_avatar(
    slide_image_path: str,
    avatar_video_path: str,
    audio_path: str,
    out_video_path: str,
    *,
    duration_sec: float,
    resolution: tuple[int, int] = (1920, 1080),
) -> str:
    """
    Compose a slide background and an animated talking-avatar inset into one MP4.

    The output keeps narration as the primary audio stream and overlays avatar
    video in the bottom-right with soft rounded presentation (simulated via
    drop-shadow and alpha blend) while preserving deterministic timing.
    """
    Path(out_video_path).parent.mkdir(parents=True, exist_ok=True)
    w, h = resolution

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(slide_image_path),
        "-i", str(avatar_video_path),
        "-i", str(audio_path),
        "-t", f"{max(duration_sec, 0.1):.3f}",
        "-filter_complex",
        (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2[bg];"
            "[1:v]scale=540:-2[avatar];"
            "[avatar]format=rgba,colorchannelmixer=aa=0.98[avatar_rgba];"
            "[bg][avatar_rgba]overlay=W-w-48:H-h-48:format=auto[v]"
        ),
        "-map", "[v]",
        "-map", "2:a:0",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        str(out_video_path),
    ]
    _run(cmd)
    logger.info("Slide+avatar composed → %s", out_video_path)
    return out_video_path


# ---------------------------------------------------------------------------
# Concatenation
# ---------------------------------------------------------------------------

def concat_videos(part_paths: List[str], out_path: str) -> str:
    """
    Concatenate multiple MP4 clips in order using the ffmpeg concat demuxer.

    Clips are expected to have the same codec/resolution (as produced by
    :func:`create_slide_video`).  A temporary concat file list is written
    to the system temp directory and cleaned up automatically.

    Args:
        part_paths: Ordered list of MP4 clip paths.
        out_path:   Destination for the final concatenated MP4.

    Returns:
        *out_path* on success.

    Example::

        concat_videos(["part_001.mp4", "part_002.mp4"], "final/lesson.mp4")
    """
    if not part_paths:
        raise ValueError("concat_videos: part_paths must not be empty")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve absolute paths and validate they exist
    abs_paths = []
    missing = []
    for p in part_paths:
        p_abs = Path(p).resolve()
        if not p_abs.is_file():
            missing.append(str(p_abs))
        abs_paths.append(p_abs)

    if missing:
        raise FileNotFoundError(
            "concat_videos: the following input files are missing:\n  - "
            + "\n  - ".join(missing)
        )

    # Create the concat file in a system temp location (absolute paths are used,
    # so ffmpeg will not try to interpret them relative to the concat file).
    concat_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as tmp:
            for p_abs in abs_paths:
                # Escape single quotes per ffmpeg concat format
                escaped = str(p_abs).replace("'", "'\\''")
                tmp.write(f"file '{escaped}'\n")
            concat_file = tmp.name

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file,
            "-c",
            "copy",
            str(out_path),
        ]
        _run(cmd)
        logger.info("Concatenated %d clips → %s", len(part_paths), out_path)
    finally:
        if concat_file and os.path.exists(concat_file):
            try:
                os.unlink(concat_file)
            except Exception:
                logger.warning("Failed to remove temporary concat file: %s", concat_file)

    return out_path


# ---------------------------------------------------------------------------
# SRT subtitle generation
# ---------------------------------------------------------------------------

def _format_srt_time(seconds: float) -> str:
    """Convert a float seconds value to SRT timestamp format HH:MM:SS,mmm."""
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms  = divmod(rem, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_vtt_time(seconds: float) -> str:
    """Convert a float seconds value to WebVTT timestamp format HH:MM:SS.mmm."""
    ms = int(round(max(float(seconds), 0.0) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def generate_srt(
    slide_texts: List[str],
    timings: List[float],
    out_srt_path: str,
) -> str:
    """
    Generate an SRT subtitle file from slide narration texts and per-slide
    durations.

    Args:
        slide_texts:  Narration text for each slide (same length as *timings*).
        timings:      Duration in seconds for each slide clip.
        out_srt_path: Destination .srt file path.

    Returns:
        *out_srt_path* on success.

    Example::

        generate_srt(["Hello", "World"], [5.0, 4.3], "final/lesson.srt")
    """
    if len(slide_texts) != len(timings):
        raise ValueError(
            f"slide_texts ({len(slide_texts)}) and timings ({len(timings)}) must have equal length"
        )

    Path(out_srt_path).parent.mkdir(parents=True, exist_ok=True)

    cursor = 0.0
    lines: List[str] = []
    for i, (text, duration) in enumerate(zip(slide_texts, timings), start=1):
        start = _format_srt_time(cursor)
        end   = _format_srt_time(cursor + duration)
        clean = text.strip().replace("\n\n", "\n") or "(no narration)"
        lines.append(f"{i}\n{start} --> {end}\n{clean}\n")
        cursor += duration

    with open(out_srt_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    logger.info("SRT written → %s (%d entries)", out_srt_path, len(lines))
    return out_srt_path


def generate_srt_from_cues(cues: List[dict], out_srt_path: str) -> str:
    """
    Generate SRT from explicit cue dictionaries.

    Cue schema:
      {"start": float_seconds, "end": float_seconds, "text": str}
    """
    Path(out_srt_path).parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = []
    for idx, cue in enumerate(cues, start=1):
        text = str(cue.get("text") or "").strip()
        if not text:
            continue
        start = _format_srt_time(float(cue.get("start") or 0.0))
        end = _format_srt_time(float(cue.get("end") or 0.0))
        lines.append(f"{idx}\n{start} --> {end}\n{text}\n")

    with open(out_srt_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    logger.info("SRT written from cues → %s (%d entries)", out_srt_path, len(lines))
    return out_srt_path


def generate_vtt_from_cues(cues: List[dict], out_vtt_path: str) -> str:
    """
    Generate WebVTT from explicit cue dictionaries.

    Cue schema:
      {"start": float_seconds, "end": float_seconds, "text": str}
    """
    Path(out_vtt_path).parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = ["WEBVTT", ""]
    written = 0
    for cue in cues:
        text = str(cue.get("text") or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
        if not text:
            continue
        try:
            start_seconds = float(cue.get("start") or 0.0)
            end_seconds = float(cue.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        if end_seconds <= start_seconds:
            continue
        written += 1
        start = _format_vtt_time(start_seconds)
        end = _format_vtt_time(end_seconds)
        lines.append(f"{written}\n{start} --> {end}\n{text}\n")

    with open(out_vtt_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))

    logger.info("WebVTT written from cues -> %s (%d entries)", out_vtt_path, written)
    return out_vtt_path


def package_hls_stream(
    input_video_path: str,
    output_dir: str,
    *,
    playlist_name: str = "index.m3u8",
    segment_pattern: str = "seg_%05d.ts",
    segment_time: int = 6,
    encrypt: bool = False,
    key_hex: str | None = None,
    key_uri: str | None = None,
    key_filename: str = "enc.key",
) -> dict:
    """
    Package an MP4 into HLS VOD assets and optionally encrypt segments.

    Returns metadata with playlist path, segment glob pattern, and encryption flag.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    playlist_path = out_dir / playlist_name
    segment_path = out_dir / segment_pattern

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_video_path),
        "-c:v", "copy",
        "-c:a", "copy",
        "-f", "hls",
        "-hls_time", str(max(2, int(segment_time))),
        "-hls_playlist_type", "vod",
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", str(segment_path),
    ]

    key_info_path = None
    key_path = None
    if encrypt:
        key_path = out_dir / key_filename
        if key_hex:
            key_bytes = bytes.fromhex(key_hex)
            if len(key_bytes) != 16:
                raise ValueError("HLS encryption key must be exactly 16 bytes (32 hex chars)")
        else:
            key_bytes = secrets.token_bytes(16)
            key_hex = key_bytes.hex()
        key_path.write_bytes(key_bytes)

        exposed_key_uri = key_uri or key_filename
        key_info_path = out_dir / "hls_key_info.txt"
        key_info_path.write_text(
            f"{exposed_key_uri}\n{key_path}\n",
            encoding="utf-8",
        )
        cmd.extend(["-hls_key_info_file", str(key_info_path)])

    cmd.append(str(playlist_path))
    _run(cmd)

    return {
        "playlist": str(playlist_path),
        "segment_pattern": segment_pattern,
        "encrypted": bool(encrypt),
        "key_file": str(key_path) if key_path else None,
        "key_uri": key_uri if encrypt else None,
        "key_hex": key_hex if encrypt else None,
    }
