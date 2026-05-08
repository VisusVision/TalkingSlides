from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranslationResult:
    success: bool
    translated_text: str = ""
    provider: str = "none"
    source_language: str = "auto"
    target_language: str = "en"
    error_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class TranslationModerationProvider:
    provider_name = "translation_moderation"

    def is_enabled(self) -> bool:
        return _bool_setting("TRANSLATION_MODERATION_ENABLED", False) and _provider_name() != "none"

    def translate_text(self, text: str) -> TranslationResult:
        provider = _provider_name()
        target_language = _target_language()
        clean_text = str(text or "").strip()
        if not clean_text:
            return _skipped_result(
                provider=provider,
                target_language=target_language,
                reason="empty_text",
            )
        if not self.is_enabled():
            return _skipped_result(
                provider=provider,
                target_language=target_language,
                reason="disabled",
            )
        if provider != "libretranslate":
            return _skipped_result(
                provider=provider,
                target_language=target_language,
                reason="unsupported_provider",
            )

        try:
            response = requests.post(
                f"{_base_url()}/translate",
                json={
                    "q": clean_text,
                    "source": "auto",
                    "target": target_language,
                    "format": "text",
                },
                timeout=_timeout_seconds(),
            )
            response.raise_for_status()
            payload = response.json()
            translated_text = str(
                payload.get("translatedText")
                or payload.get("translated_text")
                or ""
            ).strip()
            if not translated_text:
                raise ValueError("LibreTranslate response did not include translatedText.")
            return TranslationResult(
                success=True,
                translated_text=translated_text,
                provider=provider,
                source_language=_source_language(payload),
                target_language=target_language,
                metadata={
                    "base_url": _base_url(),
                    "translated_chars": len(translated_text),
                    "input_chars": len(clean_text),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("Translation moderation skipped: %s", exc)
            return TranslationResult(
                success=False,
                provider=provider,
                target_language=target_language,
                error_message=str(exc)[:240],
                metadata={
                    "skipped": True,
                    "reason": "translation_failed",
                    "error": exc.__class__.__name__,
                },
            )


def _skipped_result(*, provider: str, target_language: str, reason: str) -> TranslationResult:
    return TranslationResult(
        success=False,
        provider=provider,
        target_language=target_language,
        error_message=reason,
        metadata={"skipped": True, "reason": reason},
    )


def _source_language(payload: dict[str, Any]) -> str:
    detected = payload.get("detectedLanguage")
    if isinstance(detected, dict):
        return str(detected.get("language") or "auto")
    if isinstance(detected, str):
        return detected or "auto"
    return str(payload.get("sourceLanguage") or payload.get("source_language") or "auto")


def _provider_name() -> str:
    return _str_setting("TRANSLATION_MODERATION_PROVIDER", "none").lower()


def _base_url() -> str:
    return _str_setting("TRANSLATION_MODERATION_BASE_URL", "http://libretranslate:5000").rstrip("/")


def _target_language() -> str:
    return _str_setting("TRANSLATION_MODERATION_TARGET_LANGUAGE", "en").lower() or "en"


def _timeout_seconds() -> float:
    try:
        return max(0.1, float(_str_setting("TRANSLATION_MODERATION_TIMEOUT_SECONDS", "20")))
    except ValueError:
        return 20.0


def _bool_setting(name: str, default: bool) -> bool:
    env_value = os.environ.get(name)
    if env_value is not None:
        return str(env_value).strip().lower() in {"1", "true", "yes", "on"}
    return bool(_django_setting(name, default))


def _str_setting(name: str, default: str) -> str:
    env_value = os.environ.get(name)
    if env_value is not None:
        return str(env_value).strip() or default
    return str(_django_setting(name, default) or default).strip()


def _django_setting(name: str, default: Any) -> Any:
    try:
        from django.conf import settings

        return getattr(settings, name, default)
    except Exception:
        return default
