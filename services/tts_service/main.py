"""
services/tts_service/main.py
=============================
AI_ACADEMY TTS microservice.

POST /synthesize  — accepts JSON body, returns audio URL + metadata (JSON).
GET  /audio/{fn}  — serves a previously synthesised audio file.
GET  /health      — liveness probe.

Synthesis chain
---------------
1. XTTS v2 voice cloning when enabled and a voice reference is available.
2. gTTS (Google Text-to-Speech, pure-python, requires internet).
3. ffmpeg silent-audio fallback (always works, produces a short silent MP3).

The endpoint NEVER returns 5xx for normal synthesis failures — it always
returns HTTP 200 with ``provider="fallback"`` so the worker pipeline keeps
running even when Google TTS is unreachable or the text is unsupported.
"""

from __future__ import annotations

import json
import time
import logging
import os
import re
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator, constr

from tts_preprocess import (
    TTSPreparedText,
    clean_text_for_tts,
    get_preprocess_config,
    prepare_text_for_tts,
    split_sentences as preprocess_split_sentences,
)
from tts_preprocess.segmenter import split_oversized_unit as preprocess_split_oversized_unit

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tts_service")

app = FastAPI(title="AI Academy TTS Service", version="0.2.0")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, raw, default)
        return default
    return max(minimum, value)


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid float for %s=%r; using %s", name, raw, default)
        return default
    return max(minimum, value)


STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", "storage_local"))

# Directory where synthesised MP3 files are stored.
# Must be writable by the process user; /tmp is always writable.
TTS_AUDIO_DIR = Path(os.environ.get("TTS_AUDIO_DIR", "storage_local/tts"))
TTS_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Base URL at which *this* service is reachable by other containers.
# Worker downloads audio from  <TTS_SERVICE_URL>/audio/<filename>.
TTS_SERVICE_URL = os.environ.get("TTS_SERVICE_URL", "http://tts_service:8001").rstrip("/")

# Duration (seconds) of the silent-fallback MP3.
FALLBACK_DURATION_SEC = float(os.environ.get("TTS_FALLBACK_DURATION", "3.0"))

# Enable or disable XTTS provider (Coqui TTS voice cloning). Set to '0' or
# 'false' to skip XTTS attempts and rely on gTTS/fallback only. This helps in
# environments where heavy model loading is undesirable.
XTTS_ENABLED = str(os.environ.get("XTTS_ENABLED", "1")).lower() in ("1", "true", "yes")
XTTS_ABORT_ON_CUDA_ASSERT = str(os.environ.get("XTTS_ABORT_ON_CUDA_ASSERT", "1")).lower() in ("1", "true", "yes")
XTTS_PRELOAD_ON_STARTUP = str(os.environ.get("XTTS_PRELOAD_ON_STARTUP", "1")).lower() in ("1", "true", "yes")
XTTS_WARMUP_BLOCKING = str(os.environ.get("XTTS_WARMUP_BLOCKING", "0")).lower() in ("1", "true", "yes")
XTTS_LOAD_RECOVERY_ATTEMPTS = _env_int("XTTS_LOAD_RECOVERY_ATTEMPTS", 2)
XTTS_LOAD_RECOVERY_BACKOFF_SEC = _env_float("XTTS_LOAD_RECOVERY_BACKOFF_SEC", 2.0)

# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class SynthesizeRequest(BaseModel):
    """
    JSON body expected by POST /synthesize.

    Matches the payload sent by ``scripts/tts_client.py``::

        {"text": "...", "voice_id": "rachel", "language": "en"}
    """

    text: constr(min_length=1)
    voice_id: Optional[str] = ""
    language: Optional[str] = "tr"
    already_prepared: bool = False
    chunks: Optional[list[str]] = None
    chunk_pause_ms: Optional[list[int]] = None
    original_text: Optional[str] = None
    normalized_text: Optional[str] = None
    spoken_text: Optional[str] = None
    tts_normalization_language: Optional[str] = None
    tts_normalization_rules_applied: Optional[list[dict[str, Any]]] = None
    unknown_terms: Optional[list[str]] = None
    ambiguous_terms: Optional[list[str]] = None
    normalization_enabled: Optional[bool] = None
    normalization_mode: Optional[str] = None
    unknown_word_strategy: Optional[str] = None
    provider_preference: Optional[str] = None
    technical_overrides: Optional[dict[str, str]] = None
    abbreviation_overrides: Optional[dict[str, str]] = None
    mixed_word_overrides: Optional[dict[str, str]] = None

    @field_validator("text")
    @classmethod
    def text_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("'text' must be a non-empty string")
        return v.strip()


# ---------------------------------------------------------------------------
# Preview request / response models  (Phase 1)
# ---------------------------------------------------------------------------

class NormalizationPreviewRequest(BaseModel):
    """
    JSON body for POST /normalization/preview.

    Accepts optional runtime override maps that are merged into the
    glossary in-memory for this preview request only.  They never modify
    glossary.json.

    Override priority (highest → lowest):
      mixed_word_overrides > abbreviation_overrides > technical_overrides
      > language glossary > default normalization.
    """

    text: str
    language: Optional[str] = "tr"
    normalization_enabled: bool = True
    normalization_mode: Optional[str] = "loose"           # "loose" | "strict"
    unknown_word_strategy: Optional[str] = "keep"         # "keep" | "phonetic"
    technical_overrides: Optional[dict[str, str]] = None
    abbreviation_overrides: Optional[dict[str, str]] = None
    mixed_word_overrides: Optional[dict[str, str]] = None


class NormalizationPreviewResponse(BaseModel):
    """Response shape for POST /normalization/preview."""

    original_text: str
    normalized_text: str
    spoken_text: str
    chunks: list[str]
    chunk_pause_ms: list[int]
    tts_normalization_language: str
    tts_normalization_rules_applied: list[dict[str, Any]]
    unknown_terms: list[str]
    ambiguous_terms: list[str]
    normalization_enabled: bool
    normalization_mode: Optional[str]
    unknown_word_strategy: Optional[str]
    applied_overrides: dict[str, Any]
    warnings: list[str]
    error: Optional[str]
    fallback_used: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _new_audio_path() -> Path:
    """Return a unique, unused path inside TTS_AUDIO_DIR."""
    return TTS_AUDIO_DIR / f"{uuid.uuid4().hex}.mp3"


def _normalize_lang(lang: str | None) -> str:
    """
    Normalize a language tag to a two-letter code suitable for gTTS.

    ``"auto"``, ``None``, and ``""`` all map to ``"auto"`` so callers can
    resolve them from text.
    ``"en-US"`` → ``"en"``.
    """
    if not lang or lang.strip().lower() in ("auto", ""):
        return "auto"
    return lang.strip().split("-")[0].split("_")[0].lower() or "tr"


