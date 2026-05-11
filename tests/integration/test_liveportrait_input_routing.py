import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

runner = importlib.import_module("scripts.liveportrait_runner")


def test_liveportrait_runner_timeout_kills_process_group(monkeypatch):
    killed: dict[str, object] = {}

    class FakeProcess:
        pid = 4321
        returncode = None

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(["lp"], timeout or 1)

        def poll(self):
            return None

        def wait(self, timeout=None):
            killed["wait_timeout"] = timeout
            self.returncode = -9
            return self.returncode

        def kill(self):
            killed["process_kill"] = True

    def fake_popen(cmd, **kwargs):
        killed["cmd"] = cmd
        killed["popen_kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(runner.os, "name", "posix")
    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner.os, "getpgid", lambda pid: 9876, raising=False)
    monkeypatch.setattr(runner.os, "killpg", lambda pgid, sig: killed.update({"pgid": pgid, "sig": sig}), raising=False)

    ok, error, details = runner._run(["python", "lp.py"], timeout_seconds=1)

    assert ok is False
    assert error == "liveportrait_stage_failed:timeout_after_1s"
    assert details["return_code"] == "-1"
    assert killed.get("pgid") == 9876 or killed.get("process_kill") is True
    assert "start_new_session" in killed["popen_kwargs"] or "creationflags" in killed["popen_kwargs"]


def _make_runtime_layout(tmp_path: Path) -> dict[str, Path]:
    source_image = tmp_path / "face.png"
    source_video = tmp_path / "drive.mp4"
    output_path = tmp_path / "lp_out.mp4"

    source_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    source_video.write_bytes(b"video")

    lp_home = tmp_path / "liveportrait_home"
    lp_home.mkdir(parents=True, exist_ok=True)
    (lp_home / "outputs").mkdir(parents=True, exist_ok=True)

    lp_entrypoint = lp_home / "inference.py"
    lp_entrypoint.write_text(
        "import sys\n"
        "if '--help' in sys.argv:\n"
        "    print('--source --driving --output_path --model_path')\n",
        encoding="utf-8",
    )

    lp_model = lp_home / "models"
    lp_model.mkdir(parents=True, exist_ok=True)

    return {
        "source_image": source_image,
        "source_video": source_video,
        "output_path": output_path,
        "lp_home": lp_home,
        "lp_entrypoint": lp_entrypoint,
        "lp_model": lp_model,
    }


def _driver_metrics(
    path: Path | str,
    *,
    duration_seconds: float = 1.0,
    fps: float = 25.0,
    frame_count: int = 25,
    requested_fps: float = 0.0,
    target_frame_count: int = 0,
    unique_frames: int = 14,
    unique_ratio: float = 0.56,
    mean_mad: float = 0.9,
    near_static: bool = False,
    failure_reason: str = "",
    validation_failure_reason: str = "",
    valid: bool = True,
) -> dict[str, object]:
    return {
        "path": str(path),
        "duration_seconds": float(duration_seconds),
        "expected_duration_seconds": float(duration_seconds),
        "duration_delta_seconds": 0.0,
        "fps": float(fps),
        "requested_fps": float(requested_fps),
        "frame_count": int(frame_count),
        "target_frame_count": int(target_frame_count),
        "frame_count_delta": int(frame_count - target_frame_count) if target_frame_count else 0,
        "unique_frames": int(unique_frames),
        "unique_ratio": float(unique_ratio),
        "mean_mad": float(mean_mad),
        "near_static": bool(near_static),
        "valid": bool(valid),
        "failure_reason": str(failure_reason),
        "validation_failure_reason": str(validation_failure_reason),
    }


