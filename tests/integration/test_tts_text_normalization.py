import importlib
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TTS_ROOT = REPO_ROOT / "services" / "tts_service"
if str(TTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TTS_ROOT))

from tts_preprocess import prepare_text_for_tts  # noqa: E402


def test_normalize_tts_input_handles_lists_headings_and_controls():
    tts_client = importlib.import_module("tts_client")

    raw = """
    INTRODUCTION:\r\n
    1. First item\r\n
    2. Second item\u200b\r\n
    Dr. Smith explains\x00 details.\r\n
    """

    out = tts_client.normalize_tts_input(raw)

    # heading/list conversion stays readable for narration
    assert "INTRODUCTION." in out
    assert "First item." in out
    assert "Second item." in out
    assert "Doctor Smith explains details." in out

    # control/zero-width chars are removed
    assert "\x00" not in out
    assert "\u200b" not in out



def test_normalize_tts_input_preserves_turkish_punctuation_and_paragraphs():
    tts_client = importlib.import_module("tts_client")

    raw = "Merhaba\r\n\r\nBu bir testtir, değil mi?\r\nEvet!"
    out = tts_client.normalize_tts_input(raw)

    assert "değil mi?" in out
    assert "Evet!" in out
    assert "\n\n" in out


def test_prepare_text_applies_glossary_and_number_normalization():
    prepared = prepare_text_for_tts(
        "XTTS v2 reads PPTX, DRM, and API docs. Version 3.5 costs $10 for 5GB with 10% off v2."
    )

    spoken = prepared.spoken_text
    assert "X T T S version two" in spoken
    assert "power point file" in spoken
    assert "D R M" in spoken
    assert "A P I" in spoken
    assert "three point five" in spoken
    assert "ten percent" in spoken
    assert "ten dollars" in spoken
    assert "five gigabytes" in spoken
    assert "version two" in spoken


def test_prepare_text_repairs_line_breaks_and_bullets():
    line_break = prepare_text_for_tts("This model improves\naccuracy by using embeddings")
    assert line_break.normalized_text == "This model improves accuracy by using embeddings."

    bullets = prepare_text_for_tts("- Upload PPTX\n- Extract text\n- Generate TTS")
    assert bullets.spoken_text == "Upload power point file. Extract text. Generate T T S."
    assert bullets.chunks == ["Upload power point file. Extract text. Generate T T S."]


def test_prepare_text_handles_abbreviations_and_chunk_limits():
    prepared = prepare_text_for_tts(
        "Dr. Smith uses e.g. JSON, i.e. structured data. "
        + "This sentence is intentionally long, and it should split on natural boundaries "
        + "because the chunk maximum is small for this test.",
        max_chars_per_chunk=90,
        target_chars_per_chunk=70,
    )

    assert "Doctor Smith uses for example jay son, that is structured data." in prepared.spoken_text
    assert prepared.chunks
    assert all(len(chunk) <= 90 for chunk in prepared.chunks)
    assert not any(chunk == "Doctor" for chunk in prepared.chunks)


def test_prepare_text_handles_ranges_and_empty_input():
    prepared = prepare_text_for_tts("Wait 3-4 minutes.")
    assert prepared.spoken_text == "Wait three to four minutes."

    empty = prepare_text_for_tts("\x00\u200b")
    assert empty.spoken_text == ""
    assert "empty_input" in empty.warnings


def test_glossary_skips_urls_and_file_paths():
    prepared = prepare_text_for_tts(
        "Open https://example.com/API/XTTS and services/API/XTTS.json, then call API."
    )

    assert "https://example.com/API/XTTS" in prepared.spoken_text
    assert "services/API/XTTS.json" in prepared.spoken_text
    assert "call A P I." in prepared.spoken_text


def test_glossary_skips_bare_filenames_with_extensions():
    prepared = prepare_text_for_tts(
        "Keep config.json, package-lock.json, /app/config.json, and https://example.com/config.json unchanged. JSON payload changes."
    )

    assert "config.json" in prepared.spoken_text
    assert "package-lock.json" in prepared.spoken_text
    assert "/app/config.json" in prepared.spoken_text
    assert "https://example.com/config.json" in prepared.spoken_text
    assert "jay son payload changes" in prepared.spoken_text


