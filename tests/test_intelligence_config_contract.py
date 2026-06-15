from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_local_intelligence_profiles_keep_analytics_on_small_model_with_local_budget() -> None:
    settings_source = (REPO_ROOT / "services" / "api" / "config" / "settings.py").read_text(encoding="utf-8")

    assert '"analytics_model": "qwen2.5:3b"' in settings_source
    assert '"analytics_model": "qwen2.5:7b"' not in settings_source
    assert '"chunk_timeout_min": "30"' in settings_source
    assert '"chunk_timeout_max": "75"' in settings_source
    assert '"analytics_background_max": "180"' in settings_source
    assert "INTELLIGENCE_OLLAMA_CALIBRATION_ENABLED" in settings_source
    assert (
        'os.environ.get("ANALYTICS_INTELLIGENCE_MAX_BACKGROUND_SECONDS", '
        '_INTELLIGENCE_PROFILE["analytics_background_max"])'
    ) in settings_source