def _patch_runner_execution(monkeypatch, captured: dict) -> None:
    def _fake_ensure_driving_clip_contract(
        *,
        source_video,
        target_duration_seconds,
        work_dir,
        target_fps=0.0,
        output_name="driving_contract.mp4",
        always_materialize=False,
    ):
        captured.setdefault("ensure_calls", []).append(
            {
                "source_video": str(source_video),
                "target_duration_seconds": float(target_duration_seconds),
                "work_dir": str(work_dir),
                "target_fps": float(target_fps),
                "output_name": str(output_name),
                "always_materialize": bool(always_materialize),
            }
        )
        captured["ensure_source_video"] = str(source_video)
        captured["ensure_target_duration_seconds"] = float(target_duration_seconds)
        captured["ensure_work_dir"] = str(work_dir)
        captured["ensure_target_fps"] = float(target_fps)
        captured["ensure_output_name"] = str(output_name)
        captured["ensure_always_materialize"] = bool(always_materialize)
        return source_video, "passed_through", float(target_duration_seconds)

    def _fake_run(cmd, *, timeout_seconds):
        captured["cmd"] = list(cmd)
        captured["timeout_seconds"] = int(timeout_seconds)

        output_value = ""
        for flag in ("--output_path", "--output_video", "--result_path"):
            if flag in cmd:
                output_value = str(cmd[cmd.index(flag) + 1])
                break
        if output_value:
            output_path = Path(output_value)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"result")

        return True, "", {
            "cmd": " ".join(str(x) for x in cmd),
            "return_code": "0",
            "stderr_summary": "",
        }

    monkeypatch.setattr(runner, "_ensure_driving_clip_contract", _fake_ensure_driving_clip_contract)
    monkeypatch.setattr(runner, "_run", _fake_run)
    monkeypatch.setattr(
        runner,
        "_validate_driving_clip",
        lambda *, path, expected_duration_seconds, requested_fps, target_frame_count, fps_validation_mode: _driver_metrics(
            path,
            duration_seconds=expected_duration_seconds or 1.0,
            fps=25.0,
            frame_count=25,
            requested_fps=requested_fps,
            target_frame_count=target_frame_count,
            valid=True,
        ),
    )


def _driving_arg_from_command(cmd: list[str]) -> str:
    for flag in ("--driving", "--driving_video", "--source_video", "-d"):
        if flag in cmd:
            return str(cmd[cmd.index(flag) + 1])
    return ""


def test_probe_driving_clip_variation_parses_mean_mad_from_ffmpeg_metadata(tmp_path, monkeypatch):
    clip_path = tmp_path / "drive.mp4"
    clip_path.write_bytes(b"video")

    def _fake_run(cmd, *args, **kwargs):
        cmd_text = " ".join(str(part) for part in cmd)
        if "stream=duration" in cmd_text:
            return SimpleNamespace(returncode=0, stdout="37.25\n", stderr="")
        if "stream=avg_frame_rate,r_frame_rate" in cmd_text:
            return SimpleNamespace(returncode=0, stdout="avg_frame_rate=25/1\nr_frame_rate=25/1\n", stderr="")
        if "stream=nb_read_frames" in cmd_text:
            return SimpleNamespace(returncode=0, stdout="931\n", stderr="")
        if "-f framehash" in cmd_text:
            return SimpleNamespace(
                returncode=0,
                stdout="\n".join(
                    [
                        "0,          0,          0,        1,      100,aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "0,          0,          1,        1,      100,bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    ]
                ),
                stderr="",
            )
        if "metadata=print" in cmd_text:
            return SimpleNamespace(
                returncode=0,
                stdout="",
                stderr="lavfi.td.mean=0.400000\nlavfi.td.mean=0.600000\n",
            )
        raise AssertionError(f"Unexpected subprocess invocation: {cmd_text}")

    monkeypatch.setattr(runner.subprocess, "run", _fake_run)

    metrics = runner._probe_driving_clip_variation(clip_path)

    assert metrics["duration_seconds"] == 37.25
    assert metrics["fps"] == 25.0
    assert metrics["frame_count"] == 931
    assert metrics["mean_mad"] == 0.5


