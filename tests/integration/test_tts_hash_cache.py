from __future__ import annotations

import importlib
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TTS_ROOT = REPO_ROOT / "services" / "tts_service"
if str(TTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TTS_ROOT))

from tts_cache import TTSHashCacheStore, deterministic_cache_key  # noqa: E402


def _load_main():
    if "main" in sys.modules:
        return importlib.reload(sys.modules["main"])
    return importlib.import_module("main")


def test_deterministic_key_stability():
    payload = {"a": 1, "b": {"x": True, "y": "z"}}
    assert deterministic_cache_key(payload) == deterministic_cache_key({"b": {"y": "z", "x": True}, "a": 1})


def test_cache_miss_then_hit(monkeypatch, tmp_path):
    tts_main = _load_main()
    cache = TTSHashCacheStore(tmp_path / "tts_cache")
    monkeypatch.setattr(tts_main, "TTS_HASH_CACHE", cache)
    monkeypatch.setattr(tts_main, "TTS_CACHE_ENABLED", True)

    synth_calls = {"gtts": 0}

    def fake_gtts(_text, _lang, out_path):
        synth_calls["gtts"] += 1
        out_path.write_bytes(b"ID3test")
        return 1.0

    monkeypatch.setattr(tts_main, "_synthesize_xtts_v2_with_recovery", lambda *args, **kwargs: (None, {"fallback_reason": "xtts_v2_failed"}))
    monkeypatch.setattr(tts_main, "_synthesize_gtts", fake_gtts)
    monkeypatch.setattr(tts_main, "_audio_duration", lambda _path: 1.0)

    req = tts_main.SynthesizeRequest(text="Cache me", voice_id="", language="en")
    first = tts_main.synthesize(req)
    second = tts_main.synthesize(req)
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert synth_calls["gtts"] == 1


def test_corrupted_artifact_recovery(monkeypatch, tmp_path):
    tts_main = _load_main()
    cache = TTSHashCacheStore(tmp_path / "tts_cache")
    monkeypatch.setattr(tts_main, "TTS_HASH_CACHE", cache)
    monkeypatch.setattr(tts_main, "TTS_CACHE_ENABLED", True)

    key = deterministic_cache_key({"k": "v"})
    artifact = cache.artifact_path(key)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"")
    cache.sidecar_path(key).write_text("{}", encoding="utf-8")

    lookup = cache.lookup(key)
    assert lookup.hit is False
    assert "corrupted" in lookup.reason
    assert not artifact.exists()

    monkeypatch.setattr(tts_main, "_synthesize_xtts_v2_with_recovery", lambda *args, **kwargs: (None, {"fallback_reason": "xtts_v2_failed"}))
    monkeypatch.setattr(tts_main, "_synthesize_gtts", lambda _text, _lang, out_path: out_path.write_bytes(b"ID3ok") or 1.0)
    monkeypatch.setattr(tts_main, "_audio_duration", lambda _path: 1.0)
    req = tts_main.SynthesizeRequest(text="Corrupt recover", voice_id="", language="en")
    result = tts_main.synthesize(req)
    assert result["provider"] == "gTTS"


def test_concurrent_lookup_race_single_synthesis(monkeypatch, tmp_path):
    tts_main = _load_main()
    cache = TTSHashCacheStore(tmp_path / "tts_cache", lock_timeout_seconds=10.0)
    monkeypatch.setattr(tts_main, "TTS_HASH_CACHE", cache)
    monkeypatch.setattr(tts_main, "TTS_CACHE_ENABLED", True)
    monkeypatch.setattr(tts_main, "_synthesize_xtts_v2_with_recovery", lambda *args, **kwargs: (None, {"fallback_reason": "xtts_v2_failed"}))
    monkeypatch.setattr(tts_main, "_audio_duration", lambda _path: 1.0)

    calls = {"gtts": 0}
    lock = threading.Lock()

    def slow_gtts(_text, _lang, out_path):
        with lock:
            calls["gtts"] += 1
        time.sleep(0.15)
        out_path.write_bytes(b"ID3race")
        return 1.0

    monkeypatch.setattr(tts_main, "_synthesize_gtts", slow_gtts)
    req = tts_main.SynthesizeRequest(text="race text", voice_id="", language="en")

    results: list[dict] = []

    def run():
        results.append(tts_main.synthesize(req))

    t1 = threading.Thread(target=run)
    t2 = threading.Thread(target=run)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert calls["gtts"] == 1
    assert len(results) == 2
    assert any(item.get("cache_hit") is True for item in results)
    assert any(item.get("cache_hit") is False for item in results)


def test_sidecar_metadata_creation(monkeypatch, tmp_path):
    tts_main = _load_main()
    cache = TTSHashCacheStore(tmp_path / "tts_cache")
    monkeypatch.setattr(tts_main, "TTS_HASH_CACHE", cache)
    monkeypatch.setattr(tts_main, "TTS_CACHE_ENABLED", True)
    monkeypatch.setattr(tts_main, "_synthesize_xtts_v2_with_recovery", lambda *args, **kwargs: (None, {"fallback_reason": "xtts_v2_failed"}))
    monkeypatch.setattr(tts_main, "_audio_duration", lambda _path: 1.0)
    monkeypatch.setattr(tts_main, "_synthesize_gtts", lambda _text, _lang, out_path: out_path.write_bytes(b"ID3meta") or 1.0)

    req = tts_main.SynthesizeRequest(text="sidecar", voice_id="", language="en")
    result = tts_main.synthesize(req)
    key = result["cache_key"]
    sidecar = cache.sidecar_path(key)
    payload = __import__("json").loads(sidecar.read_text(encoding="utf-8"))
    for field in ("hash", "created_at", "provider", "model", "duration", "file_size"):
        assert field in payload
