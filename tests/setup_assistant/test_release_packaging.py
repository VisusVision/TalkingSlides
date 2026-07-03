from __future__ import annotations

import importlib.util
import os
import stat
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RELEASE_HELPER = ROOT / "packaging" / "setup_assistant" / "release.py"


def _load_release_helper():
    spec = importlib.util.spec_from_file_location("setup_assistant_release_helper", RELEASE_HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load_release_helper()


def test_next_development_version_is_0_2_0_dev() -> None:
    assert release.DEFAULT_DEVELOPMENT_VERSION == "0.2.0-dev"


def test_release_tag_parsing_accepts_only_setup_assistant_semver_tags() -> None:
    assert release.parse_release_tag("setup-assistant-v0.1.0") == "0.1.0"
    assert release.parse_release_tag("setup-assistant-v12.34.56") == "12.34.56"
    for tag in (
        "v0.1.0",
        "setup-assistant-v",
        "setup-assistant-v1.2",
        "setup-assistant-v01.2.3",
        "setup-assistant-v1.02.3",
        "setup-assistant-v1.2.03",
        "setup-assistant-v1.2.3-beta",
    ):
        with pytest.raises(ValueError):
            release.parse_release_tag(tag)


def test_version_resolution_derives_release_version_only_from_matching_tags() -> None:
    tagged = release.resolve_version(
        {
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF_TYPE": "tag",
            "GITHUB_REF_NAME": "setup-assistant-v0.1.0",
            "GITHUB_REF": "refs/tags/setup-assistant-v0.1.0",
        }
    )
    assert tagged.version == "0.1.0"
    assert tagged.tag == "setup-assistant-v0.1.0"
    assert tagged.is_release is True

    manual_tag = release.resolve_version(
        {
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF_TYPE": "tag",
            "GITHUB_REF_NAME": "setup-assistant-v0.1.0",
        }
    )
    assert manual_tag.version == "0.1.0"
    assert manual_tag.is_release is False

    pull_request = release.resolve_version(
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REF_TYPE": "branch",
            "GITHUB_REF_NAME": "123/merge",
            "GITHUB_REF": "refs/pull/123/merge",
        }
    )
    assert pull_request.version == release.DEFAULT_DEVELOPMENT_VERSION
    assert pull_request.tag == ""
    assert pull_request.is_release is False


def test_final_release_artifact_names_are_versioned_by_product_os_and_architecture() -> None:
    assert release.release_artifact_names("0.1.0") == (
        "TalkingSlides-Setup-0.1.0-windows-x64.exe",
        "talkingslides-setup-cli-0.1.0-windows-x64.exe",
        "TalkingSlides-Setup-0.1.0-linux-x64.tar.gz",
    )


def test_windows_assembly_copies_only_final_named_executables(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    output = tmp_path / "release"
    (dist / "TalkingSlides-Setup.exe").parent.mkdir(parents=True)
    (dist / "TalkingSlides-Setup.exe").write_bytes(b"gui")
    (dist / "talkingslides-setup-cli.exe").write_bytes(b"cli")
    (dist / ".env").write_text("SECRET_KEY=bad\n", encoding="utf-8")

    assembled = release.assemble_windows(dist, output, "0.1.0")

    assert [path.name for path in assembled] == [
        "TalkingSlides-Setup-0.1.0-windows-x64.exe",
        "talkingslides-setup-cli-0.1.0-windows-x64.exe",
    ]
    assert sorted(path.name for path in output.iterdir()) == [
        "TalkingSlides-Setup-0.1.0-windows-x64.exe",
        "talkingslides-setup-cli-0.1.0-windows-x64.exe",
    ]


def test_linux_archive_is_minimal_deterministic_and_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dist = tmp_path / "dist"
    output = tmp_path / "release"
    dist.mkdir()
    for name in ("TalkingSlides-Setup", "talkingslides-setup"):
        target = dist / name
        target.write_bytes(f"{name}\n".encode("utf-8"))
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (dist / ".env").write_text("SECRET_KEY=bad\n", encoding="utf-8")
    (dist / "models").mkdir()
    (dist / "models" / "large.bin").write_bytes(b"not included")
    monkeypatch.setattr(release.os, "access", lambda _path, _mode: True)

    first = release.assemble_linux(dist, output, "0.1.0")[0]
    first_bytes = first.read_bytes()
    second = release.assemble_linux(dist, output, "0.1.0")[0]

    assert first_bytes == second.read_bytes()
    with tarfile.open(second, "r:gz") as archive:
        members = archive.getmembers()
    assert [member.name for member in members] == ["TalkingSlides-Setup", "talkingslides-setup"]
    assert all(member.mode & 0o111 == 0o111 for member in members)


def test_checksum_generation_requires_complete_unique_release_artifact_set(tmp_path: Path) -> None:
    for name in release.release_artifact_names("0.1.0"):
        (tmp_path / name).write_bytes(name.encode("utf-8"))

    checksum = release.write_sha256sums(tmp_path, "0.1.0")
    release.validate_sha256sums(tmp_path, "0.1.0")
    lines = checksum.read_text(encoding="utf-8").splitlines()
    assert [line.split("  ", 1)[1] for line in lines] == list(release.release_artifact_names("0.1.0"))

    duplicate_dir = tmp_path / "nested"
    duplicate_dir.mkdir()
    duplicate = duplicate_dir / release.release_artifact_names("0.1.0")[0]
    duplicate.write_bytes(b"duplicate")
    with pytest.raises(ValueError, match="Duplicate"):
        release.validate_artifact_set(tmp_path, "0.1.0")
    duplicate.unlink()

    (tmp_path / release.release_artifact_names("0.1.0")[1]).unlink()
    with pytest.raises(ValueError, match="Missing"):
        release.validate_artifact_set(tmp_path, "0.1.0")

    (tmp_path / "unexpected.bin").write_bytes(b"extra")
    with pytest.raises(ValueError, match="Missing"):
        release.validate_artifact_set(tmp_path, "0.1.0")


def test_release_notes_rendering_replaces_version_and_tag(tmp_path: Path) -> None:
    template = tmp_path / "template.md"
    output = tmp_path / "notes.md"
    template.write_text("Version {{VERSION}}\nTag {{TAG}}\n", encoding="utf-8")

    release.render_release_notes("0.1.0", "setup-assistant-v0.1.0", output, template)

    assert output.read_text(encoding="utf-8") == "Version 0.1.0\nTag setup-assistant-v0.1.0\n"


def test_version_runtime_hook_embeds_display_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hook = release.write_version_runtime_hook("0.1.0", tmp_path / "hook.py")
    monkeypatch.delenv(release.DISPLAY_VERSION_ENV, raising=False)

    exec(hook.read_text(encoding="utf-8"), {})

    assert os.environ[release.DISPLAY_VERSION_ENV] == "0.1.0"


def test_publish_requires_github_token_before_network_use(tmp_path: Path) -> None:
    for name in release.release_artifact_names("0.1.0"):
        (tmp_path / name).write_bytes(name.encode("utf-8"))
    release.write_sha256sums(tmp_path, "0.1.0")
    notes = tmp_path / release.RELEASE_NOTES_NAME
    notes.write_text("notes\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        release.publish_github_release(
            tmp_path,
            "0.1.0",
            "setup-assistant-v0.1.0",
            notes,
            env={"GITHUB_REPOSITORY": "VisusVision/TalkingSlides"},
        )
