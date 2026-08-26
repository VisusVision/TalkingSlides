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

from avatar.digital_twin.evaluation import (  # noqa: E402
    EvaluationContractError,
    evaluate_avatar_variants,
    render_evaluation_markdown,
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvaluationContractError(f"evaluation_file_missing:{path}") from exc
    except OSError as exc:
        raise EvaluationContractError(f"evaluation_file_unreadable:{path}") from exc
    except json.JSONDecodeError as exc:
        raise EvaluationContractError(f"evaluation_json_invalid:{path}:{exc.lineno}") from exc
    if not isinstance(payload, dict):
        raise EvaluationContractError(f"evaluation_json_object_required:{path}")
    return payload


def _resolve_path(base_dir: Path, raw: Any) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text)
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def _sha256(path: Path | None) -> str:
    if path is None or not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _merge_evidence(variant: dict[str, Any], *, base_dir: Path, context: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(variant)
    evidence_path = _resolve_path(base_dir, variant.get("evidence_path"))
    if evidence_path is not None:
        resolved = {**_read_json(evidence_path), **resolved}
    render_info_path = _resolve_path(base_dir, variant.get("render_info_path"))
    if render_info_path is not None:
        render_info = _read_json(render_info_path)
        resolved.setdefault("motion_validation", render_info.get("motion_validation") or {})
        resolved.setdefault("quality_report", render_info.get("quality_report") or {})
    motion_plan_path = _resolve_path(base_dir, variant.get("motion_plan_path"))
    if motion_plan_path is not None:
        resolved["motion_plan"] = _read_json(motion_plan_path)

    video_path = _resolve_path(base_dir, variant.get("video_path"))
    audio_path = _resolve_path(base_dir, variant.get("audio_path"))
    source_image_path = _resolve_path(base_dir, variant.get("source_image_path"))
    if video_path is not None or audio_path is not None or source_image_path is not None:
        if not all(path is not None and path.exists() for path in (video_path, audio_path, source_image_path)):
            raise EvaluationContractError(f"evaluation_media_missing:{variant.get('kind') or 'variant'}")
        from avatar.digital_twin.render_quality import evaluate_render_quality
        from avatar.pipeline import accept_avatar_render, validate_avatar_render_with_audio

        validation = validate_avatar_render_with_audio(
            str(video_path),
            str(audio_path),
            validation_context=dict(variant.get("validation_context") or {}),
        )
        render_info = {
            "motion_validation": validation,
            "strict_validation_passed": bool(accept_avatar_render(validation)),
        }
        quality_report = evaluate_render_quality(
            source_image=str(source_image_path),
            output_video=str(video_path),
            audio_path=str(audio_path),
            render_info=render_info,
            environ={},
        ).as_dict()
        resolved["motion_validation"] = validation
        resolved["quality_report"] = quality_report
        resolved["artifacts"] = {
            **dict(resolved.get("artifacts") or {}),
            "video_path": str(video_path),
            "audio_path": str(audio_path),
            "source_image_path": str(source_image_path),
        }

    if not str(resolved.get("input_fingerprint") or "").strip():
        audio_hash = _sha256(audio_path)
        source_hash = _sha256(source_image_path)
        script_hash = str(context.get("script_hash") or "").strip()
        if audio_hash and source_hash:
            resolved["input_fingerprint"] = hashlib.sha256(
                f"{audio_hash}|{source_hash}|{script_hash}".encode("utf-8")
            ).hexdigest()
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare generic, personal, and prosody avatar render evidence.",
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument(
        "--require-recommendation",
        choices=["generic", "personal", "prosody"],
    )
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    manifest = _read_json(manifest_path)
    context = dict(manifest.get("context") or {})
    raw_variants = manifest.get("variants")
    if not isinstance(raw_variants, list):
        raise EvaluationContractError("evaluation_variants_missing")
    manifest["variants"] = [
        _merge_evidence(dict(variant), base_dir=manifest_path.parent, context=context)
        for variant in raw_variants
        if isinstance(variant, dict)
    ]
    report = evaluate_avatar_variants(manifest)
    output_dir = (args.output_dir or manifest_path.parent / "evaluation-output").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "avatar-evaluation.json"
    markdown_path = output_dir / "avatar-evaluation.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_evaluation_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "recommendation": report["recommendation"],
                "json_report": str(json_path),
                "markdown_report": str(markdown_path),
            },
            ensure_ascii=False,
        )
    )
    regressions = [
        regression
        for comparison in dict(report.get("comparisons") or {}).values()
        for regression in dict(comparison).get("regressions") or []
    ]
    if args.fail_on_regression and regressions:
        return 3
    if args.require_recommendation and report["recommendation"] != args.require_recommendation:
        return 4
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EvaluationContractError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from exc
