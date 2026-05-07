"""
services/scripts/tts_client.py
================================
Text-to-speech client for AI_ACADEMY.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Literal

import requests

try:
    from tts_preprocess import clean_text_for_tts, prepare_text_for_tts
except ModuleNotFoundError:
    _TTS_ROOT = Path(__file__).resolve().parents[1] / "tts_service"
    if _TTS_ROOT.exists() and str(_TTS_ROOT) not in sys.path:
        sys.path.insert(0, str(_TTS_ROOT))
    from tts_preprocess import clean_text_for_tts, prepare_text_for_tts

logger = logging.getLogger(__name__)

TTS_SERVICE_URL: str = os.environ.get("TTS_SERVICE_URL", "http://tts_service:8001")
ELEVEN_API_KEY: str | None = os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
ELEVEN_BASE_URL = "https://api.elevenlabs.io/v1"

_DEFAULT_TIMEOUT = float(os.environ.get("TTS_SERVICE_TIMEOUT", "300"))
_CONNECT_TIMEOUT = float(os.environ.get("TTS_SERVICE_CONNECT_TIMEOUT", "10"))
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = 2.0
_READY_TIMEOUT = float(os.environ.get("TTS_READY_TIMEOUT", "360"))
_READY_POLL_INTERVAL = float(os.environ.get("TTS_READY_POLL_INTERVAL", "3"))
_LOCAL_FALLBACK_DURATION = float(os.environ.get("TTS_LOCAL_FALLBACK_DURATION", "3.0"))

_TURKISH_CHARS = set("çğıöşüÇĞİÖŞÜ")
_TURKISH_WORDS = {"ve", "bir", "için", "olan", "de", "da", "ile", "bu", "çok", "değil"}
_ENGLISH_WORDS = {"the", "and", "with", "for", "of", "is", "this", "that"}


def _resolve_preview_language(text: str, language: str | None) -> str:
    cleaned = str(language or "").strip().split("-")[0].split("_")[0].lower()
    if cleaned in {"tr", "en"}:
        return cleaned

    sample = str(text or "").lower()[:6000]
    if not sample.strip():
        return "tr"

    tr_char_hits = sum(1 for ch in sample if ch in _TURKISH_CHARS)
    tokens = set(re.findall(r"[a-zçğıöşü]+", sample, flags=re.IGNORECASE))
    tr_word_hits = sum(1 for token in _TURKISH_WORDS if token in tokens)
    en_word_hits = sum(1 for token in _ENGLISH_WORDS if token in tokens)

    if tr_char_hits >= 1 or tr_word_hits > en_word_hits or (tr_word_hits >= 1 and en_word_hits == 0):
        return "tr"
    return "en"


def normalize_tts_input(text: str) -> str:
    return prepare_text_for_tts(text).spoken_text


def _clean_text_for_tts(text: str) -> str:
    return clean_text_for_tts(text)


def wait_for_tts_ready(timeout_sec: float = _READY_TIMEOUT) -> bool:
    ready_url = f"{TTS_SERVICE_URL.rstrip('/')}/ready"
    deadline = time.time() + max(timeout_sec, 0)
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        try:
            resp = requests.get(ready_url, timeout=(_CONNECT_TIMEOUT, min(10.0, _DEFAULT_TIMEOUT)))
            if resp.status_code == 200:
                logger.info("TTS readiness confirmed after %d check(s)", attempt)
                return True
        except requests.RequestException as exc:
            logger.info("TTS readiness probe failed (attempt=%d): %s", attempt, exc)

        time.sleep(max(_READY_POLL_INTERVAL, 0.5))

    logger.warning("Worker timed out waiting for TTS readiness after %.1fs", timeout_sec)
    return False


def _download_to_file(audio_url: str, out_path: str) -> None:
    dl = requests.get(audio_url, stream=True, timeout=(_CONNECT_TIMEOUT, _DEFAULT_TIMEOUT))
    dl.raise_for_status()
    with open(out_path, "wb") as fh:
        for chunk in dl.iter_content(chunk_size=8192):
            if chunk:
                fh.write(chunk)


def _write_silent_fallback(path: str, duration_sec: float = _LOCAL_FALLBACK_DURATION) -> str:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono",
                "-t",
                str(max(float(duration_sec), 0.05)),
                "-q:a",
                "9",
                "-acodec",
                "libmp3lame",
                str(output),
            ],
            check=True,
            capture_output=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ffmpeg local TTS fallback failed, writing placeholder bytes: %s", exc)
        output.write_bytes(b"")
    return str(output)


def _canonical_tts_settings(tts_settings: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(tts_settings, dict):
        return None
    overrides = tts_settings.get("overrides") if isinstance(tts_settings.get("overrides"), dict) else {}

    def _override_map(name: str) -> dict[str, str]:
        value = overrides.get(name)
        if not isinstance(value, dict):
            return {}
        cleaned: dict[str, str] = {}
        for term, replacement in value.items():
            if isinstance(term, str) and isinstance(replacement, str):
                t = term.strip()
                r = replacement.strip()
                if t and r:
                    cleaned[t] = r
        return cleaned

    provider_preference = str(tts_settings.get("provider_preference") or "auto").strip().lower()
    if provider_preference not in {"auto", "xtts_v2", "gtts"}:
        provider_preference = "auto"
    normalization_mode = str(tts_settings.get("normalization_mode") or "loose").strip().lower()
    if normalization_mode not in {"loose", "strict"}:
        normalization_mode = "loose"
    unknown_word_strategy = str(tts_settings.get("unknown_word_strategy") or "keep").strip().lower()
    if unknown_word_strategy not in {"keep", "phonetic"}:
        unknown_word_strategy = "keep"

    return {
        "provider_preference": provider_preference,
        "normalization_enabled": bool(tts_settings.get("normalization_enabled", True)),
        "normalization_mode": normalization_mode,
        "unknown_word_strategy": unknown_word_strategy,
        "overrides": {
            "technical": _override_map("technical"),
            "abbreviation": _override_map("abbreviation"),
            "mixed_word": _override_map("mixed_word"),
        },
        "speech_speed": tts_settings.get("speech_speed", 1.0),
        "volume_gain_db": tts_settings.get("volume_gain_db", 0),
        "pause_seconds": tts_settings.get("pause_seconds"),
    }


def _override_summary(settings: dict[str, Any] | None) -> dict[str, int]:
    if not settings:
        return {}
    overrides = settings.get("overrides") if isinstance(settings.get("overrides"), dict) else {}
    technical = overrides.get("technical") if isinstance(overrides.get("technical"), dict) else {}
    abbreviation = overrides.get("abbreviation") if isinstance(overrides.get("abbreviation"), dict) else {}
    mixed_word = overrides.get("mixed_word") if isinstance(overrides.get("mixed_word"), dict) else {}
    return {
        "technical_count": len(technical),
        "abbreviation_count": len(abbreviation),
        "mixed_word_count": len(mixed_word),
        "merged_override_count": len({**technical, **abbreviation, **mixed_word}),
    }


def _merged_override_glossary(settings: dict[str, Any]) -> dict[str, str]:
    overrides = settings.get("overrides") if isinstance(settings.get("overrides"), dict) else {}
    merged: dict[str, str] = {}
    for category in ("technical", "abbreviation", "mixed_word"):
        value = overrides.get(category)
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _apply_generation_overrides(
    text: str,
    override_glossary: dict[str, str],
    language: str,
) -> tuple[str, list[dict[str, Any]], dict[str, str]]:
    if not text or not override_glossary:
        return text, [], {}

    from tts_preprocess.glossary import apply_glossary_with_rules

    placeholder_glossary: dict[str, str] = {}
    replacement_map: dict[str, str] = {}
    for index, (term, replacement) in enumerate(override_glossary.items()):
        placeholder = f"__PROJECT_TTS_OVERRIDE_{index}__"
        placeholder_glossary[term] = placeholder
        replacement_map[placeholder] = replacement

    substituted, rules = apply_glossary_with_rules(text, placeholder_glossary, language=language)
    for rule in rules:
        rule["source"] = "project_tts_override"
        replacement = rule.get("replacement")
        if isinstance(replacement, str) and replacement in replacement_map:
            rule["actual_replacement"] = replacement_map[replacement]
    return substituted, rules, replacement_map


def _restore_overrides(text: str, replacement_map: dict[str, str]) -> str:
    restored = str(text or "")
    for placeholder, replacement in replacement_map.items():
        restored = restored.replace(placeholder, replacement)
    return restored


def _prepare_text_with_settings(text: str, lang: str, tts_settings: dict[str, Any] | None) -> dict[str, Any]:
    settings = _canonical_tts_settings(tts_settings)

    if settings and not settings["normalization_enabled"]:
        prepared = prepare_text_for_tts(text, language=lang, already_prepared=True)
        warnings = list(prepared.warnings or [])
        if "normalization_disabled" not in warnings:
            warnings.append("normalization_disabled")
        return {
            "settings": settings,
            "original_text": prepared.original_text or prepared.raw_text,
            "normalized_text": prepared.normalized_text,
            "spoken_text": prepared.spoken_text,
            "chunks": prepared.chunks,
            "chunk_pause_ms": prepared.chunk_pause_ms,
            "tts_normalization_language": prepared.tts_normalization_language,
            "tts_normalization_rules_applied": [],
            "unknown_terms": list(prepared.unknown_terms or []),
            "ambiguous_terms": list(prepared.ambiguous_terms or []),
            "warnings": warnings,
            "applied_overrides": _override_summary(settings),
        }

    replacement_map: dict[str, str] = {}
    pre_rules: list[dict[str, Any]] = []
    source_text = text
    if settings:
        override_glossary = _merged_override_glossary(settings)
        if override_glossary:
            source_text, pre_rules, replacement_map = _apply_generation_overrides(text, override_glossary, lang)

    prepared = prepare_text_for_tts(source_text, language=lang)
    spoken_text = _restore_overrides(prepared.spoken_text, replacement_map)
    normalized_text = _restore_overrides(prepared.normalized_text, replacement_map)
    chunks = [_restore_overrides(chunk, replacement_map) for chunk in list(prepared.chunks or [])]
    rules_applied = pre_rules + list(prepared.tts_normalization_rules_applied or [])
    return {
        "settings": settings,
        "original_text": str(text or ""),
        "normalized_text": normalized_text,
        "spoken_text": spoken_text,
        "chunks": chunks,
        "chunk_pause_ms": list(prepared.chunk_pause_ms or []),
        "tts_normalization_language": prepared.tts_normalization_language,
        "tts_normalization_rules_applied": rules_applied,
        "unknown_terms": list(prepared.unknown_terms or []),
        "ambiguous_terms": list(prepared.ambiguous_terms or []),
        "warnings": list(prepared.warnings or []),
        "applied_overrides": _override_summary(settings),
    }


def synthesize_with_service_with_metadata(
    voice_id: str,
    text: str,
    out_path: str,
    lang: str = "auto",
    tts_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{TTS_SERVICE_URL.rstrip('/')}/synthesize"
    prepared = _prepare_text_with_settings(text, lang, tts_settings)
    settings = prepared.get("settings")
    if not prepared["spoken_text"]:
        raise ValueError("synthesize_with_service: text must not be empty after normalization")

    rules_applied = list(prepared["tts_normalization_rules_applied"] or [])
    if rules_applied:
        logger.info(
            "TTS normalization applied lang=%s rules=%s",
            prepared["tts_normalization_language"],
            rules_applied,
        )

    payload = {
        "text": prepared["spoken_text"],
        "voice_id": voice_id,
        "language": lang,
        "already_prepared": True,
        "chunks": prepared["chunks"],
        "chunk_pause_ms": prepared["chunk_pause_ms"],
        "original_text": prepared["original_text"],
        "normalized_text": prepared["normalized_text"],
        "spoken_text": prepared["spoken_text"],
        "tts_normalization_language": prepared["tts_normalization_language"],
        "tts_normalization_rules_applied": rules_applied,
        "unknown_terms": prepared["unknown_terms"],
        "ambiguous_terms": prepared["ambiguous_terms"],
    }
    if settings:
        overrides = settings["overrides"]
        payload.update(
            {
                "normalization_enabled": settings["normalization_enabled"],
                "normalization_mode": settings["normalization_mode"],
                "unknown_word_strategy": settings["unknown_word_strategy"],
                "provider_preference": settings["provider_preference"],
            }
        )
        if overrides.get("technical"):
            payload["technical_overrides"] = overrides["technical"]
        if overrides.get("abbreviation"):
            payload["abbreviation_overrides"] = overrides["abbreviation"]
        if overrides.get("mixed_word"):
            payload["mixed_word_overrides"] = overrides["mixed_word"]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    metadata_base = {
        "original_text": prepared["original_text"],
        "normalized_text": prepared["normalized_text"],
        "spoken_text": prepared["spoken_text"],
        "tts_normalization_language": prepared["tts_normalization_language"],
        "tts_normalization_rules_applied": rules_applied,
        "unknown_terms": list(prepared["unknown_terms"] or []),
        "ambiguous_terms": list(prepared["ambiguous_terms"] or []),
        "preprocessing_warnings": list(prepared["warnings"] or []),
    }
    if settings:
        metadata_base.update(
            {
                "normalization_enabled": settings["normalization_enabled"],
                "normalization_mode": settings["normalization_mode"],
                "unknown_word_strategy": settings["unknown_word_strategy"],
                "provider_preference": settings["provider_preference"],
                "applied_overrides": prepared.get("applied_overrides") or {},
                "speech_speed": settings.get("speech_speed"),
                "volume_gain_db": settings.get("volume_gain_db"),
                "pause_seconds": settings.get("pause_seconds"),
            }
        )

    if not wait_for_tts_ready(timeout_sec=_READY_TIMEOUT):
        fallback_path = _write_silent_fallback(out_path)
        return {
            **metadata_base,
            "path": fallback_path,
            "provider": "local_fallback",
            "duration": _LOCAL_FALLBACK_DURATION,
            "message": "tts_ready_timeout",
            "fallback_used": True,
            "fallback_reason": "tts_ready_timeout",
        }

    last_exc: Exception | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            resp = requests.post(url, json=payload, stream=True, timeout=(_CONNECT_TIMEOUT, _DEFAULT_TIMEOUT))
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "audio" in content_type:
                with open(out_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            fh.write(chunk)
                return {**metadata_base, "path": out_path, "provider": "direct_audio", "duration": None, "fallback_used": False, "fallback_reason": ""}

            data = resp.json()
            audio_url = data.get("audio_url")
            provider = str(data.get("provider") or "").strip().lower()
            duration = data.get("duration")
            if not audio_url:
                raise RuntimeError(f"tts_service_no_audio_url:{data}")

            _download_to_file(audio_url, out_path)
            service_rules = data.get("tts_normalization_rules_applied")
            fallback_reason = str(data.get("fallback_reason") or data.get("message") or "")
            result = {
                **metadata_base,
                "path": out_path,
                "provider": provider or "unknown",
                "duration": duration,
                "tts_normalization_language": data.get("tts_normalization_language") or metadata_base["tts_normalization_language"],
                "tts_normalization_rules_applied": service_rules if isinstance(service_rules, list) else rules_applied,
                "unknown_terms": data.get("unknown_terms") if isinstance(data.get("unknown_terms"), list) else metadata_base["unknown_terms"],
                "ambiguous_terms": data.get("ambiguous_terms") if isinstance(data.get("ambiguous_terms"), list) else metadata_base["ambiguous_terms"],
                "fallback_used": bool(data.get("fallback_used", provider == "fallback")),
                "fallback_reason": fallback_reason,
            }
            for key in (
                "xtts_attempts",
                "xtts_recovery_attempts",
                "xtts_error_transient",
                "xtts_failure_reason",
            ):
                if key in data:
                    result[key] = data[key]
            return result
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError, ValueError, RuntimeError) as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS:
                logger.warning("TTS service attempt %d/%d failed (%s). Retrying in %.1fs", attempt, _RETRY_ATTEMPTS, exc, _RETRY_BACKOFF)
                time.sleep(_RETRY_BACKOFF)
                continue
            break

    raise RuntimeError(f"tts_service_failed:{last_exc}")


def synthesize_with_service(
    voice_id: str,
    text: str,
    out_path: str,
    lang: str = "auto",
    tts_settings: dict[str, Any] | None = None,
) -> str:
    meta = synthesize_with_service_with_metadata(
        voice_id=voice_id,
        text=text,
        out_path=out_path,
        lang=lang,
        tts_settings=tts_settings,
    )
    return str(meta["path"])


def synthesize_with_elevenlabs(
    voice_id: str,
    text: str,
    out_path: str,
) -> str:
    if not ELEVEN_API_KEY:
        raise EnvironmentError("ELEVEN_API_KEY (or ELEVENLABS_API_KEY) environment variable is not set.")

    url = f"{ELEVEN_BASE_URL}/text-to-speech/{voice_id}/stream"
    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, stream=True, timeout=(_CONNECT_TIMEOUT, _DEFAULT_TIMEOUT))
            resp.raise_for_status()
            with open(out_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        fh.write(chunk)
            return out_path
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt < _RETRY_ATTEMPTS:
                logger.warning("ElevenLabs attempt %d/%d failed (%s). Retrying", attempt, _RETRY_ATTEMPTS, exc)
                time.sleep(_RETRY_BACKOFF)
            else:
                raise

    raise RuntimeError("synthesize_with_elevenlabs: exhausted retries")


def synthesize_text_with_metadata(
    voice_id: str,
    text: str,
    out_path: str,
    mode: Literal["service", "eleven"] = "service",
    lang: str = "auto",
    tts_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if mode == "service":
        return synthesize_with_service_with_metadata(
            voice_id=voice_id,
            text=text,
            out_path=out_path,
            lang=lang,
            tts_settings=tts_settings,
        )
    if mode == "eleven":
        prepared = _prepare_text_with_settings(text, lang, tts_settings)
        if not prepared["spoken_text"]:
            raise ValueError("synthesize_text: text must not be empty")
        path = synthesize_with_elevenlabs(voice_id=voice_id, text=prepared["spoken_text"], out_path=out_path)
        metadata = {
            "path": path,
            "provider": "elevenlabs",
            "duration": None,
            "original_text": prepared["original_text"],
            "normalized_text": prepared["normalized_text"],
            "spoken_text": prepared["spoken_text"],
            "tts_normalization_language": prepared["tts_normalization_language"],
            "tts_normalization_rules_applied": list(prepared["tts_normalization_rules_applied"] or []),
            "unknown_terms": list(prepared["unknown_terms"] or []),
            "ambiguous_terms": list(prepared["ambiguous_terms"] or []),
            "preprocessing_warnings": list(prepared["warnings"] or []),
            "fallback_used": False,
            "fallback_reason": "",
        }
        settings = prepared.get("settings")
        if settings:
            metadata.update(
                {
                    "normalization_enabled": settings["normalization_enabled"],
                    "normalization_mode": settings["normalization_mode"],
                    "unknown_word_strategy": settings["unknown_word_strategy"],
                    "provider_preference": settings["provider_preference"],
                    "applied_overrides": prepared.get("applied_overrides") or {},
                }
            )
        return metadata
    raise ValueError(f"Unknown TTS mode: {mode!r}. Expected 'service' or 'eleven'.")


def synthesize_text(
    voice_id: str,
    text: str,
    out_path: str,
    mode: Literal["service", "eleven"] = "service",
    lang: str = "auto",
    tts_settings: dict[str, Any] | None = None,
) -> str:
    meta = synthesize_text_with_metadata(
        voice_id=voice_id,
        text=text,
        out_path=out_path,
        mode=mode,
        lang=lang,
        tts_settings=tts_settings,
    )
    return str(meta["path"])


# ---------------------------------------------------------------------------
# Phase 1 — TTS preview helper (no audio synthesis)
# ---------------------------------------------------------------------------

_PREVIEW_TIMEOUT_CONNECT = float(os.environ.get("TTS_PREVIEW_CONNECT_TIMEOUT", "5"))
_PREVIEW_TIMEOUT_READ = float(os.environ.get("TTS_PREVIEW_READ_TIMEOUT", "15"))


def preview_tts_text_with_metadata(
    text: str,
    language: str = "auto",
    normalization_enabled: bool = True,
    normalization_mode: str = "loose",
    unknown_word_strategy: str = "keep",
    technical_overrides: dict | None = None,
    abbreviation_overrides: dict | None = None,
    mixed_word_overrides: dict | None = None,
) -> dict[str, Any]:
    """
    Return what the current TTS preprocessing stack would speak for *text*
    without synthesizing any audio.

    Calls the TTS service ``POST /normalization/preview`` with a short timeout.

    Fail-open chain:
    1. Try TTS service preview endpoint.
    2. If the service is unavailable, run local ``prepare_text_for_tts`` and
       return its metadata with ``fallback_used=True``.
    3. If even the local import fails, return the original text with minimal
       metadata and ``fallback_used=True``.

    Never raises — always returns a metadata dict.
    """
    url = f"{TTS_SERVICE_URL.rstrip('/')}/normalization/preview"
    payload: dict[str, Any] = {
        "text": text,
        "language": language,
        "normalization_enabled": normalization_enabled,
        "normalization_mode": normalization_mode,
        "unknown_word_strategy": unknown_word_strategy,
    }
    if technical_overrides:
        payload["technical_overrides"] = technical_overrides
    if abbreviation_overrides:
        payload["abbreviation_overrides"] = abbreviation_overrides
    if mixed_word_overrides:
        payload["mixed_word_overrides"] = mixed_word_overrides

    # ---- Attempt 1: TTS service preview endpoint -------------------------
    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=(_PREVIEW_TIMEOUT_CONNECT, _PREVIEW_TIMEOUT_READ),
        )
        resp.raise_for_status()
        data = resp.json()
        data.setdefault("fallback_used", False)
        data.setdefault("error", None)
        return data
    except (requests.ConnectionError, requests.Timeout) as exc:
        logger.info("TTS preview service unreachable (%s) — using local fallback", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TTS preview service error (%s) — using local fallback", exc)

    # ---- Attempt 2: local prepare_text_for_tts ---------------------------
    try:
        lang_hint = _resolve_preview_language(text, language)
        if normalization_enabled:
            prepared = prepare_text_for_tts(text, language=lang_hint)
            spoken_text = prepared.spoken_text
            normalized_text = prepared.normalized_text
            chunks = prepared.chunks
            chunk_pause_ms = prepared.chunk_pause_ms
            normalization_rules = list(prepared.tts_normalization_rules_applied or [])
            unknown_terms = list(prepared.unknown_terms or [])
            ambiguous_terms = list(prepared.ambiguous_terms or [])
            warnings = list(prepared.warnings or [])
            tts_lang = prepared.tts_normalization_language
        else:
            safe_text = str(text or "")
            spoken_text = safe_text
            normalized_text = safe_text
            chunks = [safe_text] if safe_text else []
            chunk_pause_ms = [0] if safe_text else []
            normalization_rules = []
            unknown_terms = []
            ambiguous_terms = []
            warnings = ["normalization_disabled"]
            tts_lang = lang_hint
        return {
            "original_text": str(text or ""),
            "normalized_text": normalized_text,
            "spoken_text": spoken_text,
            "used_text": spoken_text,
            "chunks": chunks,
            "chunk_pause_ms": chunk_pause_ms,
            "tts_normalization_language": tts_lang,
            "tts_normalization_rules_applied": normalization_rules,
            "unknown_terms": unknown_terms,
            "ambiguous_terms": ambiguous_terms,
            "normalization_enabled": normalization_enabled,
            "normalization_mode": normalization_mode,
            "unknown_word_strategy": unknown_word_strategy,
            "applied_overrides": {
                "technical_overrides": technical_overrides or {},
                "abbreviation_overrides": abbreviation_overrides or {},
                "mixed_word_overrides": mixed_word_overrides or {},
                "merged_override_count": len({
                    **(technical_overrides or {}),
                    **(abbreviation_overrides or {}),
                    **(mixed_word_overrides or {}),
                }),
                "note": "applied by local fallback; service overrides not available",
            },
            "warnings": warnings + ["preview_used_local_fallback"],
            "error": "tts_service_unavailable",
            "fallback_used": True,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Local TTS preview fallback also failed (%s) — returning original text", exc)

    # ---- Attempt 3: bare original-text response --------------------------
    safe_text = str(text or "")
    return {
        "original_text": safe_text,
        "normalized_text": safe_text,
        "spoken_text": safe_text,
        "chunks": [safe_text] if safe_text else [],
        "chunk_pause_ms": [0] if safe_text else [],
        "tts_normalization_language": _resolve_preview_language(safe_text, language),
        "tts_normalization_rules_applied": [],
        "unknown_terms": [],
        "ambiguous_terms": [],
        "normalization_enabled": normalization_enabled,
        "normalization_mode": normalization_mode,
        "unknown_word_strategy": unknown_word_strategy,
        "applied_overrides": {},
        "warnings": ["preview_complete_fallback_to_original"],
        "error": "preview_all_paths_failed",
        "fallback_used": True,
    }