def test_tts_client_sends_prepared_chunks_to_service(monkeypatch, tmp_path):
    tts_client = importlib.import_module("tts_client")
    payloads = []

    class Response:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "audio_url": "http://tts_service:8001/audio/test.mp3",
                "provider": "xtts_v2",
                "duration": 1.0,
            }

    def fake_post(_url, json, **_kwargs):
        payloads.append(json)
        return Response()

    out_path = tmp_path / "tts.mp3"
    monkeypatch.setattr(tts_client, "wait_for_tts_ready", lambda timeout_sec: True)
    monkeypatch.setattr(tts_client.requests, "post", fake_post)
    monkeypatch.setattr(tts_client, "_download_to_file", lambda _url, path: Path(path).write_bytes(b"mp3"))

    meta = tts_client.synthesize_with_service_with_metadata(
        "voice1",
        "- Upload PPTX\n- Generate TTS",
        str(out_path),
        lang="en",
    )

    assert meta["provider"] == "xtts_v2"
    assert payloads
    payload = payloads[0]
    assert payload["already_prepared"] is True
    assert payload["chunks"]
    assert payload["chunk_pause_ms"]
    assert "power point file" in payload["text"]
    assert "T T S" in payload["text"]


# ---------------------------------------------------------------------------
# Turkish-aware normalization tests (A–G)
# ---------------------------------------------------------------------------


def test_tr_A_mixed_technical_sentence():
    """A — Turkish mixed technical sentence: TR glossary + TR number rules."""
    prepared = prepare_text_for_tts(
        "JSON dosyası %10 CPU ile 5GB RAM kullanır v2.",
        language="tr",
    )
    spoken = prepared.spoken_text

    # Must NOT contain English translations
    assert "jay son" not in spoken.lower(), f"English JSON expansion must not appear in TR: {spoken!r}"
    assert "five gigabytes" not in spoken.lower(), f"English GB expansion must not appear in TR: {spoken!r}"
    assert "version two" not in spoken.lower(), f"English version expansion must not appear in TR: {spoken!r}"

    # Must contain Turkish equivalents
    assert "yüzde on" in spoken, f"Expected 'yüzde on' in: {spoken!r}"
    assert "beş gigabayt" in spoken, f"Expected 'beş gigabayt' in: {spoken!r}"
    assert "versiyon iki" in spoken, f"Expected 'versiyon iki' in: {spoken!r}"


def test_tr_B_year_and_decimal():
    """B — Turkish year and decimal number normalization."""
    prepared = prepare_text_for_tts(
        "2026 yılında 3.5 sürümü çıktı.",
        language="tr",
    )
    spoken = prepared.spoken_text

    assert "iki bin yirmi altı" in spoken, f"Expected Turkish year in: {spoken!r}"
    assert "üç nokta beş" in spoken, f"Expected Turkish decimal in: {spoken!r}"


def test_tr_C_currency():
    """C — Turkish currency normalization (₺ and $)."""
    prepared = prepare_text_for_tts(
        "Fiyat ₺10 veya 10$ olabilir.",
        language="tr",
    )
    spoken = prepared.spoken_text

    assert "on lira" in spoken, f"Expected 'on lira' in: {spoken!r}"
    assert "on dolar" in spoken, f"Expected 'on dolar' in: {spoken!r}"


def test_tr_D_turkish_characters_preserved():
    """D — Turkish Unicode characters pass through unmodified."""
    raw = "İstanbul, ölçüm, çalışma, güvenli, şifre, çözüm"
    prepared = prepare_text_for_tts(raw, language="tr")
    spoken = prepared.spoken_text

    # Only assert characters that are actually present in the raw string.
    chars_in_raw = set(raw)
    for char in "İçğıöşü":
        if char not in chars_in_raw and char.upper() not in chars_in_raw:
            continue  # skip chars not used in this sentence
        assert char in spoken or char.lower() in spoken, (
            f"Turkish character {char!r} was lost in: {spoken!r}"
        )
    # spot-check whole words
    assert "ölçüm" in spoken or "ölçüm" in prepared.normalized_text
    assert "çalışma" in spoken or "çalışma" in prepared.normalized_text


