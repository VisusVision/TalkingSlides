"""Sequential, portfolio-safe demo packs for Digital Twin render variants."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any

from .evaluation import evaluate_avatar_variants, render_evaluation_markdown
from .motion_planning import build_personal_motion_plan


DEMO_PACK_VERSION = "avatar-demo-pack-v1"
DEMO_VARIANTS = ("generic", "personal", "prosody")


class DemoPackContractError(ValueError):
    """Raised before or during a demo run when its evidence would be invalid."""


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in {float("inf"), float("-inf")} else 0.0
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_demo_input_fingerprint(
    *,
    source_image: str | Path,
    source_video: str | Path,
    audio_path: str | Path,
    script_hash: str = "",
    model_versions: Mapping[str, Any] | None = None,
    quality_preset: str = "high",
    request_payload: Mapping[str, Any] | None = None,
    render_contract: Mapping[str, Any] | None = None,
) -> str:
    """Hash every invariant input that must remain equal across variants."""

    files = {
        "source_image": Path(source_image).resolve(),
        "source_video": Path(source_video).resolve(),
        "audio": Path(audio_path).resolve(),
    }
    for label, path in files.items():
        if not path.is_file() or path.stat().st_size <= 0:
            raise DemoPackContractError(f"demo_input_missing:{label}:{path}")
    payload = {
        "audio_sha256": _sha256_file(files["audio"]),
        "model_versions": {
            str(key): str(value)
            for key, value in sorted(dict(model_versions or {}).items(), key=lambda item: str(item[0]))
        },
        "quality_preset": str(quality_preset or "high").strip().lower(),
        "render_contract": _json_safe(dict(render_contract or {})),
        "request": _json_safe(dict(request_payload or {})),
        "script_hash": str(script_hash or "").strip().lower(),
        "source_image_sha256": _sha256_file(files["source_image"]),
        "source_video_sha256": _sha256_file(files["source_video"]),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_demo_variant_plans(
    *,
    motion_style_package: Mapping[str, Any],
    request_payload: Mapping[str, Any],
    prosody_profile: Mapping[str, Any],
    seed_material: str,
) -> list[dict[str, Any]]:
    """Build three plans while changing only personal/prosody capabilities."""

    generic = build_personal_motion_plan(
        {},
        request_payload,
        seed_material=seed_material,
        prosody_profile=None,
    )
    personal = build_personal_motion_plan(
        motion_style_package,
        request_payload,
        seed_material=seed_material,
        prosody_profile=None,
    )
    prosody = build_personal_motion_plan(
        motion_style_package,
        request_payload,
        seed_material=seed_material,
        prosody_profile=prosody_profile,
    )
    if bool(generic.get("personal_window_selected")) or bool(generic.get("prosody_timeline_selected")):
        raise DemoPackContractError("demo_generic_plan_not_generic")
    if not bool(personal.get("personal_window_selected")):
        reasons = ",".join(str(item) for item in personal.get("fallback_reasons") or [])
        raise DemoPackContractError(f"demo_personal_window_unavailable:{reasons or 'unknown'}")
    if bool(personal.get("prosody_timeline_selected")):
        raise DemoPackContractError("demo_personal_plan_contains_prosody")
    if not bool(prosody.get("personal_window_selected")):
        raise DemoPackContractError("demo_prosody_personal_window_unavailable")
    if not bool(prosody.get("prosody_timeline_selected")):
        reasons = ",".join(str(item) for item in prosody.get("prosody_fallback_reasons") or [])
        raise DemoPackContractError(f"demo_prosody_timeline_unavailable:{reasons or 'unknown'}")
    return [
        {"kind": "generic", "motion_plan": generic},
        {"kind": "personal", "motion_plan": personal},
        {"kind": "prosody", "motion_plan": prosody},
    ]


def build_demo_variant_render_inputs(
    *,
    kind: str,
    source_image: str | Path,
    source_video: str | Path,
) -> dict[str, str]:
    """Keep the generic baseline isolated from the personal performance driver."""

    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in DEMO_VARIANTS:
        raise DemoPackContractError(f"demo_variant_kind_invalid:{normalized_kind or 'missing'}")
    if normalized_kind == "generic":
        return {
            "source_image_path": str(source_image),
            "source_video_path": "",
            "avatar_reference_type": "image",
            "motion_source_policy": "generic_non_personal",
        }
    return {
        "source_image_path": str(source_image),
        "source_video_path": str(source_video),
        "avatar_reference_type": "video",
        "motion_source_policy": "personal_performance",
    }


def materialize_motion_execution(plan: Mapping[str, Any], render_info: Mapping[str, Any]) -> dict[str, Any]:
    """Persist what LivePortrait actually executed, not only what was planned."""

    resolved = dict(plan)
    stage_paths = dict(render_info.get("stage_paths") or {})
    window_materialized = bool(stage_paths.get("liveportrait_performance_window_materialized"))
    prosody_materialized = bool(stage_paths.get("liveportrait_prosody_timeline_materialized"))
    resolved["execution"] = {
        "window_source": str(stage_paths.get("liveportrait_performance_window_source") or ""),
        "window_style": str(stage_paths.get("liveportrait_performance_window_style") or ""),
        "window_start_seconds": float(stage_paths.get("liveportrait_performance_window_start") or 0.0),
        "profile_score": float(stage_paths.get("liveportrait_performance_window_profile_score") or 0.0),
        "window_materialized": window_materialized,
        "personal_motion_materialized": bool(window_materialized or prosody_materialized),
        "renderer_driver_source": str(stage_paths.get("liveportrait_driver_source") or ""),
        "renderer_reference_type": str(stage_paths.get("avatar_reference_type") or ""),
        "personal_source_video_supplied": bool(stage_paths.get("request_source_video_path")),
        "renderer_motion_preset": str(
            render_info.get("liveportrait_motion_preset") or resolved.get("motion_preset") or ""
        ),
        "prosody_timeline_source": str(stage_paths.get("liveportrait_prosody_timeline_source") or ""),
        "prosody_timeline_segment_count": int(
            stage_paths.get("liveportrait_prosody_timeline_segment_count") or 0
        ),
        "prosody_timeline_duration_seconds": float(
            stage_paths.get("liveportrait_prosody_timeline_duration") or 0.0
        ),
        "prosody_timeline_materialized": prosody_materialized,
        "prosody_timeline_failure_reason": str(
            stage_paths.get("liveportrait_prosody_timeline_failure_reason") or ""
        ),
    }
    return resolved


class GpuMemorySampler(AbstractContextManager["GpuMemorySampler"]):
    """Best-effort total GPU memory sampler suitable for one sequential render."""

    def __init__(self, *, interval_seconds: float = 0.5) -> None:
        self.interval_seconds = max(float(interval_seconds), 0.1)
        self.baseline_mb = 0.0
        self.peak_mb = 0.0
        self.available = bool(shutil.which("nvidia-smi"))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _probe() -> float:
        try:
            process = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return 0.0
        values: list[float] = []
        if process.returncode == 0:
            for line in process.stdout.splitlines():
                try:
                    values.append(max(float(line.strip()), 0.0))
                except ValueError:
                    continue
        return max(values, default=0.0)

    def _sample_until_stopped(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.peak_mb = max(self.peak_mb, self._probe())

    def __enter__(self) -> "GpuMemorySampler":
        if self.available:
            self.baseline_mb = self._probe()
            self.peak_mb = self.baseline_mb
            self._thread = threading.Thread(target=self._sample_until_stopped, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self.interval_seconds * 2.0, 1.0))
        if self.available:
            self.peak_mb = max(self.peak_mb, self._probe())
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available and self.peak_mb > 0.0,
            "baseline_mb": round(self.baseline_mb, 1),
            "peak_mb": round(self.peak_mb, 1),
            "peak_delta_mb": round(max(self.peak_mb - self.baseline_mb, 0.0), 1),
            "scope": "total_gpu_memory_used",
        }


def compose_side_by_side_video(
    *,
    videos: Mapping[str, str | Path],
    audio_path: str | Path,
    output_path: str | Path,
    panel_width: int = 480,
    panel_height: int = 720,
) -> dict[str, Any]:
    """Create a labeled, watermarked three-panel comparison with shared audio."""

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise DemoPackContractError("demo_ffmpeg_unavailable")
    ordered = [Path(videos[kind]).resolve() for kind in DEMO_VARIANTS]
    audio = Path(audio_path).resolve()
    for path in [*ordered, audio]:
        if not path.is_file() or path.stat().st_size <= 0:
            raise DemoPackContractError(f"demo_comparison_input_missing:{path}")
    width = max(240, int(panel_width))
    height = max(320, int(panel_height))
    width -= width % 2
    height -= height % 2
    filters: list[str] = []
    labels = ("GENERIC", "PERSONAL", "PERSONAL + PROSODY")
    for index, label in enumerate(labels):
        filters.append(
            f"[{index}:v]setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            "drawbox=x=0:y=0:w=iw:h=52:color=black@0.70:t=fill,"
            f"drawtext=text='{label}':x=(w-text_w)/2:y=15:fontcolor=white:fontsize=22[v{index}]"
        )
    filters.append(
        "[v0][v1][v2]hstack=inputs=3,"
        "drawbox=x=0:y=h-44:w=iw:h=44:color=black@0.65:t=fill,"
        "drawtext=text='AI AVATAR DEMO':x=w-text_w-18:y=h-text_h-12:fontcolor=white:fontsize=18[outv]"
    )
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for video in ordered:
        command.extend(["-i", str(video)])
    command.extend(
        [
            "-i",
            str(audio),
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-map",
            "3:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    process = subprocess.run(command, capture_output=True, text=True, check=False, timeout=1800)
    if process.returncode != 0 or not output.is_file() or output.stat().st_size <= 1024:
        reason = (process.stderr or "comparison output missing").strip()[-1200:]
        raise DemoPackContractError(f"demo_comparison_failed:{reason}")
    return {
        "path": str(output),
        "sha256": _sha256_file(output),
        "size_bytes": output.stat().st_size,
        "layout": "three_panel_horizontal",
        "dimensions": {"width": width * 3, "height": height},
        "watermark": "AI AVATAR DEMO",
    }


def render_portfolio_summary(report: Mapping[str, Any], pack: Mapping[str, Any]) -> str:
    comparisons = dict(report.get("comparisons") or {})
    lines = [
        f"# Avatar Demo Pack - {pack.get('suite_id') or 'demo'}",
        "",
        f"- Contract: `{pack.get('version') or ''}`",
        f"- Evaluation: `{report.get('version') or ''}`",
        f"- Automated recommendation: `{report.get('recommendation') or 'inconclusive'}`",
        "- Comparison video: `comparison.mp4`",
        "- Disclosure: `AI AVATAR DEMO` watermark is embedded in the public video.",
        "",
        "## Measured result",
        "",
    ]
    for name, comparison in comparisons.items():
        item = dict(comparison)
        deltas = dict(item.get("deltas") or {})
        lines.append(
            f"- `{name}`: quality {float(deltas.get('automated_quality_points') or 0.0):+.2f} points, "
            f"motion {float(deltas.get('motion_score') or 0.0):+.3f}, "
            f"regressions={list(item.get('regressions') or []) or ['none']}"
        )
    lines.extend(
        [
            "",
            "## Review status",
            "",
            "Automated evidence does not prove perceived naturalness. Complete the blind review",
            "scorecard in `avatar-evaluation.md` before publishing a naturalness claim.",
            "",
            "Individual renders remain in the private workspace and are intentionally excluded",
            "from the public portfolio directory.",
            "",
        ]
    )
    return "\n".join(lines)


def run_demo_pack(
    *,
    suite_id: str,
    output_dir: str | Path,
    source_image: str | Path,
    source_video: str | Path,
    audio_path: str | Path,
    motion_style_package: Mapping[str, Any],
    request_payload: Mapping[str, Any],
    prosody_profile: Mapping[str, Any],
    model_versions: Mapping[str, Any] | None,
    script_hash: str,
    quality_preset: str,
    render_contract: Mapping[str, Any],
    render_variant: Callable[[str, Mapping[str, Any], Path], Mapping[str, Any]],
    evaluate_quality: Callable[[Path, Mapping[str, Any]], Mapping[str, Any]],
    compose_comparison: Callable[..., Mapping[str, Any]] = compose_side_by_side_video,
    sampler_factory: Callable[[], AbstractContextManager[Any]] = GpuMemorySampler,
) -> dict[str, Any]:
    """Run all variants serially, evaluate them, and build a public artifact set."""

    normalized_script_hash = str(script_hash or "").strip().lower()
    if len(normalized_script_hash) != 64 or any(
        character not in "0123456789abcdef" for character in normalized_script_hash
    ):
        raise DemoPackContractError("demo_script_hash_invalid")
    if not dict(model_versions or {}):
        raise DemoPackContractError("demo_model_versions_missing")
    root = Path(output_dir).resolve()
    if root.exists() and not root.is_dir():
        raise DemoPackContractError(f"demo_output_not_directory:{root}")
    if root.exists() and any(root.iterdir()):
        raise DemoPackContractError(f"demo_output_not_empty:{root}")
    private_dir = root / "private-renders"
    public_dir = root / "portfolio"
    private_dir.mkdir(parents=True, exist_ok=True)
    public_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = build_demo_input_fingerprint(
        source_image=source_image,
        source_video=source_video,
        audio_path=audio_path,
        script_hash=normalized_script_hash,
        model_versions=model_versions,
        quality_preset=quality_preset,
        request_payload=request_payload,
        render_contract=render_contract,
    )
    plans = build_demo_variant_plans(
        motion_style_package=motion_style_package,
        request_payload=request_payload,
        prosody_profile=prosody_profile,
        seed_material=fingerprint,
    )
    evidence: list[dict[str, Any]] = []
    output_videos: dict[str, Path] = {}
    execution_order: list[str] = []
    for spec in plans:
        kind = str(spec["kind"])
        variant_dir = private_dir / kind
        variant_dir.mkdir(parents=True, exist_ok=True)
        output_video = variant_dir / "avatar.mp4"
        sampler = sampler_factory()
        try:
            with sampler:
                started = time.perf_counter()
                render_info = dict(render_variant(kind, dict(spec["motion_plan"]), output_video))
                render_seconds = max(time.perf_counter() - started, 0.0)
        except DemoPackContractError:
            raise
        except Exception as exc:
            reason = str(exc).replace("\r", " ").replace("\n", " ")[:300]
            raise DemoPackContractError(
                f"demo_render_failed:{kind}:{type(exc).__name__}:{reason}"
            ) from exc
        if not output_video.is_file() or output_video.stat().st_size <= 1024:
            raise DemoPackContractError(f"demo_render_output_missing:{kind}")
        motion_plan = materialize_motion_execution(spec["motion_plan"], render_info)
        try:
            quality_report = dict(evaluate_quality(output_video, render_info))
        except Exception as exc:
            reason = str(exc).replace("\r", " ").replace("\n", " ")[:300]
            raise DemoPackContractError(
                f"demo_quality_evaluation_failed:{kind}:{type(exc).__name__}:{reason}"
            ) from exc
        gpu = dict(sampler.as_dict()) if hasattr(sampler, "as_dict") else {}
        variant_evidence = {
            "id": f"{suite_id}-{kind}",
            "kind": kind,
            "input_fingerprint": fingerprint,
            "quality_report": quality_report,
            "motion_validation": dict(render_info.get("motion_validation") or {}),
            "motion_plan": motion_plan,
            "runtime": {
                "render_seconds": round(render_seconds, 3),
                "peak_vram_mb": float(gpu.get("peak_mb") or 0.0),
                "gpu_memory": gpu,
            },
            "artifacts": {
                "video_path": f"private-renders/{kind}/avatar.mp4",
                "video_sha256": _sha256_file(output_video),
                "motion_plan_path": f"private-renders/{kind}/motion-plan.json",
                "render_info_path": f"private-renders/{kind}/render-info.json",
            },
        }
        _write_json(variant_dir / "motion-plan.json", motion_plan)
        _write_json(variant_dir / "render-info.json", render_info)
        _write_json(variant_dir / "evidence.json", variant_evidence)
        evidence.append(variant_evidence)
        output_videos[kind] = output_video
        execution_order.append(kind)

    report = evaluate_avatar_variants(
        {
            "suite_id": suite_id,
            "context": {
                "script_hash": normalized_script_hash,
                "quality_preset": quality_preset,
                "model_versions": dict(model_versions or {}),
            },
            "variants": evidence,
        }
    )
    comparison_path = public_dir / "comparison.mp4"
    comparison = dict(
        compose_comparison(
            videos=output_videos,
            audio_path=audio_path,
            output_path=comparison_path,
        )
    )
    comparison["path"] = "comparison.mp4"
    public_request = {
        key: _json_safe(request_payload.get(key))
        for key in ("emotion", "motion_intensity")
        if key in request_payload
    }
    public_render_contract = {
        key: _json_safe(render_contract.get(key))
        for key in (
            "lipsync_engine",
            "restoration_enabled",
            "liveportrait_enabled",
            "enforce_exact_audio_duration",
            "generic_motion_source_policy",
        )
        if key in render_contract
    }
    pack = {
        "version": DEMO_PACK_VERSION,
        "suite_id": str(suite_id)[:120],
        "input_fingerprint": fingerprint,
        "execution": {
            "mode": "sequential",
            "order": execution_order,
            "parallel_gpu_renders": 1,
        },
        "recommendation": report["recommendation"],
        "experiment": {
            "request": public_request,
            "render_contract": public_render_contract,
        },
        "comparison": comparison,
        "public_artifacts": [
            "comparison.mp4",
            "avatar-evaluation.json",
            "avatar-evaluation.md",
            "portfolio-summary.md",
            "demo-pack.json",
        ],
        "private_artifacts_root": "../private-renders",
        "claim_boundary": "manual_review_required_for_perceived_naturalness",
    }
    _write_json(public_dir / "avatar-evaluation.json", report)
    (public_dir / "avatar-evaluation.md").write_text(
        render_evaluation_markdown(report), encoding="utf-8"
    )
    _write_json(public_dir / "demo-pack.json", pack)
    (public_dir / "portfolio-summary.md").write_text(
        render_portfolio_summary(report, pack), encoding="utf-8"
    )
    return {"pack": pack, "report": report, "output_dir": str(root)}
