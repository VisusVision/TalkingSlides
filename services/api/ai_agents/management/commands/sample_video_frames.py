from __future__ import annotations

import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import Project


def _ensure_services_on_path() -> None:
    services_root = Path(__file__).resolve().parents[4]
    if str(services_root) not in sys.path:
        sys.path.insert(0, str(services_root))


class Command(BaseCommand):
    help = "Sample local video frames for manual moderation smoke testing."

    def add_arguments(self, parser):
        parser.add_argument("--video-path", required=True, help="Local video file to sample.")
        parser.add_argument("--output-dir", required=True, help="Directory where sampled frames should be written.")
        parser.add_argument("--every-seconds", type=float, default=5.0, help="Sample every N seconds. Defaults to 5.")
        parser.add_argument("--max-frames", type=int, default=10, help="Maximum frames to sample. Defaults to 10.")
        parser.add_argument(
            "--skip-first-frame",
            action="store_true",
            help="Start at --every-seconds instead of sampling timestamp 0 first.",
        )
        parser.add_argument(
            "--moderate",
            action="store_true",
            help="Run sampled frames through the local image rules provider in report-only mode.",
        )
        parser.add_argument("--project-id", type=int, default=None, help="Project id required when --moderate is used.")

    def handle(self, *args, **options):
        _ensure_services_on_path()
        from worker.ai_agents.video_frame_moderation import sample_video_frames

        project = None
        if options.get("moderate"):
            project_id = options.get("project_id")
            if project_id is None:
                raise CommandError("--project-id is required when --moderate is used.")
            project = Project.objects.filter(pk=int(project_id)).first()
            if project is None:
                raise CommandError(f"Project {project_id} not found.")

        result = sample_video_frames(
            video_path=options["video_path"],
            output_dir=options["output_dir"],
            every_seconds=float(options["every_seconds"]),
            max_frames=int(options["max_frames"]),
            include_first_frame=not bool(options.get("skip_first_frame")),
        )
        self._print_sampling_result(result)
        if options.get("moderate") and result.success and project is not None:
            self._moderate_frames(project, result.sampled_frames)

    def _print_sampling_result(self, result) -> None:
        self.stdout.write(f"Success: {result.success}")
        self.stdout.write(f"Error: {result.error_message}")
        self.stdout.write(f"FFmpeg: {result.ffmpeg_path}")
        self.stdout.write(f"Video path: {result.video_path}")
        self.stdout.write(f"Output dir: {result.output_dir}")
        self.stdout.write(f"Frame count: {len(result.sampled_frames)}")
        for frame in result.sampled_frames:
            self.stdout.write(
                "Frame: "
                f"path={frame.frame_path} "
                f"timestamp_seconds={frame.timestamp_seconds:g} "
                f"timestamp_label={frame.timestamp_label}"
            )

    def _moderate_frames(self, project: Project, sampled_frames) -> None:
        from worker.ai_agents.providers.local_image_rules_provider import LocalImageRulesProvider
        from worker.ai_agents.video_frame_moderation import VideoFrameItem, VideoFrameModerationAgent

        frames = [
            VideoFrameItem(
                project_id=project.id,
                frame_path=frame.frame_path,
                timestamp_seconds=frame.timestamp_seconds,
                timestamp_label=frame.timestamp_label,
                ui_anchor=f"manual-video-frame-{index}",
            )
            for index, frame in enumerate(sampled_frames)
        ]
        result = VideoFrameModerationAgent(provider=LocalImageRulesProvider()).scan_video_frames(project, frames=frames)
        self.stdout.write(f"Moderation provider: {result.provider}")
        self.stdout.write(f"Moderation decision: {result.decision}")
        self.stdout.write(f"Moderation finding count: {len(result.findings)}")
        for finding in result.findings:
            self.stdout.write(
                "Moderation finding: "
                f"category={finding.category} severity={finding.severity} decision={finding.decision} "
                f"message={finding.user_message}"
            )