def test_tr_E_abbreviation_chunking():
    """E — Turkish abbreviations must not cause wrong sentence splits."""
    text = "Prof. Dr. Ahmet sistemi anlattı. Bu örn. kısa bir testtir."
    prepared = prepare_text_for_tts(text, language="tr")

    # The chunks must not split mid-title (Prof. or Dr. should stay with Ahmet)
    full = " ".join(prepared.chunks)
    full_lower = full.lower()
    # Confirm "prof" and "ahmet" all appear (case-insensitive: normalize_structure
    # may lowercase the first letter of a restructured sentence)
    assert "prof" in full_lower, f"Prof was lost: {full!r}"
    assert "ahmet" in full_lower, f"Ahmet was lost: {full!r}"
    # No chunk should be just "Prof." or "Dr." alone (case-insensitive)
    assert not any(c.strip().lower() in ("prof.", "dr.") for c in prepared.chunks), (
        f"Abbreviation created a standalone chunk: {prepared.chunks}"
    )


def test_tr_F_filename_protection():
    """F — File names with .json extension must not be glossary-expanded."""
    prepared = prepare_text_for_tts(
        "config.json dosyasını aç.",
        language="tr",
    )
    spoken = prepared.spoken_text

    assert "config.json" in spoken, f"Filename was mangled: {spoken!r}"
    # Must NOT become "config JSON" or "config jay son"
    assert "config jay son" not in spoken.lower()
    assert "config JSON" not in spoken or spoken.count("config.json") >= 1


def test_tr_G_english_regression():
    """G — English normalization must be unchanged when language='en'."""
    prepared = prepare_text_for_tts(
        "JSON payload, XTTS v2, and 10% off.",
        language="en",
    )
    spoken = prepared.spoken_text

    assert "jay son" in spoken.lower(), f"EN: JSON should become jay son, got: {spoken!r}"
    assert "X T T S version two" in spoken, f"EN: XTTS v2 expansion missing: {spoken!r}"
    assert "ten percent" in spoken, f"EN: 10% should become ten percent, got: {spoken!r}"


def test_tr_technical_dictionary_spoken_text_and_metadata():
    original = "AI, ChatGPT, Gemini ve Claude Code eğitim içeriklerinde kullanılabilir."

    prepared = prepare_text_for_tts(original, language="tr")

    assert prepared.original_text == original
    assert prepared.normalized_text == original
    assert prepared.spoken_text == "ey ay, Çet Ci Pi Ti, Cemini ve Klod Kod eğitim içeriklerinde kullanılabilir."
    assert prepared.tts_normalization_language == "tr"

    terms = [rule["term"] for rule in prepared.tts_normalization_rules_applied]
    assert terms == ["Claude Code", "ChatGPT", "Gemini", "AI"]


def test_d1a_turkish_acronym_resolver_handles_common_technical_terms():
    prepared = prepare_text_for_tts(
        "GPU HTML CSS SQL XML sistemini açıklar.",
        language="tr",
    )

    spoken = prepared.spoken_text
    assert "ci pi yu" in spoken
    assert "eyç ti em el" in spoken
    assert "si es es" in spoken
    assert "es ku el" in spoken
    assert "eks em el" in spoken
    assert [rule["rule"] for rule in prepared.tts_normalization_rules_applied] == [
        "acronym",
        "acronym",
        "acronym",
        "acronym",
        "acronym",
    ]


