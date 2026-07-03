import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from core.avatar_placement import resolve_avatar_layout  # noqa: E402


def test_avatar_layout_resolver_uses_system_fallback_when_higher_levels_are_absent():
    resolved = resolve_avatar_layout()

    assert resolved == {
        "position": "top-right",
        "size": "medium",
        "visible": True,
        "sources": {
            "position": "system",
            "size": "system",
            "visible": "system",
        },
        "source_level": "system",
    }


def test_avatar_layout_resolver_applies_each_inheritance_level_per_field():
    resolved = resolve_avatar_layout(
        slide_override={"visible": False},
        lesson_override={"size": "large"},
        publisher_default={"position": "bottom-left"},
    )

    assert resolved["position"] == "bottom-left"
    assert resolved["size"] == "large"
    assert resolved["visible"] is False
    assert resolved["sources"] == {
        "position": "publisher",
        "size": "lesson",
        "visible": "slide",
    }
    assert resolved["source_level"] == "slide"


@pytest.mark.parametrize(
    ("slide_override", "expected"),
    [
        ({"position": "center"}, {"position": "top-right", "source": "system"}),
        ({"size": "giant"}, {"size": "medium", "source": "system"}),
        ({"visible": "maybe"}, {"visible": True, "source": "system"}),
        ({"visible": "false"}, {"visible": False, "source": "slide"}),
        ({"visible": False}, {"visible": False, "source": "slide"}),
        ({"visible": None}, {"visible": True, "source": "system"}),
        ({}, {"visible": True, "source": "system"}),
    ],
)
def test_avatar_layout_resolver_handles_malformed_missing_null_and_false(slide_override, expected):
    resolved = resolve_avatar_layout(slide_override=slide_override)
    field = next(key for key in ("position", "size", "visible") if key in expected)

    assert resolved[field] == expected[field]
    assert resolved["sources"][field] == expected["source"]


def test_avatar_layout_resolver_is_deterministic_and_equal_explicit_values_report_slide_source():
    inputs = {
        "slide_override": {"position": "top-right", "size": "medium", "visible": True},
        "publisher_default": {"position": "top-right", "size": "medium", "visible": True},
    }

    first = resolve_avatar_layout(**inputs)
    second = resolve_avatar_layout(**inputs)

    assert first == second
    assert first["sources"] == {
        "position": "slide",
        "size": "slide",
        "visible": "slide",
    }
    assert first["source_level"] == "slide"
