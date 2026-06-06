from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from .policy_engine import PolicyEngine
from .providers.base import VisualModerationProvider
from .providers.noop_visual_provider import (
    NOOP_VISUAL_AGENT_SLUG,
    NOOP_VISUAL_AGENT_VERSION,
    NoopVisualProvider,
)
from .schemas import AgentFindingSchema, AgentResultSchema, FindingLocation


@dataclass(frozen=True)
class VideoFrameItem:
    project_id: int
    frame_path: str = ""
    timestamp_seconds: float | None = None
    timestamp_label: str | None = None
    slide_order: int | None = None
    ui_anchor: str | None = None


@dataclass(frozen=True)
class SampledVideoFrame:
    frame_path: str
    timestamp_seconds: float
    timestamp_label: str


@dataclass(frozen=True)
class VideoFrameSamplingResult:
    video_path: str
    output_dir: str
    sampled_frames: list[SampledVideoFrame]
    success: bool
    error_message: str = ""
    ffmpeg_path: str = ""


class VideoFrameModerationAgent:
    def __init__(
        self,
        provider: VisualModerationProvider | None = None,
        policy_engine: PolicyEngine | None = None,
    ) -> None:
        self.provider = provider or NoopVisualProvider()
        self.policy_engine = policy_engine or PolicyEngine()

    def scan_frame(
        self,
        *,
        project_id: int,
        frame_path: str | None = "",
        timestamp_seconds: float | None = None,
        timestamp_label: str | None = None,
        slide_order: int | None = None,
        ui_anchor: str | None = None,
    ) -> AgentResultSchema:
        location = FindingLocation(
            project_id=int(project_id),
            slide_order=slide_order,
            asset_type="video_frame",
            frame_path=str(frame_path or ""),
            timestamp_seconds=timestamp_seconds,
            timestamp_label=timestamp_label or None,
            ui_anchor=ui_anchor or None,
        )
        return self.provider.review_frame(frame_path, location)

    def scan_video_frames(self, project, frames: Iterable[VideoFrameItem | dict] | None = None) -> AgentResultSchema:
        results: list[AgentResultSchema] = []
        for frame in frames or []:
            item = _frame_item(frame, project_id=int(project.id))
            results.append(
                self.scan_frame(
                    project_id=item.project_id,
                    frame_path=item.frame_path,
                    timestamp_seconds=item.timestamp_seconds,
                    timestamp_label=item.timestamp_label,
                    slide_order=item.slide_order,
                    ui_anchor=item.ui_anchor,
                )
            )
        return self._aggregate_results(results)

    def _aggregate_results(self, results: Iterable[AgentResultSchema]) -> AgentResultSchema:
        result_list = list(results)
        findings: list[AgentFindingSchema] = []
        for result in result_list:
            findings.extend(result.findings)
        agent_slug = getattr(self.provider, "agent_slug", NOOP_VISUAL_AGENT_SLUG)
        agent_version = getattr(self.provider, "agent_version", NOOP_VISUAL_AGENT_VERSION)
        provider_name = getattr(self.provider, "provider_name", "visual_provider")
        is_noop = provider_name == "noop_visual"
        return AgentResultSchema(
            agent_slug=agent_slug,
            agent_version=agent_version,
            modality="video_frame",
            provider=provider_name,
            decision=self.policy_engine.combine_results(result_list),
            confidence=max((result.confidence for result in result_list), default=0.0),
            findings=findings,
            metadata={
                "noop": is_noop,
                "scanned_asset_type": "video_frames",
                "scanned_asset_count": len(result_list),
            },
        )


def _frame_item(frame: VideoFrameItem | dict, *, project_id: int) -> VideoFrameItem:
    if isinstance(frame, VideoFrameItem):
        return frame
    return VideoFrameItem(
        project_id=int(frame.get("project_id") or project_id),
        frame_path=str(frame.get("frame_path") or ""),
        timestamp_seconds=frame.get("timestamp_seconds"),
        timestamp_label=frame.get("timestamp_label"),
        slide_order=frame.get("slide_order"),
        ui_anchor=frame.get("ui_anchor"),
    )