def test_image_input_routes_to_image_driven_composer(tmp_path, monkeypatch, capsys):
    paths = _make_runtime_layout(tmp_path)
    captured: dict[str, object] = {}

    def _fake_compose(target_duration_s, output_path, **kwargs):
        captured["compose_target_duration"] = float(target_duration_s)
        captured["compose_output_path"] = str(output_path)
        captured["compose_kwargs"] = dict(kwargs)
        Path(output_path).write_bytes(b"driving")
        return True

    monkeypatch.setattr(runner, "_motion_composer", SimpleNamespace(compose=_fake_compose))
    _patch_runner_execution(monkeypatch, captured)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "liveportrait_runner",
            "--source_image",
            str(paths["source_image"]),
            "--output_path",
            str(paths["output_path"]),
            "--liveportrait_home",
            str(paths["lp_home"]),
            "--liveportrait_entrypoint",
            str(paths["lp_entrypoint"]),
            "--liveportrait_model_path",
            str(paths["lp_model"]),
            "--timeout_seconds",
            "30",
        ],
    )

    assert runner.main() == 0

    compose_kwargs = dict(captured.get("compose_kwargs") or {})
    assert compose_kwargs.get("source_kind") == "image"
    assert compose_kwargs.get("source_image_path") == paths["source_image"]
    assert compose_kwargs.get("source_video_path") is None
    assert compose_kwargs.get("motion_preset") == "natural_conservative"
    assert compose_kwargs.get("motion_profile") == "default"

    driving_arg = _driving_arg_from_command(list(captured.get("cmd") or []))
    assert driving_arg != str(paths["source_image"])
    assert driving_arg.endswith("composed_drive.mp4")

    stderr_text = capsys.readouterr().err
    assert "motion_source=image_composed" in stderr_text
    assert "liveportrait_driver_source=composer" in stderr_text
    assert "liveportrait_composer_used=1" in stderr_text
    assert "liveportrait_boosted_retry_used=0" in stderr_text
    assert "input_kind=image" in stderr_text


def test_duration_contract_uses_requested_fps_not_internal_composer_fps(tmp_path, monkeypatch, capsys):
    paths = _make_runtime_layout(tmp_path)
    captured: dict[str, object] = {}

    def _fake_compose(target_duration_s, output_path, **kwargs):
        captured["compose_target_duration"] = float(target_duration_s)
        captured["compose_kwargs"] = dict(kwargs)
        Path(output_path).write_bytes(b"driving")
        return True

    monkeypatch.setattr(runner, "_motion_composer", SimpleNamespace(compose=_fake_compose))
    _patch_runner_execution(monkeypatch, captured)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "liveportrait_runner",
            "--source_image",
            str(paths["source_image"]),
            "--output_path",
            str(paths["output_path"]),
            "--liveportrait_home",
            str(paths["lp_home"]),
            "--liveportrait_entrypoint",
            str(paths["lp_entrypoint"]),
            "--liveportrait_model_path",
            str(paths["lp_model"]),
            "--timeout_seconds",
            "30",
            "--fps",
            "16",
            "--target_frame_count",
            "596",
        ],
    )

    assert runner.main() == 0
    assert captured["compose_target_duration"] == 37.25
    assert captured["ensure_target_duration_seconds"] == 37.25

    compose_kwargs = dict(captured.get("compose_kwargs") or {})
    assert compose_kwargs.get("requested_fps") == 16.0
    assert compose_kwargs.get("target_frame_count") == 596
    assert compose_kwargs.get("expected_duration_seconds") == 37.25
    assert compose_kwargs.get("render_fps") == runner._TARGET_FPS
    assert compose_kwargs.get("motion_preset") == "natural_conservative"

    stderr_text = capsys.readouterr().err
    assert "requested_fps=16.0000" in stderr_text
    assert f"internal_composer_fps={runner._TARGET_FPS}" in stderr_text
    assert "target_duration_seconds=37.2500" in stderr_text
    assert "expected_duration_seconds=37.2500" in stderr_text