_TURKISH_CHARS = set("çğıöşüÇĞİÖŞÜ")
_TURKISH_WORDS = {"ve", "bir", "için", "olan", "de", "da", "ile", "bu", "çok", "değil"}
_ENGLISH_WORDS = {"the", "and", "with", "for", "of", "is", "this", "that"}


def _detect_tts_language(text: str, hint: str | None = None) -> str:
    """Resolve language for TTS using hint first, then lightweight text analysis."""
    normalized_hint = _normalize_lang(hint)
    if normalized_hint in {"en", "tr"}:
        return normalized_hint

    sample = (text or "").lower()[:6000]
    if not sample.strip():
        return "tr"

    tr_char_hits = sum(1 for ch in sample if ch in _TURKISH_CHARS)
    tokens = re.findall(r"[a-zçğıöşü]+", sample, flags=re.IGNORECASE)
    token_set = set(tokens)
    tr_word_hits = sum(1 for token in _TURKISH_WORDS if token in token_set)
    en_word_hits = sum(1 for token in _ENGLISH_WORDS if token in token_set)

    if tr_char_hits >= 1 or tr_word_hits > en_word_hits or (tr_word_hits >= 1 and en_word_hits == 0):
        return "tr"
    return "en"


def _audio_duration(path: Path) -> float:
    """Return audio duration in seconds via ffprobe; returns 0.0 on error."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception as exc:
        logger.warning("ffprobe could not read duration of %s: %s", path, exc)
        return 0.0


def _describe_torch_runtime(torch) -> tuple[bool, str]:
    """Return whether CUDA is usable and a human-readable runtime explanation."""
    build_cuda = getattr(getattr(torch, "version", None), "cuda", None)

    if build_cuda is None:
        return False, f"PyTorch {torch.__version__} is a CPU-only build."

    try:
        if torch.cuda.is_available():
            device_count = torch.cuda.device_count()
            device_name = torch.cuda.get_device_name(0) if device_count else "unknown GPU"
            return True, (
                f"PyTorch {torch.__version__} CUDA build {build_cuda} detected; "
                f"{device_count} GPU(s) visible, using {device_name}."
            )
        return False, (
            f"PyTorch {torch.__version__} has CUDA build {build_cuda}, but no GPU is visible "
            "inside the container. Check Docker Compose GPU runtime configuration."
        )
    except Exception as exc:
        return False, f"Could not inspect CUDA runtime: {exc}"


# ---------------------------------------------------------------------------
# XTTS text-chunking helpers
# ---------------------------------------------------------------------------

# Conservative per-language character limits to stay well within XTTS context.
# Turkish (tr) has the tightest advertised limit of 226 chars.
_XTTS_CHAR_LIMITS: dict[str, int] = {
    "tr": 200,
    "zh": 80,
    "ja": 80,
    "ko": 80,
    "ar": 180,
}
_XTTS_DEFAULT_CHAR_LIMIT = 220
_MAX_FAILURE_SAMPLE_CHARS = 140


def _get_xtts_char_limit(lang: str) -> int:
    return _XTTS_CHAR_LIMITS.get(lang.lower().split("-")[0], _XTTS_DEFAULT_CHAR_LIMIT)


def _normalize_tts_text(text: str) -> str:
    """Compatibility wrapper for the canonical TTS preprocessor."""
    return prepare_text_for_tts(text).spoken_text


def _clean_text_for_tts(text: str) -> str:
    """Strip malformed/control characters while preserving readable punctuation and spacing."""
    return clean_text_for_tts(text)


def _split_sentences(text: str) -> list[str]:
    return preprocess_split_sentences(text)


def _split_oversized_unit(unit: str, max_chars: int) -> list[str]:
    """Split one oversized sentence-like unit with clause-first boundaries."""
    return preprocess_split_oversized_unit(unit, max_chars=max_chars)


def _chunk_pause_seconds(chunk_text: str) -> float:
    """Short punctuation-aware pauses between chunks for smoother cadence."""
    cfg = get_preprocess_config()
    txt = (chunk_text or "").rstrip()
    if txt.endswith((".", "!", "?", "…")):
        return max(cfg.sentence_pause_ms, 0) / 1000.0
    if txt.endswith((",", ";", ":")):
        return max(int(cfg.sentence_pause_ms * 0.75), 0) / 1000.0
    return max(cfg.sentence_pause_ms, 0) / 1000.0


def _chunk_pause_seconds_at(index: int, chunk_text: str, chunk_pause_ms: list[int] | None = None) -> float:
    if chunk_pause_ms and 0 <= index < len(chunk_pause_ms):
        return max(float(chunk_pause_ms[index]), 0.0) / 1000.0
    return _chunk_pause_seconds(chunk_text)


def _write_silence_wav(path: Path, duration_sec: float) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "anullsrc=r=24000:cl=mono",
            "-t", f"{max(duration_sec, 0.05):.2f}",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _is_cuda_device_assert_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "device-side assert triggered" in msg
        or ("cuda error" in msg and "assert" in msg)
    )


def _clear_cuda_cache_best_effort() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _short_error_reason(error: object, max_chars: int = 180) -> str:
    if isinstance(error, BaseException):
        raw = str(error).strip() or error.__class__.__name__
    else:
        raw = str(error or "").strip()
    compact = re.sub(r"\s+", " ", raw)
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3] + "..."


_TRANSIENT_XTTS_ERROR_PATTERNS = (
    "connection aborted",
    "remote disconnected",
    "read timed out",
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "failed to establish a new connection",
    "network is unreachable",
    "temporary failure in name resolution",
    "httpsconnectionpool",
    "max retries exceeded",
    "ssl",
    "ssleoferror",
    "unexpected_eof_while_reading",
    "model failed to load",
    "stage=download",
)


def _is_transient_xtts_error(error_message: str) -> bool:
    msg = str(error_message or "").lower()
    if not msg:
        return False
    if "device-side assert triggered" in msg or "cuda assert" in msg or ("cuda error" in msg and "assert" in msg):
        return True
    return any(pattern in msg for pattern in _TRANSIENT_XTTS_ERROR_PATTERNS)


def _chunk_text_sample(text: str, max_chars: int = _MAX_FAILURE_SAMPLE_CHARS) -> str:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars] + "..."


def _classify_chunk_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if _is_cuda_device_assert_error(exc):
        return "runtime_cuda"
    if "index out of range" in msg or "shape" in msg or "tensor" in msg:
        return "bad_chunk"
    return "runtime"


def _split_text_for_tts(text: str, max_chars: int = 200) -> list[str]:
    """
    Split *text* into chunks of at most *max_chars* at natural boundaries.

    Priority: sentence end (.!?…) → comma → word boundary.
    Empty or whitespace-only chunks are discarded.
    """
    prepared = prepare_text_for_tts(
        text,
        max_chars_per_chunk=max_chars,
        target_chars_per_chunk=min(max(max_chars // 2, 80), max_chars),
    )
    return prepared.chunks


def _coerce_pause_ms(value: object, default_ms: int) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return max(int(default_ms), 0)


def _prepared_text_from_chunks(req: SynthesizeRequest) -> TTSPreparedText | None:
    if not req.already_prepared or not req.chunks:
        return None

    cfg = get_preprocess_config()
    chunks: list[str] = []
    pauses: list[int] = []
    for index, raw_chunk in enumerate(req.chunks):
        chunk = clean_text_for_tts(str(raw_chunk or "")).strip()
        if not chunk:
            continue
        chunks.append(chunk)
        pauses.append(
            _coerce_pause_ms(req.chunk_pause_ms[index], cfg.sentence_pause_ms)
            if req.chunk_pause_ms and index < len(req.chunk_pause_ms)
            else cfg.sentence_pause_ms
        )
    if not chunks:
        return None
    if pauses:
        pauses[-1] = 0

    spoken_text = " ".join(chunks).strip()
    return TTSPreparedText(
        raw_text=str(req.original_text or req.text or ""),
        normalized_text=str(req.normalized_text or spoken_text),
        spoken_text=spoken_text,
        chunks=chunks,
        warnings=[],
        chunk_pause_ms=pauses,
        original_text=str(req.original_text or req.text or ""),
        tts_normalization_language=str(req.tts_normalization_language or req.language or ""),
        tts_normalization_rules_applied=list(req.tts_normalization_rules_applied or []),
        unknown_terms=list(req.unknown_terms or []),
        ambiguous_terms=list(req.ambiguous_terms or []),
    )


def _ensure_xtts_safe_chunks(
    chunks: list[str],
    chunk_pause_ms: list[int] | None,
    char_limit: int,
) -> tuple[list[str], list[int]]:
    cfg = get_preprocess_config()
    safe_chunks: list[str] = []
    safe_pauses: list[int] = []

    for index, raw_chunk in enumerate(chunks):
        chunk_text = str(raw_chunk or "").strip()
        if not chunk_text:
            continue

        split_chunks = (
            _split_text_for_tts(chunk_text, max_chars=char_limit)
            if len(chunk_text) > char_limit
            else [chunk_text]
        )
        split_chunks = [
            chunk
            for chunk in split_chunks
            if chunk.strip() and any(char.isalpha() for char in chunk)
        ]
        if not split_chunks:
            continue

        base_pause = (
            _coerce_pause_ms(chunk_pause_ms[index], cfg.sentence_pause_ms)
            if chunk_pause_ms and index < len(chunk_pause_ms)
            else cfg.sentence_pause_ms
        )
        if len(split_chunks) > 1:
            safe_pauses.extend([cfg.sentence_pause_ms] * (len(split_chunks) - 1))
        safe_pauses.append(base_pause)
        safe_chunks.extend(split_chunks)

    if safe_pauses:
        safe_pauses[-1] = 0
    return safe_chunks, safe_pauses


# ---------------------------------------------------------------------------
# XTTS v2 Provider State
# ---------------------------------------------------------------------------
_XTTS_MODEL = None
_XTTS_MODEL_CPU = None
_XTTS_LOAD_ERROR = None
_XTTS_LOCK = threading.Lock()
_XTTS_WARMUP_STARTED_AT: float | None = None
_XTTS_WARMUP_FINISHED_AT: float | None = None
_XTTS_WARMUP_ERROR: str | None = None
_XTTS_WARMUP_IN_PROGRESS = False


def _classify_xtts_init_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "cuda" in msg or "cudnn" in msg or "nvrtc" in msg:
        return "gpu_init"
    if "download" in msg or "http" in msg or "connection" in msg or "timeout" in msg:
        return "download"
    return "warmup"


def _xtts_ready_state() -> tuple[bool, str]:
    if not XTTS_ENABLED:
        return False, "disabled"
    if _XTTS_MODEL is not None:
        return True, "ready"
    if _XTTS_WARMUP_IN_PROGRESS:
        return False, "warming_up"
    if _XTTS_WARMUP_ERROR:
        return False, "failed"
    return False, "not_initialized"


def _warmup_xtts_once() -> None:
    global _XTTS_WARMUP_STARTED_AT, _XTTS_WARMUP_FINISHED_AT, _XTTS_WARMUP_ERROR, _XTTS_WARMUP_IN_PROGRESS
    if not XTTS_ENABLED:
        return
    if _XTTS_MODEL is not None:
        return

    if _XTTS_WARMUP_IN_PROGRESS:
        return

    _XTTS_WARMUP_IN_PROGRESS = True
    _XTTS_WARMUP_ERROR = None
    _XTTS_WARMUP_STARTED_AT = time.time()
    logger.info("XTTS warmup started")

    try:
        _get_xtts_model()
    except Exception as exc:
        stage = _classify_xtts_init_error(exc)
        _XTTS_WARMUP_ERROR = f"stage={stage} error={exc}"
        logger.warning("XTTS warmup failed (stage=%s): %s", stage, exc)
    finally:
        _XTTS_WARMUP_IN_PROGRESS = False
        _XTTS_WARMUP_FINISHED_AT = time.time()
        elapsed = (_XTTS_WARMUP_FINISHED_AT - _XTTS_WARMUP_STARTED_AT) if _XTTS_WARMUP_STARTED_AT else 0.0
        ready, state = _xtts_ready_state()
        logger.info("XTTS warmup completed: ready=%s state=%s elapsed=%.2fs", ready, state, elapsed)


def _start_xtts_warmup_background() -> None:
    if not XTTS_ENABLED:
        return
    if _XTTS_MODEL is not None or _XTTS_WARMUP_IN_PROGRESS:
        return
    t = threading.Thread(target=_warmup_xtts_once, daemon=True, name="xtts-warmup")
    t.start()


def reset_xtts_model_state(reason: str = "") -> None:
    """Clear cached XTTS model/load state so a later attempt can reload it."""
    global _XTTS_MODEL, _XTTS_MODEL_CPU, _XTTS_LOAD_ERROR, _XTTS_WARMUP_ERROR
    with _XTTS_LOCK:
        _XTTS_MODEL = None
        _XTTS_MODEL_CPU = None
        _XTTS_LOAD_ERROR = None
        _XTTS_WARMUP_ERROR = None
    if reason:
        logger.warning("XTTS model state reset after transient failure: %s", _short_error_reason(reason))
    else:
        logger.warning("XTTS model state reset")


def _get_xtts_model():
    global _XTTS_MODEL, _XTTS_LOAD_ERROR
    if not XTTS_ENABLED:
        raise RuntimeError("XTTS provider is disabled via XTTS_ENABLED=0")
    if _XTTS_MODEL is not None:
        return _XTTS_MODEL
    if _XTTS_LOAD_ERROR is not None:
        raise RuntimeError(_XTTS_LOAD_ERROR)
        
    with _XTTS_LOCK:
        if _XTTS_MODEL is not None:
            return _XTTS_MODEL
        if _XTTS_LOAD_ERROR is not None:
            raise RuntimeError(_XTTS_LOAD_ERROR)
            
        logger.info("Initializing XTTS model (this may take a while)...")
        try:
            import torch
            
            # --- PyTorch 2.6+ workaround for Coqui XTTS checkpoints ---
            _original_load = torch.load
            def _safe_load(*args, **kwargs):
                kwargs["weights_only"] = False
                return _original_load(*args, **kwargs)
            torch.load = _safe_load
            # -------------------------------------------------------------
            
            use_gpu, runtime_msg = _describe_torch_runtime(torch)
            logger.info("Torch runtime: %s", runtime_msg)
            logger.info(
                "Container GPU env: NVIDIA_VISIBLE_DEVICES=%s NVIDIA_DRIVER_CAPABILITIES=%s",
                os.environ.get("NVIDIA_VISIBLE_DEVICES", "<unset>"),
                os.environ.get("NVIDIA_DRIVER_CAPABILITIES", "<unset>"),
            )
            
            from TTS.api import TTS
            try:
                import TTS as _tts_pkg
                logger.info("TTS package version: %s", getattr(_tts_pkg, "__version__", "unknown"))
            except Exception:
                logger.debug("TTS package version not available")

            logger.info("XTTS using GPU: %s", use_gpu)
            
            _XTTS_MODEL = TTS(
                model_name="tts_models/multilingual/multi-dataset/xtts_v2", 
                progress_bar=False, 
                gpu=use_gpu
            )
            logger.info("XTTS model loaded successfully.")
            return _XTTS_MODEL
        except Exception as exc:
            stage = _classify_xtts_init_error(exc)
            _XTTS_LOAD_ERROR = f"stage={stage} error={exc}"
            logger.exception("Failed to load XTTS model (stage=%s): %s", stage, exc)
            raise RuntimeError(f"Failed to load XTTS model: {exc}") from exc


def _get_xtts_model_cpu():
    global _XTTS_MODEL_CPU
    if not XTTS_ENABLED:
        raise RuntimeError("XTTS provider is disabled via XTTS_ENABLED=0")
    if _XTTS_MODEL_CPU is not None:
        return _XTTS_MODEL_CPU

    with _XTTS_LOCK:
        if _XTTS_MODEL_CPU is not None:
            return _XTTS_MODEL_CPU
        logger.info("Initializing XTTS CPU fallback model ...")
        try:
            import torch

            _original_load = torch.load

            def _safe_load(*args, **kwargs):
                kwargs["weights_only"] = False
                return _original_load(*args, **kwargs)

            torch.load = _safe_load

            from TTS.api import TTS

            _XTTS_MODEL_CPU = TTS(
                model_name="tts_models/multilingual/multi-dataset/xtts_v2",
                progress_bar=False,
                gpu=False,
            )
            logger.info("XTTS CPU fallback model loaded.")
            return _XTTS_MODEL_CPU
        except Exception as exc:
            logger.exception("Failed to load XTTS CPU fallback model: %s", exc)
            raise RuntimeError(f"Failed to load XTTS CPU fallback model: {exc}") from exc


def _synthesize_xtts_v2(
    text: str,
    voice_id: str,
    lang: str,
    out_path: Path,
    chunks: list[str] | None = None,
    chunk_pause_ms: list[int] | None = None,
) -> float:
    """
    Synthesize *text* to *out_path* using Coqui XTTS v2 voice cloning.

    Long texts are split into safe-length chunks (respecting per-language
    character limits) and each chunk is synthesised separately.  The resulting
    WAV segments are concatenated by ffmpeg into a single MP3 so the caller
    always receives one audio file.
    """
    if not voice_id:
        raise ValueError("voice_id is required for XTTS v2 voice cloning")

    voices_dir = STORAGE_ROOT / "voices"
    ref_wav = voices_dir / f"{voice_id}.wav"

    if not ref_wav.exists():
        raise FileNotFoundError(f"Reference voice not found: {ref_wav}")

    model = _get_xtts_model()

    char_limit = _get_xtts_char_limit(lang)
    if not chunks:
        prepared = prepare_text_for_tts(
            text,
            language=lang,
            max_chars_per_chunk=char_limit,
            target_chars_per_chunk=min(get_preprocess_config().target_chars_per_chunk, char_limit),
        )
        chunks = prepared.chunks
        chunk_pause_ms = prepared.chunk_pause_ms
    # Drop chunks that have no alphabetic content; they can cause XTTS index errors.
    chunks, chunk_pause_ms = _ensure_xtts_safe_chunks(chunks, chunk_pause_ms, char_limit)
    if not chunks:
        raise ValueError("Text contains no synthesisable content after chunking")

    logger.info(
        "XTTS synthesis: %d chunk(s), char_limit=%d, voice=%s, lang=%s",
        len(chunks), char_limit, voice_id, lang,
    )

    with tempfile.TemporaryDirectory() as tmpd:
        concat_items: list[Path] = []
        for i, chunk_text in enumerate(chunks):
            chunk_wav = Path(tmpd) / f"chunk_{i:03d}.wav"
            logger.info(
                "XTTS chunk %d/%d  %d chars: %r…",
                i + 1, len(chunks), len(chunk_text), chunk_text[:60],
            )
            try:
                model.tts_to_file(
                    text=chunk_text,
                    speaker_wav=[str(ref_wav)],
                    language=lang,
                    file_path=str(chunk_wav),
                )
                concat_items.append(chunk_wav)
                if i < len(chunks) - 1:
                    silence_wav = Path(tmpd) / f"gap_{i:03d}.wav"
                    _write_silence_wav(silence_wav, _chunk_pause_seconds_at(i, chunk_text, chunk_pause_ms))
                    concat_items.append(silence_wav)
            except Exception as exc:
                if XTTS_ABORT_ON_CUDA_ASSERT and _is_cuda_device_assert_error(exc):
                    _clear_cuda_cache_best_effort()
                    logger.warning(
                        "XTTS CUDA assert at chunk %d/%d (lang=%s voice=%s chars=%d). Trying XTTS CPU fallback.",
                        i + 1,
                        len(chunks),
                        lang,
                        voice_id,
                        len(chunk_text),
                    )
                    cpu_model = _get_xtts_model_cpu()
                    cpu_model.tts_to_file(
                        text=chunk_text,
                        speaker_wav=[str(ref_wav)],
                        language=lang,
                        file_path=str(chunk_wav),
                    )
                    concat_items.append(chunk_wav)
                    if i < len(chunks) - 1:
                        silence_wav = Path(tmpd) / f"gap_cpu_{i:03d}.wav"
                        _write_silence_wav(silence_wav, _chunk_pause_seconds_at(i, chunk_text, chunk_pause_ms))
                        concat_items.append(silence_wav)
                    continue

                logger.warning(
                    "XTTS chunk %d/%d failed (reason=%s lang=%s voice=%s chars=%d sample=%r); retrying smaller pieces: %s",
                    i + 1,
                    len(chunks),
                    _classify_chunk_error(exc),
                    lang,
                    voice_id,
                    len(chunk_text),
                    _chunk_text_sample(chunk_text),
                    exc,
                )

                recovered = False
                smaller_limit = max(80, char_limit // 2)
                failed_pieces: set[str] = set()
                for j, small_piece in enumerate(_split_text_for_tts(chunk_text, max_chars=smaller_limit)):
                    piece_key = small_piece.strip().lower()
                    if piece_key in failed_pieces:
                        continue

                    retry_wav = Path(tmpd) / f"chunk_{i:03d}_retry_{j:02d}.wav"
                    try:
                        model.tts_to_file(
                            text=small_piece,
                            speaker_wav=[str(ref_wav)],
                            language=lang,
                            file_path=str(retry_wav),
                        )
                        concat_items.append(retry_wav)
                        recovered = True
                    except Exception as retry_exc:
                        failed_pieces.add(piece_key)
                        if XTTS_ABORT_ON_CUDA_ASSERT and _is_cuda_device_assert_error(retry_exc):
                            _clear_cuda_cache_best_effort()
                            logger.warning(
                                "XTTS CUDA assert at retry chunk %d.%d (lang=%s voice=%s chars=%d). Trying XTTS CPU fallback.",
                                i + 1,
                                j + 1,
                                lang,
                                voice_id,
                                len(small_piece),
                            )
                            cpu_model = _get_xtts_model_cpu()
                            cpu_model.tts_to_file(
                                text=small_piece,
                                speaker_wav=[str(ref_wav)],
                                language=lang,
                                file_path=str(retry_wav),
                            )
                            concat_items.append(retry_wav)
                            recovered = True
                            continue

                        logger.warning(
                            "XTTS retry chunk %d.%d failed (reason=%s lang=%s voice=%s chars=%d sample=%r): %s",
                            i + 1,
                            j + 1,
                            _classify_chunk_error(retry_exc),
                            lang,
                            voice_id,
                            len(small_piece),
                            _chunk_text_sample(small_piece),
                            retry_exc,
                        )

                if recovered and i < len(chunks) - 1:
                    silence_wav = Path(tmpd) / f"gap_retry_{i:03d}.wav"
                    _write_silence_wav(silence_wav, _chunk_pause_seconds_at(i, chunk_text, chunk_pause_ms))
                    concat_items.append(silence_wav)

        if not concat_items:
            raise RuntimeError("All XTTS chunks failed — cannot produce audio")

        # Concatenate chunk WAVs → final MP3 via ffmpeg
        concat_list = Path(tmpd) / "concat.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for cp in concat_items:
                f.write(f"file '{cp}'\n")

        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-codec:a", "libmp3lame",
                "-q:a", "4",
                str(out_path),
            ],
            check=True,
            capture_output=True,
        )

    duration = _audio_duration(out_path)
    logger.info(
        "XTTS OK  → %s  lang=%s  %.2fs  (%d chunk(s))",
        out_path.name, lang, duration, len(chunks),
    )
    return duration


def _synthesize_gtts(text: str, lang: str, out_path: Path) -> float:
    """
    Synthesize *text* to *out_path* using gTTS.

    Returns the audio duration in seconds.

    Raises
    ------
    ImportError     If gTTS is not installed.
    Exception       If synthesis or network call fails.
    """
    from gtts import gTTS  # type: ignore

    tts = gTTS(text=text, lang=lang, slow=False)
    tts.save(str(out_path))
    duration = _audio_duration(out_path)
    logger.info("gTTS OK  → %s  lang=%s  %.2fs", out_path.name, lang, duration)
    return duration


def _synthesize_silent(duration_sec: float, out_path: Path) -> float:
    """
    Generate a silent MP3 of *duration_sec* seconds using ffmpeg.

    This is the last-resort fallback so the worker always gets a valid
    audio file and the pipeline can continue.

    Returns *duration_sec* on success.
    """
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "anullsrc=r=44100:cl=mono",
            "-t", str(duration_sec),
            "-q:a", "9",
            "-acodec", "libmp3lame",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    logger.info("Silent fallback OK → %s  %.2fs", out_path.name, duration_sec)
    return duration_sec


def _audio_url(path: Path) -> str:
    """Build the HTTP URL for *path* served by GET /audio/{filename}."""
    return f"{TTS_SERVICE_URL}/audio/{path.name}"


def _prepare_request_for_tts(req: SynthesizeRequest, lang: str) -> TTSPreparedText:
    cfg = get_preprocess_config()
    prepared = _prepared_text_from_chunks(req)
    if prepared is not None:
        return prepared

    if req.normalization_enabled is False:
        return prepare_text_for_tts(
            req.text,
            language=lang,
            already_prepared=True,
            max_chars_per_chunk=cfg.max_chars_per_chunk,
            target_chars_per_chunk=cfg.target_chars_per_chunk,
        )

    override_glossary = _build_preview_override_glossary(req)
    if override_glossary:
        pre_substituted, pre_rules, replacement_map = _apply_pre_normalization_overrides(
            req.text,
            override_glossary,
            lang,
            source="project_tts_override",
        )
        prepared = prepare_text_for_tts(
            pre_substituted,
            language=lang,
            max_chars_per_chunk=cfg.max_chars_per_chunk,
            target_chars_per_chunk=cfg.target_chars_per_chunk,
        )
        spoken_text = prepared.spoken_text
        normalized_text = prepared.normalized_text
        chunks = list(prepared.chunks or [])
        for placeholder, replacement_value in replacement_map.items():
            spoken_text = spoken_text.replace(placeholder, replacement_value)
            normalized_text = normalized_text.replace(placeholder, replacement_value)
            chunks = [chunk.replace(placeholder, replacement_value) for chunk in chunks]
        return TTSPreparedText(
            raw_text=str(req.text or ""),
            normalized_text=normalized_text,
            spoken_text=spoken_text,
            chunks=chunks,
            warnings=list(prepared.warnings or []),
            chunk_pause_ms=list(prepared.chunk_pause_ms or []),
            original_text=str(req.text or ""),
            tts_normalization_language=prepared.tts_normalization_language,
            tts_normalization_rules_applied=pre_rules + list(prepared.tts_normalization_rules_applied or []),
            unknown_terms=list(prepared.unknown_terms or []),
            ambiguous_terms=list(prepared.ambiguous_terms or []),
        )

    return prepare_text_for_tts(
        req.text,
        language=lang,
        max_chars_per_chunk=cfg.max_chars_per_chunk,
        target_chars_per_chunk=cfg.target_chars_per_chunk,
    )


def _request_override_summary(req: SynthesizeRequest) -> dict[str, int]:
    technical = req.technical_overrides if isinstance(req.technical_overrides, dict) else {}
    abbreviation = req.abbreviation_overrides if isinstance(req.abbreviation_overrides, dict) else {}
    mixed_word = req.mixed_word_overrides if isinstance(req.mixed_word_overrides, dict) else {}
    return {
        "technical_count": len(technical),
        "abbreviation_count": len(abbreviation),
        "mixed_word_count": len(mixed_word),
        "merged_override_count": len({**technical, **abbreviation, **mixed_word}),
    }


def _format_xtts_fallback_reason(reason: str, transient: bool) -> str:
    lower_reason = reason.lower()
    if "reference voice not found" in lower_reason or "no such file" in lower_reason:
        return "xtts_v2_unavailable: reference voice file not found"
    if "xtts provider is disabled" in lower_reason:
        return "xtts_v2_unavailable: disabled"
    if "no voice_id" in lower_reason:
        return "xtts_v2_unavailable: no voice_id provided"
    if transient:
        return "xtts_v2_temporarily_unavailable: transient_model_load_network_error"
    return f"xtts_v2_failed: {_short_error_reason(reason)}"


def _public_xtts_failure_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "xtts_error_transient": bool(metadata.get("xtts_error_transient", False)),
        "xtts_recovery_attempts": int(metadata.get("xtts_recovery_attempts") or 0),
    }
    if metadata.get("xtts_attempts") is not None:
        result["xtts_attempts"] = int(metadata.get("xtts_attempts") or 0)
    if metadata.get("xtts_failure_reason"):
        result["xtts_failure_reason"] = _short_error_reason(metadata["xtts_failure_reason"])
    return result


def _synthesize_xtts_v2_with_recovery(
    text: str,
    voice_id: str,
    lang: str,
    out_path: Path,
    chunks: list[str] | None = None,
    chunk_pause_ms: list[int] | None = None,
) -> tuple[float | None, dict[str, Any]]:
    metadata: dict[str, Any] = {
        "xtts_attempts": 0,
        "xtts_recovery_attempts": 0,
        "xtts_error_transient": False,
        "xtts_failure_reason": "",
        "fallback_reason": "",
    }
    max_retries = max(0, XTTS_LOAD_RECOVERY_ATTEMPTS)

    for attempt_index in range(max_retries + 1):
        metadata["xtts_attempts"] = attempt_index + 1
        try:
            duration = _synthesize_xtts_v2(
                text,
                voice_id,
                lang,
                out_path,
                chunks=chunks,
                chunk_pause_ms=chunk_pause_ms,
            )
            return duration, metadata
        except Exception as exc:  # noqa: BLE001
            reason = _short_error_reason(exc)
            transient = _is_transient_xtts_error(reason)
            metadata["xtts_failure_reason"] = reason
            metadata["xtts_error_transient"] = transient
            metadata["fallback_reason"] = _format_xtts_fallback_reason(reason, transient)
            out_path.unlink(missing_ok=True)

            if isinstance(exc, FileNotFoundError):
                logger.warning("XTTS reference voice unavailable; falling back to gTTS")
                break
            if not transient:
                logger.warning("XTTS failed with non-transient error (%s); falling back to gTTS", reason)
                break
            if attempt_index >= max_retries:
                logger.warning(
                    "XTTS transient failure persisted after %d attempt(s): %s",
                    attempt_index + 1,
                    reason,
                )
                break

            metadata["xtts_recovery_attempts"] = int(metadata["xtts_recovery_attempts"]) + 1
            reset_xtts_model_state(reason)
            if XTTS_LOAD_RECOVERY_BACKOFF_SEC > 0:
                time.sleep(XTTS_LOAD_RECOVERY_BACKOFF_SEC)

    return None, metadata


def _attach_request_tts_metadata(response: dict[str, Any], req: SynthesizeRequest, prepared: TTSPreparedText) -> dict[str, Any]:
    if req.normalization_enabled is not None:
        response["normalization_enabled"] = req.normalization_enabled
    if req.normalization_mode is not None:
        response["normalization_mode"] = req.normalization_mode
    if req.unknown_word_strategy is not None:
        response["unknown_word_strategy"] = req.unknown_word_strategy
    if req.provider_preference is not None:
        response["provider_preference"] = req.provider_preference
    if req.technical_overrides or req.abbreviation_overrides or req.mixed_word_overrides:
        response["applied_overrides"] = _request_override_summary(req)
    response["spoken_text"] = prepared.spoken_text
    response["original_text"] = prepared.original_text or prepared.raw_text
    response["unknown_terms"] = list(prepared.unknown_terms or [])
    response["ambiguous_terms"] = list(prepared.ambiguous_terms or [])
    response["fallback_used"] = bool(
        response.get("fallback_used", str(response.get("provider") or "").lower() == "fallback")
    )
    response["fallback_reason"] = str(response.get("fallback_reason") or response.get("message") or "")
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    """Liveness probe."""
    ready, state = _xtts_ready_state()
    return {
        "status": "ok",
        "xtts_enabled": XTTS_ENABLED,
        "xtts_ready": ready,
        "xtts_state": state,
    }


@app.get("/ready")
def ready() -> dict:
    """Readiness probe: returns ready only when XTTS is loaded or explicitly disabled."""
    ready, state = _xtts_ready_state()
    payload = {
        "status": "ready" if ready else "not_ready",
        "xtts_enabled": XTTS_ENABLED,
        "xtts_ready": ready,
        "xtts_state": state,
        "warmup_started_at": _XTTS_WARMUP_STARTED_AT,
        "warmup_finished_at": _XTTS_WARMUP_FINISHED_AT,
        "warmup_error": _XTTS_WARMUP_ERROR,
    }
    if not XTTS_ENABLED:
        return payload
    if not ready:
        raise HTTPException(status_code=503, detail=payload)
    return payload


@app.on_event("startup")
def on_startup() -> None:
    if not XTTS_PRELOAD_ON_STARTUP:
        logger.info("XTTS preload on startup disabled")
        return

    logger.info("XTTS preload on startup enabled (blocking=%s)", XTTS_WARMUP_BLOCKING)
    if XTTS_WARMUP_BLOCKING:
        _warmup_xtts_once()
    else:
        _start_xtts_warmup_background()


@app.post("/synthesize")
def synthesize(req: SynthesizeRequest) -> dict:
    """
    Synthesize speech for ``req.text``.

    **Always returns HTTP 200** — if gTTS fails the endpoint returns a short
    silent MP3 as fallback so the worker pipeline is never blocked.

    Response JSON::

        {
          "audio_url": "http://tts_service:8001/audio/<uuid>.mp3",
          "duration":  3.14,
          "provider":  "xtts_v2" | "gTTS" | "fallback",
          "message":   "optional error description"   # only on fallback
        }

    The worker (``scripts/tts_client.py``) will:
    1. See that Content-Type is ``application/json`` (not ``audio/*``).
    2. Extract ``audio_url`` from the body.
    3. Download the audio and save it to its own ``out_path``.
    """
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=422, detail="Text is required")

    lang = _detect_tts_language(req.text, req.language)
    prepared = _prepare_request_for_tts(req, lang)
    tts_text = prepared.spoken_text or prepared.normalized_text

    logger.info(
        "synthesize  voice=%r  lang=%r  raw_len=%d  spoken_len=%d  chunks=%d  rules=%d",
        req.voice_id, lang, len(req.text), len(tts_text), len(prepared.chunks),
        len(prepared.tts_normalization_rules_applied),
    )
    if prepared.tts_normalization_rules_applied:
        logger.info(
            "TTS normalization rules applied lang=%s rules=%s",
            prepared.tts_normalization_language or lang,
            prepared.tts_normalization_rules_applied,
        )

    # ---- Attempt 1: XTTS v2 ---------------------------------------------
    out_path = _new_audio_path()
    xtts_metadata: dict[str, Any] = {}
    if req.voice_id:
        duration, xtts_metadata = _synthesize_xtts_v2_with_recovery(
            tts_text,
            req.voice_id,
            lang,
            out_path,
            chunks=prepared.chunks,
            chunk_pause_ms=prepared.chunk_pause_ms,
        )
        if duration is not None:
            response = {
                "audio_url": _audio_url(out_path),
                "duration":  duration,
                "provider":  "xtts_v2",
                "xtts_recovery_attempts": int(xtts_metadata.get("xtts_recovery_attempts") or 0),
                "xtts_attempts": int(xtts_metadata.get("xtts_attempts") or 1),
            }
            if prepared.warnings:
                response["preprocessing_warnings"] = prepared.warnings
            if prepared.tts_normalization_rules_applied:
                response["tts_normalization_language"] = prepared.tts_normalization_language or lang
                response["tts_normalization_rules_applied"] = prepared.tts_normalization_rules_applied
            response["fallback_used"] = False
            response["fallback_reason"] = ""
            return _attach_request_tts_metadata(response, req, prepared)
        fallback_reason = str(xtts_metadata.get("fallback_reason") or "xtts_v2_failed")
    else:
        fallback_reason = _format_xtts_fallback_reason("no voice_id provided", transient=False)

    # ---- Attempt 2: gTTS ------------------------------------------------
    out_path = _new_audio_path()
    try:
        duration = _synthesize_gtts(tts_text, lang, out_path)
        response = {
            "audio_url": _audio_url(out_path),
            "duration":  duration,
            "provider":  "gTTS",
        }
        if prepared.warnings:
            response["preprocessing_warnings"] = prepared.warnings
        if prepared.tts_normalization_rules_applied:
            response["tts_normalization_language"] = prepared.tts_normalization_language or lang
            response["tts_normalization_rules_applied"] = prepared.tts_normalization_rules_applied
        if fallback_reason:
            response["fallback_used"] = True
            response["fallback_reason"] = fallback_reason
            response.update(_public_xtts_failure_metadata(xtts_metadata))
        else:
            response["fallback_used"] = False
            response["fallback_reason"] = ""
        return _attach_request_tts_metadata(response, req, prepared)
    except ImportError:
        fallback_reason = f"{fallback_reason} | gTTS not installed"
        logger.warning("gTTS not installed — using silent fallback")
    except Exception as exc:  # noqa: BLE001
        fallback_reason = f"{fallback_reason} | gTTS failed: {exc}"
        logger.warning("gTTS failed (%s) — using silent fallback", exc)
        # Clean up partial file before trying fallback path
        out_path.unlink(missing_ok=True)

    # ---- Attempt 3: ffmpeg silent audio ----------------------------------
    out_path = _new_audio_path()
    try:
        duration = _synthesize_silent(FALLBACK_DURATION_SEC, out_path)
        response = {
            "audio_url": _audio_url(out_path),
            "duration":  duration,
            "provider":  "fallback",
            "message":   fallback_reason,
            "preprocessing_warnings": prepared.warnings,
            "tts_normalization_language": prepared.tts_normalization_language or lang,
            "tts_normalization_rules_applied": prepared.tts_normalization_rules_applied,
            "fallback_used": True,
            "fallback_reason": fallback_reason,
        }
        response.update(_public_xtts_failure_metadata(xtts_metadata))
        return _attach_request_tts_metadata(response, req, prepared)
    except Exception as exc:
        # Both strategies failed — ffmpeg must be missing or broken.
        # Only now do we return a 500.
        logger.exception("ffmpeg silent fallback also failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=(
                f"All TTS strategies failed. "
                f"Reasons: {fallback_reason}. "
                f"ffmpeg fallback: {exc}"
            ),
        )


# ---------------------------------------------------------------------------
# Preview route  (Phase 1 — no audio synthesis)
# ---------------------------------------------------------------------------

def _build_preview_override_glossary(req: NormalizationPreviewRequest) -> dict[str, str]:
    """
    Merge all three override maps into a single dict with priority:

        mixed_word_overrides > abbreviation_overrides > technical_overrides

    Later writes win (technical first → mixed last), so mixed always beats
    abbreviation, and abbreviation beats technical.
    """
    merged: dict[str, str] = {}
    for source in (
        req.technical_overrides or {},
        req.abbreviation_overrides or {},
        req.mixed_word_overrides or {},
    ):
        for term, spoken in source.items():
            t = str(term or "").strip()
            s = str(spoken or "").strip()
            if t and s:
                merged[t] = s
    return merged


def _apply_pre_normalization_overrides(
    text: str,
    override_glossary: dict[str, str],
    language: str,
    source: str = "preview_pre_override",
) -> tuple[str, list[dict[str, Any]], dict[str, str]]:
    """
    Apply *override_glossary* to *text* **before** the standard normalizer
    runs, using the same longest-match / word-boundary / protected-span
    engine as the existing tts_preprocess glossary.

    This ensures that a raw-input term like "ChatGPT" can be overridden to
    "chat gpt" before the default TR glossary would expand it to
    "Çet Ci Pi Ti".

    Returns ``(substituted_text, pre_override_rules, replacement_map)``.
    Rules are tagged with ``source`` so callers can distinguish them from
    post-normalization rules.
    
    replacement_map is a dict of placeholder_token -> replacement_value
    used to protect replacements from re-normalization. The substituted_text
    contains these placeholder tokens instead of the actual replacement values.
    """
    if not text or not override_glossary:
        return text, [], {}

    from tts_preprocess.glossary import apply_glossary_with_rules

    # Create placeholder tokens for each replacement to protect them from re-normalization.
    # Map: original_term -> placeholder
    placeholder_glossary: dict[str, str] = {}
    # Map: placeholder -> actual_replacement (for later restoration)
    replacement_map: dict[str, str] = {}
    
    for index, (original_term, replacement_value) in enumerate(override_glossary.items()):
        placeholder = f"__OVERRIDE_{index}__"
        placeholder_glossary[original_term] = placeholder
        replacement_map[placeholder] = replacement_value

    # Apply overrides using placeholders so the normalizer can't re-normalize the replacement values.
    substituted, rules = apply_glossary_with_rules(text, placeholder_glossary, language=language)
    for rule in rules:
        rule["source"] = source
        # Track the actual replacement value for reference
        if "replacement" in rule:
            for idx, placeholder in enumerate(replacement_map.keys()):
                if placeholder in rule.get("replacement", ""):
                    rule["actual_replacement"] = replacement_map[placeholder]
                    break
    
    return substituted, rules, replacement_map


def _run_preview_normalization(req: NormalizationPreviewRequest) -> NormalizationPreviewResponse:
    """
    Execute the tts_preprocess pipeline with highest-priority runtime overrides.

    Override precedence (highest → lowest):
      1. mixed_word_overrides   )
      2. abbreviation_overrides ) applied as PRE-normalization substitutions
      3. technical_overrides    )   on the raw input text
      4. default glossary / standard normalization

    Pre-normalization application means raw user terms (e.g. "ChatGPT") are
    substituted before the standard glossary can expand them.  The substituted
    tokens are then passed through the standard normalizer; because they no
    longer match glossary keys they pass through untouched.

    original_text is always preserved unchanged in the response.
    Override maps never touch glossary.json.
    Fail-open on any error.
    """
    from tts_preprocess.segmenter import split_text_to_chunks

    lang = _detect_tts_language(req.text, req.language)
    original_text = req.text.strip()

    override_glossary = _build_preview_override_glossary(req)
    applied_overrides: dict[str, Any] = {
        "technical_overrides": req.technical_overrides or {},
        "abbreviation_overrides": req.abbreviation_overrides or {},
        "mixed_word_overrides": req.mixed_word_overrides or {},
        "merged_override_count": len(override_glossary),
    }

    warnings: list[str] = []
    rules_applied: list[dict[str, Any]] = []
    error: str | None = None
    fallback_used = False

    try:
        if not req.normalization_enabled:
            # Skip ALL normalization — return original text as spoken text.
            # Overrides are also skipped so the response is exactly the
            # original input (mirrors the "disabled" contract).
            warnings.append("normalization_disabled")
            prepared = prepare_text_for_tts(
                original_text,
                language=lang,
                already_prepared=True,
            )
            spoken_text = prepared.spoken_text
            normalized_text = prepared.normalized_text
            unknown_terms = list(prepared.unknown_terms or [])
            ambiguous_terms = list(prepared.ambiguous_terms or [])

        else:
            # ---- Step 1: pre-normalization override pass -----------------
            # Apply override maps to the raw input FIRST using placeholders
            # so user-specified replacements are protected from re-normalization.
            if override_glossary:
                pre_substituted, pre_rules, replacement_map = _apply_pre_normalization_overrides(
                    original_text, override_glossary, lang
                )
                rules_applied.extend(pre_rules)
            else:
                pre_substituted = original_text
                pre_rules = []
                replacement_map = {}

            # ---- Step 2: standard normalization on the substituted text --
            # The normalizer will NOT re-normalize replacement values because
            # they are hidden behind placeholder tokens (e.g., __OVERRIDE_0__).
            prepared = prepare_text_for_tts(pre_substituted, language=lang)
            spoken_text = prepared.spoken_text
            normalized_text = prepared.normalized_text
            unknown_terms = list(prepared.unknown_terms or [])
            ambiguous_terms = list(prepared.ambiguous_terms or [])
            warnings.extend(prepared.warnings)
            # Append standard rules (pre-override rules are already first)
            rules_applied.extend(prepared.tts_normalization_rules_applied)
            
            # ---- Step 2b: restore protected replacements ------------------
            # Now that normalization is done, restore the placeholder tokens
            # with the actual replacement values so they appear exactly as
            # the user specified them (e.g., "chat gpt" stays "chat gpt").
            for placeholder, replacement_value in replacement_map.items():
                spoken_text = spoken_text.replace(placeholder, replacement_value)
                normalized_text = normalized_text.replace(placeholder, replacement_value)

        # ---- Step 3: re-chunk the final spoken text ----------------------
        cfg = get_preprocess_config()
        chunks, chunk_pause_ms, chunk_warnings = split_text_to_chunks(
            spoken_text,
            max_chars=cfg.max_chars_per_chunk,
            target_chars=cfg.target_chars_per_chunk,
            sentence_pause_ms=cfg.sentence_pause_ms,
            paragraph_pause_ms=cfg.paragraph_pause_ms,
        )
        warnings.extend(w for w in chunk_warnings if w not in warnings)

    except Exception as exc:  # noqa: BLE001
        logger.warning("Preview normalization failed (fail-open): %s", exc)
        error = str(exc)
        fallback_used = True
        spoken_text = original_text
        normalized_text = original_text
        chunks = [original_text] if original_text else []
        chunk_pause_ms = [0] if chunks else []
        unknown_terms = []
        ambiguous_terms = []
        warnings.append("preview_failed_fallback_to_original")

    return NormalizationPreviewResponse(
        original_text=original_text,
        normalized_text=normalized_text,
        spoken_text=spoken_text,
        chunks=chunks,
        chunk_pause_ms=chunk_pause_ms,
        tts_normalization_language=lang,
        tts_normalization_rules_applied=rules_applied,
        unknown_terms=unknown_terms,
        ambiguous_terms=ambiguous_terms,
        normalization_enabled=req.normalization_enabled,
        normalization_mode=req.normalization_mode,
        unknown_word_strategy=req.unknown_word_strategy,
        applied_overrides=applied_overrides,
        warnings=warnings,
        error=error,
        fallback_used=fallback_used,
    )


@app.post("/normalization/preview", response_model=NormalizationPreviewResponse)
def normalization_preview(req: NormalizationPreviewRequest) -> NormalizationPreviewResponse:
    """
    Return what the current TTS preprocessing stack would speak for *req.text*
    without rendering any audio.

    Supports runtime override maps that are applied in-memory and never
    written to glossary.json.

    Always returns HTTP 200 (fail-open).
    """
    if not (req.text or "").strip():
        raise HTTPException(status_code=422, detail="text must be a non-empty string")

    result = _run_preview_normalization(req)
    logger.info(
        "normalization_preview  lang=%r  normalization_enabled=%s  overrides=%d  "
        "fallback_used=%s  warnings=%s",
        result.tts_normalization_language,
        req.normalization_enabled,
        result.applied_overrides.get("merged_override_count", 0),
        result.fallback_used,
        result.warnings,
    )
    return result


@app.get("/audio/{filename}")
def serve_audio(filename: str) -> FileResponse:
    """
    Serve a previously synthesised audio file by name.

    The worker calls this URL after receiving ``audio_url`` from ``/synthesize``.
    """
    # Basic path-traversal guard
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = TTS_AUDIO_DIR / filename
    if not file_path.exists():
        logger.warning("Audio file not found: %s", file_path)
        raise HTTPException(status_code=404, detail=f"Audio file not found: {filename}")

    return FileResponse(
        path=str(file_path),
        media_type="audio/mpeg",
        filename=filename,
    )
