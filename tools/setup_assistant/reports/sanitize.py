from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_SECRET_KEY = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(r"(?im)\b([A-Z][A-Z0-9_]{2,})\s*=\s*([^\s,;]+)")
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_URL_USERINFO = re.compile(r"(?P<scheme>https?://)[^/@\s:]+:[^/@\s]+@")
_SECRET_OPTION = re.compile(
    r"(?i)^--?[^=]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)"
)


def sanitize_text(value: str) -> str:
    text = str(value).replace("\x00", "")
    text = _ASSIGNMENT.sub(
        lambda match: (
            f"{match.group(1)}=<redacted>"
            if _SECRET_KEY.search(match.group(1))
            else match.group(0)
        ),
        text,
    )
    text = _BEARER.sub(lambda match: f"{match.group(1)} <redacted>", text)
    text = _URL_USERINFO.sub(lambda match: match.group("scheme") + "<redacted>@", text)
    home = str(Path.home())
    if home:
        text = text.replace(home, "~").replace(home.replace("\\", "/"), "~")
    return text


def sanitize_data(value: Any, key: str = "") -> Any:
    if _SECRET_KEY.search(key):
        if value in (None, "", False):
            return value
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): sanitize_data(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_data(item) for item in value]
    if isinstance(value, Path):
        return sanitize_text(os.fspath(value))
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def sanitize_argv(argv: Any) -> tuple[str, ...]:
    sanitized: list[str] = []
    redact_next = False
    for value in argv:
        item = str(value)
        if redact_next:
            sanitized.append("<redacted>")
            redact_next = False
            continue
        if _SECRET_OPTION.match(item):
            if "=" in item:
                sanitized.append(f"{item.split('=', 1)[0]}=<redacted>")
            else:
                sanitized.append(item)
                redact_next = True
            continue
        sanitized.append(sanitize_text(item))
    return tuple(sanitized)
