from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Iterable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import Job, Project, TranslatedSubtitleTrack, TranscriptPage
from core.storage_adapter import get_storage_adapter


@dataclass(frozen=True)
class TranslationCue:
    """One display-text subtitle cue passed to or returned by a translation provider."""

    page_key: str
    chunk_index: int
    start: float
    end: float
    text: str

    @classmethod
    def from_mapping(cls, cue: dict) -> "TranslationCue":
        return cls(
            page_key=str(cue.get("page_key") or ""),
            chunk_index=int(cue.get("chunk_index") or 0),
            start=float(cue.get("start") or 0.0),
            end=float(cue.get("end") or 0.0),
            text=str(cue.get("text") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "page_key": self.page_key,
            "chunk_index": self.chunk_index,
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }


class SubtitleTranslationProvider(Protocol):
    """Provider interface for text-only subtitle translation.

    Providers must preserve cue count, cue order, page keys, chunk indexes, and
    timing. The only field a provider may change is ``text``.
    """

    provider_name: str

    def translate_cues(
        self,
        cues: list[TranslationCue],
        *,
        source_language: str,
        target_language: str,
        timeout_seconds: float,
    ) -> list[TranslationCue]:
        ...


class SubtitleProviderUnavailable(RuntimeError):
    """A provider cannot run with the current local configuration/runtime."""


class MockSubtitleTranslationProvider:
    """Deterministic test provider; never calls external services."""

    provider_name = "mock"
    last_metadata: dict[str, Any]

    def __init__(self) -> None:
        self.last_metadata = {"provider_used": self.provider_name}

    def translate_cues(
        self,
        cues: list[TranslationCue],
        *,
        source_language: str,
        target_language: str,
        timeout_seconds: float,
    ) -> list[TranslationCue]:
        return [
            TranslationCue(
                page_key=cue.page_key,
                chunk_index=cue.chunk_index,
                start=cue.start,
                end=cue.end,
                text=f"[{target_language}] {cue.text}",
            )
            for cue in cues
        ]


def normalize_translation_cues(cues: Iterable[dict | TranslationCue]) -> list[TranslationCue]:
    normalized: list[TranslationCue] = []
    for cue in cues or []:
        item = cue if isinstance(cue, TranslationCue) else TranslationCue.from_mapping(cue)
        if item.end <= item.start:
            raise ValueError("subtitle translation cue end must be greater than start")
        if not item.text.strip():
            raise ValueError("subtitle translation cue text cannot be blank")
        normalized.append(item)
    return normalized


def validate_translated_cues(original: list[TranslationCue], translated: list[TranslationCue]) -> list[TranslationCue]:
    if len(original) != len(translated):
        raise ValueError("translated subtitle cue count must match original cue count")

    for index, (source, result) in enumerate(zip(original, translated)):
        if source.page_key != result.page_key:
            raise ValueError(f"translated cue {index} page_key changed")
        if source.chunk_index != result.chunk_index:
            raise ValueError(f"translated cue {index} chunk_index changed")
        if abs(source.start - result.start) > 0.001 or abs(source.end - result.end) > 0.001:
            raise ValueError(f"translated cue {index} timing changed")
        if not result.text.strip():
            raise ValueError(f"translated cue {index} text is blank")
    return translated


def _bool_setting(name: str, default: bool = False) -> bool:
    return bool(getattr(settings, name, default))


def _string_setting(name: str, default: str = "") -> str:
    return str(getattr(settings, name, default) or "").strip()


def _int_setting(name: str, default: int) -> int:
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return int(default)


def _provider_attempt(provider: str, status_value: str, error: str = "") -> dict[str, str]:
    payload = {"provider": provider, "status": status_value}
    if error:
        payload["error"] = re.sub(r"\s+", " ", str(error).strip())[:240]
    return payload


def _provider_chain_from_settings() -> list[str]:
    raw = _string_setting("SUBTITLE_TRANSLATION_PROVIDER_CHAIN", "api,ollama,libretranslate,argos,mock")
    providers = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return providers or ["api", "ollama", "libretranslate", "argos", "mock"]


def _batch_translation_cues(
    cues: list[TranslationCue],
    *,
    max_cues: int,
    max_chars: int,
) -> list[tuple[int, list[TranslationCue]]]:
    cue_limit = max(int(max_cues or 0), 1)
    char_limit = max(int(max_chars or 0), 500)
    batches: list[tuple[int, list[TranslationCue]]] = []
    current: list[TranslationCue] = []
    current_start_index = 0
    current_chars = 0
    for index, cue in enumerate(cues):
        cue_chars = max(len(cue.text or ""), 1)
        if current and (len(current) >= cue_limit or current_chars + cue_chars > char_limit):
            batches.append((current_start_index, current))
            current = []
            current_start_index = index
            current_chars = 0
        current.append(cue)
        current_chars += cue_chars
    if current:
        batches.append((current_start_index, current))
    return batches


def _cue_payloads(cues: list[TranslationCue], *, offset: int = 0) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for relative_index, cue in enumerate(cues):
        absolute_index = offset + relative_index
        page_key = str(cue.page_key or f"cue-{absolute_index}")
        cue_id = f"{page_key}:{cue.chunk_index}"
        if cue_id in seen:
            cue_id = f"{cue_id}:{absolute_index}"
        seen.add(cue_id)
        payloads.append(
            {
                "cue_id": cue_id,
                "page_key": cue.page_key,
                "chunk_index": cue.chunk_index,
                "start": cue.start,
                "end": cue.end,
                "text": cue.text,
            }
        )
    return payloads


def _context_batch_payload(
    cues: list[TranslationCue],
    *,
    source_language: str,
    target_language: str,
    offset: int,
) -> dict[str, Any]:
    source = str(source_language or "").strip() or "auto"
    if source == "original":
        source = "auto"
    return {
        "source_language_code": source,
        "target_language_code": str(target_language or "").strip(),
        "cues": _cue_payloads(cues, offset=offset),
    }


def _json_object_from_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Try to find the outermost { } pair and handle trailing/leading junk
        start = raw.find("{")
        if start >= 0:
            end = raw.rfind("}")
            if end > start:
                candidate = raw[start : end + 1]
                while candidate:
                    try:
                        data = json.loads(candidate)
                        return data
                    except json.JSONDecodeError:
                        # Try shrinking from the end to exclude trailing junk (like reasoning)
                        end = raw.rfind("}", 0, end)
                        if end <= start:
                            break
                        candidate = raw[start : end + 1]
        raise SubtitleProviderUnavailable("provider returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise SubtitleProviderUnavailable("provider JSON response must be an object")
    return data


def _translated_cues_from_id_response(
    original_batch: list[TranslationCue],
    response_payload: dict[str, Any],
    *,
    offset: int,
    provider_name: str,
) -> list[TranslationCue]:
    translations = response_payload.get("translations") if isinstance(response_payload, dict) else None
    if not isinstance(translations, list):
        raise SubtitleProviderUnavailable(f"{provider_name} response missing translations list")
    expected_payloads = _cue_payloads(original_batch, offset=offset)
    if len(translations) != len(expected_payloads):
        raise ValueError(f"{provider_name} returned the wrong translation count")

    translated: list[TranslationCue] = []
    for index, (source, expected, item) in enumerate(zip(original_batch, expected_payloads, translations)):
        if not isinstance(item, dict):
            raise ValueError(f"{provider_name} translation {index} must be an object")
        if str(item.get("cue_id") or "") != expected["cue_id"]:
            raise ValueError(f"{provider_name} translation {index} cue_id changed")
        text = _clean_caption_text(item.get("text") or "")
        if not text:
            raise ValueError(f"{provider_name} translation {index} text is blank")
        translated.append(
            TranslationCue(
                page_key=source.page_key,
                chunk_index=source.chunk_index,
                start=source.start,
                end=source.end,
                text=text,
            )
        )
    return translated


def _translated_texts_to_cues(
    original_batch: list[TranslationCue],
    translated_texts: list[str],
    *,
    provider_name: str,
) -> list[TranslationCue]:
    if len(translated_texts) != len(original_batch):
        raise ValueError(f"{provider_name} returned the wrong translation count")
    translated: list[TranslationCue] = []
    for index, (source, text) in enumerate(zip(original_batch, translated_texts)):
        clean = _clean_caption_text(text)
        if not clean:
            raise ValueError(f"{provider_name} translation {index} text is blank")
        translated.append(
            TranslationCue(
                page_key=source.page_key,
                chunk_index=source.chunk_index,
                start=source.start,
                end=source.end,
                text=clean,
            )
        )
    return translated


class ApiSubtitleTranslationProvider:
    """Generic HTTP provider foundation; skipped unless explicitly configured."""

    provider_name = "api"

    def __init__(self) -> None:
        self.api_provider = _string_setting("SUBTITLE_TRANSLATION_API_PROVIDER")
        self.base_url = _string_setting("SUBTITLE_TRANSLATION_API_BASE_URL").rstrip("/")
        self.api_key = _string_setting("SUBTITLE_TRANSLATION_API_KEY")
        self.model = _string_setting("SUBTITLE_TRANSLATION_API_MODEL")
        self.last_metadata: dict[str, Any] = {"provider_used": self.provider_name}

    def _ensure_configured(self) -> None:
        missing = []
        if not self.api_provider:
            missing.append("SUBTITLE_TRANSLATION_API_PROVIDER")
        if not self.base_url:
            missing.append("SUBTITLE_TRANSLATION_API_BASE_URL")
        if not self.api_key:
            missing.append("SUBTITLE_TRANSLATION_API_KEY")
        if missing:
            raise SubtitleProviderUnavailable(f"api provider skipped; missing {', '.join(missing)}")

    def translate_cues(
        self,
        cues: list[TranslationCue],
        *,
        source_language: str,
        target_language: str,
        timeout_seconds: float,
    ) -> list[TranslationCue]:
        self._ensure_configured()
        translated: list[TranslationCue] = []
        max_cues = _int_setting("OLLAMA_TRANSLATION_MAX_CUES_PER_BATCH", 40)
        max_chars = _int_setting("OLLAMA_TRANSLATION_MAX_CHARS_PER_BATCH", 6000)
        batches = _batch_translation_cues(cues, max_cues=max_cues, max_chars=max_chars)
        for offset, batch in batches:
            payload = {
                "provider": self.api_provider,
                "model": self.model,
                **_context_batch_payload(
                    batch,
                    source_language=source_language,
                    target_language=target_language,
                    offset=offset,
                ),
            }
            request = Request(
                self.base_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=float(timeout_seconds or 20)) as response:
                    body = response.read().decode("utf-8")
                data = json.loads(body)
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                raise SubtitleProviderUnavailable(f"api provider request failed: {exc.__class__.__name__}") from exc
            translated.extend(
                _translated_cues_from_id_response(
                    batch,
                    data,
                    offset=offset,
                    provider_name=self.provider_name,
                )
            )
        self.last_metadata = {
            "provider_used": self.provider_name,
            "batch_count": len(batches),
            "context_aware": True,
        }
        return translated


class OllamaSubtitleTranslationProvider:
    """Context-aware local LLM subtitle translation through Ollama."""

    provider_name = "ollama"

    def __init__(self) -> None:
        self.enabled = _bool_setting("OLLAMA_TRANSLATION_ENABLED", True)
        self.base_url = _string_setting("OLLAMA_TRANSLATION_BASE_URL", "http://host.docker.internal:11434").rstrip("/")
        self.model = _string_setting("OLLAMA_TRANSLATION_MODEL", "qwen2.5:7b-instruct")
        self.timeout_seconds = float(getattr(settings, "OLLAMA_TRANSLATION_TIMEOUT_SECONDS", 300) or 300)
        self.max_cues_per_batch = _int_setting("OLLAMA_TRANSLATION_MAX_CUES_PER_BATCH", 40)
        self.max_chars_per_batch = _int_setting("OLLAMA_TRANSLATION_MAX_CHARS_PER_BATCH", 6000)
        self.last_metadata: dict[str, Any] = {"provider_used": self.provider_name}
        self._retry_count = 0

    def _ensure_configured(self) -> None:
        if not self.enabled:
            raise SubtitleProviderUnavailable("Ollama translation provider is disabled")
        if not self.base_url:
            raise SubtitleProviderUnavailable("Ollama base URL is not configured")
        if not self.model:
            raise SubtitleProviderUnavailable("Ollama translation model is not configured")

    def _prompt_for_batch(self, payload: dict[str, Any], *, retry_reason: str = "") -> str:
        cue_ids = [
            str(item.get("cue_id") or "")
            for item in payload.get("cues", [])
            if isinstance(item, dict) and str(item.get("cue_id") or "")
        ]
        retry_instruction = (
            f"Previous response was invalid: {retry_reason}. Correct it now.\n"
            if retry_reason
            else ""
        )
        return (
            "You are translating subtitle cue text for a lesson. Return JSON only.\n"
            f"{retry_instruction}"
            f"Return exactly {len(cue_ids)} translations in this exact cue_id order: {json.dumps(cue_ids, ensure_ascii=False)}.\n"
            "Preserve cue_id exactly. Preserve cue count and order. Do not merge or split cues.\n"
            "Every input cue must have exactly one output object, even if two cues look similar.\n"
            "Only translate the text field. Do not include timestamps in translated text.\n"
            "Do not translate proper names unless they are normally translated in the target language.\n"
            "Do not add explanations, markdown, or extra keys.\n"
            "Required output shape:\n"
            '{"translations":[{"cue_id":"same cue_id","text":"translated text"}]}\n'
            "Input JSON:\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

    def _translate_batch(
        self,
        batch: list[TranslationCue],
        *,
        source_language: str,
        target_language: str,
        offset: int,
        timeout_seconds: float,
        retry_reason: str = "",
    ) -> list[TranslationCue]:
        payload = _context_batch_payload(
            batch,
            source_language=source_language,
            target_language=target_language,
            offset=offset,
        )
        request_payload = {
            "model": self.model,
            "prompt": self._prompt_for_batch(payload, retry_reason=retry_reason),
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        request = Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(request_payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=float(timeout_seconds or self.timeout_seconds)) as response:
                body = response.read().decode("utf-8")
            data = json.loads(body)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise SubtitleProviderUnavailable(f"Ollama request failed: {exc.__class__.__name__}") from exc

        if not isinstance(data, dict):
            raise SubtitleProviderUnavailable("Ollama response must be a JSON object")
        response_text = data.get("response")
        if response_text is None and "translations" in data:
            response_payload = data
        else:
            response_payload = _json_object_from_text(str(response_text or ""))
        return _translated_cues_from_id_response(
            batch,
            response_payload,
            offset=offset,
            provider_name=self.provider_name,
        )

    def _translate_batch_with_retry(
        self,
        batch: list[TranslationCue],
        *,
        source_language: str,
        target_language: str,
        offset: int,
        timeout_seconds: float,
    ) -> list[TranslationCue]:
        try:
            return self._translate_batch(
                batch,
                source_language=source_language,
                target_language=target_language,
                offset=offset,
                timeout_seconds=timeout_seconds,
            )
        except ValueError as exc:
            message = str(exc)
            if "wrong translation count" not in message and "cue_id" not in message:
                raise
            self._retry_count += 1
            return self._translate_batch(
                batch,
                source_language=source_language,
                target_language=target_language,
                offset=offset,
                timeout_seconds=timeout_seconds,
                retry_reason=message,
            )

    def translate_cues(
        self,
        cues: list[TranslationCue],
        *,
        source_language: str,
        target_language: str,
        timeout_seconds: float,
    ) -> list[TranslationCue]:
        self._ensure_configured()
        self._retry_count = 0
        batches = _batch_translation_cues(
            cues,
            max_cues=self.max_cues_per_batch,
            max_chars=self.max_chars_per_batch,
        )
        effective_timeout = self.timeout_seconds or float(timeout_seconds or 60)
        translated: list[TranslationCue] = []
        for offset, batch in batches:
            translated.extend(
                self._translate_batch_with_retry(
                    batch,
                    source_language=source_language,
                    target_language=target_language,
                    offset=offset,
                    timeout_seconds=effective_timeout,
                )
            )
        self.last_metadata = {
            "provider_used": self.provider_name,
            "batch_count": len(batches),
            "context_aware": True,
            "model": self.model,
            "retry_count": self._retry_count,
        }
        return translated


class LibreTranslateSubtitleTranslationProvider:
    provider_name = "libretranslate"

    def __init__(self) -> None:
        self.base_url = _string_setting("LIBRETRANSLATE_BASE_URL", "http://localhost:5000").rstrip("/")
        self.api_key = _string_setting("LIBRETRANSLATE_API_KEY")
        self.last_metadata: dict[str, Any] = {"provider_used": self.provider_name}

    def translate_cues(
        self,
        cues: list[TranslationCue],
        *,
        source_language: str,
        target_language: str,
        timeout_seconds: float,
    ) -> list[TranslationCue]:
        if not self.base_url:
            raise SubtitleProviderUnavailable("LibreTranslate base URL is not configured")

        source = str(source_language or "").split("-", 1)[0].strip().lower()
        if not source or source == "original":
            source = "auto"
        target = str(target_language or "").split("-", 1)[0].strip().lower()
        endpoint = f"{self.base_url}/translate"
        translated: list[TranslationCue] = []
        batches = _batch_translation_cues(
            cues,
            max_cues=_int_setting("OLLAMA_TRANSLATION_MAX_CUES_PER_BATCH", 40),
            max_chars=_int_setting("OLLAMA_TRANSLATION_MAX_CHARS_PER_BATCH", 6000),
        )
        for _offset, batch in batches:
            payload = {
                "q": [cue.text for cue in batch],
                "source": source,
                "target": target,
                "format": "text",
            }
            if self.api_key:
                payload["api_key"] = self.api_key
            request = Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=float(timeout_seconds or 20)) as response:
                    body = response.read().decode("utf-8")
                data = json.loads(body)
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                raise SubtitleProviderUnavailable(f"LibreTranslate request failed: {exc.__class__.__name__}") from exc
            translated_text = data.get("translatedText") if isinstance(data, dict) else None
            if isinstance(translated_text, list):
                translated_texts = [str(item or "") for item in translated_text]
            elif isinstance(translated_text, str) and len(batch) == 1:
                translated_texts = [translated_text]
            else:
                translations = data.get("translations") if isinstance(data, dict) else None
                if isinstance(translations, list):
                    translated_texts = [
                        str(item.get("translatedText") or item.get("text") or "")
                        if isinstance(item, dict)
                        else str(item or "")
                        for item in translations
                    ]
                else:
                    raise SubtitleProviderUnavailable("LibreTranslate response missing translatedText")
            translated.extend(
                _translated_texts_to_cues(
                    batch,
                    translated_texts,
                    provider_name=self.provider_name,
                )
            )
        self.last_metadata = {
            "provider_used": self.provider_name,
            "batch_count": len(batches),
        }
        return translated


class ArgosSubtitleTranslationProvider:
    provider_name = "argos"

    def __init__(self) -> None:
        packages_dir = _string_setting("ARGOS_TRANSLATE_PACKAGES_DIR")
        if packages_dir:
            os.environ.setdefault("ARGOS_PACKAGES_DIR", packages_dir)
        self.auto_install = _bool_setting("ARGOS_TRANSLATE_AUTO_INSTALL", False)
        self.enabled = _bool_setting("ARGOS_TRANSLATE_ENABLED", True)
        self.last_metadata: dict[str, Any] = {"provider_used": self.provider_name}

    def _translate_module(self):
        if not self.enabled:
            raise SubtitleProviderUnavailable("Argos Translate is disabled")
        try:
            return importlib.import_module("argostranslate.translate")
        except ModuleNotFoundError as exc:
            raise SubtitleProviderUnavailable("Argos Translate package is not installed") from exc

    def _install_packages_if_requested(self) -> None:
        if not self.auto_install:
            return
        try:
            package_module = importlib.import_module("argostranslate.package")
            package_module.update_package_index()
        except Exception as exc:  # noqa: BLE001
            raise SubtitleProviderUnavailable(f"Argos package auto-install failed: {exc.__class__.__name__}") from exc

    def translate_cues(
        self,
        cues: list[TranslationCue],
        *,
        source_language: str,
        target_language: str,
        timeout_seconds: float,
    ) -> list[TranslationCue]:
        translate_module = self._translate_module()
        self._install_packages_if_requested()
        source = str(source_language or "").split("-", 1)[0].strip().lower()
        target = str(target_language or "").split("-", 1)[0].strip().lower()
        if not source or source == "original":
            raise SubtitleProviderUnavailable("Argos requires a known source language")

        languages = translate_module.get_installed_languages()
        from_lang = next((language for language in languages if getattr(language, "code", "") == source), None)
        to_lang = next((language for language in languages if getattr(language, "code", "") == target), None)
        if from_lang is None or to_lang is None:
            raise SubtitleProviderUnavailable(f"Argos language pair unavailable: {source}->{target}")
        translation = from_lang.get_translation(to_lang)
        if translation is None:
            raise SubtitleProviderUnavailable(f"Argos translation model unavailable: {source}->{target}")

        translated: list[TranslationCue] = []
        batches = _batch_translation_cues(
            cues,
            max_cues=_int_setting("OLLAMA_TRANSLATION_MAX_CUES_PER_BATCH", 40),
            max_chars=_int_setting("OLLAMA_TRANSLATION_MAX_CHARS_PER_BATCH", 6000),
        )
        for _offset, batch in batches:
            lines: list[str] = []
            for index, cue in enumerate(batch):
                lines.append(f"[[VISUS_CUE_{index}]]")
                lines.append(cue.text)
                lines.append(f"[[/VISUS_CUE_{index}]]")
            translated_block = str(translation.translate("\n".join(lines)) or "")
            translated_texts: list[str] = []
            for index in range(len(batch)):
                pattern = rf"\[\[VISUS_CUE_{index}\]\](.*?)\[\[/VISUS_CUE_{index}\]\]"
                match = re.search(pattern, translated_block, flags=re.DOTALL)
                if not match:
                    raise SubtitleProviderUnavailable("Argos batch response did not preserve cue markers")
                translated_texts.append(match.group(1).strip())
            translated.extend(
                _translated_texts_to_cues(
                    batch,
                    translated_texts,
                    provider_name=self.provider_name,
                )
            )
        self.last_metadata = {
            "provider_used": self.provider_name,
            "batch_count": len(batches),
        }
        return translated


class AutoFallbackSubtitleTranslationProvider:
    provider_name = "auto"

    def __init__(self, chain: list[str] | None = None, allow_mock_fallback: bool | None = None) -> None:
        self.chain = chain or _provider_chain_from_settings()
        self.allow_mock_fallback = (
            _bool_setting("SUBTITLE_TRANSLATION_ALLOW_MOCK_FALLBACK", True)
            if allow_mock_fallback is None
            else bool(allow_mock_fallback)
        )
        self.last_metadata: dict[str, Any] = {
            "provider_used": "",
            "provider_chain_attempts": [],
            "fallback_used": False,
        }

    def translate_cues(
        self,
        cues: list[TranslationCue],
        *,
        source_language: str,
        target_language: str,
        timeout_seconds: float,
    ) -> list[TranslationCue]:
        attempts: list[dict[str, str]] = []
        first_provider = ""
        for provider_name in self.chain:
            provider_name = str(provider_name or "").strip().lower()
            if not provider_name:
                continue
            if not first_provider:
                first_provider = provider_name
            if provider_name == "auto":
                attempts.append(_provider_attempt(provider_name, "skipped", "auto cannot include itself"))
                continue
            if provider_name == "mock" and not self.allow_mock_fallback:
                attempts.append(_provider_attempt(provider_name, "skipped", "mock fallback disabled"))
                continue
            try:
                provider = get_subtitle_translation_provider(provider_name)
                translated = normalize_translation_cues(
                    provider.translate_cues(
                        cues,
                        source_language=source_language,
                        target_language=target_language,
                        timeout_seconds=timeout_seconds,
                    )
                )
                validate_translated_cues(cues, translated)
            except SubtitleProviderUnavailable as exc:
                attempts.append(_provider_attempt(provider_name, "skipped", exc))
                continue
            except Exception as exc:  # noqa: BLE001
                attempts.append(_provider_attempt(provider_name, "failed", exc))
                continue

            attempts.append(_provider_attempt(provider_name, "success"))
            provider_metadata = dict(getattr(provider, "last_metadata", {}) or {})
            self.last_metadata = {
                **provider_metadata,
                "provider_used": provider_name,
                "provider_chain_attempts": attempts,
                "fallback_used": bool(first_provider and provider_name != first_provider),
            }
            return translated

        self.last_metadata = {
            "provider_used": "",
            "provider_chain_attempts": attempts,
            "fallback_used": bool(attempts),
        }
        error_text = "; ".join(f"{item['provider']}:{item['status']}" for item in attempts) or "no providers configured"
        raise SubtitleProviderUnavailable(f"no subtitle translation provider available ({error_text})")


def get_subtitle_translation_provider(
    provider_name: str | None = None,
    *,
    allow_mock_fallback: bool | None = None,
) -> SubtitleTranslationProvider:
    provider = str(provider_name or getattr(settings, "SUBTITLE_TRANSLATION_PROVIDER", "auto") or "auto").strip().lower()
    if provider == "auto":
        return AutoFallbackSubtitleTranslationProvider(allow_mock_fallback=allow_mock_fallback)
    if provider == "api":
        return ApiSubtitleTranslationProvider()
    if provider == "ollama":
        return OllamaSubtitleTranslationProvider()
    if provider == "libretranslate":
        return LibreTranslateSubtitleTranslationProvider()
    if provider == "argos":
        return ArgosSubtitleTranslationProvider()
    if provider == "mock":
        return MockSubtitleTranslationProvider()
    raise ValueError(f"subtitle translation provider is not configured: {provider}")


class SubtitleTranslationError(RuntimeError):
    """Public-safe translation generation failure."""

    def __init__(self, message: str, *, track: TranslatedSubtitleTrack | None = None):
        super().__init__(message)
        self.track = track


def _safe_error_message(error: Exception | str, *, limit: int = 500) -> str:
    if isinstance(error, PermissionError):
        return "subtitle output directory is not writable by the worker process"
    text = str(error or "").strip() or "subtitle translation failed"
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _normalize_language_code(value: str) -> str:
    code = str(value or "").strip().lower().replace("_", "-")
    if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})?", code):
        raise ValueError("language_code must be a BCP-47 style language code, for example 'en' or 'pt-br'")
    return code


def _default_language_label(language_code: str) -> str:
    labels = {
        "ar": "Arabic",
        "en": "English",
        "tr": "Turkish",
    }
    return labels.get(language_code, language_code.upper())


def _storage_root(storage_root: str | Path | None = None) -> Path:
    return Path(storage_root or getattr(settings, "STORAGE_ROOT", "storage_local"))


def _language_detection_payload(project_id: int, *, storage_root: str | Path | None = None) -> dict:
    try:
        raw = get_storage_adapter(_storage_root(storage_root)).read_text(f"{project_id}/language_detection.json", encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _source_language_code(project_id: int, *, storage_root: str | Path | None = None, fallback: str = "") -> str:
    data = _language_detection_payload(project_id, storage_root=storage_root)
    code = str(data.get("resolved_language") or data.get("detected_language") or fallback or "").strip().lower()
    return code


def _is_finite_number(value) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _clean_caption_text(value) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _normalize_caption_compare(value) -> str:
    return re.sub(r"\s+", " ", _clean_caption_text(value)).strip()


def _page_display_text(page: TranscriptPage) -> str:
    editor_document = getattr(page, "editor_document", None)
    if isinstance(editor_document, dict):
        paragraphs = editor_document.get("paragraphs")
        if isinstance(paragraphs, list):
            paragraph_text = "\n".join(
                _clean_caption_text(item.get("text"))
                for item in paragraphs
                if isinstance(item, dict) and _clean_caption_text(item.get("text"))
            )
            if paragraph_text.strip():
                return paragraph_text.strip()
        html_text = _clean_caption_text(editor_document.get("text") or editor_document.get("plain_text"))
        if html_text:
            return html_text
    return _clean_caption_text(getattr(page, "narration_text", ""))


def _safe_subtitle_chunks_for_page(page: TranscriptPage) -> list[str]:
    raw_chunks = getattr(page, "subtitle_chunks", None)
    if not isinstance(raw_chunks, list):
        raw_chunks = []
    chunks = [_clean_caption_text(item) for item in raw_chunks]
    chunks = [chunk for chunk in chunks if chunk]
    display_text = _page_display_text(page)
    if chunks and display_text and _normalize_caption_compare(" ".join(chunks)) != _normalize_caption_compare(display_text):
        return []
    if chunks:
        return chunks
    return []


def _page_boundaries(page: TranscriptPage, fallback_start: float) -> tuple[float, float]:
    start_value = getattr(page, "start_seconds", None)
    end_value = getattr(page, "end_seconds", None)
    if _is_finite_number(start_value) and _is_finite_number(end_value):
        start = max(float(start_value), 0.0)
        end = max(float(end_value), start)
        if end > start:
            return round(start, 3), round(end, 3)
    duration_value = getattr(page, "duration_seconds", None)
    duration = max(float(duration_value), 0.05) if _is_finite_number(duration_value) else 0.05
    start = max(float(fallback_start or 0.0), 0.0)
    return round(start, 3), round(start + duration, 3)


def _cue_from_values(*, page_key: str, chunk_index: int, start: float, end: float, text: str) -> TranslationCue | None:
    clean = _clean_caption_text(text)
    if not clean:
        return None
    start = max(float(start), 0.0)
    end = max(float(end), 0.0)
    if end <= start:
        return None
    return TranslationCue(
        page_key=str(page_key or ""),
        chunk_index=int(chunk_index),
        start=round(start, 3),
        end=round(end, 3),
        text=clean,
    )


def _allocate_chunk_timings(chunks: list[str], total_duration: float) -> list[dict]:
    safe_duration = max(float(total_duration or 0.0), 0.05)
    cleaned = [chunk.strip() for chunk in chunks if chunk and chunk.strip()]
    if not cleaned:
        return []
    weights = [max(len(chunk), 1) for chunk in cleaned]
    total_weight = sum(weights)
    cursor = 0.0
    timeline: list[dict] = []
    for index, (chunk, weight) in enumerate(zip(cleaned, weights)):
        is_last = index == len(cleaned) - 1
        segment = safe_duration * (weight / total_weight)
        start = cursor
        end = safe_duration if is_last else min(safe_duration, cursor + segment)
        timeline.append({"index": index, "start": round(start, 3), "end": round(end, 3), "text": chunk})
        cursor = end
    return timeline


def _validated_chunk_timeline(page: TranscriptPage, page_start: float, page_end: float) -> list[TranslationCue] | None:
    raw_timeline = getattr(page, "chunk_timeline", None)
    if not isinstance(raw_timeline, list) or not raw_timeline:
        return None

    page_key = str(getattr(page, "page_key", "") or "")
    chunks = _safe_subtitle_chunks_for_page(page)
    display_text = _page_display_text(page)
    if chunks and len(chunks) != len(raw_timeline):
        return None

    cues: list[TranslationCue] = []
    previous_end: float | None = None
    epsilon = 0.001
    for position, item in enumerate(raw_timeline):
        if not isinstance(item, dict):
            return None
        if not _is_finite_number(item.get("start")) or not _is_finite_number(item.get("end")):
            return None
        start = float(item.get("start"))
        end = float(item.get("end"))
        if start < 0 or end < 0 or end <= start:
            return None
        if start < page_start - epsilon or end > page_end + epsilon:
            return None
        start = max(page_start, min(page_end, start))
        end = max(page_start, min(page_end, end))
        if previous_end is not None and start < previous_end - epsilon:
            return None

        raw_index = item.get("chunk_index", item.get("index", position))
        chunk_index = int(raw_index) if _is_finite_number(raw_index) else position
        timeline_text = _clean_caption_text(item.get("text") or "")
        if chunks and position < len(chunks):
            text = chunks[position]
        elif timeline_text:
            text = timeline_text
        elif display_text and len(raw_timeline) == 1:
            text = display_text
        else:
            return None

        cue = _cue_from_values(page_key=page_key, chunk_index=chunk_index, start=start, end=end, text=text)
        if cue is None:
            return None
        cues.append(cue)
        previous_end = end

    if display_text and not chunks:
        joined = _normalize_caption_compare(" ".join(cue.text for cue in cues))
        if joined != _normalize_caption_compare(display_text):
            return None
    return cues


def _distributed_page_cues(page: TranscriptPage, page_start: float, page_end: float) -> list[TranslationCue]:
    page_key = str(getattr(page, "page_key", "") or "")
    chunks = _safe_subtitle_chunks_for_page(page)
    display_text = _page_display_text(page)
    if not chunks and display_text:
        chunks = [display_text]
    if not chunks:
        return []
    duration = max(page_end - page_start, 0.05)
    cues: list[TranslationCue] = []
    for chunk_index, chunk in enumerate(_allocate_chunk_timings(chunks, duration)):
        cue = _cue_from_values(
            page_key=page_key,
            chunk_index=chunk_index,
            start=page_start + float(chunk.get("start") or 0.0),
            end=min(page_end, page_start + float(chunk.get("end") or 0.0)),
            text=chunk.get("text") or "",
        )
        if cue is not None:
            cues.append(cue)
    return cues


def build_translation_source_cues(project: Project) -> list[TranslationCue]:
    """
    Build translation source cues from original display-caption transcript data.

    This intentionally mirrors the Phase 1 source priority: active transcript
    pages, valid chunk_timeline when available, otherwise distributed
    subtitle_chunks, otherwise narration_text as display fallback.
    """
    pages = list(
        TranscriptPage.objects.filter(project=project, is_active=True, deleted_at__isnull=True).order_by("order", "id")
    )
    if not pages:
        raise ValueError("project has no active transcript pages to translate")

    cues: list[TranslationCue] = []
    cursor = 0.0
    for page in pages:
        page_start, page_end = _page_boundaries(page, cursor)
        page_cues = _validated_chunk_timeline(page, page_start, page_end)
        if not page_cues:
            page_cues = _distributed_page_cues(page, page_start, page_end)
        cues.extend(page_cues)
        cursor = max(cursor, page_end)

    cues = sorted(cues, key=lambda cue: (cue.start, cue.chunk_index))
    if not cues:
        raise ValueError("project has no valid subtitle cues to translate")
    return cues


def _format_srt_time(seconds: float) -> str:
    ms = int(round(max(float(seconds), 0.0) * 1000))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _format_vtt_time(seconds: float) -> str:
    return _format_srt_time(seconds).replace(",", ".")


def _ensure_subtitle_output_dir(root: Path, project_id: int | str, *, mode: int = 0o777) -> Path:
    subtitle_dir = root / str(project_id) / "subtitles"
    try:
        subtitle_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(subtitle_dir, mode)
    except PermissionError as exc:
        if not os.access(subtitle_dir, os.W_OK):
            raise PermissionError("subtitle output directory is not writable by the worker process") from exc
    except OSError:
        if not os.access(subtitle_dir, os.W_OK):
            raise PermissionError("subtitle output directory is not writable by the worker process")
    if not os.access(subtitle_dir, os.W_OK):
        raise PermissionError("subtitle output directory is not writable by the worker process")
    return subtitle_dir


def _write_srt_from_translation_cues(cues: list[TranslationCue], out_path: Path) -> str:
    lines: list[str] = []
    sequence = 1
    for cue in cues:
        text = _clean_caption_text(cue.text)
        if not text or cue.end <= cue.start:
            continue
        lines.append(f"{sequence}\n{_format_srt_time(cue.start)} --> {_format_srt_time(cue.end)}\n{text}\n")
        sequence += 1
    out_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return str(out_path)


def _write_vtt_from_translation_cues(cues: list[TranslationCue], out_path: Path) -> str:
    lines: list[str] = ["WEBVTT", ""]
    for cue in cues:
        text = _clean_caption_text(cue.text)
        if not text or cue.end <= cue.start:
            continue
        lines.append(f"{_format_vtt_time(cue.start)} --> {_format_vtt_time(cue.end)}")
        lines.append(text)
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return str(out_path)


def generate_translated_subtitle_track(
    project_id: int | str,
    target_language_code: str,
    provider: str | None = None,
    job_id: int | None = None,
    *,
    language_label: str = "",
    source_language_code: str = "",
    storage_root: str | Path | None = None,
    allow_mock_fallback: bool | None = None,
) -> TranslatedSubtitleTrack:
    """
    Generate translated SRT/VTT sidecars from canonical original display cues.

    This is synchronous Phase 4 scaffolding. It calls the configured provider
    abstraction only and never mutates transcripts, TTS settings, render jobs,
    or video outputs.
    """
    project = Project.objects.get(pk=int(project_id))
    language_code = _normalize_language_code(target_language_code)
    provider_requested = str(provider or getattr(settings, "SUBTITLE_TRANSLATION_PROVIDER", "auto") or "auto").strip().lower()
    label = _clean_caption_text(language_label) or _default_language_label(language_code)
    root = _storage_root(storage_root)
    job = None
    if job_id is not None:
        job = Job.objects.filter(pk=int(job_id), project=project, status="done").first()
        if job is None:
            raise ValueError("completed job_id was not found for this project")
    else:
        job = project.jobs.filter(status="done").order_by("-created_at", "-id").first()
    if job is None:
        raise ValueError("no completed render job is available for subtitle translation")

    source_language = str(source_language_code or _source_language_code(project.id, storage_root=root) or "original").strip().lower()
    srt_rel_path = f"{project.id}/subtitles/{language_code}.srt"
    vtt_rel_path = f"{project.id}/subtitles/{language_code}.vtt"

    with transaction.atomic():
        track, _created = TranslatedSubtitleTrack.objects.select_for_update().get_or_create(
            project=project,
            language_code=language_code,
            defaults={"language_label": label, "provider": provider_requested},
        )
        track.job = job
        track.language_label = label
        track.source_language_code = source_language
        track.provider = provider_requested
        track.status = "processing"
        track.srt_path = ""
        track.vtt_path = ""
        track.cue_count = 0
        track.error_message = ""
        track.metadata = {
            "phase": "phase4_sync_generation",
            "source": "canonical_original_display_cues",
            "timing_policy": "copy_original_cue_timing",
            "started_at": timezone.now().isoformat(),
            "provider_requested": provider_requested,
            "source_language_code": source_language,
            "target_language_code": language_code,
        }
        track.save()

    provider_impl: SubtitleTranslationProvider | None = None
    try:
        original_cues = build_translation_source_cues(project)
        provider_impl = get_subtitle_translation_provider(provider_requested, allow_mock_fallback=allow_mock_fallback)
        translated_cues = normalize_translation_cues(
            provider_impl.translate_cues(
                original_cues,
                source_language=source_language,
                target_language=language_code,
                timeout_seconds=float(getattr(settings, "SUBTITLE_TRANSLATION_TIMEOUT_SECONDS", 20)),
            )
        )
        validate_translated_cues(original_cues, translated_cues)

        subtitle_dir = _ensure_subtitle_output_dir(root, project.id)
        srt_abs_path = subtitle_dir / f"{language_code}.srt"
        vtt_abs_path = subtitle_dir / f"{language_code}.vtt"
        _write_srt_from_translation_cues(translated_cues, srt_abs_path)
        _write_vtt_from_translation_cues(translated_cues, vtt_abs_path)

        provider_metadata = dict(getattr(provider_impl, "last_metadata", {}) or {})
        provider_used = str(provider_metadata.get("provider_used") or getattr(provider_impl, "provider_name", provider_requested) or provider_requested)
        track.job = job
        track.language_label = label
        track.source_language_code = source_language
        track.provider = provider_used
        track.status = "ready"
        track.srt_path = srt_rel_path
        track.vtt_path = vtt_rel_path
        track.cue_count = len(translated_cues)
        track.error_message = ""
        track.metadata = {
            "phase": "phase4_sync_generation",
            "source": "canonical_original_display_cues",
            "timing_policy": "copy_original_cue_timing",
            "source_cue_count": len(original_cues),
            "provider": provider_used,
            "provider_requested": provider_requested,
            "provider_used": provider_used,
            "provider_chain_attempts": provider_metadata.get("provider_chain_attempts", []),
            "fallback_used": bool(provider_metadata.get("fallback_used", provider_used != provider_requested)),
            "source_language_code": source_language,
            "target_language_code": language_code,
            "generated_at": timezone.now().isoformat(),
        }
        track.save()
        return track
    except Exception as exc:
        message = _safe_error_message(exc)
        provider_metadata = dict(getattr(provider_impl, "last_metadata", {}) or {}) if provider_impl is not None else {}
        track.status = "failed"
        track.error_message = message
        track.cue_count = 0
        track.srt_path = ""
        track.vtt_path = ""
        track.metadata = {
            **(track.metadata or {}),
            "failed_at": timezone.now().isoformat(),
            "error_type": exc.__class__.__name__,
            "provider_requested": provider_requested,
            "provider_used": provider_metadata.get("provider_used", ""),
            "provider_chain_attempts": provider_metadata.get("provider_chain_attempts", []),
            "fallback_used": bool(provider_metadata.get("fallback_used", False)),
            "source_language_code": source_language,
            "target_language_code": language_code,
        }
        track.save()
        raise SubtitleTranslationError(message, track=track) from exc