def sample_video_frames(
    *,
    video_path: str | Path,
    output_dir: str | Path,
    every_seconds: float = 5.0,
    max_frames: int = 10,
    include_first_frame: bool = True,
    ffmpeg_path: str | None = None,
    timeout_seconds: int = 30,
) -> VideoFrameSamplingResult:
    resolved_video_path = str(video_path or "").strip()
    resolved_output_dir = str(output_dir or "").strip()
    if not resolved_video_path or not Path(resolved_video_path).is_file():
        return VideoFrameSamplingResult(
            video_path=resolved_video_path,
            output_dir=resolved_output_dir,
            sampled_frames=[],
            success=False,
            error_message=f"Video file not found: {resolved_video_path}",
            ffmpeg_path=ffmpeg_path or "",
        )
    if float(every_seconds) <= 0:
        return VideoFrameSamplingResult(
            video_path=resolved_video_path,
            output_dir=resolved_output_dir,
            sampled_frames=[],
            success=False,
            error_message="every_seconds must be greater than 0.",
            ffmpeg_path=ffmpeg_path or "",
        )
    if int(max_frames) <= 0:
        return VideoFrameSamplingResult(
            video_path=resolved_video_path,
            output_dir=resolved_output_dir,
            sampled_frames=[],
            success=False,
            error_message="max_frames must be greater than 0.",
            ffmpeg_path=ffmpeg_path or "",
        )

    ffmpeg = ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        return VideoFrameSamplingResult(
            video_path=resolved_video_path,
            output_dir=resolved_output_dir,
            sampled_frames=[],
            success=False,
            error_message="ffmpeg executable was not found on PATH.",
            ffmpeg_path="",
        )

    output_path = Path(resolved_output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    sampled_frames: list[SampledVideoFrame] = []
    first_error = ""
    for index, timestamp_seconds in enumerate(
        _sampling_timestamps(
            every_seconds=float(every_seconds),
            max_frames=int(max_frames),
            include_first_frame=include_first_frame,
        )
    ):
        frame_path = output_path / _frame_filename(index=index, timestamp_seconds=timestamp_seconds)
        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp_seconds:.3f}",
            "-i",
            resolved_video_path,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(frame_path),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            first_error = first_error or f"{exc.__class__.__name__}: {exc}"
            break

        if completed.returncode != 0:
            first_error = first_error or (completed.stderr.strip() or f"ffmpeg exited with {completed.returncode}")
            if not sampled_frames:
                break
            continue
        if not frame_path.is_file():
            first_error = first_error or "ffmpeg completed but did not create a frame."
            if not sampled_frames:
                break
            continue
        sampled_frames.append(
            SampledVideoFrame(
                frame_path=str(frame_path),
                timestamp_seconds=timestamp_seconds,
                timestamp_label=format_timestamp_label(timestamp_seconds),
            )
        )

    return VideoFrameSamplingResult(
        video_path=resolved_video_path,
        output_dir=resolved_output_dir,
        sampled_frames=sampled_frames,
        success=bool(sampled_frames),
        error_message="" if sampled_frames else first_error or "No frames were sampled.",
        ffmpeg_path=str(ffmpeg),
    )


def format_timestamp_label(timestamp_seconds: float | int | None) -> str:
    total_milliseconds = max(0, int(round(float(timestamp_seconds or 0) * 1000)))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    if milliseconds:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _sampling_timestamps(
    *,
    every_seconds: float,
    max_frames: int,
    include_first_frame: bool,
) -> list[float]:
    timestamps: list[float] = []
    if include_first_frame:
        timestamps.append(0.0)
    next_timestamp = every_seconds
    while len(timestamps) < max_frames:
        timestamps.append(round(next_timestamp, 3))
        next_timestamp += every_seconds
    return timestamps[:max_frames]


def _frame_filename(*, index: int, timestamp_seconds: float) -> str:
    milliseconds = int(round(timestamp_seconds * 1000))
    return f"frame_{index:04d}_{milliseconds:010d}ms.jpg"
