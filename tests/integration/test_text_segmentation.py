import importlib
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TTS_ROOT = REPO_ROOT / "services" / "tts_service"
if str(TTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TTS_ROOT))

from tts_preprocess import prepare_text_for_tts  # noqa: E402


def test_build_slide_page_structure_splits_long_slide():
    segmentation = importlib.import_module("text_segmentation")

    long_text = (
        "This is a long teaching paragraph about neural networks and optimization. "
        "It explains gradient descent behavior, practical convergence checks, and common pitfalls in model tuning. "
        "Students should understand how to adjust learning rates and why validation metrics can diverge from training metrics. "
        "Finally, this section summarizes regularization strategies and how they improve generalization. "
        "As a final recap, compare L1 and L2 regularization, dropout, and data augmentation in practical projects."
    )

    pages = segmentation.build_slide_page_structure(0, long_text)
    assert len(pages) >= 2
    assert all(page["subtitle_chunks"] for page in pages)
    assert pages[0]["page_key"].startswith("s1-p")


def test_allocate_chunk_timings_covers_total_duration():
    segmentation = importlib.import_module("text_segmentation")

    chunks = [
        "First breath-sized subtitle chunk.",
        "Second subtitle chunk with a little more detail.",
        "Final chunk.",
    ]
    timeline = segmentation.allocate_chunk_timings(chunks, 9.0)

    assert len(timeline) == 3
    assert timeline[0]["start"] == 0.0
    assert timeline[-1]["end"] == 9.0
    assert all(item["end"] > item["start"] for item in timeline)


def test_tts_preprocessor_chunks_respect_max_length():
    text = " ".join(
        [
            "This long narration sentence contains multiple clauses, commas, and conjunctions",
            "because the TTS preprocessor should prefer natural boundaries while still",
            "respecting a strict maximum chunk size for XTTS v2.",
        ]
    )

    prepared = prepare_text_for_tts(text, max_chars_per_chunk=95, target_chars_per_chunk=70)

    assert prepared.chunks
    assert all(len(chunk) <= 95 for chunk in prepared.chunks)
    assert any("X T T S version two" in chunk for chunk in prepared.chunks)