def test_video_input_uses_real_video_source(tmp_path, monkeypatch, capsys):
    paths = _make_runtime_layout(tmp_path)
    captured: dict[str, object] = {"compose_called": False}

    def _should_not_compose(*_args, **_kwargs):
        captured["compose_called"] = True
        return False

    monkeypatch.setattr(runner, "_motion_composer", SimpleNamespace(compose=_should_not_compose))
    _patch_runner_execution(monkeypatch, captured)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "liveportrait_runner",
            "--source_image",
            str(paths["source_image"]),
            "--source_video",
            str(paths["source_video"]),
            "--output_path",
            str(paths["output_path"]),
            "--liveportrait_home",
            str(paths["lp_home"]),
            "--liveportrait_entrypoint",
            str(paths["lp_entrypoint"]),
            "--liveportrait_model_path",
            str(paths["lp_model"]),
            "--timeout_seconds",
            "30",
        ],
    )

    assert runner.main() == 0
    assert captured["compose_called"] is False

    driving_arg = _driving_arg_from_command(list(captured.get("cmd") or []))
    assert driving_arg == str(paths["source_video"])

    stderr_text = capsys.readouterr().err
    assert "motion_source=real_video" in stderr_text
    assert "liveportrait_driver_source=source_video" in stderr_text
    assert "liveportrait_composer_used=0" in stderr_text
    assert "input_kind=video" in stderr_text


def test_image_input_never_reuses_image_as_video_driving_path(tmp_path, monkeypatch):
    paths = _make_runtime_layout(tmp_path)
    captured: dict[str, object] = {}

    def _fake_compose(_target_duration_s, output_path, **kwargs):
        captured["compose_kwargs"] = dict(kwargs)
        Path(output_path).write_bytes(b"driving")
        return True

    monkeypatch.setattr(runner, "_motion_composer", SimpleNamespace(compose=_fake_compose))
    _patch_runner_execution(monkeypatch, captured)

    # Regression case: source_video argument accidentally receives an image path.
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "liveportrait_runner",
            "--source_image",
            str(paths["source_image"]),
            "--source_video",
            str(paths["source_image"]),
            "--output_path",
            str(paths["output_path"]),
            "--liveportrait_home",
            str(paths["lp_home"]),
            "--liveportrait_entrypoint",
            str(paths["lp_entrypoint"]),
            "--liveportrait_model_path",
            str(paths["lp_model"]),
            "--timeout_seconds",
            "30",
        ],
    )

    assert runner.main() == 0

    compose_kwargs = dict(captured.get("compose_kwargs") or {})
    assert compose_kwargs.get("source_kind") == "image"

    driving_arg = _driving_arg_from_command(list(captured.get("cmd") or []))
    assert driving_arg != str(paths["source_image"])
    assert driving_arg.endswith("composed_drive.mp4")


def test_image_input_rejects_near_static_driver_before_liveportrait(tmp_path, monkeypatch):
    paths = _make_runtime_layout(tmp_path)
    captured: dict[str, object] = {"run_called": False}

    def _fake_compose(_target_duration_s, output_path, **_kwargs):
        Path(output_path).write_bytes(b"driving")
        return True

    def _fake_run(*_args, **_kwargs):
        captured["run_called"] = True
        return True, "", {"cmd": "", "return_code": "0", "stderr_summary": ""}

    monkeypatch.setattr(runner, "_motion_composer", SimpleNamespace(compose=_fake_compose))
    monkeypatch.setattr(runner, "_run", _fake_run)
    monkeypatch.setattr(
        runner,
        "_validate_driving_clip",
        lambda *, path, expected_duration_seconds, requested_fps, target_frame_count, fps_validation_mode: _driver_metrics(
            path,
            duration_seconds=expected_duration_seconds or 1.0,
            fps=25.0,
            frame_count=25,
            requested_fps=requested_fps,
            target_frame_count=target_frame_count,
            unique_frames=1,
            unique_ratio=0.04,
            mean_mad=0.01,
            near_static=True,
            valid=False,
            failure_reason="driver_near_static:unique_frames=1<min_6",
            validation_failure_reason="driver_invalid:driver_near_static:unique_frames=1<min_6",
        ),
    )
    monkeypatch.setattr(
        runner,
        "_ensure_driving_clip_contract",
        lambda **kwargs: (kwargs["source_video"], "passed_through", kwargs["target_duration_seconds"]),
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "liveportrait_runner",
            "--source_image",
            str(paths["source_image"]),
            "--output_path",
            str(paths["output_path"]),
            "--liveportrait_home",
            str(paths["lp_home"]),
            "--liveportrait_entrypoint",
            str(paths["lp_entrypoint"]),
            "--liveportrait_model_path",
            str(paths["lp_model"]),
            "--timeout_seconds",
            "30",
        ],
    )

    try:
        runner.main()
        assert False, "runner.main() should have raised RuntimeError"
    except RuntimeError as exc:
        assert "liveportrait_invalid_driving_clip" in str(exc)

    assert captured["run_called"] is False


