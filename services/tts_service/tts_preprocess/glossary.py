from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable


_PROTECTED_RE = re.compile(
    r"(?s:```.*?```)"
    r"|`[^`\n]+`"
    r"|(?:https?://|ftp://|www\.)[^\s<>()]+"
    r"|\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    r"|\b[A-Za-z]:[\\/][^\s<>()]+"
    r"|(?<!\w)/(?:[^\s<>()/]+[\\/])+[^\s<>()]+"
    r"|(?<!\w)(?:\.{1,2}[\\/]|[A-Za-z0-9_.-]+[\\/])"
    r"(?:[A-Za-z0-9_.-]+[\\/])*[A-Za-z0-9_.-]+"
    r"|(?<![A-Za-z0-9_])--?[A-Za-z0-9][A-Za-z0-9_-]*(?:[= ][^\s<>()]+)?"
    r"|(?<![A-Za-z0-9_/-])"
    r"[A-Za-z0-9_.-]+\."
    r"(?:json|py|csv|txt|md|yml|yaml|toml|ini|env|lock|js|jsx|ts|tsx|html|css|wav|mp3|mp4|png|jpg|jpeg|webp|pdf|pptx|docx)"
    r"(?![A-Za-z0-9_/-])",
    re.IGNORECASE,
)

# Language keys recognised in a bilingual glossary JSON.
_KNOWN_LANG_KEYS = {"en", "tr"}

_COMMAND_LINE_RE = re.compile(
    r"^(?:"
    r"python3?|pip3?|git|npm|npx|yarn|pnpm|node|docker|docker-compose|kubectl|uv|"
    r"uvicorn|celery|pytest|ffmpeg|ffprobe|curl|wget"
    r")\b(?:\s+(?:[-./\\A-Za-z0-9_:=]+|\"[^\"]+\"|'[^']+'))+\s*$"
)


def protected_spans(text: str) -> list[tuple[int, int]]:
    value = text or ""
    spans = [(m.start(), m.end()) for m in _PROTECTED_RE.finditer(value)]
    cursor = 0
    for line in value.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        stripped = line_body.strip()
        if (
            stripped.startswith(("$ ", "> ", "PS> "))
            or _COMMAND_LINE_RE.match(stripped)
        ):
            start = cursor + line.index(stripped)
            spans.append((start, start + len(stripped)))
        cursor += len(line)
    if not spans:
        return []

    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def apply_outside_protected(text: str, transform: Callable[[str], str]) -> str:
    value = text or ""
    spans = protected_spans(value)
    if not spans:
        return transform(value)

    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        if cursor < start:
            parts.append(transform(value[cursor:start]))
        parts.append(value[start:end])
        cursor = end
    if cursor < len(value):
        parts.append(transform(value[cursor:]))
    return "".join(parts)


def _parse_glossary_dict(data: dict) -> dict[str, str]:
    """Convert raw JSON dict to a clean {term: spoken} mapping."""
    glossary: dict[str, str] = {}
    for key, value in data.items():
        term = str(key or "").strip()
        spoken = str(value or "").strip()
        if term and spoken:
            glossary[term] = spoken
    return glossary


def _is_bilingual(data: dict) -> bool:
    """Return True if *data* uses language top-level keys (en/tr format)."""
    return bool(data) and all(k in _KNOWN_LANG_KEYS for k in data)


def load_glossary(path: Path, language: str = "en") -> tuple[dict[str, str], list[str]]:
    """Load the glossary for *language* from *path*.

    Supports two formats:

    1. **Bilingual** — top-level keys are language codes (``"en"``, ``"tr"``).
       The sub-dict for *language* is returned; falls back to ``"en"`` if the
       requested language is not present.

    2. **Flat / legacy** — a flat ``{term: spoken}`` dict.  Treated as English
       regardless of the *language* argument (backward compatibility).
    """
    warnings: list[str] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}, [f"glossary_not_found:{path}"]
    except Exception as exc:  # noqa: BLE001
        return {}, [f"glossary_load_failed:{exc}"]

    if not isinstance(data, dict):
        return {}, [f"glossary_invalid:{path}"]

    if _is_bilingual(data):
        lang_key = language if language in data else "en"
        lang_data = data.get(lang_key, {})
        if not isinstance(lang_data, dict):
            return {}, [f"glossary_invalid_lang_section:{lang_key}:{path}"]
        glossary = _parse_glossary_dict(lang_data)
    else:
        # Legacy flat format — treat as English always.
        glossary = _parse_glossary_dict(data)

    if not glossary:
        warnings.append(f"glossary_empty:{path}")
    return glossary, warnings


def load_glossary_for_lang(path: Path, language: str) -> tuple[dict[str, str], list[str]]:
    """Convenience alias — always passes *language* explicitly."""
    return load_glossary(path, language=language)


def _term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term).replace(r"\ ", r"\s+")
    return re.compile(
        rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )


def apply_glossary(text: str, glossary: dict[str, str]) -> str:
    return apply_glossary_with_rules(text, glossary)[0]


def apply_glossary_with_rules(
    text: str,
    glossary: dict[str, str],
    *,
    language: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    if not text or not glossary:
        return text or "", []

    ordered_terms = sorted(glossary.items(), key=lambda item: len(item[0]), reverse=True)
    rules_applied: list[dict[str, Any]] = []

    def transform(segment: str) -> str:
        updated = segment
        for term, spoken in ordered_terms:
            pattern = _term_pattern(term)
            matches = list(pattern.finditer(updated))
            if matches:
                rules_applied.append(
                    {
                        "rule": "glossary",
                        "language": language,
                        "term": term,
                        "replacement": spoken,
                        "count": len(matches),
                    }
                )
                updated = pattern.sub(spoken, updated)
        return updated

    return apply_outside_protected(text, transform), rules_applied
