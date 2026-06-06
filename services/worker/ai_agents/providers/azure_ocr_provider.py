from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from django.conf import settings
import requests

from ..ocr_bridge import OCRTextResult
from ..schemas import FindingLocation
from .base import OCRProvider


class AzureOCRProvider(OCRProvider):
    provider_name = "azure_ocr"

    def extract(self, image_path: str | None, location: FindingLocation) -> OCRTextResult:
        normalized_path = str(image_path or "").strip()
        base_metadata = self._base_metadata()
        if not bool(getattr(settings, "AZURE_OCR_ENABLED", False)):
            return self._result(
                location=location,
                image_path=normalized_path,
                success=False,
                error_message="Azure OCR is disabled.",
                metadata={**base_metadata, "skipped": True, "reason": "azure_ocr_disabled"},
            )

        endpoint = str(getattr(settings, "AZURE_OCR_ENDPOINT", "") or "").strip().rstrip("/")
        key = str(getattr(settings, "AZURE_OCR_KEY", "") or "").strip()
        if not endpoint or not key:
            return self._result(
                location=location,
                image_path=normalized_path,
                success=False,
                error_message="Azure OCR endpoint or key is not configured.",
                metadata={**base_metadata, "skipped": True, "reason": "azure_ocr_missing_config"},
            )

        try:
            image_bytes = self._read_image_bytes(normalized_path)
        except _AzureOCRSkip as exc:
            return self._result(
                location=location,
                image_path=normalized_path,
                success=False,
                error_message=str(exc),
                metadata={**base_metadata, "skipped": True, "reason": exc.reason, **exc.metadata},
            )

        try:
            response_json = self._submit_and_poll(
                endpoint=endpoint,
                key=key,
                image_bytes=image_bytes,
            )
            text, confidence = _extract_text_and_confidence(response_json)
            return self._result(
                location=location,
                image_path=normalized_path,
                text=text,
                success=True,
                metadata={
                    **base_metadata,
                    "text_length": len(text),
                    "confidence": confidence,
                    "status": str(response_json.get("status") or "succeeded"),
                },
            )
        except requests.Timeout:
            return self._result(
                location=location,
                image_path=normalized_path,
                success=False,
                error_message="Azure OCR request timed out.",
                metadata={**base_metadata, "skipped": True, "reason": "azure_ocr_timeout"},
            )
        except requests.RequestException as exc:
            return self._result(
                location=location,
                image_path=normalized_path,
                success=False,
                error_message=_short_error(exc),
                metadata={**base_metadata, "skipped": True, "reason": "azure_ocr_request_error"},
            )
        except (ValueError, TypeError, KeyError) as exc:
            return self._result(
                location=location,
                image_path=normalized_path,
                success=False,
                error_message=_short_error(exc),
                metadata={**base_metadata, "skipped": True, "reason": "azure_ocr_invalid_response"},
            )

    def _base_metadata(self) -> dict[str, Any]:
        return {
            "model": str(getattr(settings, "AZURE_OCR_MODEL", "prebuilt-read") or "prebuilt-read"),
            "api_version": str(getattr(settings, "AZURE_OCR_API_VERSION", "2024-02-29-preview") or ""),
            "language_hints": _language_hints(),
            "max_image_bytes": int(getattr(settings, "AZURE_OCR_MAX_IMAGE_BYTES", 10485760) or 10485760),
            "endpoint_configured": bool(str(getattr(settings, "AZURE_OCR_ENDPOINT", "") or "").strip()),
            "key_configured": bool(str(getattr(settings, "AZURE_OCR_KEY", "") or "").strip()),
        }

    def _read_image_bytes(self, image_path: str) -> bytes:
        if not image_path:
            raise _AzureOCRSkip("Image path is empty.", reason="missing_image_path")
        path = Path(image_path)
        if not path.is_file():
            raise _AzureOCRSkip("Image file was not found.", reason="missing_image_file")
        max_bytes = int(getattr(settings, "AZURE_OCR_MAX_IMAGE_BYTES", 10485760) or 10485760)
        size = path.stat().st_size
        if size > max_bytes:
            raise _AzureOCRSkip(
                "Image file is larger than the Azure OCR size limit.",
                reason="image_too_large",
                metadata={"file_size_bytes": size},
            )
        return path.read_bytes()

    def _submit_and_poll(self, *, endpoint: str, key: str, image_bytes: bytes) -> dict[str, Any]:
        timeout_seconds = float(getattr(settings, "AZURE_OCR_TIMEOUT_SECONDS", 30) or 30)
        model = str(getattr(settings, "AZURE_OCR_MODEL", "prebuilt-read") or "prebuilt-read")
        api_version = str(getattr(settings, "AZURE_OCR_API_VERSION", "2024-02-29-preview") or "2024-02-29-preview")
        analyze_url = f"{endpoint}/documentintelligence/documentModels/{model}:analyze"
        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/octet-stream",
        }
        params = {"api-version": api_version}
        locale = _locale_hint()
        if locale:
            params["locale"] = locale
        response = requests.post(
            analyze_url,
            params=params,
            headers=headers,
            data=image_bytes,
            timeout=timeout_seconds,
        )
        if response.status_code == 202:
            operation_url = response.headers.get("Operation-Location") or response.headers.get("operation-location")
            if not operation_url:
                raise ValueError("Azure OCR 202 response did not include Operation-Location.")
            return self._poll_operation(operation_url, key, timeout_seconds)

        response.raise_for_status()
        return response.json()

    def _poll_operation(self, operation_url: str, key: str, timeout_seconds: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        headers = {"Ocp-Apim-Subscription-Key": key}
        last_payload: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = requests.get(operation_url, headers=headers, timeout=timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Azure OCR poll response was not an object.")
            last_payload = payload
            status = str(payload.get("status") or "").lower()
            if status in {"succeeded", "partiallysucceeded"}:
                return payload
            if status in {"failed", "canceled", "cancelled"}:
                raise ValueError(f"Azure OCR operation ended with status={status}.")
            time.sleep(0.5)
        raise requests.Timeout(f"Azure OCR polling exceeded {timeout_seconds:g} seconds.")

    def _result(
        self,
        *,
        location: FindingLocation,
        image_path: str,
        text: str = "",
        success: bool,
        error_message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> OCRTextResult:
        return OCRTextResult(
            text=text,
            location=location,
            provider=self.provider_name,
            success=success,
            error_message=error_message,
            image_path=image_path,
            asset_type=location.asset_type,
            slide_order=location.slide_order,
            metadata=metadata or {},
        )


class _AzureOCRSkip(Exception):
    def __init__(self, message: str, *, reason: str, metadata: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.metadata = metadata or {}


def _language_hints() -> list[str]:
    raw = str(getattr(settings, "AZURE_OCR_LANG_HINTS", "en,tr,ar") or "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _locale_hint() -> str:
    hints = _language_hints()
    return hints[0] if hints else ""


def _extract_text_and_confidence(payload: dict[str, Any]) -> tuple[str, float | None]:
    if not isinstance(payload, dict):
        raise ValueError("Azure OCR response was not an object.")
    analyze_result = payload.get("analyzeResult") or payload.get("analyze_result") or payload
    if not isinstance(analyze_result, dict):
        raise ValueError("Azure OCR response did not include analyzeResult.")

    content = str(analyze_result.get("content") or "").strip()
    if not content:
        lines = []
        for page in analyze_result.get("pages") or []:
            if not isinstance(page, dict):
                continue
            for line in page.get("lines") or []:
                if isinstance(line, dict) and line.get("content"):
                    lines.append(str(line["content"]))
        content = "\n".join(lines).strip()

    confidence_values = []
    for page in analyze_result.get("pages") or []:
        if not isinstance(page, dict):
            continue
        for word in page.get("words") or []:
            if isinstance(word, dict) and isinstance(word.get("confidence"), (int, float)):
                confidence_values.append(float(word["confidence"]))
    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else None
    return content, confidence


def _short_error(exc: BaseException, limit: int = 240) -> str:
    return f"{exc.__class__.__name__}: {exc}"[:limit]
