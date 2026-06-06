import io
import sys
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from avatar import preprocess as avatar_preprocess  # noqa: E402


def _png_bytes(width=960, height=1280, color=(205, 200, 194)):
    image = Image.new("RGB", (width, height), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_preprocess_avatar_video_builds_stable_identity_package(tmp_path, monkeypatch):
    frame_bytes = _png_bytes()

    monkeypatch.setattr(
        avatar_preprocess,
        "_extract_reference_frame_from_video",
        lambda _path: (frame_bytes, {"frame_index": 12, "accepted_frames": 5, "rejected_frames": 1}),
    )
    monkeypatch.setattr(avatar_preprocess, "_detect_face_bbox", lambda _img: (300, 330, 650, 770))

    payload = avatar_preprocess.preprocess_avatar_video(
        video_bytes=b"fake-video-stream",
        original_filename="teacher.mov",
        storage_root=str(tmp_path),
        teacher_id=77,
        model_version="liveportrait+musetalk:v1",
    )

    assert payload["video_rel_path"].endswith("/raw/source.mov")
    assert payload["processed_rel_path"].endswith("/identity/processed.png")
    assert payload["identity_package_rel_path"].endswith("/identity_video.json")
    assert len(payload["references_rel_paths"]) == 2

    processed_abs = Path(tmp_path) / payload["processed_rel_path"]
    identity_abs = Path(tmp_path) / payload["identity_package_rel_path"]
    assert processed_abs.exists()
    assert identity_abs.exists()

    metadata = __import__("json").loads(identity_abs.read_text(encoding="utf-8"))
    assert metadata["video"]["frame_selection"]["frame_index"] == 12


def test_preprocess_avatar_video_reuses_hash_package(tmp_path, monkeypatch):
    frame_bytes = _png_bytes()
    calls = {"count": 0}

    def fake_extract(_path):
        calls["count"] += 1
        return frame_bytes, {"frame_index": 3, "accepted_frames": 4, "rejected_frames": 0}

    monkeypatch.setattr(avatar_preprocess, "_extract_reference_frame_from_video", fake_extract)
    monkeypatch.setattr(avatar_preprocess, "_detect_face_bbox", lambda _img: (300, 330, 650, 770))

    kwargs = {
        "video_bytes": b"same-bytes-video",
        "original_filename": "teacher.mp4",
        "storage_root": str(tmp_path),
        "teacher_id": 77,
        "model_version": "liveportrait+musetalk:v1",
    }

    first = avatar_preprocess.preprocess_avatar_video(**kwargs)
    second = avatar_preprocess.preprocess_avatar_video(**kwargs)

    assert calls["count"] == 1
    assert first["processed_rel_path"] == second["processed_rel_path"]
    assert first["identity_package_rel_path"] == second["identity_package_rel_path"]
