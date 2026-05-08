from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


Decision = Literal["allow", "warn", "block", "needs_admin_review"]
Severity = Literal["low", "medium", "high", "critical"]
Modality = Literal["text", "image", "video_frame", "ocr"]
VisualAssetType = Literal["cover", "slide_image", "video_frame", "ocr_text", "avatar_image"]
ModerationCategory = Literal[
    "profanity",
    "sexual",
    "violence",
    "illegal_activity",
    "self_harm",
    "hate_or_harassment",
    "political_or_targeted_abuse",
    "dangerous_instruction",
    "graphic_content",
    "privacy_or_personal_data",
    "unknown",
]


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindingLocation(StrictSchema):
    project_id: int | None = None
    transcript_page_id: int | None = None
    page_key: str | None = None
    slide_order: int | None = None
    asset_type: VisualAssetType | None = None
    image_path: str | None = None
    frame_path: str | None = None
    timestamp_seconds: float | None = None
    timestamp_label: str | None = None
    field_name: str | None = None
    start_char: int | None = None
    end_char: int | None = None
    ui_anchor: str | None = None


class AgentFindingSchema(StrictSchema):
    category: ModerationCategory
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    decision: Decision
    location: FindingLocation = Field(default_factory=FindingLocation)
    user_message: str = ""
    admin_message: str = ""
    evidence_excerpt: str = ""


class AgentResultSchema(StrictSchema):
    agent_slug: str
    agent_version: str
    modality: Modality
    provider: str
    decision: Decision
    confidence: float = Field(ge=0.0, le=1.0)
    findings: list[AgentFindingSchema] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
