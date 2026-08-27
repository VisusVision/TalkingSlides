"""Install the pinned OpenCV YuNet + SFace identity model pack."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen


SERVICES_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from avatar.digital_twin.identity_sface import (  # noqa: E402
    OPENCV_ZOO_REVISION,
    SFACE_FILENAME,
    SFACE_SHA256,
    YUNET_FILENAME,
    YUNET_SHA256,
)


MEDIA_ROOT = "https://media.githubusercontent.com/media/opencv/opencv_zoo"
RAW_ROOT = "https://raw.githubusercontent.com/opencv/opencv_zoo"
MODELS = (
    {
        "name": "yunet",
        "filename": YUNET_FILENAME,
        "sha256": YUNET_SHA256,
        "directory": "face_detection_yunet",
        "license": "MIT",
    },
    {
        "name": "sface",
        "filename": SFACE_FILENAME,
        "sha256": SFACE_SHA256,
        "directory": "face_recognition_sface",
        "license": "Apache-2.0",
    },
)


def _digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _download(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "TalkingSlides-model-installer/1"})
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def install(destination: Path) -> dict[str, object]:
    destination.mkdir(parents=True, exist_ok=True)
    installed: list[dict[str, str]] = []
    for model in MODELS:
        target = destination / str(model["filename"])
        expected = str(model["sha256"])
        model_url = (
            f"{MEDIA_ROOT}/{OPENCV_ZOO_REVISION}/models/"
            f"{model['directory']}/{model['filename']}"
        )
        if not target.is_file() or _digest(target) != expected:
            _download(model_url, target)
        actual = _digest(target)
        if actual != expected:
            target.unlink(missing_ok=True)
            raise RuntimeError(f"checksum_mismatch:{model['name']}:{actual}")

        license_name = f"LICENSE.{model['name']}.txt"
        license_path = destination / license_name
        license_url = (
            f"{RAW_ROOT}/{OPENCV_ZOO_REVISION}/models/"
            f"{model['directory']}/LICENSE"
        )
        _download(license_url, license_path)
        installed.append(
            {
                "name": str(model["name"]),
                "path": str(target.resolve()),
                "sha256": actual,
                "license": str(model["license"]),
                "license_file": str(license_path.resolve()),
                "source": model_url,
            }
        )

    manifest: dict[str, object] = {
        "schema_version": "avatar-identity-model-pack-v1",
        "source_repository": "https://github.com/opencv/opencv_zoo",
        "source_revision": OPENCV_ZOO_REVISION,
        "models": installed,
    }
    manifest_path = destination / "model-pack.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("storage_local/models/identity"),
        help="Private model directory; model weights are not committed.",
    )
    args = parser.parse_args()
    manifest = install(args.destination)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