def test_image_input_regenerates_driver_until_variation_is_valid(tmp_path, monkeypatch, capsys):
    paths = _make_runtime_layout(tmp_path)
    captured: dict[str, object] = {}
    compose_profiles: list[str] = []
    probe_calls = {"count": 0}
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_ALLOW_BOOSTED_RETRY", "1")

    def _fake_compose(_target_duration_s, output_path, **kwargs):
        compose_profiles.append(str(kwargs.get("motion_profile") or "default"))
        Path(output_path).write_bytes(b"driving")
        return True

    def _fake_validate(*, path, expected_duration_seconds, requested_fps, target_frame_count, fps_validation_mode):
        probe_calls["count"] += 1
        if probe_calls["count"] == 1:
            return _driver_metrics(
                path,
                duration_seconds=expected_duration_seconds or 1.0,
                fps=25.0,
                frame_count=25,
                requested_fps=requested_fps,
                target_frame_count=target_frame_count,
                unique_frames=2,
                unique_ratio=0.08,
                mean_mad=0.11,
                near_static=True,
                valid=False,
                failure_reason="driver_near_static:unique_ratio=0.08<min_0.16",
                validation_failure_reason="driver_invalid:driver_near_static:unique_ratio=0.08<min_0.16",
            )
        return _driver_metrics(
            path,
            duration_seconds=expected_duration_seconds or 1.0,
            fps=25.0,
            frame_count=25,
            requested_fps=requested_fps,
            target_frame_count=target_frame_count,
            unique_frames=13,
            unique_ratio=0.52,
            mean_mad=1.10,
            near_static=False,
            valid=True,
        )

    monkeypatch.setattr(runner, "_motion_composer", SimpleNamespace(compose=_fake_compose))
    monkeypatch.setattr(
        runner,
        "_ensure_driving_clip_contract",
        lambda **kwargs: (kwargs["source_video"], "passed_through", kwargs["target_duration_seconds"]),
    )
    _patch_runner_execution(monkeypatch, captured)
    monkeypatch.setattr(runner, "_validate_driving_clip", _fake_validate)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "liveportrait_runner",
            "--source_image",
            str(paths["source_image"]),
            "--output_path",
            str(paths["output_path"]),
            "--liveportrait_home",
            str(paths["lp_home"]),
            "--liveportrait_entrypoint",
            str(paths["lp_entrypoint"]),
            "--liveportrait_model_path",
            str(paths["lp_model"]),
            "--timeout_seconds",
            "30",
        ],
    )

    assert runner.main() == 0
    assert compose_profiles == ["default", "boosted"]
    assert probe_calls["count"] == 2

    stderr_text = capsys.readouterr().err
    assert "driver candidate rejected candidate=image_composed profile=default" in stderr_text
    assert "final_driver_recipe motion_source=image_composed:boosted" in stderr_text
    assert "liveportrait_boosted_retry_used=1" in stderr_text