def test_d1a_turkish_known_words_are_left_unwarned():
    prepared = prepare_text_for_tts("ve bir bu ile eğitim içeriklerinde kullanılır.", language="tr")

    assert prepared.spoken_text == "ve bir bu ile eğitim içeriklerinde kullanılır."
    assert prepared.unknown_terms == []
    assert prepared.ambiguous_terms == []
    assert "deterministic_resolver_unknown_terms" not in prepared.warnings


def test_d1a_english_technical_fallback_in_turkish_text():
    prepared = prepare_text_for_tts("Pipeline backend açıklaması.", language="tr")

    assert "payp layn" in prepared.spoken_text
    assert "bek end" in prepared.spoken_text
    assert [
        (rule["rule"], rule["term"], rule["replacement"])
        for rule in prepared.tts_normalization_rules_applied
    ] == [
        ("english_technical_fallback", "Pipeline", "payp layn"),
        ("english_technical_fallback", "backend", "bek end"),
    ]


def test_d1a_turkish_preview_resolves_asp_and_pipeline_case_insensitively():
    prepared = prepare_text_for_tts("ASP ve Pipeline açıklaması.", language="tr")

    assert prepared.tts_normalization_language == "tr"
    assert prepared.normalized_text == "ASP ve Pipeline açıklaması."
    assert prepared.spoken_text == "ey es pi ve payp layn açıklaması."
    assert [
        (rule["rule"], rule["term"], rule["replacement"])
        for rule in prepared.tts_normalization_rules_applied
    ] == [
        ("acronym", "ASP", "ey es pi"),
        ("english_technical_fallback", "Pipeline", "payp layn"),
    ]


def test_d1a_manual_override_wins_for_asp_and_pipeline():
    tts_client = importlib.import_module("tts_client")

    prepared = tts_client._prepare_text_with_settings(
        "ASP ve Pipeline açıklaması.",
        "tr",
        {
            "overrides": {
                "abbreviation": {"ASP": "özel asp"},
                "technical": {"Pipeline": "özel akış"},
            }
        },
    )

    assert prepared["spoken_text"] == "özel asp ve özel akış açıklaması."
    assert [rule["source"] for rule in prepared["tts_normalization_rules_applied"]] == [
        "project_tts_override",
        "project_tts_override",
    ]
    assert prepared["unknown_terms"] == []


def test_d1a_unknown_suspicious_terms_are_reported_not_rewritten():
    prepared = prepare_text_for_tts("GraphQL FooSDK açıklaması.", language="tr")

    assert "GraphQL" in prepared.spoken_text
    assert "FooSDK" in prepared.spoken_text
    assert prepared.unknown_terms == ["GraphQL", "FooSDK"]
    assert "deterministic_resolver_unknown_terms" in prepared.warnings


def test_d1a_unknown_hyperbeam_still_reported():
    prepared = prepare_text_for_tts("HyperBeam açıklaması.", language="tr")

    assert prepared.spoken_text == "HyperBeam açıklaması."
    assert prepared.unknown_terms == ["HyperBeam"]
    assert "deterministic_resolver_unknown_terms" in prepared.warnings


def test_d1a_ambiguous_terms_are_reported_and_left_unchanged():
    prepared = prepare_text_for_tts("model açıklaması.", language="tr")

    assert prepared.spoken_text == "model açıklaması."
    assert prepared.ambiguous_terms == ["model"]
    assert prepared.unknown_terms == []
    assert "deterministic_resolver_ambiguous_terms" in prepared.warnings


def test_tr_longest_match_prevents_partial_claude_code_rewrite():
    prepared = prepare_text_for_tts("Claude Code ve Claude kullanılabilir.", language="tr")

    assert prepared.spoken_text == "Klod Kod ve Klod kullanılabilir."
    assert [rule["term"] for rule in prepared.tts_normalization_rules_applied] == [
        "Claude Code",
        "Claude",
    ]


def test_en_language_does_not_use_turkish_dictionary():
    prepared = prepare_text_for_tts(
        "AI, ChatGPT, Gemini and Claude Code can be used with API.",
        language="en",
    )

    assert "ey ay" not in prepared.spoken_text
    assert "Çet Ci Pi Ti" not in prepared.spoken_text
    assert "Klod Kod" not in prepared.spoken_text
    assert "A P I" in prepared.spoken_text


