import io
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
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


def test_video_frame_extraction_decodes_unknown_length_webm_sequentially(monkeypatch, tmp_path):
    class FakeCapture:
        def __init__(self):
            self.index = 0
            self.released = False

        def isOpened(self):
            return True

        def get(self, prop):
            if prop == 1:
                return -9.223372036854776e18
            if prop == 2:
                return 1000.0
            return 0

        def read(self):
            if self.index >= 75:
                return False, None
            self.index += 1
            return True, np.zeros((400, 400, 3), dtype=np.uint8)

        def set(self, *_args):
            raise AssertionError("Random frame seeking must not be used for MediaRecorder WebM")

        def release(self):
            self.released = True

    class FakeCascade:
        def __init__(self, profile=False):
            self.profile = profile

        def detectMultiScale(self, *_args, **_kwargs):
            return [] if self.profile else [(100, 100, 60, 60)]

    capture = FakeCapture()
    fake_cv2 = SimpleNamespace(
        CAP_PROP_FRAME_COUNT=1,
        CAP_PROP_FPS=2,
        COLOR_BGR2GRAY=3,
        data=SimpleNamespace(haarcascades="/fake/"),
        VideoCapture=lambda _path: capture,
        CascadeClassifier=lambda path: FakeCascade("profileface" in path),
        cvtColor=lambda _frame, _mode: np.zeros((400, 400), dtype=np.uint8),
        imencode=lambda _ext, _frame: (True, np.array([1, 2, 3], dtype=np.uint8)),
    )
    monkeypatch.setattr(avatar_preprocess, "cv2", fake_cv2)
    # Typical compressed 720p webcam face crops score in the 20-35 range.
    monkeypatch.setattr(avatar_preprocess, "_opencv_blur_score", lambda _gray: 25.0)

    frame_bytes, metadata = avatar_preprocess._extract_reference_frame_from_video(tmp_path / "capture.webm")

    assert frame_bytes == b"\x01\x02\x03"
    assert metadata["face_area_ratio"] == 0.0225
    assert metadata["accepted_frames"] > 0
    assert metadata["decoded_frames"] > metadata["evaluated_frames"]
    assert capture.released is True