def test_image_input_does_not_auto_boost_without_flag(tmp_path, monkeypatch):
    paths = _make_runtime_layout(tmp_path)
    captured: dict[str, object] = {}
    compose_profiles: list[str] = []

    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_ALLOW_BOOSTED_RETRY", raising=False)
    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_MOTION_PRESET", raising=False)

    def _fake_compose(_target_duration_s, output_path, **kwargs):
        compose_profiles.append(str(kwargs.get("motion_profile") or "default"))
        Path(output_path).write_bytes(b"driving")
        return True

    def _fake_validate(*, path, expected_duration_seconds, requested_fps, target_frame_count, fps_validation_mode):
        return _driver_metrics(
            path,
            duration_seconds=expected_duration_seconds or 1.0,
            fps=25.0,
            frame_count=25,
            requested_fps=requested_fps,
            target_frame_count=target_frame_count,
            unique_frames=2,
            unique_ratio=0.08,
            mean_mad=0.11,
            near_static=True,
            valid=False,
            failure_reason="driver_near_static:unique_ratio=0.08<min_0.16",
            validation_failure_reason="driver_invalid:driver_near_static:unique_ratio=0.08<min_0.16",
        )

    monkeypatch.setattr(runner, "_motion_composer", SimpleNamespace(compose=_fake_compose))
    monkeypatch.setattr(
        runner,
        "_ensure_driving_clip_contract",
        lambda **kwargs: (kwargs["source_video"], "passed_through", kwargs["target_duration_seconds"]),
    )
    _patch_runner_execution(monkeypatch, captured)
    monkeypatch.setattr(runner, "_validate_driving_clip", _fake_validate)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "liveportrait_runner",
            "--source_image",
            str(paths["source_image"]),
            "--output_path",
            str(paths["output_path"]),
            "--liveportrait_home",
            str(paths["lp_home"]),
            "--liveportrait_entrypoint",
            str(paths["lp_entrypoint"]),
            "--liveportrait_model_path",
            str(paths["lp_model"]),
            "--timeout_seconds",
            "30",
        ],
    )

    try:
        runner.main()
        assert False, "runner.main() should have raised RuntimeError"
    except RuntimeError as exc:
        assert "liveportrait_invalid_driving_clip" in str(exc)

    assert compose_profiles == ["default"]
    assert captured.get("run_called") is not True


def test_image_input_prefers_template_and_materializes_exact_requested_contract(tmp_path, monkeypatch):
    paths = _make_runtime_layout(tmp_path)
    captured: dict[str, object] = {"compose_called": False}
    template_path = tmp_path / "template.mp4"
    materialized_path = tmp_path / "materialized_template.mp4"
    template_path.write_bytes(b"template")
    materialized_path.write_bytes(b"materialized")

    def _should_not_compose(*_args, **_kwargs):
        captured["compose_called"] = True
        return False

    def _fake_ensure(**kwargs):
        captured["template_ensure"] = dict(kwargs)
        return materialized_path, "contract_materialized", 37.25

    def _fake_validate(*, path, expected_duration_seconds, requested_fps, target_frame_count, fps_validation_mode):
        return _driver_metrics(
            path,
            duration_seconds=expected_duration_seconds,
            fps=requested_fps,
            frame_count=target_frame_count,
            requested_fps=requested_fps,
            target_frame_count=target_frame_count,
            valid=True,
        )

    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_IMAGE_DRIVING_TEMPLATE", str(template_path))
    _patch_runner_execution(monkeypatch, captured)
    monkeypatch.setattr(runner, "_motion_composer", SimpleNamespace(compose=_should_not_compose))
    monkeypatch.setattr(runner, "_ensure_driving_clip_contract", _fake_ensure)
    monkeypatch.setattr(runner, "_validate_driving_clip", _fake_validate)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "liveportrait_runner",
            "--source_image",
            str(paths["source_image"]),
            "--output_path",
            str(paths["output_path"]),
            "--liveportrait_home",
            str(paths["lp_home"]),
            "--liveportrait_entrypoint",
            str(paths["lp_entrypoint"]),
            "--liveportrait_model_path",
            str(paths["lp_model"]),
            "--timeout_seconds",
            "30",
            "--fps",
            "16",
            "--target_frame_count",
            "596",
        ],
    )

    assert runner.main() == 0
    assert captured["compose_called"] is False

    ensure_kwargs = dict(captured.get("template_ensure") or {})
    assert ensure_kwargs.get("source_video") == template_path
    assert ensure_kwargs.get("target_duration_seconds") == 37.25
    assert ensure_kwargs.get("target_fps") == 16.0
    assert ensure_kwargs.get("always_materialize") is True
    assert str(ensure_kwargs.get("output_name") or "").startswith("image_template_drive_")

    driving_arg = _driving_arg_from_command(list(captured.get("cmd") or []))
    assert driving_arg == str(materialized_path)