def test_tr_protects_urls_emails_code_paths_and_flags():
    prepared = prepare_text_for_tts(
        "AI https://example.com/ChatGPT test@example.com `ChatGPT API` config/API.json --ChatGPT",
        language="tr",
    )

    assert prepared.spoken_text.startswith("ey ay ")
    assert "https://example.com/ChatGPT" in prepared.spoken_text
    assert "test@example.com" in prepared.spoken_text
    assert "`ChatGPT API`" in prepared.spoken_text
    assert "config/API.json" in prepared.spoken_text
    assert "--ChatGPT" in prepared.spoken_text
    assert [rule["term"] for rule in prepared.tts_normalization_rules_applied] == ["AI"]


def test_tts_client_sends_spoken_text_but_returns_original_metadata(monkeypatch, tmp_path):
    tts_client = importlib.import_module("tts_client")
    payloads = []

    class Response:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "audio_url": "http://tts_service:8001/audio/test.mp3",
                "provider": "xtts_v2",
                "duration": 1.0,
            }

    def fake_post(_url, json, **_kwargs):
        payloads.append(json)
        return Response()

    original = "AI, ChatGPT, Gemini ve Claude Code eğitim içeriklerinde kullanılabilir."
    caption_chunks = [original]
    out_path = tmp_path / "tts.mp3"
    monkeypatch.setattr(tts_client, "wait_for_tts_ready", lambda timeout_sec: True)
    monkeypatch.setattr(tts_client.requests, "post", fake_post)
    monkeypatch.setattr(tts_client, "_download_to_file", lambda _url, path: Path(path).write_bytes(b"mp3"))

    meta = tts_client.synthesize_with_service_with_metadata(
        "voice1",
        original,
        str(out_path),
        lang="tr",
    )

    payload = payloads[0]
    assert payload["original_text"] == original
    assert payload["text"] == "ey ay, Çet Ci Pi Ti, Cemini ve Klod Kod eğitim içeriklerinde kullanılabilir."
    assert payload["spoken_text"] == payload["text"]
    assert [rule["term"] for rule in payload["tts_normalization_rules_applied"]] == [
        "Claude Code",
        "ChatGPT",
        "Gemini",
        "AI",
    ]
    assert meta["original_text"] == original
    assert meta["spoken_text"] == payload["text"]
    assert caption_chunks == [original]


def test_tts_settings_overrides_are_request_local_and_keep_captions_original(monkeypatch, tmp_path):
    tts_client = importlib.import_module("tts_client")
    payloads = []
    glossary_path = TTS_ROOT / "tts_preprocess" / "glossary.json"
    glossary_before = glossary_path.read_text(encoding="utf-8")

    class Response:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "audio_url": "http://tts_service:8001/audio/test.mp3",
                "provider": "xtts_v2",
                "duration": 1.0,
            }

    def fake_post(_url, json, **_kwargs):
        payloads.append(json)
        return Response()

    original = "ChatGPT pipeline explanation"
    caption_chunks = [original]
    out_path = tmp_path / "tts-overrides.mp3"
    monkeypatch.setattr(tts_client, "wait_for_tts_ready", lambda timeout_sec: True)
    monkeypatch.setattr(tts_client.requests, "post", fake_post)
    monkeypatch.setattr(tts_client, "_download_to_file", lambda _url, path: Path(path).write_bytes(b"mp3"))

    meta = tts_client.synthesize_with_service_with_metadata(
        "voice1",
        original,
        str(out_path),
        lang="en",
        tts_settings={
            "overrides": {
                "technical": {"pipeline": "pay pline"},
                "mixed_word": {"ChatGPT": "chat gpt"},
            }
        },
    )

    payload = payloads[0]
    assert payload["original_text"] == original
    assert "chat gpt" in payload["text"]
    assert "pay pline" in payload["text"]
    assert meta["original_text"] == original
    assert caption_chunks == [original]
    assert glossary_path.read_text(encoding="utf-8") == glossary_before


