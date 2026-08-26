from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


SERVICES_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from avatar.digital_twin.demo_pack import (  # noqa: E402
    DemoPackContractError,
    run_demo_pack,
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DemoPackContractError(f"demo_file_missing:{path}") from exc
    except OSError as exc:
        raise DemoPackContractError(f"demo_file_unreadable:{path}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DemoPackContractError(f"demo_json_invalid:{path}") from exc
    if not isinstance(payload, dict):
        raise DemoPackContractError(f"demo_json_object_required:{path}")
    return payload


def _resolve(base_dir: Path, raw: Any, *, label: str) -> Path:
    value = str(raw or "").strip()
    if not value:
        raise DemoPackContractError(f"demo_path_missing:{label}")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def _sha256_text_file(path: Path) -> str:
    if not path.is_file():
        raise DemoPackContractError(f"demo_script_missing:{path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render generic, personal, and prosody avatar variants sequentially.",
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Private workspace whose portfolio/ subdirectory is safe to publish after review.",
    )
    parser.add_argument(
        "--allow-non-prosody-recommendation",
        action="store_true",
        help="Return success even when quality gates do not recommend the prosody variant.",
    )
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    manifest = _read_json(manifest_path)
    base_dir = manifest_path.parent
    inputs = dict(manifest.get("inputs") or {})
    source_image = _resolve(base_dir, inputs.get("source_image"), label="source_image")
    source_video = _resolve(base_dir, inputs.get("source_video"), label="source_video")
    audio_path = _resolve(base_dir, inputs.get("audio_path"), label="audio_path")
    motion_style_path = _resolve(
        base_dir,
        inputs.get("motion_style_path"),
        label="motion_style_path",
    )
    script_hash = str(inputs.get("script_hash") or "").strip().lower()
    if not script_hash and inputs.get("script_path"):
        script_hash = _sha256_text_file(
            _resolve(base_dir, inputs.get("script_path"), label="script_path")
        )
    if not script_hash:
        raise DemoPackContractError("demo_script_hash_missing")
    if len(script_hash) != 64 or any(
        character not in "0123456789abcdef" for character in script_hash
    ):
        raise DemoPackContractError("demo_script_hash_invalid")

    motion_style_package = _read_json(motion_style_path)
    request_payload = dict(manifest.get("request") or {})
    render_options = dict(manifest.get("render") or {})
    quality_preset = str(render_options.get("quality_preset") or "high").strip().lower()
    model_versions = dict(manifest.get("model_versions") or {})
    if not model_versions:
        raise DemoPackContractError("demo_model_versions_missing")

    from avatar.digital_twin.prosody import analyze_audio_prosody
    from avatar.digital_twin.render_quality import evaluate_render_quality
    from avatar.pipeline import AvatarRenderRequest, render_avatar_segment_local

    prosody_profile = analyze_audio_prosody(audio_path)
    if not bool(prosody_profile.get("accepted")):
        reasons = ",".join(str(item) for item in prosody_profile.get("warnings") or [])
        raise DemoPackContractError(f"demo_prosody_audio_not_accepted:{reasons or 'unknown'}")

    def render_variant(kind: str, motion_plan: dict[str, Any], output_path: Path) -> dict[str, Any]:
        performance_window = (
            dict(motion_plan.get("performance_window") or {}) if kind != "generic" else {}
        )
        performance_timeline = (
            dict(motion_plan.get("performance_timeline") or {}) if kind == "prosody" else {}
        )
        request = AvatarRenderRequest(
            source_image_path=str(source_image),
            source_image_original_path=str(source_image),
            source_video_path=str(source_video),
            avatar_reference_type="video",
            audio_path=str(audio_path),
            output_path=str(output_path),
            motion_preset=str(motion_plan.get("motion_preset") or "natural"),
            quality_preset=quality_preset,
            lipsync_engine=str(render_options.get("lipsync_engine") or "musetalk"),
            restoration_enabled=bool(render_options.get("restoration_enabled", True)),
            liveportrait_enabled=True,
            enforce_exact_audio_duration=True,
            performance_window=performance_window,
            performance_timeline=performance_timeline,
        )
        return dict(render_avatar_segment_local(request))

    def quality_evaluator(output_path: Path, render_info: dict[str, Any]) -> dict[str, Any]:
        return evaluate_render_quality(
            source_image=source_image,
            output_video=output_path,
            audio_path=audio_path,
            render_info=render_info,
        ).as_dict()

    result = run_demo_pack(
        suite_id=str(manifest.get("suite_id") or "avatar-demo"),
        output_dir=args.output_dir,
        source_image=source_image,
        source_video=source_video,
        audio_path=audio_path,
        motion_style_package=motion_style_package,
        request_payload=request_payload,
        prosody_profile=prosody_profile,
        model_versions=model_versions,
        script_hash=script_hash,
        quality_preset=quality_preset,
        render_contract={
            "lipsync_engine": str(render_options.get("lipsync_engine") or "musetalk"),
            "restoration_enabled": bool(render_options.get("restoration_enabled", True)),
            "liveportrait_enabled": True,
            "enforce_exact_audio_duration": True,
        },
        render_variant=render_variant,
        evaluate_quality=quality_evaluator,
    )
    recommendation = str(result["report"]["recommendation"])
    print(
        json.dumps(
            {
                "status": "completed",
                "recommendation": recommendation,
                "portfolio_dir": str(Path(result["output_dir"]) / "portfolio"),
                "private_renders_dir": str(Path(result["output_dir"]) / "private-renders"),
            },
            ensure_ascii=False,
        )
    )
    if recommendation != "prosody" and not args.allow_non_prosody_recommendation:
        return 3
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DemoPackContractError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from exc