def test_image_input_prefers_strongest_valid_asset_template(tmp_path, monkeypatch):
    paths = _make_runtime_layout(tmp_path)
    captured: dict[str, object] = {"compose_called": False}
    template_a = tmp_path / "asset_a.mp4"
    template_b = tmp_path / "asset_b.mp4"
    candidate_a = tmp_path / "asset_a.materialized.mp4"
    candidate_b = tmp_path / "asset_b.materialized.mp4"
    for path in (template_a, template_b, candidate_a, candidate_b):
        path.write_bytes(b"video")

    def _should_not_compose(*_args, **_kwargs):
        captured["compose_called"] = True
        return False

    def _fake_discover(*, liveportrait_home, repo_root):
        return [("asset:auto_a", template_a), ("asset:auto_b", template_b)]

    def _fake_ensure(**kwargs):
        source_video = kwargs["source_video"]
        if source_video == template_a:
            return candidate_a, "contract_materialized", 37.25
        return candidate_b, "contract_materialized", 37.25

    def _fake_validate(*, path, expected_duration_seconds, requested_fps, target_frame_count, fps_validation_mode):
        if Path(path) == candidate_a:
            return _driver_metrics(
                path,
                duration_seconds=expected_duration_seconds,
                fps=16.0,
                frame_count=target_frame_count,
                requested_fps=requested_fps,
                target_frame_count=target_frame_count,
                unique_frames=596,
                unique_ratio=1.0,
                mean_mad=0.5,
                valid=True,
            )
        return _driver_metrics(
            path,
            duration_seconds=expected_duration_seconds,
            fps=16.0,
            frame_count=target_frame_count,
            requested_fps=requested_fps,
            target_frame_count=target_frame_count,
            unique_frames=295,
            unique_ratio=0.49,
            mean_mad=2.8,
            valid=True,
        )

    _patch_runner_execution(monkeypatch, captured)
    monkeypatch.setattr(runner, "_motion_composer", SimpleNamespace(compose=_should_not_compose))
    monkeypatch.setattr(runner, "_discover_image_driving_templates", _fake_discover)
    monkeypatch.setattr(runner, "_ensure_driving_clip_contract", _fake_ensure)
    monkeypatch.setattr(runner, "_validate_driving_clip", _fake_validate)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "liveportrait_runner",
            "--source_image",
            str(paths["source_image"]),
            "--output_path",
            str(paths["output_path"]),
            "--liveportrait_home",
            str(paths["lp_home"]),
            "--liveportrait_entrypoint",
            str(paths["lp_entrypoint"]),
            "--liveportrait_model_path",
            str(paths["lp_model"]),
            "--timeout_seconds",
            "30",
            "--fps",
            "16",
            "--target_frame_count",
            "596",
        ],
    )

    assert runner.main() == 0
    assert captured["compose_called"] is False

    driving_arg = _driving_arg_from_command(list(captured.get("cmd") or []))
    assert driving_arg == str(candidate_b)
