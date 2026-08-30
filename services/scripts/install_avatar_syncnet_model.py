"""Install the pinned LatentSync SyncNet evaluation checkpoint."""

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

from avatar.digital_twin.lipsync_syncnet import (  # noqa: E402
    LATENTSYNC_MODEL_REVISION,
    S3FD_FILENAME,
    S3FD_SHA256,
    SYNCNET_FILENAME,
    SYNCNET_MODEL_LICENSE,
    SYNCNET_SHA256,
)


MODEL_REPOSITORY = "ByteDance/LatentSync"
MODEL_URL = (
    f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/"
    f"{LATENTSYNC_MODEL_REVISION}/auxiliary/{SYNCNET_FILENAME}?download=true"
)
MODEL_CARD_URL = (
    f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/"
    f"{LATENTSYNC_MODEL_REVISION}/README.md?download=true"
)
S3FD_URL = (
    f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/"
    f"{LATENTSYNC_MODEL_REVISION}/auxiliary/{S3FD_FILENAME}?download=true"
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
        with urlopen(request, timeout=180) as response, temporary.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def install(destination: Path, torch_home: Path) -> dict[str, object]:
    destination.mkdir(parents=True, exist_ok=True)
    checkpoint = destination / SYNCNET_FILENAME
    if not checkpoint.is_file() or _digest(checkpoint) != SYNCNET_SHA256:
        _download(MODEL_URL, checkpoint)
    actual = _digest(checkpoint)
    if actual != SYNCNET_SHA256:
        checkpoint.unlink(missing_ok=True)
        raise RuntimeError(f"checksum_mismatch:syncnet:{actual}")

    model_card = destination / "MODEL_CARD.md"
    _download(MODEL_CARD_URL, model_card)
    s3fd = torch_home / "hub" / "checkpoints" / S3FD_FILENAME
    s3fd.parent.mkdir(parents=True, exist_ok=True)
    if not s3fd.is_file() or _digest(s3fd) != S3FD_SHA256:
        _download(S3FD_URL, s3fd)
    s3fd_actual = _digest(s3fd)
    if s3fd_actual != S3FD_SHA256:
        s3fd.unlink(missing_ok=True)
        raise RuntimeError(f"checksum_mismatch:s3fd:{s3fd_actual}")
    manifest: dict[str, object] = {
        "schema_version": "avatar-syncnet-model-pack-v1",
        "source_repository": f"https://huggingface.co/{MODEL_REPOSITORY}",
        "source_revision": LATENTSYNC_MODEL_REVISION,
        "model_license": SYNCNET_MODEL_LICENSE,
        "checkpoint": {
            "path": str(checkpoint.resolve()),
            "sha256": actual,
            "size_bytes": checkpoint.stat().st_size,
            "source": MODEL_URL,
        },
        "face_detector": {
            "path": str(s3fd.resolve()),
            "sha256": s3fd_actual,
            "size_bytes": s3fd.stat().st_size,
            "source": S3FD_URL,
        },
        "model_card": str(model_card.resolve()),
    }
    manifest_path = destination / "model-pack.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("storage_local/models/syncnet"),
        help="Private model directory; model weights are not committed.",
    )
    parser.add_argument(
        "--torch-home",
        type=Path,
        default=Path("storage_local/models/torch"),
        help="Torch cache root used by the pinned LatentSync S3FD detector.",
    )
    args = parser.parse_args()
    print(json.dumps(install(args.destination, args.torch_home), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
