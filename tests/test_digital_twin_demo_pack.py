import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from avatar.digital_twin.demo_pack import (
    DemoPackContractError,
    build_demo_input_fingerprint,
    build_demo_variant_render_inputs,
    build_demo_variant_plans,
    compose_side_by_side_video,
    run_demo_pack,
)


def _motion_package(*, usable: bool = True) -> dict:
    def intervals(score: float) -> list[dict]:
        return [
            {
                "start_seconds": float(index * 8),
                "end_seconds": float(index * 8 + 8),
                "duration_seconds": 8.0,
                "score": score + index * 0.01,
                "motion_intensity": score,
                "expression_intensity": score,
                "head_activity": score / 2.0,
                "face_coverage": 1.0,
                "landmark_coverage": 0.98,
            }
            for index in range(3)
        ]

    return {
        "version": "motion-style-v2",
        "accepted": True,
        "usable_for_motion_planning": usable,
        "duration_seconds": 30.0,
        "selected_intervals": {
            "calm": intervals(0.2),
            "natural": intervals(0.5),
            "expressive": intervals(0.8),
        },
    }


def _prosody_profile() -> dict:
    return {
        "version": "prosody-v1",
        "status": "ready",
        "accepted": True,
        "duration_seconds": 6.0,
        "warnings": [],
        "segments": [
            {"duration_seconds": 2.0, "style": "calm", "pause": True},
            {"duration_seconds": 2.0, "style": "natural", "energy": 0.5},
            {"duration_seconds": 2.0, "style": "expressive", "energy": 0.9, "emphasis": True},
        ],
    }


def _quality_report() -> dict:
    signal = {"score": 0.9, "passed": True, "assurance": "strong", "provider": "test"}
    return {
        "decision": "passed",
        "publish_allowed": True,
        "identity": dict(signal),
        "lip_sync": dict(signal),
        "temporal": {**signal, "assurance": "verified"},
        "technical": {
            "strict_validation_passed": True,
            "audio_match": True,
            "duration_mismatch": False,
            "artifact_detected": False,
            "landmark_stable": True,
        },
    }


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    source_image = tmp_path / "portrait.png"
    source_video = tmp_path / "performance.mp4"
    audio = tmp_path / "speech.wav"
    source_image.write_bytes(b"image-fixture")
    source_video.write_bytes(b"video-fixture")
    audio.write_bytes(b"audio-fixture")
    return source_image, source_video, audio


class _FakeSampler:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def as_dict(self) -> dict:
        return {
            "available": True,
            "baseline_mb": 500.0,
            "peak_mb": 5_600.0,
            "peak_delta_mb": 5_100.0,
            "scope": "test",
        }


def test_variant_plans_change_only_the_capability_under_test():
    plans = build_demo_variant_plans(
        motion_style_package=_motion_package(),
        request_payload={"emotion": "neutral", "motion_intensity": 0.5},
        prosody_profile=_prosody_profile(),
        seed_material="fixed-input",
    )

    assert [item["kind"] for item in plans] == ["generic", "personal", "prosody"]
    generic, personal, prosody = [item["motion_plan"] for item in plans]
    assert generic["personal_window_selected"] is False
    assert personal["personal_window_selected"] is True
    assert personal["prosody_timeline_selected"] is False
    assert prosody["personal_window_selected"] is True
    assert prosody["prosody_timeline_selected"] is True
    assert personal["selected_interval"] == prosody["selected_interval"]


def test_variant_plans_fail_before_render_when_personal_evidence_is_unavailable():
    with pytest.raises(DemoPackContractError, match="demo_personal_window_unavailable"):
        build_demo_variant_plans(
            motion_style_package=_motion_package(usable=False),
            request_payload={},
            prosody_profile=_prosody_profile(),
            seed_material="fixed-input",
        )


def test_generic_render_input_withholds_the_personal_performance_video(tmp_path):
    source_image, source_video, _audio = _write_inputs(tmp_path)

    generic = build_demo_variant_render_inputs(
        kind="generic",
        source_image=source_image,
        source_video=source_video,
    )
    personal = build_demo_variant_render_inputs(
        kind="personal",
        source_image=source_image,
        source_video=source_video,
    )
    prosody = build_demo_variant_render_inputs(
        kind="prosody",
        source_image=source_image,
        source_video=source_video,
    )

    assert generic == {
        "source_image_path": str(source_image),
        "source_video_path": "",
        "avatar_reference_type": "image",
        "motion_source_policy": "generic_non_personal",
    }
    assert personal["source_video_path"] == str(source_video)
    assert personal["avatar_reference_type"] == "video"
    assert personal["motion_source_policy"] == "personal_performance"
    assert prosody == personal


