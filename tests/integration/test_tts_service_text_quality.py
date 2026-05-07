import importlib
import sys
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - environment-dependent path
    TestClient = None


REPO_ROOT = Path(__file__).resolve().parents[2]
TTS_ROOT = REPO_ROOT / "services" / "tts_service"
if str(TTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TTS_ROOT))


def test_split_text_for_turkish_and_abbreviations():
    tts_main = importlib.import_module("main")

    text = (
        "Dr. Ahmet bugün konuştu. "
        "Bu, oldukça uzun bir Türkçe paragraftır ve anlamı koruyarak bölünmelidir. "
        "Peki neden? Çünkü sentez akışı doğal kalmalıdır!"
    )

    chunks = tts_main._split_text_for_tts(text, max_chars=90)
    assert chunks
    assert all(len(c) <= 90 for c in chunks)
    assert any("Doctor Ahmet" in c for c in chunks)


def test_synthesize_falls_back_to_gtts_when_xtts_fails(monkeypatch, tmp_path):
    tts_main = importlib.import_module("main")

    out_file = tmp_path / "out.mp3"

    # Keep request/model path lightweight
    monkeypatch.setattr(tts_main, "_new_audio_path", lambda: out_file)

    def fake_xtts(*_args, **_kwargs):
        raise RuntimeError("synthetic xtts failure")

    gtts_inputs = []

    def fake_gtts(text, _lang, out_path):
        gtts_inputs.append(text)
        out_path.write_bytes(b"gtts")
        return 1.23

    monkeypatch.setattr(tts_main, "_synthesize_xtts_v2", fake_xtts)
    monkeypatch.setattr(tts_main, "_synthesize_gtts", fake_gtts)

    req = tts_main.SynthesizeRequest(text="Merhaba dünya.", voice_id="voice1", language="tr")
    data = tts_main.synthesize(req)

    assert data["provider"] == "gTTS"
    assert out_file.exists()
    assert gtts_inputs == ["Merhaba dünya."]
    assert "ü" in gtts_inputs[0]


