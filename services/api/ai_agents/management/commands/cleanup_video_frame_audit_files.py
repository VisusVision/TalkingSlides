from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Delete old sampled video frame audit files from the configured storage tree."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=int(getattr(settings, "VIDEO_FRAME_AUDIT_FRAME_RETENTION_DAYS", 7) or 7),
            help="Delete files older than this many days. Defaults to VIDEO_FRAME_AUDIT_FRAME_RETENTION_DAYS.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report candidates without deleting anything.")
        parser.add_argument("--all", action="store_true", help="Delete all sampled frame files under the audit frame base.")

    def handle(self, *args, **options):
        result = cleanup_video_frame_audit_tree(
            days=int(options.get("days") or 0),
            dry_run=bool(options.get("dry_run")),
            delete_all=bool(options.get("all")),
        )
        self.stdout.write(f"Base: {result.base}")
        self.stdout.write(f"Exists: {result.exists}")
        self.stdout.write(f"Dry run: {result.dry_run}")
        self.stdout.write(f"All: {result.delete_all}")
        self.stdout.write(f"Retention days: {result.days}")
        self.stdout.write(f"Candidate files: {result.candidate_files}")
        self.stdout.write(f"Candidate dirs: {result.candidate_dirs}")
        self.stdout.write(f"Deleted files: {result.deleted_files}")
        self.stdout.write(f"Deleted dirs: {result.deleted_dirs}")
        self.stdout.write(f"Skipped: {result.skipped}")


@dataclass(frozen=True)
class CleanupResult:
    base: str
    exists: bool
    dry_run: bool
    delete_all: bool
    days: int
    candidate_files: int
    candidate_dirs: int
    deleted_files: int
    deleted_dirs: int
    skipped: int


def cleanup_video_frame_audit_tree(*, days: int, dry_run: bool = False, delete_all: bool = False) -> CleanupResult:
    base = _frame_audit_base()
    if not base.exists():
        return CleanupResult(
            base=str(base),
            exists=False,
            dry_run=dry_run,
            delete_all=delete_all,
            days=days,
            candidate_files=0,
            candidate_dirs=0,
            deleted_files=0,
            deleted_dirs=0,
            skipped=0,
        )

    cutoff = time.time() - (max(int(days), 0) * 24 * 60 * 60)
    files, dirs = _candidate_paths(base=base, cutoff=cutoff, delete_all=delete_all)
    if dry_run:
        return CleanupResult(
            base=str(base),
            exists=True,
            dry_run=True,
            delete_all=delete_all,
            days=days,
            candidate_files=len(files),
            candidate_dirs=len(dirs),
            deleted_files=0,
            deleted_dirs=0,
            skipped=0,
        )

    deleted_files = 0
    deleted_dirs = 0
    skipped = 0
    for file_path in files:
        if not _path_is_within(file_path, base):
            skipped += 1
            continue
        try:
            if file_path.exists() and file_path.is_file():
                file_path.unlink()
                deleted_files += 1
        except OSError:
            skipped += 1

    for dir_path in sorted(dirs, key=lambda item: len(item.parts), reverse=True):
        if not _path_is_within(dir_path, base):
            skipped += 1
            continue
        try:
            if dir_path.exists() and dir_path.is_dir():
                if delete_all:
                    file_count = sum(1 for item in dir_path.rglob("*") if item.is_file())
                    dir_count = sum(1 for item in dir_path.rglob("*") if item.is_dir()) + 1
                    for child in sorted(dir_path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                        if child.is_file():
                            child.unlink()
                        elif child.is_dir():
                            child.rmdir()
                    dir_path.rmdir()
                    deleted_files += file_count
                    deleted_dirs += dir_count
                else:
                    dir_path.rmdir()
                    deleted_dirs += 1
        except OSError:
            skipped += 1

    return CleanupResult(
        base=str(base),
        exists=True,
        dry_run=False,
        delete_all=delete_all,
        days=days,
        candidate_files=len(files),
        candidate_dirs=len(dirs),
        deleted_files=deleted_files,
        deleted_dirs=deleted_dirs,
        skipped=skipped,
    )


def _frame_audit_base() -> Path:
    return (Path(str(getattr(settings, "STORAGE_ROOT", "storage_local"))) / "moderation" / "video_frames").resolve()


def _candidate_paths(*, base: Path, cutoff: float, delete_all: bool) -> tuple[list[Path], list[Path]]:
    if delete_all:
        files: list[Path] = []
        dirs: list[Path] = []
        for child in base.iterdir():
            if child.is_file():
                files.append(child.resolve())
            elif child.is_dir():
                dirs.append(child.resolve())
        return files, dirs

    files = [
        path.resolve()
        for path in base.rglob("*")
        if path.is_file() and path.stat().st_mtime <= cutoff
    ]
    dirs = [
        path.resolve()
        for path in base.rglob("*")
        if path.is_dir() and path.stat().st_mtime <= cutoff and _dir_is_empty_or_old(path, cutoff)
    ]
    return files, dirs


def _dir_is_empty_or_old(path: Path, cutoff: float) -> bool:
    try:
        children = list(path.iterdir())
    except OSError:
        return False
    if not children:
        return True
    return all(child.stat().st_mtime <= cutoff for child in children)


def _path_is_within(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False
