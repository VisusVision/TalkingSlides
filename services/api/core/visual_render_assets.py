"""Private, content-addressed local files, deliberately outside public STORAGE_ROOT."""
import os
from pathlib import Path
import re
import tempfile

from django.conf import settings
from core.visual_design.schemas import require
from core.visual_design.render_snapshot import asset_reference, bytes_hash

MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024


class PrivateRenderAssets:
    def __init__(self, project_id):
        require(re.fullmatch(r"[1-9][0-9]*", str(project_id)) is not None, "identity", "Numeric project identity required")
        configured = getattr(settings, "VISUAL_RENDER_PRIVATE_ROOT", os.environ.get("VISUAL_RENDER_PRIVATE_ROOT"))
        require(isinstance(configured, (str, os.PathLike)) and str(configured).strip()
                and Path(configured).is_absolute(), "private_storage", "Configure an absolute persistent VISUAL_RENDER_PRIVATE_ROOT before capturing snapshots")
        self.root = Path(configured).resolve()
        public = Path(settings.STORAGE_ROOT).resolve()
        require(not self.root.is_relative_to(public) and not public.is_relative_to(self.root), "private_storage", "Private render assets must be separate from public storage")
        self.project_root = self.root / str(project_id)
        require(self.project_root.resolve().is_relative_to(self.root), "asset_path", "Private project symlink escaped")
        self.seen = {}

    def reference(self, data, extension, *, pin=False):
        require(type(data) is bytes and 0 < len(data) <= MAX_ASSET_BYTES, "asset_limit", "Unsupported asset size")
        ref = {"sha256": bytes_hash(data), "size": len(data), "extension": extension}
        asset_reference(ref)
        self.seen[(ref["sha256"], extension)] = len(data)
        require(sum(self.seen.values()) <= MAX_TOTAL_BYTES, "asset_limit", "Snapshot media budget exceeded")
        if pin:
            path = self.path(ref)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not path.exists():
                fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pin-")
                try:
                    with os.fdopen(fd, "wb") as output:
                        output.write(data); output.flush(); os.fsync(output.fileno())
                    try: os.link(temp, path)  # Never overwrite an existing content identity.
                    except FileExistsError: pass
                finally: os.unlink(temp)
            require(self.read(ref) == data, "asset_integrity", "Pinned asset content differs")
        return ref

    def path(self, ref):
        asset_reference(ref)
        path = self.project_root / (ref["sha256"][7:] + "." + ref["extension"])
        require(path.resolve().is_relative_to(self.project_root.resolve()) and self.project_root.resolve().is_relative_to(self.root),
                "asset_path", "Private asset symlink escaped")
        return path

    def read(self, ref):
        path = self.path(ref)
        require(path.is_file() and path.stat().st_size == ref["size"], "asset_integrity", "Pinned asset missing or size changed")
        with path.open("rb") as file: data = file.read(MAX_ASSET_BYTES + 1)
        require(len(data) == ref["size"] and bytes_hash(data) == ref["sha256"], "asset_integrity", "Pinned content hash changed")
        return data