def test_transient_xtts_failure_resets_and_retries_then_succeeds(monkeypatch, tmp_path):
    tts_main = importlib.import_module("main")

    out_file = tmp_path / "xtts-retry.mp3"
    monkeypatch.setattr(tts_main, "_new_audio_path", lambda: out_file)
    monkeypatch.setattr(tts_main, "XTTS_LOAD_RECOVERY_ATTEMPTS", 2)
    monkeypatch.setattr(tts_main, "XTTS_LOAD_RECOVERY_BACKOFF_SEC", 0)

    reset_reasons = []
    monkeypatch.setattr(tts_main, "reset_xtts_model_state", lambda reason="": reset_reasons.append(reason))

    calls = []

    def fake_xtts(_text, _voice_id, _lang, out_path, chunks=None, chunk_pause_ms=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("HTTPSConnectionPool read timed out")
        out_path.write_bytes(b"xtts")
        return 1.5

    def fail_gtts(*_args, **_kwargs):
        raise AssertionError("gTTS should not be used when XTTS retry succeeds")

    monkeypatch.setattr(tts_main, "_synthesize_xtts_v2", fake_xtts)
    monkeypatch.setattr(tts_main, "_synthesize_gtts", fail_gtts)

    req = tts_main.SynthesizeRequest(text="Hello world.", voice_id="voice1", language="en")
    data = tts_main.synthesize(req)

    assert data["provider"] == "xtts_v2"
    assert data["fallback_used"] is False
    assert data["xtts_attempts"] == 2
    assert data["xtts_recovery_attempts"] == 1
    assert len(calls) == 2
    assert len(reset_reasons) == 1
    assert out_file.read_bytes() == b"xtts"


def test_transient_xtts_failure_exhausts_retries_then_falls_back(monkeypatch, tmp_path):
    tts_main = importlib.import_module("main")

    out_file = tmp_path / "transient-gtts.mp3"
    monkeypatch.setattr(tts_main, "_new_audio_path", lambda: out_file)
    monkeypatch.setattr(tts_main, "XTTS_LOAD_RECOVERY_ATTEMPTS", 2)
    monkeypatch.setattr(tts_main, "XTTS_LOAD_RECOVERY_BACKOFF_SEC", 0)

    reset_reasons = []
    monkeypatch.setattr(tts_main, "reset_xtts_model_state", lambda reason="": reset_reasons.append(reason))

    calls = []

    def fake_xtts(*_args, **_kwargs):
        calls.append(1)
        raise RuntimeError("Connection aborted by remote disconnected peer")

    def fake_gtts(_text, _lang, out_path):
        out_path.write_bytes(b"gtts")
        return 2.0

    monkeypatch.setattr(tts_main, "_synthesize_xtts_v2", fake_xtts)
    monkeypatch.setattr(tts_main, "_synthesize_gtts", fake_gtts)

    req = tts_main.SynthesizeRequest(text="Hello world.", voice_id="voice1", language="en")
    data = tts_main.synthesize(req)

    assert data["provider"] == "gTTS"
    assert data["fallback_used"] is True
    assert data["fallback_reason"] == "xtts_v2_temporarily_unavailable: transient_model_load_network_error"
    assert data["xtts_error_transient"] is True
    assert data["xtts_attempts"] == 3
    assert data["xtts_recovery_attempts"] == 2
    assert len(calls) == 3
    assert len(reset_reasons) == 2
    assert out_file.read_bytes() == b"gtts"


def test_non_transient_xtts_failure_does_not_retry(monkeypatch, tmp_path):
    tts_main = importlib.import_module("main")

    out_file = tmp_path / "non-transient-gtts.mp3"
    monkeypatch.setattr(tts_main, "_new_audio_path", lambda: out_file)
    monkeypatch.setattr(tts_main, "XTTS_LOAD_RECOVERY_ATTEMPTS", 2)
    monkeypatch.setattr(tts_main, "XTTS_LOAD_RECOVERY_BACKOFF_SEC", 0)
    monkeypatch.setattr(
        tts_main,
        "reset_xtts_model_state",
        lambda reason="": (_ for _ in ()).throw(AssertionError("non-transient errors must not reset/retry")),
    )

    calls = []

    def fake_xtts(*_args, **_kwargs):
        calls.append(1)
        raise RuntimeError("invalid speaker reference format")

    def fake_gtts(_text, _lang, out_path):
        out_path.write_bytes(b"gtts")
        return 2.0

    monkeypatch.setattr(tts_main, "_synthesize_xtts_v2", fake_xtts)
    monkeypatch.setattr(tts_main, "_synthesize_gtts", fake_gtts)

    req = tts_main.SynthesizeRequest(text="Hello world.", voice_id="voice1", language="en")
    data = tts_main.synthesize(req)

    assert data["provider"] == "gTTS"
    assert data["fallback_used"] is True
    assert data["fallback_reason"].startswith("xtts_v2_failed: invalid speaker reference format")
    assert data["xtts_error_transient"] is False
    assert data["xtts_attempts"] == 1
    assert data["xtts_recovery_attempts"] == 0
    assert len(calls) == 1


def test_missing_reference_voice_does_not_retry_and_falls_back(monkeypatch, tmp_path):
    tts_main = importlib.import_module("main")

    out_file = tmp_path / "missing-reference-gtts.mp3"
    monkeypatch.setattr(tts_main, "_new_audio_path", lambda: out_file)
    monkeypatch.setattr(tts_main, "XTTS_LOAD_RECOVERY_ATTEMPTS", 2)
    monkeypatch.setattr(tts_main, "XTTS_LOAD_RECOVERY_BACKOFF_SEC", 0)
    monkeypatch.setattr(
        tts_main,
        "reset_xtts_model_state",
        lambda reason="": (_ for _ in ()).throw(AssertionError("missing references must not reset/retry")),
    )

    calls = []

    def fake_xtts(*_args, **_kwargs):
        calls.append(1)
        raise FileNotFoundError("Reference voice not found: /tmp/missing.wav")

    def fake_gtts(_text, _lang, out_path):
        out_path.write_bytes(b"gtts")
        return 2.0

    monkeypatch.setattr(tts_main, "_synthesize_xtts_v2", fake_xtts)
    monkeypatch.setattr(tts_main, "_synthesize_gtts", fake_gtts)

    req = tts_main.SynthesizeRequest(text="Hello world.", voice_id="missing", language="en")
    data = tts_main.synthesize(req)

    assert data["provider"] == "gTTS"
    assert data["fallback_used"] is True
    assert data["fallback_reason"] == "xtts_v2_unavailable: reference voice file not found"
    assert data["xtts_error_transient"] is False
    assert data["xtts_attempts"] == 1
    assert data["xtts_recovery_attempts"] == 0
    assert len(calls) == 1


def test_xtts_disabled_dev_mode_still_falls_back(monkeypatch, tmp_path):
    tts_main = importlib.import_module("main")

    out_file = tmp_path / "xtts-disabled-gtts.mp3"
    monkeypatch.setattr(tts_main, "_new_audio_path", lambda: out_file)
    monkeypatch.setattr(tts_main, "XTTS_ENABLED", False)
    monkeypatch.setattr(tts_main, "XTTS_LOAD_RECOVERY_ATTEMPTS", 2)
    monkeypatch.setattr(tts_main, "XTTS_LOAD_RECOVERY_BACKOFF_SEC", 0)

    calls = []

    def fake_xtts(*_args, **_kwargs):
        calls.append(1)
        raise RuntimeError("XTTS provider is disabled via XTTS_ENABLED=0")

    def fake_gtts(_text, _lang, out_path):
        out_path.write_bytes(b"gtts")
        return 2.0

    monkeypatch.setattr(tts_main, "_synthesize_xtts_v2", fake_xtts)
    monkeypatch.setattr(tts_main, "_synthesize_gtts", fake_gtts)

    req = tts_main.SynthesizeRequest(text="Hello world.", voice_id="voice1", language="en")
    data = tts_main.synthesize(req)

    assert data["provider"] == "gTTS"
    assert data["fallback_used"] is True
    assert data["fallback_reason"] == "xtts_v2_unavailable: disabled"
    assert data["xtts_error_transient"] is False
    assert data["xtts_attempts"] == 1
    assert data["xtts_recovery_attempts"] == 0
    assert len(calls) == 1


def test_synthesize_prefers_xtts_when_voice_id_is_available(monkeypatch, tmp_path):
    tts_main = importlib.import_module("main")

    out_file = tmp_path / "xtts.mp3"

    monkeypatch.setattr(tts_main, "_new_audio_path", lambda: out_file)

    xtts_inputs = []

    def fake_xtts(text, _voice_id, _lang, out_path, chunks=None, chunk_pause_ms=None):
        xtts_inputs.append((text, chunks, chunk_pause_ms))
        out_path.write_bytes(b"xtts")
        return 1.75

    def fail_gtts(*_args, **_kwargs):
        raise AssertionError("gTTS should not be used when XTTS succeeds")

    monkeypatch.setattr(tts_main, "_synthesize_xtts_v2", fake_xtts)
    monkeypatch.setattr(tts_main, "_synthesize_gtts", fail_gtts)

    req = tts_main.SynthesizeRequest(text="Merhaba dunya.", voice_id="voice1", language="tr")
    data = tts_main.synthesize(req)

    assert data["provider"] == "xtts_v2"
    assert data["duration"] == pytest.approx(1.75)
    assert out_file.exists()
    assert xtts_inputs
    assert xtts_inputs[0][1]


def test_synthesize_preprocesses_text_before_xtts(monkeypatch, tmp_path):
    tts_main = importlib.import_module("main")

    out_file = tmp_path / "xtts_preprocessed.mp3"
    monkeypatch.setattr(tts_main, "_new_audio_path", lambda: out_file)

    seen = {}

    def fake_xtts(text, _voice_id, _lang, out_path, chunks=None, chunk_pause_ms=None):
        seen["text"] = text
        seen["chunks"] = chunks
        seen["chunk_pause_ms"] = chunk_pause_ms
        out_path.write_bytes(b"xtts")
        return 2.0

    monkeypatch.setattr(tts_main, "_synthesize_xtts_v2", fake_xtts)

    req = tts_main.SynthesizeRequest(text="- Upload PPTX\n- Generate TTS with XTTS v2.", voice_id="voice1", language="en")
    data = tts_main.synthesize(req)

    assert data["provider"] == "xtts_v2"
    assert "power point file" in seen["text"]
    assert "T T S" in seen["text"]
    assert "X T T S version two" in seen["text"]
    assert seen["chunks"]
    assert seen["chunk_pause_ms"]


def test_already_prepared_chunks_skip_service_side_renormalization(monkeypatch):
    tts_main = importlib.import_module("main")

    def fail_prepare(*_args, **_kwargs):
        raise AssertionError("already prepared chunk requests must not be re-normalized")

    monkeypatch.setattr(tts_main, "prepare_text_for_tts", fail_prepare)

    req = tts_main.SynthesizeRequest(
        text="raw text should not be used here",
        voice_id="voice1",
        language="en",
        already_prepared=True,
        chunks=["JSON payload.", "Second chunk."],
        chunk_pause_ms=[250, 0],
    )
    prepared = tts_main._prepare_request_for_tts(req, "en")

    assert prepared.spoken_text == "JSON payload. Second chunk."
    assert prepared.chunks == ["JSON payload.", "Second chunk."]
    assert prepared.chunk_pause_ms == [250, 0]


def test_xtts_safe_chunk_resplitting_keeps_pause_count_aligned():
    tts_main = importlib.import_module("main")

    chunks, pauses = tts_main._ensure_xtts_safe_chunks(
        [
            "This prepared chunk is intentionally long, because it needs to be split into smaller XTTS chunks while keeping pauses aligned.",
            "Done.",
        ],
        [450, 0],
        90,
    )

    assert len(chunks) > 2
    assert len(pauses) == len(chunks)
    assert pauses[-1] == 0
    assert all(len(chunk) <= 90 for chunk in chunks)


def test_normalization_preview_override_precedence_and_no_synthesis(monkeypatch):
    tts_main = importlib.import_module("main")

    def fail_synthesize(*_args, **_kwargs):
        raise AssertionError("preview must not call synthesis")

    monkeypatch.setattr(tts_main, "_synthesize_xtts_v2", fail_synthesize)
    monkeypatch.setattr(tts_main, "_synthesize_gtts", fail_synthesize)

    payload = {
        "text": "ChatGPT AI pipeline",
        "language": "tr",
        "normalization_enabled": True,
        "technical_overrides": {"pipeline": "teknik akis"},
        "abbreviation_overrides": {"AI": "yapay zeka"},
        "mixed_word_overrides": {"ChatGPT": "chat gpt"},
    }

    if TestClient is not None:
        client = TestClient(tts_main.app)
        response = client.post("/normalization/preview", json=payload)
        assert response.status_code == 200
        data = response.json()
    else:
        req = tts_main.NormalizationPreviewRequest(**payload)
        response_model = tts_main.normalization_preview(req)
        if hasattr(response_model, "model_dump"):
            data = response_model.model_dump()
        else:  # pragma: no cover - pydantic v1 fallback
            data = response_model.dict()

    spoken = data["spoken_text"].lower()

    # Override protection fix: replacement values must NOT be re-normalized
    assert "chat gpt" in spoken, (
        f"Override 'chat gpt' was re-normalized in: {spoken!r}"
    )
    assert "yapay zeka" in spoken
    assert "teknik akis" in spoken
    # These should NOT appear because overrides are protected from re-normalization
    assert "chat ci pi ti" not in spoken, (
        f"'chat gpt' was incorrectly re-normalized to 'chat ci pi ti' in: {spoken!r}"
    )
    assert "çet ci pi ti" not in spoken
    assert "ey ay" not in spoken
    assert data["fallback_used"] is False
