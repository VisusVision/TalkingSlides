from __future__ import annotations

from ..schemas import AgentResultSchema, FindingLocation, Modality
from .base import VisualModerationProvider


NOOP_VISUAL_AGENT_SLUG = "visual_moderation_noop"
NOOP_VISUAL_AGENT_VERSION = "noop-visual:v1"


class NoopVisualProvider(VisualModerationProvider):
    provider_name = "noop_visual"
    agent_slug = NOOP_VISUAL_AGENT_SLUG
    agent_version = NOOP_VISUAL_AGENT_VERSION

    def review_image(self, image_path: str | None, location: FindingLocation) -> AgentResultSchema:
        return self._allow_result(
            modality="image",
            asset_path=image_path,
            location=location,
        )

    def review_frame(self, frame_path: str | None, location: FindingLocation) -> AgentResultSchema:
        return self._allow_result(
            modality="video_frame",
            asset_path=frame_path,
            location=location,
        )

    def _allow_result(
        self,
        *,
        modality: Modality,
        asset_path: str | None,
        location: FindingLocation,
    ) -> AgentResultSchema:
        return AgentResultSchema(
            agent_slug=NOOP_VISUAL_AGENT_SLUG,
            agent_version=NOOP_VISUAL_AGENT_VERSION,
            modality=modality,
            provider=self.provider_name,
            decision="allow",
            confidence=0.0,
            findings=[],
            metadata={
                "noop": True,
                "asset_missing": not bool(asset_path),
                "location": location.model_dump(exclude_none=True),
            },
        )
