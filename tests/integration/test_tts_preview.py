"""
tests/integration/test_tts_preview.py
======================================
Phase 1 — TTS preview endpoint tests.

Covers:
  - preview endpoint returns original_text and spoken/used text
  - disabled normalization returns original text as spoken/used text
  - abbreviation override beats default behavior for preview
  - technical override works for preview
  - mixed word override works for preview
  - TTS service unavailable path fails open from the Django/helper layer
  - import isolation: preview uses tts_preprocess, not old preprocess
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap (mirrors what tts_client does at runtime)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
TTS_ROOT = REPO_ROOT / "services" / "tts_service"
SCRIPTS_ROOT = REPO_ROOT / "services" / "scripts"

for _p in (TTS_ROOT, SCRIPTS_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ===========================================================================
# Helper — reload tts_client so monkeypatches take effect per-test
# ===========================================================================

def _get_tts_client():
    if "tts_client" in sys.modules:
        return sys.modules["tts_client"]
    return importlib.import_module("tts_client")


# ===========================================================================
# 1. TTS service preview endpoint: returns original_text + spoken_text
# ===========================================================================

class TestPreviewEndpointReturnsMetadata:

    def test_basic_preview_returns_original_and_spoken_text(self, monkeypatch):
        """Preview call returns original_text and spoken_text from the service."""
        tts_client = _get_tts_client()

        payload_sent: list[dict] = []

        class MockResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "original_text": "AI ve ChatGPT pipeline anlatımı",
                    "normalized_text": "AI ve ChatGPT pipeline anlatımı",
                    "spoken_text": "ey ay ve Çet Ci Pi Ti payplayn anlatımı",
                    "chunks": ["ey ay ve Çet Ci Pi Ti payplayn anlatımı"],
                    "chunk_pause_ms": [0],
                    "tts_normalization_language": "tr",
                    "tts_normalization_rules_applied": [
                        {"rule": "glossary", "term": "AI", "replacement": "ey ay", "count": 1},
                    ],
                    "normalization_enabled": True,
                    "normalization_mode": "loose",
                    "unknown_word_strategy": "keep",
                    "applied_overrides": {"merged_override_count": 0},
                    "warnings": [],
                    "error": None,
                    "fallback_used": False,
                }

        def fake_post(url, json, **kwargs):
            payload_sent.append(json)
            return MockResp()

        monkeypatch.setattr(tts_client.requests, "post", fake_post)

        result = tts_client.preview_tts_text_with_metadata(
            text="AI ve ChatGPT pipeline anlatımı",
            language="tr",
        )

        assert result["original_text"] == "AI ve ChatGPT pipeline anlatımı"
        assert "ey ay" in result["spoken_text"]
        assert result["fallback_used"] is False
        assert result["error"] is None
        assert payload_sent, "No POST was made to preview endpoint"

    def test_preview_endpoint_url_is_normalization_preview(self, monkeypatch):
        """The helper must call /normalization/preview, not /synthesize."""
        tts_client = _get_tts_client()
        urls_called: list[str] = []

        class MockResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "original_text": "hello",
                    "normalized_text": "hello",
                    "spoken_text": "hello",
                    "chunks": ["hello"],
                    "chunk_pause_ms": [0],
                    "tts_normalization_language": "en",
                    "tts_normalization_rules_applied": [],
                    "normalization_enabled": True,
                    "normalization_mode": "loose",
                    "unknown_word_strategy": "keep",
                    "applied_overrides": {},
                    "warnings": [],
                    "error": None,
                    "fallback_used": False,
                }

        def fake_post(url, **kwargs):
            urls_called.append(url)
            return MockResp()

        monkeypatch.setattr(tts_client.requests, "post", fake_post)
        tts_client.preview_tts_text_with_metadata(text="hello", language="en")

        assert urls_called, "No request was made"
        assert "/normalization/preview" in urls_called[0], (
            f"Expected /normalization/preview, got: {urls_called[0]!r}"
        )
        assert "/synthesize" not in urls_called[0], "Preview must NOT call /synthesize"


# ===========================================================================
# 2. Disabled normalization returns original text as spoken/used text
# ===========================================================================

class TestDisabledNormalization:

    def test_normalization_disabled_returns_original_text_as_spoken(self, monkeypatch):
        """When normalization_enabled=False, spoken_text equals original_text."""
        tts_client = _get_tts_client()

        class MockResp:
            def raise_for_status(self):
                pass

            def json(self):
                original = "AI ve ChatGPT pipeline anlatımı"
                return {
                    "original_text": original,
                    "normalized_text": original,
                    "spoken_text": original,   # not normalized
                    "chunks": [original],
                    "chunk_pause_ms": [0],
                    "tts_normalization_language": "tr",
                    "tts_normalization_rules_applied": [],
                    "normalization_enabled": False,
                    "normalization_mode": "loose",
                    "unknown_word_strategy": "keep",
                    "applied_overrides": {},
                    "warnings": ["normalization_disabled"],
                    "error": None,
                    "fallback_used": False,
                }

        monkeypatch.setattr(tts_client.requests, "post", lambda *a, **kw: MockResp())

        result = tts_client.preview_tts_text_with_metadata(
            text="AI ve ChatGPT pipeline anlatımı",
            language="tr",
            normalization_enabled=False,
        )

        assert result["spoken_text"] == result["original_text"]
        assert "normalization_disabled" in result.get("warnings", [])


# ===========================================================================
# 3. Abbreviation override beats default normalization
# ===========================================================================

class TestAbbreviationOverride:

    def test_abbreviation_override_beats_default(self, monkeypatch):
        """abbreviation_overrides must override what the default normalizer would produce."""
        tts_client = _get_tts_client()

        class MockResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "original_text": "AI açıklaması",
                    "normalized_text": "AI açıklaması",
                    "spoken_text": "yapay zeka açıklaması",   # override applied
                    "chunks": ["yapay zeka açıklaması"],
                    "chunk_pause_ms": [0],
                    "tts_normalization_language": "tr",
                    "tts_normalization_rules_applied": [
                        {
                            "rule": "glossary",
                            "term": "AI",
                            "replacement": "yapay zeka",
                            "source": "preview_override",
                            "count": 1,
                        }
                    ],
                    "normalization_enabled": True,
                    "normalization_mode": "loose",
                    "unknown_word_strategy": "keep",
                    "applied_overrides": {
                        "abbreviation_overrides": {"AI": "yapay zeka"},
                        "merged_override_count": 1,
                    },
                    "warnings": [],
                    "error": None,
                    "fallback_used": False,
                }

        monkeypatch.setattr(tts_client.requests, "post", lambda *a, **kw: MockResp())

        result = tts_client.preview_tts_text_with_metadata(
            text="AI açıklaması",
            language="tr",
            abbreviation_overrides={"AI": "yapay zeka"},
        )

        assert "yapay zeka" in result["spoken_text"], (
            f"Abbreviation override not applied: {result['spoken_text']!r}"
        )
        # Make sure the default "ey ay" did NOT win
        assert "ey ay" not in result["spoken_text"], (
            f"Default normalization won over abbreviation override: {result['spoken_text']!r}"
        )


# ===========================================================================
# 4. Technical override works for preview
# ===========================================================================

class TestTechnicalOverride:

    def test_technical_override_applied(self, monkeypatch):
        """technical_overrides must be forwarded and reflected in spoken_text."""
        tts_client = _get_tts_client()

        class MockResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "original_text": "AI ve ChatGPT pipeline anlatımı",
                    "normalized_text": "AI ve ChatGPT pipeline anlatımı",
                    "spoken_text": "ey ay ve Çet Ci Pi Ti payplayn anlatımı",
                    "chunks": ["ey ay ve Çet Ci Pi Ti payplayn anlatımı"],
                    "chunk_pause_ms": [0],
                    "tts_normalization_language": "tr",
                    "tts_normalization_rules_applied": [
                        {"rule": "glossary", "term": "pipeline", "replacement": "payplayn",
                         "source": "preview_override", "count": 1},
                    ],
                    "normalization_enabled": True,
                    "normalization_mode": "loose",
                    "unknown_word_strategy": "keep",
                    "applied_overrides": {
                        "technical_overrides": {"pipeline": "payplayn"},
                        "merged_override_count": 1,
                    },
                    "warnings": [],
                    "error": None,
                    "fallback_used": False,
                }

        payload_captured: list[dict] = []

        def fake_post(url, json=None, **kwargs):
            payload_captured.append(json or {})
            return MockResp()

        monkeypatch.setattr(tts_client.requests, "post", fake_post)

        result = tts_client.preview_tts_text_with_metadata(
            text="AI ve ChatGPT pipeline anlatımı",
            language="tr",
            technical_overrides={"pipeline": "payplayn"},
        )

        assert "payplayn" in result["spoken_text"]
        assert payload_captured
        assert payload_captured[0].get("technical_overrides") == {"pipeline": "payplayn"}, (
            f"technical_overrides not forwarded in payload: {payload_captured[0]!r}"
        )


# ===========================================================================
# 5. Mixed word override works for preview
# ===========================================================================

class TestMixedWordOverride:

    def test_mixed_word_override_applied(self, monkeypatch):
        """mixed_word_overrides must be forwarded and reflected in spoken_text."""
        tts_client = _get_tts_client()

        class MockResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "original_text": "AI ve ChatGPT pipeline anlatımı",
                    "normalized_text": "AI ve ChatGPT pipeline anlatımı",
                    "spoken_text": "ey ay ve chat gpt pipeline anlatımı",
                    "chunks": ["ey ay ve chat gpt pipeline anlatımı"],
                    "chunk_pause_ms": [0],
                    "tts_normalization_language": "tr",
                    "tts_normalization_rules_applied": [
                        {"rule": "glossary", "term": "ChatGPT", "replacement": "chat gpt",
                         "source": "preview_override", "count": 1},
                    ],
                    "normalization_enabled": True,
                    "normalization_mode": "loose",
                    "unknown_word_strategy": "keep",
                    "applied_overrides": {
                        "mixed_word_overrides": {"ChatGPT": "chat gpt"},
                        "merged_override_count": 1,
                    },
                    "warnings": [],
                    "error": None,
                    "fallback_used": False,
                }

        payload_captured: list[dict] = []

        def fake_post(url, json=None, **kwargs):
            payload_captured.append(json or {})
            return MockResp()

        monkeypatch.setattr(tts_client.requests, "post", fake_post)

        result = tts_client.preview_tts_text_with_metadata(
            text="AI ve ChatGPT pipeline anlatımı",
            language="tr",
            mixed_word_overrides={"ChatGPT": "chat gpt"},
        )

        assert "chat gpt" in result["spoken_text"]
        assert payload_captured[0].get("mixed_word_overrides") == {"ChatGPT": "chat gpt"}


# ===========================================================================
# 6. TTS service unavailable → fail-open from helper layer
# ===========================================================================

class TestPreviewFailOpen:

    def test_service_unavailable_fails_open_with_local_fallback(self, monkeypatch):
        """When TTS service is unreachable, helper runs local prepare_text_for_tts."""
        import requests as _requests

        tts_client = _get_tts_client()

        def fail_connect(url, **kwargs):
            raise _requests.ConnectionError("Connection refused")

        monkeypatch.setattr(tts_client.requests, "post", fail_connect)

        result = tts_client.preview_tts_text_with_metadata(
            text="AI açıklaması",
            language="tr",
        )

        # Must return useful metadata, not raise
        assert "original_text" in result
        assert "spoken_text" in result
        assert result["fallback_used"] is True
        assert result["original_text"] == "AI açıklaması"
        # Local fallback should still normalize (ey ay is the TR glossary value for AI)
        # OR it falls through to bare original text — either is acceptable
        assert result["spoken_text"], "spoken_text must not be empty on fail-open"

    def test_service_unavailable_does_not_raise(self, monkeypatch):
        """preview_tts_text_with_metadata must never raise even on total failure."""
        import requests as _requests

        tts_client = _get_tts_client()

        def fail_all(url, **kwargs):
            raise _requests.ConnectionError("down")

        monkeypatch.setattr(tts_client.requests, "post", fail_all)

        # Should not raise under any circumstances
        try:
            result = tts_client.preview_tts_text_with_metadata(
                text="test text",
                language="tr",
            )
            assert isinstance(result, dict)
        except Exception as exc:
            pytest.fail(f"preview_tts_text_with_metadata raised unexpectedly: {exc}")

    def test_service_timeout_fails_open(self, monkeypatch):
        """Timeout must also fail open."""
        import requests as _requests

        tts_client = _get_tts_client()

        def timeout_post(url, **kwargs):
            raise _requests.Timeout("read timeout")

        monkeypatch.setattr(tts_client.requests, "post", timeout_post)

        result = tts_client.preview_tts_text_with_metadata(
            text="Hello world",
            language="en",
        )

        assert result["fallback_used"] is True
        assert result["spoken_text"], "spoken_text must not be empty"

    def test_service_unavailable_local_fallback_resolves_asp_pipeline(self, monkeypatch):
        """Local preview fallback must keep D1 resolver behavior when the service is down."""
        import requests as _requests

        tts_client = _get_tts_client()

        def fail_connect(url, **kwargs):
            raise _requests.ConnectionError("Connection refused")

        monkeypatch.setattr(tts_client.requests, "post", fail_connect)

        result = tts_client.preview_tts_text_with_metadata(
            text="ASP ve Pipeline HyperBeam açıklaması.",
            language="auto",
        )

        assert result["fallback_used"] is True
        assert result["tts_normalization_language"] == "tr"
        assert result["spoken_text"] == "ey es pi ve payp layn HyperBeam açıklaması."
        assert result["unknown_terms"] == ["HyperBeam"]
        rules = [
            (rule["rule"], rule["term"], rule["replacement"])
            for rule in result["tts_normalization_rules_applied"]
        ]
        assert ("acronym", "ASP", "ey es pi") in rules
        assert ("english_technical_fallback", "Pipeline", "payp layn") in rules


# ===========================================================================
# 7. Import isolation: preview uses tts_preprocess, not old preprocess
# ===========================================================================

class TestPreviewImportIsolation:

    def test_preview_helper_uses_tts_preprocess_not_old_preprocess(self, monkeypatch):
        """
        Simulate worker environment (avatar dir on sys.path) and verify
        preview_tts_text_with_metadata is backed by tts_preprocess, not
        avatar/preprocess.py.
        """
        avatar_dir = REPO_ROOT / "services" / "avatar"
        monkeypatch.syspath_prepend(str(avatar_dir))
        monkeypatch.syspath_prepend(str(TTS_ROOT))
        monkeypatch.syspath_prepend(str(SCRIPTS_ROOT))

        # Remove any cached modules
        for mod in list(sys.modules.keys()):
            if mod in ("tts_client", "tts_preprocess", "preprocess"):
                del sys.modules[mod]

        import requests as _requests

        tts_client = importlib.import_module("tts_client")

        # Verify prepare_text_for_tts exists and comes from tts_preprocess
        assert hasattr(tts_client, "prepare_text_for_tts")

        # Confirm tts_preprocess loaded from tts_service, not avatar
        assert "tts_preprocess" in sys.modules
        preprocess_mod = sys.modules["tts_preprocess"]
        assert "tts_service" in str(preprocess_mod.__file__), (
            f"tts_preprocess resolved to wrong path: {preprocess_mod.__file__}"
        )
        assert not hasattr(preprocess_mod, "AvatarValidationError"), (
            "tts_preprocess incorrectly resolved to avatar preprocessing"
        )
        assert hasattr(preprocess_mod, "prepare_text_for_tts")

    def test_preview_helper_does_not_import_old_preprocess(self, monkeypatch):
        """
        After calling preview_tts_text_with_metadata with a fail-open (service down),
        the module 'preprocess' must not be in sys.modules (only 'tts_preprocess').
        """
        import requests as _requests

        # Remove cached modules
        for mod in list(sys.modules.keys()):
            if mod in ("tts_client", "tts_preprocess", "preprocess"):
                del sys.modules[mod]

        tts_client = importlib.import_module("tts_client")

        def fail_connect(url, **kwargs):
            raise _requests.ConnectionError("down")

        monkeypatch.setattr(tts_client.requests, "post", fail_connect)

        tts_client.preview_tts_text_with_metadata(text="Test", language="tr")

        # The old bare 'preprocess' module must not have been imported
        assert "preprocess" not in sys.modules, (
            "Old 'preprocess' module was imported during preview — import isolation violated"
        )


# ===========================================================================
# 8. Auto language resolution
# ===========================================================================

class TestPreviewLanguageResolution:
    """Preview auto language should preserve explicit choices and infer Turkish."""

    def test_tts_service_auto_language_detects_turkish_text(self):
        tts_service = importlib.import_module("main")

        assert tts_service._detect_tts_language("Bu bir konu ve olan metin ile devam eder", "auto") == "tr"
        assert tts_service._detect_tts_language("Anlat\u0131m i\u00e7in haz\u0131r metin", "auto") == "tr"
        assert tts_service._detect_tts_language("This is an explanation for the lesson", "auto") == "en"
        assert tts_service._detect_tts_language("Bu bir konu ve olan metin", "en") == "en"

    def test_preview_helper_local_fallback_auto_language_detects_turkish(self, monkeypatch):
        tts_client = _get_tts_client()

        def fail_connect(url, **kwargs):
            raise tts_client.requests.ConnectionError("down")

        monkeypatch.setattr(tts_client.requests, "post", fail_connect)

        result = tts_client.preview_tts_text_with_metadata(
            text="Bu bir konu ve olan metin ile devam eder",
            language="auto",
        )

        assert result["fallback_used"] is True
        assert result["tts_normalization_language"] == "tr"


# ===========================================================================
# 9. Local preview normalization (direct tts_preprocess calls)
# ===========================================================================

class TestLocalPreviewNormalization:
    """Tests that exercise local tts_preprocess directly (no HTTP)."""

    def test_local_preview_normalization_enabled_tr(self):
        """
        When the local fallback runs for TR text, spoken_text must reflect
        Turkish glossary normalization (e.g. AI -> ey ay).
        """
        from tts_preprocess import prepare_text_for_tts

        prepared = prepare_text_for_tts("AI açıklaması", language="tr")
        assert prepared.original_text == "AI açıklaması"
        assert prepared.spoken_text
        assert "ey ay" in prepared.spoken_text, (
            f"Expected 'ey ay' for AI in TR: {prepared.spoken_text!r}"
        )

    def test_local_preview_disabled_normalization(self):
        """already_prepared=True disables normalization and spoken_text == normalized_text."""
        from tts_preprocess import prepare_text_for_tts

        raw = "AI ve ChatGPT açıklaması"
        prepared = prepare_text_for_tts(raw, language="tr", already_prepared=True)
        assert prepared.spoken_text == prepared.normalized_text

    def test_local_preview_with_mixed_override_via_glossary(self):
        """
        Simulate the override adapter: apply a merged override map on top of
        a normally-prepared text, as the FastAPI endpoint would do.

        Note: overrides target the *post-normalized* spoken text (the output
        of prepare_text_for_tts), not the raw input, because the preview
        endpoint applies overrides after the regular normalizer runs.
        """
        from tts_preprocess import prepare_text_for_tts
        from tts_preprocess.glossary import apply_glossary_with_rules

        text = "AI ve ChatGPT pipeline anlatımı"
        prepared = prepare_text_for_tts(text, language="tr")

        # Default TR: AI -> ey ay, ChatGPT -> Çet Ci Pi Ti
        assert "ey ay" in prepared.spoken_text

        # Apply runtime overrides on top of the already-normalized spoken text.
        # The override map targets post-normalized terms. The D1 resolver now
        # expands pipeline through the curated technical fallback first.
        overrides = {
            "payp layn": "payplayn",
            # To override ChatGPT post-normalization we target its spoken form.
            "Çet Ci Pi Ti": "chat gpt",
        }
        final_text, rules = apply_glossary_with_rules(prepared.spoken_text, overrides, language="tr")

        assert "payplayn" in final_text, f"Technical override not applied: {final_text!r}"
        assert "chat gpt" in final_text, f"Mixed word override (post-norm form) not applied: {final_text!r}"
        rule_terms = [r["term"] for r in rules]
        assert "pipeline" in rule_terms or "Çet Ci Pi Ti" in rule_terms

    def test_local_preview_abbreviation_override_via_glossary(self):
        """
        abbreviation_overrides should take precedence. Simulate by applying
        the override map after the standard prepare_text_for_tts.
        """
        from tts_preprocess import prepare_text_for_tts
        from tts_preprocess.glossary import apply_glossary_with_rules

        text = "AI açıklaması"
        prepared = prepare_text_for_tts(text, language="tr")
        # Default: "ey ay açıklaması"

        override = {"ey ay": "yapay zeka"}  # override the already-normalized text
        final, rules = apply_glossary_with_rules(prepared.spoken_text, override, language="tr")
        assert "yapay zeka" in final, f"Abbreviation override not applied: {final!r}"