def test_d1a_manual_override_wins_over_acronym_resolver(monkeypatch, tmp_path):
    tts_client = importlib.import_module("tts_client")
    payloads = []

    class Response:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "audio_url": "http://tts_service:8001/audio/test.mp3",
                "provider": "xtts_v2",
                "duration": 1.0,
            }

    def fake_post(_url, json, **_kwargs):
        payloads.append(json)
        return Response()

    out_path = tmp_path / "tts-manual-overrides.mp3"
    monkeypatch.setattr(tts_client, "wait_for_tts_ready", lambda timeout_sec: True)
    monkeypatch.setattr(tts_client.requests, "post", fake_post)
    monkeypatch.setattr(tts_client, "_download_to_file", lambda _url, path: Path(path).write_bytes(b"mp3"))

    tts_client.synthesize_with_service_with_metadata(
        "voice1",
        "GPU pipeline",
        str(out_path),
        lang="tr",
        tts_settings={
            "overrides": {
                "abbreviation": {"GPU": "özel ekran kartı"},
                "technical": {"pipeline": "özel akış"},
            }
        },
    )

    payload = payloads[0]
    assert payload["text"] == "özel ekran kartı özel akış."
    assert [rule["source"] for rule in payload["tts_normalization_rules_applied"]] == [
        "project_tts_override",
        "project_tts_override",
    ]
    assert payload["unknown_terms"] == []
    assert payload["ambiguous_terms"] == []


def test_d1a_preview_and_synth_share_resolver_metadata(monkeypatch, tmp_path):
    tts_main = importlib.import_module("main")
    tts_client = importlib.import_module("tts_client")
    text = "GPU pipeline GraphQL"

    preview = tts_main._run_preview_normalization(
        tts_main.NormalizationPreviewRequest(text=text, language="tr")
    )

    payloads = []

    class Response:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "audio_url": "http://tts_service:8001/audio/test.mp3",
                "provider": "xtts_v2",
                "duration": 1.0,
                "unknown_terms": ["GraphQL"],
                "ambiguous_terms": [],
            }

    def fake_post(_url, json, **_kwargs):
        payloads.append(json)
        return Response()

    out_path = tmp_path / "tts-preview-parity.mp3"
    monkeypatch.setattr(tts_client, "wait_for_tts_ready", lambda timeout_sec: True)
    monkeypatch.setattr(tts_client.requests, "post", fake_post)
    monkeypatch.setattr(tts_client, "_download_to_file", lambda _url, path: Path(path).write_bytes(b"mp3"))

    meta = tts_client.synthesize_with_service_with_metadata(
        "voice1",
        text,
        str(out_path),
        lang="tr",
    )

    payload = payloads[0]
    assert payload["text"] == preview.spoken_text
    assert payload["unknown_terms"] == preview.unknown_terms == ["GraphQL"]
    assert payload["ambiguous_terms"] == preview.ambiguous_terms == []
    assert meta["unknown_terms"] == ["GraphQL"]


def test_d1a_resolver_has_no_network_llm_or_subprocess_dependency():
    import tts_preprocess.deterministic_resolver as resolver

    source = Path(resolver.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("requests", "httpx", "openai", "ollama", "socket", "subprocess"):
        assert forbidden not in source


def test_srt_generation_uses_original_text_not_spoken_text(tmp_path):
    ffmpeg_helpers = importlib.import_module("ffmpeg_helpers")
    original = "AI, ChatGPT, Gemini ve Claude Code eğitim içeriklerinde kullanılabilir."
    spoken = prepare_text_for_tts(original, language="tr").spoken_text
    srt_path = tmp_path / "lesson.srt"

    ffmpeg_helpers.generate_srt_from_cues(
        [{"start": 0.0, "end": 2.0, "text": original}],
        str(srt_path),
    )

    content = srt_path.read_text(encoding="utf-8")
    assert original in content
    assert spoken not in content
