"""Preview-only visual design; importing this package performs no application I/O."""
from .planner import DeterministicPlanner, VisualPlanner
from .schemas import DesignError, SlideSourceSnapshot, VisualPlan, VisualPreview
from .svg_renderer import render_preview
from .validation import validate_plan, validate_svg

__all__ = [
    "DesignError", "SlideSourceSnapshot", "VisualPlan", "VisualPreview",
    "DeterministicPlanner", "VisualPlanner", "render_preview", "validate_plan", "validate_svg",
]
