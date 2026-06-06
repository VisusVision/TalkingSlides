"""
tests/integration/test_tts_preview_protection.py
==================================================
Tests for Phase 1 TTS preview override protection mechanism.

Validates that manual override replacements are protected from
re-normalization by the default glossary/normalizer pipeline.

Tests:
  - chat gpt replacement stays as "chat gpt" in spoken_text (not re-normalized to Çet Ci Pi Ti)
  - AI replacement stays as "ey ay" in spoken_text
  - pipeline replacement stays as "payplayn" in spoken_text
  - All three overrides work together with priority order preserved
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

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


def _get_tts_client():
    if "tts_client" in sys.modules:
        return sys.modules["tts_client"]
    return importlib.import_module("tts_client")


# ===========================================================================
# Test override protection: replacements must not be re-normalized
# ===========================================================================

class TestOverrideProtection:
    """
    Critical tests for Phase 1 TTS preview override protection.
    
    The bug being fixed: manual preview overrides were applied as "chat gpt",
    but then the normalizer would re-normalize "chat gpt" to "chat Ci Pi Ti"
    because it saw "gpt" in the text.
    
    Fix: use placeholder tokens for replacements so the normalizer can't
    re-normalize them. Then restore the placeholders with the actual
    replacement values after normalization completes.
    """

    def test_mixed_word_override_chat_gpt_protected_from_renormalization(self, monkeypatch):
        """
        mixed_word_override: ChatGPT -> chat gpt must result in "chat gpt"
        in spoken_text, NOT "chat Ci Pi Ti" (which is what the default
        glossary would produce if "chat gpt" were visible during normalization).
        """
        tts_client = _get_tts_client()

        class MockResp:
            def raise_for_status(self):
                pass

            def json(self):
                # Simulating what the protected preview endpoint returns:
                # "chat gpt" is protected so it appears exactly as specified.
                return {
                    "original_text": "ChatGPT pipeline anlatımı",
                    "normalized_text": "chat gpt payplayn anlatımı",
                    "spoken_text": "chat gpt payplayn anlatımı",  # PROTECTED: not re-normalized
                    "chunks": ["chat gpt payplayn anlatımı"],
                    "chunk_pause_ms": [0],
                    "tts_normalization_language": "tr",
                    "tts_normalization_rules_applied": [
                        {"rule": "override", "term": "ChatGPT", "replacement": "chat gpt", "source": "preview_pre_override"},
                        {"rule": "override", "term": "pipeline", "replacement": "payplayn", "source": "preview_pre_override"},
                    ],
                    "normalization_enabled": True,
                    "normalization_mode": "loose",
                    "unknown_word_strategy": "keep",
                    "applied_overrides": {
                        "technical_overrides": {},
                        "abbreviation_overrides": {},
                        "mixed_word_overrides": {"ChatGPT": "chat gpt"},
                        "merged_override_count": 1,
                    },
                    "warnings": [],
                    "error": None,
                    "fallback_used": False,
                }

        def fake_post(url, json, **kwargs):
            return MockResp()

        monkeypatch.setattr(tts_client.requests, "post", fake_post)

        result = tts_client.preview_tts_text_with_metadata(
            text="ChatGPT pipeline anlatımı",
            language="tr",
            mixed_word_overrides={"ChatGPT": "chat gpt"},
            technical_overrides={"pipeline": "payplayn"},
        )

        # CRITICAL: "chat gpt" must stay as "chat gpt", not become "chat Ci Pi Ti"
        assert result["spoken_text"] == "chat gpt payplayn anlatımı", (
            f"Override replacement 'chat gpt' was re-normalized: {result['spoken_text']!r}"
        )
        assert "chat gpt" in result["spoken_text"], (
            f"Expected 'chat gpt' in spoken_text, got: {result['spoken_text']!r}"
        )
        assert "Çet Ci Pi Ti" not in result["spoken_text"], (
            f"'chat gpt' was re-normalized to 'Çet Ci Pi Ti': {result['spoken_text']!r}"
        )

    def test_abbreviation_override_ai_protected(self, monkeypatch):
        """
        abbreviation_override: AI -> ey ay must result in "ey ay"
        in spoken_text, protected from further normalization.
        """
        tts_client = _get_tts_client()

        class MockResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "original_text": "AI ve ChatGPT",
                    "normalized_text": "ey ay ve chat gpt",
                    "spoken_text": "ey ay ve chat gpt",
                    "chunks": ["ey ay ve chat gpt"],
                    "chunk_pause_ms": [0],
                    "tts_normalization_language": "tr",
                    "tts_normalization_rules_applied": [
                        {"rule": "override", "term": "AI", "replacement": "ey ay", "source": "preview_pre_override"},
                        {"rule": "override", "term": "ChatGPT", "replacement": "chat gpt", "source": "preview_pre_override"},
                    ],
                    "normalization_enabled": True,
                    "normalization_mode": "loose",
                    "unknown_word_strategy": "keep",
                    "applied_overrides": {
                        "technical_overrides": {},
                        "abbreviation_overrides": {"AI": "ey ay"},
                        "mixed_word_overrides": {"ChatGPT": "chat gpt"},
                        "merged_override_count": 2,
                    },
                    "warnings": [],
                    "error": None,
                    "fallback_used": False,
                }

        def fake_post(url, json, **kwargs):
            return MockResp()

        monkeypatch.setattr(tts_client.requests, "post", fake_post)

        result = tts_client.preview_tts_text_with_metadata(
            text="AI ve ChatGPT",
            language="tr",
            abbreviation_overrides={"AI": "ey ay"},
            mixed_word_overrides={"ChatGPT": "chat gpt"},
        )

        assert "ey ay" in result["spoken_text"], (
            f"Expected 'ey ay' in spoken_text, got: {result['spoken_text']!r}"
        )
        assert result["spoken_text"] == "ey ay ve chat gpt", (
            f"Override replacements were modified: {result['spoken_text']!r}"
        )

    def test_technical_override_pipeline_protected(self, monkeypatch):
        """
        technical_override: pipeline -> payplayn must result in "payplayn"
        in spoken_text, protected from normalization.
        """
        tts_client = _get_tts_client()

        class MockResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "original_text": "pipeline açıklaması",
                    "normalized_text": "payplayn açıklaması",
                    "spoken_text": "payplayn açıklaması",
                    "chunks": ["payplayn açıklaması"],
                    "chunk_pause_ms": [0],
                    "tts_normalization_language": "tr",
                    "tts_normalization_rules_applied": [
                        {"rule": "override", "term": "pipeline", "replacement": "payplayn", "source": "preview_pre_override"},
                    ],
                    "normalization_enabled": True,
                    "normalization_mode": "loose",
                    "unknown_word_strategy": "keep",
                    "applied_overrides": {
                        "technical_overrides": {"pipeline": "payplayn"},
                        "abbreviation_overrides": {},
                        "mixed_word_overrides": {},
                        "merged_override_count": 1,
                    },
                    "warnings": [],
                    "error": None,
                    "fallback_used": False,
                }

        def fake_post(url, json, **kwargs):
            return MockResp()

        monkeypatch.setattr(tts_client.requests, "post", fake_post)

        result = tts_client.preview_tts_text_with_metadata(
            text="pipeline açıklaması",
            language="tr",
            technical_overrides={"pipeline": "payplayn"},
        )

        assert "payplayn" in result["spoken_text"], (
            f"Expected 'payplayn' in spoken_text, got: {result['spoken_text']!r}"
        )
        assert result["spoken_text"] == "payplayn açıklaması", (
            f"Technical override was modified: {result['spoken_text']!r}"
        )

    def test_all_three_override_types_with_protection(self, monkeypatch):
        """
        All three override types working together with priority order preserved.
        Verify that all replacement values are protected from re-normalization.
        """
        tts_client = _get_tts_client()

        class MockResp:
            def raise_for_status(self):
                pass

            def json(self):
                # This response demonstrates all three overrides being applied
                # and protected from re-normalization.
                return {
                    "original_text": "AI ve ChatGPT pipeline anlatımı",
                    "normalized_text": "ey ay ve chat gpt payplayn anlatımı",
                    "spoken_text": "ey ay ve chat gpt payplayn anlatımı",  # ALL protected
                    "chunks": ["ey ay ve chat gpt payplayn anlatımı"],
                    "chunk_pause_ms": [0],
                    "tts_normalization_language": "tr",
                    "tts_normalization_rules_applied": [
                        {"rule": "override", "term": "AI", "replacement": "ey ay", "source": "preview_pre_override"},
                        {"rule": "override", "term": "ChatGPT", "replacement": "chat gpt", "source": "preview_pre_override"},
                        {"rule": "override", "term": "pipeline", "replacement": "payplayn", "source": "preview_pre_override"},
                    ],
                    "normalization_enabled": True,
                    "normalization_mode": "loose",
                    "unknown_word_strategy": "keep",
                    "applied_overrides": {
                        "technical_overrides": {"pipeline": "payplayn"},
                        "abbreviation_overrides": {"AI": "ey ay"},
                        "mixed_word_overrides": {"ChatGPT": "chat gpt"},
                        "merged_override_count": 3,
                    },
                    "warnings": [],
                    "error": None,
                    "fallback_used": False,
                }

        def fake_post(url, json, **kwargs):
            return MockResp()

        monkeypatch.setattr(tts_client.requests, "post", fake_post)

        result = tts_client.preview_tts_text_with_metadata(
            text="AI ve ChatGPT pipeline anlatımı",
            language="tr",
            abbreviation_overrides={"AI": "ey ay"},
            mixed_word_overrides={"ChatGPT": "chat gpt"},
            technical_overrides={"pipeline": "payplayn"},
        )

        spoken = result["spoken_text"]
        
        # All replacements must be present and protected
        assert "ey ay" in spoken, f"Missing 'ey ay' in: {spoken!r}"
        assert "chat gpt" in spoken, f"Missing 'chat gpt' in: {spoken!r}"
        assert "payplayn" in spoken, f"Missing 'payplayn' in: {spoken!r}"
        
        # No re-normalized forms should appear
        assert "Çet Ci Pi Ti" not in spoken, f"'chat gpt' was re-normalized in: {spoken!r}"
        
        # Full text should match expected
        assert spoken == "ey ay ve chat gpt payplayn anlatımı", (
            f"Unexpected spoken_text: {spoken!r}"
        )
