"""
Text segmentation utilities for narration, subtitles, and editor pages.

This module centralizes:
- Natural narration chunking (sentence + breath pauses)
- Long-slide splitting into multiple readable pages
- Deterministic identifiers for slide/page timeline sync
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SegmentationConfig:
    """Tunable limits for chunking and splitting behavior."""

    max_chunk_chars: int = 120
    min_chunk_chars: int = 30
    max_page_chars: int = 420
    min_page_chars: int = 140


_COMMON_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "vs.", "etc.", "e.g.", "i.e.",
    "sn.", "doc.", "yrd.", "orn.", "vb.", "no.",
}


def normalize_source_text(text: str) -> str:
    """Normalize whitespace while preserving paragraph intent."""
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\u200b-\u200d\ufeff]", "", value)
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n+", value) if p.strip()]
    return "\n\n".join(paragraphs).strip()


def _protect_abbreviations(text: str) -> str:
    protected = text
    for abbr in _COMMON_ABBREVIATIONS:
        protected = re.sub(re.escape(abbr), abbr.replace(".", "<DOT>"), protected, flags=re.IGNORECASE)
    return protected


def _restore_abbreviations(text: str) -> str:
    return text.replace("<DOT>", ".")


def split_sentences(text: str) -> list[str]:
    """Split text into sentence-like units with punctuation preserved."""
    if not text:
        return []

    protected = _protect_abbreviations(text)
    protected = re.sub(r"(?<=\d)\.(?=\d)", "<DECIMAL_DOT>", protected)
    parts = re.split(r"(?<=[.!?…])(?:[\"'\)\]]+)?\s+", protected)

    return [
        _restore_abbreviations(part.replace("<DECIMAL_DOT>", ".")).strip()
        for part in parts
        if part and part.strip()
    ]


def split_for_readability(text: str, *, max_chars: int, min_chars: int) -> list[str]:
    """Split long text into readable pages, preferring paragraph/sentence boundaries."""
    normalized = normalize_source_text(text)
    if not normalized:
        return []

    paragraphs = [p.strip() for p in normalized.split("\n\n") if p.strip()]
    pages: list[str] = []
    current = ""

    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            pages.append(current)
            current = ""

        if len(paragraph) <= max_chars:
            current = paragraph
            continue

        # Paragraph itself is too long; split by sentence and then clauses.
        for sentence in split_sentences(paragraph):
            sent = sentence.strip()
            if not sent:
                continue

            sentence_candidate = f"{current} {sent}".strip() if current else sent
            if len(sentence_candidate) <= max_chars:
                current = sentence_candidate
                continue

            if current:
                pages.append(current)
                current = ""

            if len(sent) <= max_chars:
                current = sent
                continue

            # Last resort: clause/word boundaries.
            for clause in re.split(r"(?<=[,;:])\s+", sent):
                clause = clause.strip()
                if not clause:
                    continue
                clause_candidate = f"{current} {clause}".strip() if current else clause
                if len(clause_candidate) <= max_chars:
                    current = clause_candidate
                    continue
                if current:
                    pages.append(current)
                if len(clause) <= max_chars:
                    current = clause
                    continue

                for word in clause.split():
                    word_candidate = f"{current} {word}".strip() if current else word
                    if len(word_candidate) <= max_chars:
                        current = word_candidate
                    else:
                        if current:
                            pages.append(current)
                        current = word

    if current:
        pages.append(current)

    merged: list[str] = []
    for page in pages:
        page = page.strip()
        if not page:
            continue
        if merged and len(page) < min_chars:
            merged_candidate = f"{merged[-1]}\n\n{page}".strip()
            if len(merged_candidate) <= max_chars:
                merged[-1] = merged_candidate
                continue
        merged.append(page)

    return merged


def split_narration_chunks(text: str, *, max_chars: int, min_chars: int) -> list[str]:
    """Split text into breath-sized subtitle/narration chunks."""
    normalized = normalize_source_text(text).replace("\n\n", " ")
    if not normalized:
        return []

    if len(normalized) <= max_chars:
        return [normalized]

    chunks: list[str] = []
    current = ""

    for sentence in split_sentences(normalized):
        sent = sentence.strip()
        if not sent:
            continue

        candidate = f"{current} {sent}".strip() if current else sent
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(sent) <= max_chars:
            current = sent
            continue

        for clause in re.split(r"(?<=[,;:])\s+", sent):
            clause = clause.strip()
            if not clause:
                continue
            clause_candidate = f"{current} {clause}".strip() if current else clause
            if len(clause_candidate) <= max_chars:
                current = clause_candidate
            else:
                if current:
                    chunks.append(current)
                if len(clause) <= max_chars:
                    current = clause
                    continue
                for word in clause.split():
                    word_candidate = f"{current} {word}".strip() if current else word
                    if len(word_candidate) <= max_chars:
                        current = word_candidate
                    else:
                        if current:
                            chunks.append(current)
                        current = word

    if current:
        chunks.append(current)

    # Merge tiny fragments into neighboring chunks when possible.
    merged: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        if not merged:
            merged.append(chunk)
            continue
        if len(chunk) < min_chars:
            candidate = f"{merged[-1]} {chunk}".strip()
            if len(candidate) <= max_chars:
                merged[-1] = candidate
                continue
        merged.append(chunk)

    return merged


def build_slide_page_structure(
    source_slide_index: int,
    text: str,
    *,
    config: SegmentationConfig | None = None,
) -> list[dict]:
    """
    Build split pages and narration chunks for one source slide.

    Returns a list of page dictionaries with deterministic page keys.
    """
    cfg = config or SegmentationConfig()
    page_texts = split_for_readability(
        text,
        max_chars=cfg.max_page_chars,
        min_chars=cfg.min_page_chars,
    ) or [normalize_source_text(text) or "Slide content unavailable."]

    pages: list[dict] = []
    for split_index, page_text in enumerate(page_texts):
        chunks = split_narration_chunks(
            page_text,
            max_chars=cfg.max_chunk_chars,
            min_chars=cfg.min_chunk_chars,
        ) or [page_text]
        pages.append(
            {
                "source_slide_index": source_slide_index,
                "split_index": split_index,
                "page_key": f"s{source_slide_index + 1}-p{split_index + 1}",
                "original_text": page_text,
                "narration_text": page_text,
                "subtitle_chunks": chunks,
            }
        )

    return pages


def allocate_chunk_timings(chunks: list[str], total_duration: float) -> list[dict]:
    """
    Allocate cue timings over *total_duration* proportionally by chunk length.
    """
    safe_duration = max(float(total_duration or 0.0), 0.05)
    cleaned = [c.strip() for c in chunks if c and c.strip()]
    if not cleaned:
        return []

    weights = [max(len(c), 1) for c in cleaned]
    total_weight = sum(weights)

    timeline: list[dict] = []
    cursor = 0.0
    for i, (chunk, weight) in enumerate(zip(cleaned, weights)):
        is_last = i == len(cleaned) - 1
        segment = safe_duration * (weight / total_weight)
        start = cursor
        end = safe_duration if is_last else min(safe_duration, cursor + segment)
        timeline.append({
            "index": i,
            "text": chunk,
            "start": round(start, 3),
            "end": round(end, 3),
        })
        cursor = end

    return timeline