def test_fingerprint_changes_when_an_invariant_input_changes(tmp_path):
    source_image, source_video, audio = _write_inputs(tmp_path)
    first = build_demo_input_fingerprint(
        source_image=source_image,
        source_video=source_video,
        audio_path=audio,
        model_versions={"liveportrait": "v1"},
    )
    second = build_demo_input_fingerprint(
        source_image=source_image,
        source_video=source_video,
        audio_path=audio,
        model_versions={"liveportrait": "v2"},
    )
    third = build_demo_input_fingerprint(
        source_image=source_image,
        source_video=source_video,
        audio_path=audio,
        model_versions={"liveportrait": "v1"},
        request_payload={"emotion": "happy"},
    )

    assert len(first) == 64
    assert first != second
    assert first != third


def test_cli_returns_a_structured_contract_error_before_gpu_work(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"suite_id": "broken", "inputs": {}}), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "services" / "scripts" / "build_avatar_demo_pack.py"

    process = subprocess.run(
        [
            sys.executable,
            str(script),
            str(manifest_path),
            "--output-dir",
            str(tmp_path / "output"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert process.returncode == 2
    assert json.loads(process.stderr) == {"error": "demo_path_missing:source_image"}


def test_demo_pack_rejects_a_nonempty_output_to_avoid_cached_benchmark_results(tmp_path):
    source_image, source_video, audio = _write_inputs(tmp_path)
    output_dir = tmp_path / "existing"
    output_dir.mkdir()
    (output_dir / "old-result.json").write_text("{}", encoding="utf-8")

    with pytest.raises(DemoPackContractError, match="demo_output_not_empty"):
        run_demo_pack(
            suite_id="portfolio-001",
            output_dir=output_dir,
            source_image=source_image,
            source_video=source_video,
            audio_path=audio,
            motion_style_package=_motion_package(),
            request_payload={},
            prosody_profile=_prosody_profile(),
            model_versions={"liveportrait": "test"},
            script_hash="a" * 64,
            quality_preset="high",
            render_contract={"lipsync_engine": "musetalk"},
            render_variant=lambda *_args: {},
            evaluate_quality=lambda *_args: {},
        )


def test_demo_pack_identifies_the_variant_that_failed_to_render(tmp_path):
    source_image, source_video, audio = _write_inputs(tmp_path)

    with pytest.raises(
        DemoPackContractError,
        match="demo_render_failed:generic:RuntimeError:renderer crashed",
    ):
        run_demo_pack(
            suite_id="portfolio-001",
            output_dir=tmp_path / "failed-demo",
            source_image=source_image,
            source_video=source_video,
            audio_path=audio,
            motion_style_package=_motion_package(),
            request_payload={},
            prosody_profile=_prosody_profile(),
            model_versions={"liveportrait": "test"},
            script_hash="a" * 64,
            quality_preset="high",
            render_contract={"lipsync_engine": "musetalk"},
            render_variant=lambda *_args: (_ for _ in ()).throw(RuntimeError("renderer crashed")),
            evaluate_quality=lambda *_args: {},
            sampler_factory=_FakeSampler,
        )


def test_demo_pack_runs_serially_and_separates_public_from_private_artifacts(tmp_path):
    source_image, source_video, audio = _write_inputs(tmp_path)
    output_dir = tmp_path / "demo"
    calls: list[str] = []
    active = 0

    def render_variant(kind: str, plan: dict, output_path: Path) -> dict:
        nonlocal active
        assert active == 0
        active += 1
        calls.append(kind)
        output_path.write_bytes((kind.encode("utf-8") + b"-") * 1_000)
        active -= 1
        stage_paths = {
            "avatar_reference_type": "image" if kind == "generic" else "video",
            "request_source_video_path": "" if kind == "generic" else str(source_video),
            "liveportrait_driver_source": "vetted_template" if kind == "generic" else "source_video",
            "liveportrait_appearance_source_policy": (
                "image_reference"
                if kind == "generic"
                else "personal_image_original_identity_video_motion_v1"
            ),
            "liveportrait_performance_window_source": (
                "motion_style_v2" if kind in {"personal", "prosody"} else ""
            ),
            "liveportrait_performance_window_style": plan.get("style"),
            "liveportrait_performance_window_start": plan.get("performance_window", {}).get(
                "start_seconds", 0.0
            ),
            "liveportrait_performance_window_profile_score": 0.9,
            "liveportrait_performance_window_materialized": kind == "personal",
            "liveportrait_prosody_timeline_source": "prosody_v1" if kind == "prosody" else "",
            "liveportrait_prosody_timeline_segment_count": 3 if kind == "prosody" else 0,
            "liveportrait_prosody_timeline_duration": 6.0 if kind == "prosody" else 0.0,
            "liveportrait_prosody_timeline_materialized": kind == "prosody",
        }
        return {
            "strict_validation_passed": True,
            "stage_paths": stage_paths,
            "motion_validation": {
                "motion_real": True,
                "audio_match": True,
                "duration_mismatch": False,
                "duration_delta_seconds": 0.02,
                "duration_tolerance_seconds": 0.45,
                "avatar_visual_motion_score": {
                    "generic": 0.50,
                    "personal": 0.62,
                    "prosody": 0.72,
                }[kind],
                "quality_checks": {"landmark_stable": True},
            },
        }

    def compose_comparison(*, videos: dict, audio_path: Path, output_path: Path) -> dict:
        assert list(videos) == ["generic", "personal", "prosody"]
        assert Path(audio_path) == audio
        output_path.write_bytes(b"comparison" * 200)
        return {
            "path": str(output_path),
            "sha256": "comparison-sha",
            "size_bytes": output_path.stat().st_size,
            "watermark": "AI AVATAR DEMO",
        }

    result = run_demo_pack(
        suite_id="portfolio-001",
        output_dir=output_dir,
        source_image=source_image,
        source_video=source_video,
        audio_path=audio,
        motion_style_package=_motion_package(),
        request_payload={
            "emotion": "neutral",
            "motion_intensity": 0.5,
            "script": "private script text must not enter public artifacts",
        },
        prosody_profile=_prosody_profile(),
        model_versions={"liveportrait": "test"},
        script_hash="a" * 64,
        quality_preset="high",
        render_contract={
            "lipsync_engine": "musetalk",
            "restoration_enabled": True,
            "generic_motion_source_policy": "generic_non_personal",
        },
        render_variant=render_variant,
        evaluate_quality=lambda *_args: _quality_report(),
        compose_comparison=compose_comparison,
        sampler_factory=_FakeSampler,
    )

    assert calls == ["generic", "personal", "prosody"]
    assert result["pack"]["execution"]["mode"] == "sequential"
    assert result["pack"]["execution"]["parallel_gpu_renders"] == 1
    assert result["pack"]["experiment"]["render_contract"]["generic_motion_source_policy"] == (
        "generic_non_personal"
    )
    assert result["report"]["recommendation"] == "prosody"
    variants = {item["kind"]: item for item in result["report"]["variants"]}
    assert variants["personal"]["personal_window_materialized"] is True
    assert variants["prosody"]["personal_window_materialized"] is True
    assert variants["prosody"]["prosody_timeline_materialized"] is True
    assert variants["personal"]["identity_motion_decoupled"] is True
    assert variants["prosody"]["identity_motion_decoupled"] is True
    assert variants["generic"]["generic_baseline_isolated"] is True
    assert result["report"]["automated_claims"]["generic_baseline_isolated"] is True
    assert result["report"]["automated_claims"]["personal_identity_motion_decoupled"] is True
    assert result["report"]["automated_claims"]["prosody_identity_motion_decoupled"] is True
    public_dir = output_dir / "portfolio"
    assert sorted(path.name for path in public_dir.iterdir()) == sorted(
        [
            "avatar-evaluation.json",
            "avatar-evaluation.md",
            "comparison.mp4",
            "demo-pack.json",
            "portfolio-summary.md",
        ]
    )
    assert (output_dir / "private-renders" / "prosody" / "evidence.json").exists()
    public_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in public_dir.iterdir()
        if path.suffix in {".json", ".md"}
    )
    assert str(source_image) not in public_text
    assert str(source_video) not in public_text
    assert str(audio) not in public_text
    assert "private script text" not in public_text
    assert "manual_review_required_for_perceived_naturalness" in public_text


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is unavailable")
def test_real_ffmpeg_composes_labeled_three_panel_video(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    videos: dict[str, Path] = {}
    for kind, color in (("generic", "red"), ("personal", "green"), ("prosody", "blue")):
        path = tmp_path / f"{kind}.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=160x240:d=0.7",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
            timeout=30,
        )
        videos[kind] = path
    audio = tmp_path / "audio.wav"
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.7",
            str(audio),
        ],
        check=True,
        timeout=30,
    )

    output = tmp_path / "comparison.mp4"
    result = compose_side_by_side_video(
        videos=videos,
        audio_path=audio,
        output_path=output,
        panel_width=240,
        panel_height=320,
    )

    assert output.stat().st_size > 1_024
    assert result["dimensions"] == {"width": 720, "height": 320}
    assert result["watermark"] == "AI AVATAR DEMO"
