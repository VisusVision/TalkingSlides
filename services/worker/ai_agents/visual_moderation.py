from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .policy_engine import PolicyEngine
from .providers.base import VisualModerationProvider
from .providers.noop_visual_provider import (
    NOOP_VISUAL_AGENT_SLUG,
    NOOP_VISUAL_AGENT_VERSION,
    NoopVisualProvider,
)
from .schemas import AgentFindingSchema, AgentResultSchema, FindingLocation


@dataclass(frozen=True)
class SlideImageAsset:
    image_path: str = ""
    slide_order: int | None = None
    transcript_page_id: int | None = None
    page_key: str | None = None
    ui_anchor: str | None = None
    asset_type: str = "slide_image"


class VisualModerationAgent:
    def __init__(
        self,
        provider: VisualModerationProvider | None = None,
        policy_engine: PolicyEngine | None = None,
    ) -> None:
        self.provider = provider or NoopVisualProvider()
        self.policy_engine = policy_engine or PolicyEngine()

    def scan_project_visual_assets(self, project) -> AgentResultSchema:
        results = [self.scan_cover_image(project), self.scan_slide_images(project)]
        return self._aggregate_results(results, scanned_asset_type="project_visual_assets")

    def scan_cover_image(self, project, image_path: str | None = None) -> AgentResultSchema:
        project_id = int(project.id)
        resolved_image_path = _first_path(
            image_path,
            getattr(project, "cover_image_processed", ""),
            getattr(project, "cover_image_original", ""),
        )
        location = FindingLocation(
            project_id=project_id,
            asset_type="cover",
            image_path=resolved_image_path,
            ui_anchor=f"project-{project_id}-cover-image",
        )
        return self.provider.review_image(resolved_image_path, location)

    def scan_slide_images(self, project, slide_assets: Iterable[SlideImageAsset | dict] | None = None) -> AgentResultSchema:
        results: list[AgentResultSchema] = []
        if slide_assets is not None:
            for asset in slide_assets:
                slide_asset = _slide_asset(asset)
                results.append(
                    self.scan_slide_image(
                        project_id=int(project.id),
                        image_path=slide_asset.image_path,
                        slide_order=slide_asset.slide_order,
                        transcript_page_id=slide_asset.transcript_page_id,
                        page_key=slide_asset.page_key,
                        ui_anchor=slide_asset.ui_anchor,
                        asset_type=slide_asset.asset_type,
                    )
                )
            return self._aggregate_results(results, scanned_asset_type="slide_images")

        slides = getattr(project, "slides", None)
        if slides is None:
            return self._aggregate_results(results, scanned_asset_type="slide_images")
        try:
            slides = slides.all()
        except Exception:
            pass
        if hasattr(slides, "order_by"):
            slides = slides.order_by("order", "id")

        for slide in slides:
            results.append(
                self.scan_slide_image(
                    project_id=int(project.id),
                    image_path=_first_path(getattr(slide, "image_file", "")),
                    slide_order=_safe_int(getattr(slide, "order", None)),
                    ui_anchor=f"slide-{getattr(slide, 'id', '')}-image",
                )
            )
        return self._aggregate_results(results, scanned_asset_type="slide_images")

    def scan_slide_image(
        self,
        *,
        project_id: int,
        image_path: str | None = "",
        slide_order: int | None = None,
        transcript_page_id: int | None = None,
        page_key: str | None = None,
        ui_anchor: str | None = None,
        asset_type: str = "slide_image",
    ) -> AgentResultSchema:
        location = FindingLocation(
            project_id=int(project_id),
            transcript_page_id=transcript_page_id,
            page_key=page_key or None,
            slide_order=slide_order,
            asset_type=_visual_asset_type(asset_type),
            image_path=str(image_path or ""),
            ui_anchor=ui_anchor or None,
        )
        return self.provider.review_image(image_path, location)

    def _aggregate_results(
        self,
        results: Iterable[AgentResultSchema],
        *,
        scanned_asset_type: str,
    ) -> AgentResultSchema:
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
            modality="image",
            provider=provider_name,
            decision=self.policy_engine.combine_results(result_list),
            confidence=max((result.confidence for result in result_list), default=0.0),
            findings=findings,
            metadata={
                "noop": is_noop,
                "scanned_asset_type": scanned_asset_type,
                "scanned_asset_count": len(result_list),
            },
        )


def _first_path(*values) -> str:
    for value in values:
        path = _path_from_value(value)
        if path:
            return path
    return ""


def _path_from_value(value) -> str:
    if not value:
        return ""
    for attr_name in ("path", "name", "url"):
        try:
            attr_value = getattr(value, attr_name, "")
        except Exception:
            continue
        if attr_value:
            return str(attr_value)
    return str(value)


def _safe_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _slide_asset(asset: SlideImageAsset | dict) -> SlideImageAsset:
    if isinstance(asset, SlideImageAsset):
        return asset
    return SlideImageAsset(
        image_path=_first_path(asset.get("image_path"), asset.get("path")),
        slide_order=_safe_int(asset.get("slide_order")),
        transcript_page_id=_safe_int(asset.get("transcript_page_id")),
        page_key=asset.get("page_key"),
        ui_anchor=asset.get("ui_anchor"),
        asset_type=_visual_asset_type(asset.get("asset_type") or "slide_image"),
    )


def _visual_asset_type(value: str | None) -> str:
    raw = str(value or "slide_image").strip().lower()
    if raw in {"cover", "custom_background", "slide_image", "draft_visual_asset", "video_frame"}:
        return raw
    return "slide_image"
