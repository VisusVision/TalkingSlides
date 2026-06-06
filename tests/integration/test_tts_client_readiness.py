import importlib
from pathlib import Path


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_wait_for_tts_ready_eventually_true(monkeypatch):
    tts_client = importlib.import_module("tts_client")

    responses = [
        _Resp(503, {"detail": {"xtts_state": "warming_up"}}),
        _Resp(503, {"detail": {"xtts_state": "warming_up"}}),
        _Resp(200, {"status": "ready"}),
    ]

    def fake_get(*_args, **_kwargs):
        return responses.pop(0)

    monkeypatch.setattr(tts_client.requests, "get", fake_get)
    monkeypatch.setattr(tts_client.time, "sleep", lambda *_args, **_kwargs: None)

    assert tts_client.wait_for_tts_ready(timeout_sec=5) is True


def test_synthesize_with_service_when_not_ready_uses_fallback(monkeypatch, tmp_path):
    tts_client = importlib.import_module("tts_client")

    out_path = tmp_path / "slide.mp3"

    monkeypatch.setattr(tts_client, "wait_for_tts_ready", lambda timeout_sec: False)

    def fake_fallback(path, duration_sec=3.0):
        p = Path(path)
        p.write_bytes(b"fallback")
        return str(p)

    monkeypatch.setattr(tts_client, "_write_silent_fallback", fake_fallback)

    post_called = {"value": False}

    def fake_post(*_args, **_kwargs):
        post_called["value"] = True
        raise AssertionError("requests.post should not be called when readiness fails")

    monkeypatch.setattr(tts_client.requests, "post", fake_post)

    result = tts_client.synthesize_with_service(
        voice_id="voice1",
        text="Hello world",
        out_path=str(out_path),
        lang="en",
    )

    assert result == str(out_path)
    assert out_path.exists()
    assert out_path.read_bytes() == b"fallback"
    assert post_called["value"] is False


def test_synthesize_with_service_accepts_service_fallback_audio(monkeypatch, tmp_path):
    tts_client = importlib.import_module("tts_client")

    out_path = tmp_path / "service-fallback.mp3"
    monkeypatch.setattr(tts_client, "wait_for_tts_ready", lambda timeout_sec: True)

    class _PostResp:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "audio_url": "http://tts_service:8001/audio/fallback.mp3",
                "provider": "fallback",
                "duration": 3.0,
                "message": "synthetic fallback",
            }

    monkeypatch.setattr(tts_client.requests, "post", lambda *_args, **_kwargs: _PostResp())
    monkeypatch.setattr(tts_client, "_download_to_file", lambda _url, path: Path(path).write_bytes(b"service-fallback"))

    meta = tts_client.synthesize_with_service_with_metadata(
        voice_id="voice1",
        text="Hello world",
        out_path=str(out_path),
        lang="en",
    )

    assert meta["provider"] == "fallback"
    assert out_path.read_bytes() == b"service-fallback"


def test_synthesize_with_service_preserves_xtts_recovery_metadata(monkeypatch, tmp_path):
    tts_client = importlib.import_module("tts_client")

    out_path = tmp_path / "xtts-recovery-meta.mp3"
    monkeypatch.setattr(tts_client, "wait_for_tts_ready", lambda timeout_sec: True)

    class _PostResp:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "audio_url": "http://tts_service:8001/audio/gtts.mp3",
                "provider": "gTTS",
                "duration": 2.0,
                "message": "legacy message should not win",
                "fallback_used": True,
                "fallback_reason": "xtts_v2_temporarily_unavailable: transient_model_load_network_error",
                "xtts_error_transient": True,
                "xtts_attempts": 3,
                "xtts_recovery_attempts": 2,
                "xtts_failure_reason": "HTTPSConnectionPool read timed out",
            }

    monkeypatch.setattr(tts_client.requests, "post", lambda *_args, **_kwargs: _PostResp())
    monkeypatch.setattr(tts_client, "_download_to_file", lambda _url, path: Path(path).write_bytes(b"gtts"))

    meta = tts_client.synthesize_with_service_with_metadata(
        voice_id="voice1",
        text="Hello world",
        out_path=str(out_path),
        lang="en",
    )

    assert meta["provider"] == "gtts"
    assert meta["fallback_reason"] == "xtts_v2_temporarily_unavailable: transient_model_load_network_error"
    assert meta["xtts_error_transient"] is True
    assert meta["xtts_attempts"] == 3
    assert meta["xtts_recovery_attempts"] == 2
    assert meta["xtts_failure_reason"] == "HTTPSConnectionPool read timed out"


