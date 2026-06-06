from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..schemas import AgentFindingSchema, AgentResultSchema, FindingLocation


class TextModerationProvider(ABC):
    provider_name = "base"

    @abstractmethod
    def scan_text(self, text: str, location: FindingLocation) -> list[AgentFindingSchema]:
        raise NotImplementedError


class VisualModerationProvider(ABC):
    provider_name = "base_visual"

    @abstractmethod
    def review_image(self, image_path: str | None, location: FindingLocation) -> AgentResultSchema:
        raise NotImplementedError

    @abstractmethod
    def review_frame(self, frame_path: str | None, location: FindingLocation) -> AgentResultSchema:
        raise NotImplementedError


class OCRProvider(ABC):
    provider_name = "base_ocr"

    @abstractmethod
    def extract(self, image_path: str | None, location: FindingLocation) -> Any:
        raise NotImplementedError
