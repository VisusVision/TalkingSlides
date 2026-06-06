import os
import sys
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from avatar import preprocess as avatar_preprocess  # noqa: E402


def test_avatar_crop_expands_upward_for_headroom(tmp_path, monkeypatch):
    image = Image.new("RGB", (1200, 1400), (210, 205, 198))
    image_bytes_path = tmp_path / "input.png"
    image.save(image_bytes_path, format="PNG")

    monkeypatch.setattr(avatar_preprocess, "_detect_face_bbox", lambda _img: (420, 560, 780, 980))

    result = avatar_preprocess.preprocess_avatar_image(
        image_bytes=image_bytes_path.read_bytes(),
        original_filename="teacher.png",
        storage_root=str(tmp_path),
        teacher_id=55,
        model_version="liveportrait+musetalk:v1",
    )

    identity_path = Path(tmp_path) / result.identity_package_rel_path
    payload = __import__("json").loads(identity_path.read_text(encoding="utf-8"))
    crop_box = payload["image"]["crop_box"]
    detected = payload["image"]["detected_face_bbox"]

    assert detected == [420, 560, 780, 980]
    assert crop_box[1] < detected[1], "crop must include forehead/hairline above detected face"
    assert crop_box[3] > detected[3], "crop should include lower area for presenter frame"
