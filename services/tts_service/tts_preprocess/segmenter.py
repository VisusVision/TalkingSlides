from __future__ import annotations

import re

from .glossary import protected_spans


COMMON_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "vs.", "etc.",
    "e.g.", "i.e.", "sn.", "doc.", "yrd.", "orn.", "vb.", "no.",
    "doç.", "örn.",
}


def _mask_protected(text: str) -> tuple[str, dict[str, str]]:
    value = text or ""
    spans = protected_spans(value)
    if not spans:
        return value, {}

    parts: list[str] = []
    replacements: dict[str, str] = {}
    cursor = 0
    for index, (start, end) in enumerate(spans):
        token = f"__TTS_PROTECTED_{index}__"
        if cursor < start:
            parts.append(value[cursor:start])
        replacements[token] = value[start:end]
        parts.append(token)
        cursor = end
    if cursor < len(value):
        parts.append(value[cursor:])
    return "".join(parts), replacements


def _restore_protected(text: str, replacements: dict[str, str]) -> str:
    restored = text
    for token, value in replacements.items():
        restored = restored.replace(token, value)
    return restored


def _protect_abbreviations(text: str) -> str:
    protected = text
    for abbr in COMMON_ABBREVIATIONS:
        protected = re.sub(
            re.escape(abbr),
            abbr.replace(".", "<DOT>"),
            protected,
            flags=re.IGNORECASE,
        )
    return protected


def _restore_abbreviations(text: str) -> str:
    return text.replace("<DOT>", ".")


def split_sentences(text: str) -> list[str]:
    if not text:
        return []

    masked, replacements = _mask_protected(text)
    protected = _protect_abbreviations(masked)
    protected = re.sub(r"(?<=\d)\.(?=\d)", "<DECIMAL_DOT>", protected)

    sentences: list[str] = []
    start = 0
    index = 0
    while index < len(protected):
        char = protected[index]
        if char not in ".!?":
            index += 1
            continue

        end = index + 1
        while end < len(protected) and protected[end] in "\"')]}":
            end += 1
        if end == len(protected) or protected[end].isspace():
            piece = protected[start:end].strip()
            if piece:
                sentences.append(piece)
            start = end
            while start < len(protected) and protected[start].isspace():
                start += 1
            index = start
            continue
        index += 1

    tail = protected[start:].strip()
    if tail:
        sentences.append(tail)

    restored: list[str] = []
    for sentence in sentences:
        sentence = sentence.replace("<DECIMAL_DOT>", ".")
        sentence = _restore_abbreviations(sentence)
        sentence = _restore_protected(sentence, replacements).strip()
        if sentence:
            restored.append(sentence)
    return restored


def split_oversized_unit(unit: str, max_chars: int) -> list[str]:
    if len(unit) <= max_chars:
        return [unit]

    pieces: list[str] = []
    current = ""
    boundaries = [
        r"(?<=[;:])\s+",
        r"(?<=,)\s+",
        r"\s+(?=(?:and|but|or|so|because|while|which|that)\b)",
    ]

    clauses = [unit]
    for boundary in boundaries:
        next_clauses: list[str] = []
        for clause in clauses:
            if len(clause) <= max_chars:
                next_clauses.append(clause)
            else:
                next_clauses.extend(re.split(boundary, clause, flags=re.IGNORECASE))
        clauses = next_clauses

    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        candidate = f"{current} {clause}".strip() if current else clause
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            pieces.append(current)
            current = ""
        if len(clause) <= max_chars:
            current = clause
            continue
        for word in clause.split():
            candidate = f"{current} {word}".strip() if current else word
            if len(candidate) <= max_chars or not current:
                current = candidate
            else:
                pieces.append(current)
                current = word

    if current:
        pieces.append(current)
    return [piece for piece in pieces if piece.strip()]


def _chunk_paragraph(paragraph: str, *, max_chars: int, target_chars: int) -> list[str]:
    sentences = split_sentences(paragraph)
    if not sentences:
        return []

    chunks: list[str] = []
    current = ""
    min_soft = max(80, int(target_chars * 0.65))

    for sentence in sentences:
        units = split_oversized_unit(sentence, max_chars=max_chars)
        for unit in units:
            unit = unit.strip()
            if not unit:
                continue
            candidate = f"{current} {unit}".strip() if current else unit
            if current and len(candidate) > target_chars and len(current) >= min_soft:
                chunks.append(current)
                current = unit
            elif len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = unit

    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]


def split_text_to_chunks(
    text: str,
    *,
    max_chars: int,
    target_chars: int,
    sentence_pause_ms: int,
    paragraph_pause_ms: int,
) -> tuple[list[str], list[int], list[str]]:
    warnings: list[str] = []
    safe_max = max(int(max_chars or 0), 80)
    safe_target = min(max(int(target_chars or 0), 60), safe_max)

    value = (text or "").strip()
    if not value:
        return [], [], ["empty_input"]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", value) if p.strip()]
    chunks: list[str] = []
    paragraph_ends: set[int] = set()

    for paragraph in paragraphs:
        paragraph_chunks = _chunk_paragraph(
            re.sub(r"\s+", " ", paragraph).strip(),
            max_chars=safe_max,
            target_chars=safe_target,
        )
        if not paragraph_chunks:
            continue
        chunks.extend(paragraph_chunks)
        paragraph_ends.add(len(chunks) - 1)

    chunks = [chunk.strip() for chunk in chunks if chunk and chunk.strip()]
    if not chunks:
        return [], [], ["empty_input"]

    for chunk in chunks:
        if len(chunk) > safe_max:
            warnings.append(f"chunk_exceeds_max:{len(chunk)}>{safe_max}")

    pauses: list[int] = []
    for index, _chunk in enumerate(chunks):
        if index == len(chunks) - 1:
            pauses.append(0)
        elif index in paragraph_ends:
            pauses.append(int(paragraph_pause_ms))
        else:
            pauses.append(int(sentence_pause_ms))

    return chunks, pauses, warnings
