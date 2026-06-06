from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path
import shutil
import time
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


SUPPORTED_STYLES = {"none", "box", "bold"}
SUPPORTED_DETECTORS = {"auto"}
ENGINE_VERSION = "highlight-v1"


@dataclass
class DetectionRegion:
    x: int
    y: int
    width: int
    height: int


class BaseDetector:
    name: str = "base"

    def detect(self, image: Image.Image, text: str) -> list[DetectionRegion]:
        raise NotImplementedError


class HeuristicDetector(BaseDetector):
    name: str = "auto"

    def detect(self, image: Image.Image, text: str) -> list[DetectionRegion]:
        width, height = image.size
        normalized = " ".join(str(text or "").split())
        density = max(1.0, min(2.0, len(normalized) / 240.0))
        box_w = int(width * min(0.8, 0.42 + (0.18 * density)))
        box_h = int(height * min(0.6, 0.18 + (0.08 * density)))
        x = max(0, int((width - box_w) / 2))
        y = max(0, int((height - box_h) / 2))
        return [DetectionRegion(x=x, y=y, width=max(1, box_w), height=max(1, box_h))]


class BaseRenderer:
    name: str = "base"

    def render(self, image: Image.Image, regions: list[DetectionRegion]) -> Image.Image:
        raise NotImplementedError


class BoxRenderer(BaseRenderer):
    name: str = "box"

    def render(self, image: Image.Image, regions: list[DetectionRegion]) -> Image.Image:
        rendered = image.convert("RGBA").copy()
        draw = ImageDraw.Draw(rendered, "RGBA")
        stroke = max(2, int(min(image.size) * 0.006))
        for region in regions:
            draw.rectangle(
                (region.x, region.y, region.x + region.width, region.y + region.height),
                outline=(255, 48, 48, 255),
                width=stroke,
            )
        return rendered.convert("RGB")


def _apply_bold_emphasis(region: Image.Image) -> Image.Image:
    base = region.convert("RGBA")
    contrasted = ImageEnhance.Contrast(base.convert("RGB")).enhance(1.6).convert("RGBA")
    sharpened = ImageEnhance.Sharpness(contrasted.convert("RGB")).enhance(2.3).convert("RGBA")
    luminance = sharpened.convert("L")
    ink_strength = luminance.point(lambda px: max(0, min(255, int((180 - int(px)) * 1.8))))
    ink_strength = ink_strength.filter(ImageFilter.MaxFilter(3))
    ink_alpha = ink_strength.point(lambda px: int(int(px) * 0.34))

    ink_layer = Image.new("RGBA", sharpened.size, (0, 0, 0, 0))
    ink_layer.putalpha(ink_alpha)
    emphasized = sharpened.copy()
    for dx, dy in ((1, 0), (0, 1), (-1, 0)):
        shifted = Image.new("RGBA", emphasized.size, (0, 0, 0, 0))
        shifted.paste(ink_layer, (dx, dy))
        emphasized = Image.alpha_composite(emphasized, shifted)
    return emphasized


class BoldRenderer(BaseRenderer):
    name: str = "bold"

    def render(self, image: Image.Image, regions: list[DetectionRegion]) -> Image.Image:
        rendered = image.convert("RGBA").copy()
        original = image.convert("RGBA")
        for region in regions:
            crop = original.crop((region.x, region.y, region.x + region.width, region.y + region.height))
            rendered.paste(_apply_bold_emphasis(crop), (region.x, region.y))
        return rendered.convert("RGB")


def _resolve_detector(name: str) -> BaseDetector:
    normalized = str(name or "auto").strip().lower()
    if normalized not in SUPPORTED_DETECTORS:
        raise ValueError("detector must be auto")
    return HeuristicDetector()


def _resolve_renderer(style: str) -> BaseRenderer | None:
    normalized = str(style or "").strip().lower()
    if normalized not in SUPPORTED_STYLES:
        raise ValueError("style must be one of: none, box, bold")
    if normalized in {"", "none"}:
        return None
    if normalized == "box":
        return BoxRenderer()
    return BoldRenderer()


def _serialize_region(region: DetectionRegion) -> dict[str, Any]:
    return {
        "x": float(region.x),
        "y": float(region.y),
        "width": float(region.width),
        "height": float(region.height),
    }


def apply_highlight(
    *,
    image_path: str,
    text: str,
    style: str,
    detector: str = "auto",
    output_path: str,
    timeout_sec: float = 12.0,
) -> dict[str, Any]:
    started = time.perf_counter()
    normalized_style = str(style or "").strip().lower() or "none"
    detector_obj = _resolve_detector(detector)
    renderer = _resolve_renderer(normalized_style)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    def _work() -> dict[str, Any]:
        with Image.open(image_path).convert("RGB") as image:
            regions = detector_obj.detect(image, text)
            if renderer is None:
                image.save(output, format="PNG")
            return {
                "output_path": str(output),
                "regions": [_serialize_region(region) for region in regions],
                "detector_used": detector_obj.name,
                "renderer_used": "none",
                "engine_version": ENGINE_VERSION,
                "fallback_used": False,
                "error_reason": "",
                "success": True,
            }
            rendered = renderer.render(image, regions)
            rendered.save(output, format="PNG")
            return {
                "output_path": str(output),
                "regions": [_serialize_region(region) for region in regions],
                "detector_used": detector_obj.name,
                "renderer_used": renderer.name,
                "engine_version": ENGINE_VERSION,
                "fallback_used": False,
                "error_reason": "",
                "success": True,
            }

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_work)
            payload = future.result(timeout=max(0.1, float(timeout_sec)))
    except FuturesTimeoutError:
        shutil.copy2(image_path, output)
        payload = {
            "output_path": str(output),
            "regions": [],
            "detector_used": detector_obj.name,
            "renderer_used": normalized_style,
            "engine_version": ENGINE_VERSION,
            "fallback_used": True,
            "error_reason": "timeout",
            "success": False,
        }
    except Exception as exc:
        shutil.copy2(image_path, output)
        payload = {
            "output_path": str(output),
            "regions": [],
            "detector_used": detector_obj.name,
            "renderer_used": normalized_style,
            "engine_version": ENGINE_VERSION,
            "fallback_used": True,
            "error_reason": str(exc),
            "success": False,
        }

    payload["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
    return payload
