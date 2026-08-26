from avatar.digital_twin.prosody import analyze_audio_prosody, build_prosody_profile


def test_prosody_profile_detects_pause_speech_and_emphasis_regions():
    sample_rate = 1_000
    samples = (
        [0] * 800
        + [3_000, -3_000] * 400
        + [18_000, -18_000] * 400
    )

    profile = build_prosody_profile(samples, sample_rate=sample_rate)

    styles = {segment["style"] for segment in profile["segments"]}
    assert profile["version"] == "prosody-v1"
    assert profile["accepted"] is True
    assert profile["duration_seconds"] == 2.4
    assert {"calm", "natural", "expressive"}.issubset(styles)
    assert any(segment["pause"] for segment in profile["segments"])
    assert any(segment["emphasis"] for segment in profile["segments"])
    assert profile["summary"]["speech_ratio"] > 0.5


def test_prosody_profile_is_deterministic_and_bounded():
    samples = ([0] * 400 + [12_000, -12_000] * 800) * 20

    first = build_prosody_profile(samples, sample_rate=2_000)
    second = build_prosody_profile(samples, sample_rate=2_000)

    assert first == second
    assert len(first["segments"]) <= 24
    assert sum(segment["duration_seconds"] for segment in first["segments"]) == first["duration_seconds"]


def test_near_silent_audio_is_an_explicit_safe_fallback():
    profile = build_prosody_profile([0] * 16_000, sample_rate=16_000)

    assert profile["accepted"] is False
    assert profile["status"] == "unavailable"
    assert profile["segments"] == []
    assert profile["warnings"] == ["prosody_audio_near_silent"]


def test_missing_audio_is_an_explicit_safe_fallback(tmp_path):
    profile = analyze_audio_prosody(tmp_path / "missing.mp3", duration_hint=3.5)

    assert profile["accepted"] is False
    assert profile["duration_seconds"] == 3.5
    assert profile["warnings"] == ["prosody_audio_missing"]