def test_synthesize_with_service_maps_tts_settings_to_payload(monkeypatch, tmp_path):
    tts_client = importlib.import_module("tts_client")

    out_path = tmp_path / "mapped-settings.mp3"
    payloads = []
    monkeypatch.setattr(tts_client, "wait_for_tts_ready", lambda timeout_sec: True)

    class _PostResp:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "audio_url": "http://tts_service:8001/audio/mapped.mp3",
                "provider": "xtts_v2",
                "duration": 1.0,
            }

    def fake_post(_url, json, **_kwargs):
        payloads.append(json)
        return _PostResp()

    monkeypatch.setattr(tts_client.requests, "post", fake_post)
    monkeypatch.setattr(tts_client, "_download_to_file", lambda _url, path: Path(path).write_bytes(b"mapped"))

    meta = tts_client.synthesize_with_service_with_metadata(
        voice_id="voice1",
        text="pipeline demo",
        out_path=str(out_path),
        lang="en",
        tts_settings={
            "provider_preference": "gtts",
            "normalization_enabled": True,
            "normalization_mode": "strict",
            "unknown_word_strategy": "phonetic",
            "overrides": {
                "technical": {"pipeline": "pay pline"},
                "abbreviation": {"GPU": "jee pee you"},
                "mixed_word": {"ChatGPT": "chat gpt"},
            },
            "speech_speed": 1.1,
            "volume_gain_db": -2,
            "pause_seconds": 1.5,
        },
    )

    assert out_path.read_bytes() == b"mapped"
    assert payloads
    payload = payloads[0]
    assert payload["already_prepared"] is True
    assert payload["normalization_enabled"] is True
    assert payload["normalization_mode"] == "strict"
    assert payload["unknown_word_strategy"] == "phonetic"
    assert payload["provider_preference"] == "gtts"
    assert payload["technical_overrides"] == {"pipeline": "pay pline"}
    assert payload["abbreviation_overrides"] == {"GPU": "jee pee you"}
    assert payload["mixed_word_overrides"] == {"ChatGPT": "chat gpt"}
    assert "pay pline" in payload["text"]
    assert meta["provider_preference"] == "gtts"
    assert meta["applied_overrides"]["technical_count"] == 1
    assert meta["speech_speed"] == 1.1
    assert meta["volume_gain_db"] == -2
    assert meta["pause_seconds"] == 1.5


def test_tts_settings_provider_preference_does_not_hard_fail_on_service_fallback(monkeypatch, tmp_path):
    tts_client = importlib.import_module("tts_client")

    out_path = tmp_path / "preferred-provider-fallback.mp3"
    monkeypatch.setattr(tts_client, "wait_for_tts_ready", lambda timeout_sec: True)

    class _PostResp:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "audio_url": "http://tts_service:8001/audio/gtts.mp3",
                "provider": "gTTS",
                "duration": 1.0,
                "fallback_used": True,
                "fallback_reason": "xtts_unavailable",
            }

    monkeypatch.setattr(tts_client.requests, "post", lambda *_args, **_kwargs: _PostResp())
    monkeypatch.setattr(tts_client, "_download_to_file", lambda _url, path: Path(path).write_bytes(b"gtts"))

    meta = tts_client.synthesize_with_service_with_metadata(
        voice_id="voice1",
        text="Hello world",
        out_path=str(out_path),
        lang="en",
        tts_settings={"provider_preference": "xtts_v2"},
    )

    assert meta["provider"] == "gtts"
    assert meta["provider_preference"] == "xtts_v2"
    assert meta["fallback_used"] is True
    assert meta["fallback_reason"] == "xtts_unavailable"
    assert out_path.read_bytes() == b"gtts"


# ---------------------------------------------------------------------------
# Phase 1 — preview helper readiness / fail-open tests
# ---------------------------------------------------------------------------

def test_preview_fails_open_when_service_unavailable(monkeypatch):
    """
    When the TTS service is unreachable, preview_tts_text_with_metadata must
    not raise and must return fallback_used=True.
    """
    tts_client = importlib.import_module("tts_client")

    def fail_connect(*args, **kwargs):
        import requests as _r
        raise _r.ConnectionError("Connection refused")

    monkeypatch.setattr(tts_client.requests, "post", fail_connect)

    result = tts_client.preview_tts_text_with_metadata(
        text="Hello world",
        language="en",
    )

    assert isinstance(result, dict), "preview_tts_text_with_metadata must return a dict"
    assert result.get("fallback_used") is True, "fallback_used must be True when service is down"
    assert result.get("original_text") == "Hello world"
    assert result.get("spoken_text"), "spoken_text must not be empty on fail-open"


def test_preview_passes_through_service_response(monkeypatch):
    """
    When the TTS service is available, the response is passed through with
    fallback_used defaulting to False.
    """
    tts_client = importlib.import_module("tts_client")

    class _MockPreviewResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "original_text": "Hello world",
                "normalized_text": "Hello world.",
                "spoken_text": "Hello world.",
                "chunks": ["Hello world."],
                "chunk_pause_ms": [0],
                "tts_normalization_language": "en",
                "tts_normalization_rules_applied": [],
                "normalization_enabled": True,
                "normalization_mode": "loose",
                "unknown_word_strategy": "keep",
                "applied_overrides": {},
                "warnings": [],
                "error": None,
                # fallback_used intentionally omitted to test .setdefault()
            }

    monkeypatch.setattr(tts_client.requests, "post", lambda *a, **kw: _MockPreviewResp())

    result = tts_client.preview_tts_text_with_metadata(
        text="Hello world",
        language="en",
    )

    assert result["fallback_used"] is False
    assert result["error"] is None
    assert result["spoken_text"] == "Hello world."
