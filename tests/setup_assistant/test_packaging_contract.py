from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_windows_gui_and_cli_names_do_not_collide() -> None:
    gui = (ROOT / "packaging" / "setup_assistant" / "TalkingSlides-Setup-GUI.spec").read_text(encoding="utf-8")
    cli = (ROOT / "packaging" / "setup_assistant" / "talkingslides-setup-cli.spec").read_text(encoding="utf-8")
    assert 'name="TalkingSlides-Setup"' in gui
    assert '"talkingslides-setup-cli" if sys.platform.startswith("win")' in cli
    assert '"talkingslides-setup"' in cli
    assert "gui_entry.py" in gui
    assert "cli_entry.py" in cli


def test_cli_package_excludes_gui_dependency() -> None:
    spec = (ROOT / "packaging" / "setup_assistant" / "talkingslides-setup-cli.spec").read_text(encoding="utf-8")
    assert '"PySide6"' in spec
    assert '"tools.setup_assistant.gui"' in spec


def test_packaging_workflow_tests_before_build_and_uses_target_matrix() -> None:
    workflow = (ROOT / ".github" / "workflows" / "setup-assistant-package.yml").read_text(encoding="utf-8")
    assert workflow.index("Run setup-assistant tests") < workflow.index("Build target-native executables")
    assert "windows-latest" in workflow
    assert "ubuntu-latest" in workflow
    assert "TalkingSlides-Setup-0.1.0-" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "check --profile core --repository . --json" in workflow
    assert "report --profile core --repository . --format json" in workflow


def test_packaging_does_not_track_generated_artifacts() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "build/" in gitignore
    assert "dist/" in gitignore
