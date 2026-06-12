"""Report-only inventory of storage paths referenced by database rows and sidecars."""

from __future__ import annotations

import glob
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError

from core.models import AvatarRenderJob, Job, Project, TranslatedSubtitleTrack, UserProfile, VoiceProfile


CATEGORIES = (
    "uploads",
    "render_outputs",
    "playback_sidecars",
    "subtitles",
    "profiles",
    "avatar_assets",
    "tts_voice_audio",
    "hls_assets",
    "unknown_or_unclassified",
)

CRITICALITIES = ("critical", "regenerable", "optional", "unknown")


@dataclass(frozen=True)
class ReferenceSource:
    category: str
    owner_model: str
    owner_id: str
    source: str
    path: str
    criticality: str = "unknown"
    notes: tuple[str, ...] = field(default_factory=tuple)


def build_storage_reference_inventory(
    *,
    storage_root: str | Path | None = None,
    project_id: int | str | None = None,
    include_missing: bool = True,
) -> dict[str, Any]:
    """Build a deterministic, read-only manifest of known storage references."""

    root = Path(storage_root or getattr(settings, "STORAGE_ROOT", "storage_local")).expanduser().resolve()
    warnings: list[str] = []
    try:
        refs = list(_db_references(project_id=project_id))
        refs.extend(_project_upload_file_references(root, project_id=project_id))
        refs.extend(_playback_sidecar_references(root, project_id=project_id))
        db_available = True
    except (OperationalError, ProgrammingError) as exc:
        refs = []
        db_available = False
        warnings.append(f"database_unavailable:{exc.__class__.__name__}")

    entries = [_reference_payload(root, ref) for ref in refs]
    if not include_missing:
        entries = [entry for entry in entries if entry["exists"]]
    entries = _dedupe_entries(entries)
    entries.sort(key=lambda item: _entry_sort_key(item))

    return {
        "mode": "read-only/report-only",
        "storage_root": str(root),
        "project_id": str(project_id) if project_id not in (None, "") else "",
        "db_available": db_available,
        "warnings": warnings,
        "summary": _summary(entries),
        "references": entries,
        "references_by_category": {
            category: [entry for entry in entries if entry["category"] == category]
            for category in CATEGORIES
            if any(entry["category"] == category for entry in entries)
        },
    }


def _db_references(*, project_id: int | str | None) -> Iterable[ReferenceSource]:
    project_filter = _project_filter(project_id)
    related_project_filter = _related_project_filter(project_id)

    for project in Project.objects.filter(**project_filter).order_by("id"):
        owner_id = str(project.id)
        for field_name in ("cover_image_original", "cover_image_processed"):
            value = str(getattr(project, field_name, "") or "")
            if value:
                yield ReferenceSource(
                    "uploads",
                    "Project",
                    owner_id,
                    field_name,
                    value,
                    "critical",
                    ("project_media_field",),
                )
        if project.avatar_output_path:
            yield ReferenceSource(
                "avatar_assets",
                "Project",
                owner_id,
                "avatar_output_path",
                project.avatar_output_path,
                "critical",
                ("current_project_avatar_output",),
            )

    for job in Job.objects.filter(**_job_project_filter(project_id)).order_by("id"):
        owner_id = str(job.id)
        if job.result_url:
            yield ReferenceSource(
                "render_outputs",
                "Job",
                owner_id,
                "result_url",
                job.result_url,
                "critical",
                (f"project_id:{job.project_id or ''}",),
            )
        if job.srt_url:
            yield ReferenceSource(
                "subtitles",
                "Job",
                owner_id,
                "srt_url",
                job.srt_url,
                "regenerable",
                (f"project_id:{job.project_id or ''}",),
            )

    for track in TranslatedSubtitleTrack.objects.filter(**related_project_filter).order_by("id"):
        owner_id = str(track.id)
        for field_name in ("srt_path", "vtt_path"):
            value = str(getattr(track, field_name, "") or "")
            if value:
                yield ReferenceSource(
                    "subtitles",
                    "TranslatedSubtitleTrack",
                    owner_id,
                    field_name,
                    value,
                    "regenerable",
                    (f"project_id:{track.project_id}", f"language:{track.language_code}"),
                )

    user_ids = _project_user_ids(project_id)
    user_profile_qs = UserProfile.objects.all()
    voice_profile_qs = VoiceProfile.objects.all()
    avatar_job_qs = AvatarRenderJob.objects.all()
    if user_ids is not None:
        user_profile_qs = user_profile_qs.filter(user_id__in=user_ids)
        voice_profile_qs = voice_profile_qs.filter(user_id__in=user_ids)
        avatar_job_qs = avatar_job_qs.filter(lesson_id=project_id)

    for profile in user_profile_qs.order_by("user_id"):
        owner_id = str(profile.user_id)
        for field_name in (
            "banner_image_original",
            "banner_image_processed",
            "banner_image_pending_original",
            "banner_image_pending_processed",
            "logo_image_original",
            "logo_image_processed",
            "logo_image_pending_original",
            "logo_image_pending_processed",
        ):
            value = str(getattr(profile, field_name, "") or "")
            if value:
                yield ReferenceSource("profiles", "UserProfile", owner_id, field_name, value, "critical")
        for field_name in (
            "avatar_image_original",
            "avatar_image_processed",
            "avatar_video_original",
            "avatar_video_processed",
            "avatar_preview_video",
            "avatar_last_preview_path",
        ):
            value = str(getattr(profile, field_name, "") or "")
            if value:
                yield ReferenceSource("avatar_assets", "UserProfile", owner_id, field_name, value, "critical")

    for voice_profile in voice_profile_qs.exclude(voice_id="").order_by("user_id"):
        voice_id = str(voice_profile.voice_id or "").strip()
        if voice_id:
            yield ReferenceSource(
                "tts_voice_audio",
                "VoiceProfile",
                str(voice_profile.user_id),
                "voice_id",
                f"voices/{voice_id}.wav",
                "critical",
                ("conventional_voice_reference_path",),
            )

    for avatar_job in avatar_job_qs.exclude(output_path="").order_by("id"):
        yield ReferenceSource(
            "avatar_assets",
            "AvatarRenderJob",
            str(avatar_job.id),
            "output_path",
            avatar_job.output_path,
            "critical",
            (f"project_id:{avatar_job.lesson_id}", f"teacher_id:{avatar_job.teacher_id}"),
        )


def _project_upload_file_references(root: Path, *, project_id: int | str | None) -> Iterable[ReferenceSource]:
    projects = Project.objects.all()
    if project_id not in (None, ""):
        projects = projects.filter(id=project_id)
    for project in projects.order_by("id"):
        upload_dir = root / "uploads" / str(project.id)
        if not upload_dir.exists() or not upload_dir.is_dir():
            continue
        for file_path in sorted(path for path in upload_dir.rglob("*") if path.is_file()):
            rel_path = file_path.relative_to(root).as_posix()
            yield ReferenceSource(
                "uploads",
                "Project",
                str(project.id),
                "uploads_directory",
                rel_path,
                "critical",
                ("project_upload_namespace",),
            )


def _playback_sidecar_references(root: Path, *, project_id: int | str | None) -> Iterable[ReferenceSource]:
    projects = Project.objects.all()
    if project_id not in (None, ""):
        projects = projects.filter(id=project_id)
    for project in projects.order_by("id"):
        sidecar_rel = f"{project.id}/playback_assets.json"
        sidecar_ref = ReferenceSource(
            "playback_sidecars",
            "Project",
            str(project.id),
            "playback_assets.json",
            sidecar_rel,
            "critical",
            ("known_playback_sidecar",),
        )
        yield sidecar_ref
        payload = _read_safe_json_sidecar(root, sidecar_rel)
        if not isinstance(payload, dict):
            continue
        yield from _references_from_playback_payload(project_id=project.id, payload=payload)


def _references_from_playback_payload(*, project_id: int, payload: dict[str, Any]) -> Iterable[ReferenceSource]:
    scalar_fields = (
        ("mp4_rel_path", "render_outputs", "critical"),
        ("srt_rel_path", "subtitles", "regenerable"),
        ("vtt_rel_path", "subtitles", "regenerable"),
    )
    for field_name, category, criticality in scalar_fields:
        value = str(payload.get(field_name) or "")
        if value:
            yield ReferenceSource(category, "Project", str(project_id), f"playback_assets.{field_name}", value, criticality)

    for index, value in enumerate(payload.get("slides") or []):
        if value:
            yield ReferenceSource(
                "render_outputs",
                "Project",
                str(project_id),
                f"playback_assets.slides[{index}]",
                str(value),
                "regenerable",
            )
    for index, value in enumerate(payload.get("tts_audio") or []):
        if value:
            yield ReferenceSource(
                "tts_voice_audio",
                "Project",
                str(project_id),
                f"playback_assets.tts_audio[{index}]",
                str(value),
                "regenerable",
            )
    for index, value in enumerate(payload.get("avatar_clips") or []):
        if value:
            yield ReferenceSource(
                "avatar_assets",
                "Project",
                str(project_id),
                f"playback_assets.avatar_clips[{index}]",
                str(value),
                "regenerable",
            )

    for index, segment in enumerate(payload.get("final_segments") or []):
        if not isinstance(segment, dict):
            continue
        for field_name, category, criticality in (
            ("part_rel_path", "render_outputs", "regenerable"),
            ("tts_audio", "tts_voice_audio", "regenerable"),
            ("tts_audio_path", "tts_voice_audio", "regenerable"),
            ("avatar_clip", "avatar_assets", "regenerable"),
        ):
            value = str(segment.get(field_name) or "")
            if value:
                yield ReferenceSource(
                    category,
                    "Project",
                    str(project_id),
                    f"playback_assets.final_segments[{index}].{field_name}",
                    value,
                    criticality,
                )

    avatar = payload.get("avatar")
    if isinstance(avatar, dict):
        for field_name in ("track_rel_path", "output_path"):
            value = str(avatar.get(field_name) or "")
            if value:
                yield ReferenceSource(
                    "avatar_assets",
                    "Project",
                    str(project_id),
                    f"playback_assets.avatar.{field_name}",
                    value,
                    "critical",
                )
        for index, segment in enumerate(avatar.get("segments") or []):
            if isinstance(segment, dict):
                value = str(segment.get("rel_path") or segment.get("output_path") or "")
            else:
                value = str(segment or "")
            if value:
                yield ReferenceSource(
                    "avatar_assets",
                    "Project",
                    str(project_id),
                    f"playback_assets.avatar.segments[{index}]",
                    value,
                    "regenerable",
                )

    hls = payload.get("hls")
    if isinstance(hls, dict):
        manifest = str(hls.get("manifest_rel_path") or "")
        if manifest:
            yield ReferenceSource("hls_assets", "Project", str(project_id), "playback_assets.hls.manifest_rel_path", manifest, "critical")
        segment_glob = str(hls.get("segment_glob") or "")
        if segment_glob:
            yield ReferenceSource(
                "hls_assets",
                "Project",
                str(project_id),
                "playback_assets.hls.segment_glob",
                segment_glob,
                "critical",
                ("glob_reference",),
            )
        if manifest and bool(hls.get("encrypted")):
            key_path = str(PurePosixPath(manifest).parent / "enc.key")
            yield ReferenceSource(
                "hls_assets",
                "Project",
                str(project_id),
                "playback_assets.hls.encrypted_key_inferred",
                key_path,
                "critical",
                ("inferred_from_encrypted_hls_manifest",),
            )


def _reference_payload(root: Path, ref: ReferenceSource) -> dict[str, Any]:
    safe_path, safety_notes = _normalize_reference_path(ref.path)
    notes = [*ref.notes, *safety_notes]
    exists = False
    size_bytes = 0

    if safe_path:
        if "*" in safe_path:
            matches = _safe_glob_matches(root, safe_path)
            exists = bool(matches)
            size_bytes = sum(_file_size(path) for path in matches)
            if not matches:
                notes.append("glob_no_matches")
        else:
            full_path = (root / safe_path).resolve()
            try:
                full_path.relative_to(root)
            except ValueError:
                notes.append("unsafe_path:escapes_storage_root")
            else:
                if full_path.exists() and full_path.is_file():
                    exists = True
                    size_bytes = _file_size(full_path)

    return {
        "category": ref.category if ref.category in CATEGORIES else "unknown_or_unclassified",
        "owner_model": ref.owner_model,
        "owner_id": str(ref.owner_id),
        "field": ref.source,
        "source": ref.source,
        "path": str(ref.path or ""),
        "exists": exists,
        "size_bytes": size_bytes if exists else 0,
        "criticality": ref.criticality if ref.criticality in CRITICALITIES else "unknown",
        "notes": sorted(dict.fromkeys(note for note in notes if note)),
    }


def _normalize_reference_path(raw_path: str) -> tuple[str, list[str]]:
    raw = str(raw_path or "").strip().replace("\\", "/")
    notes: list[str] = []
    if not raw:
        return "", ["empty_path"]
    lowered = raw.lower()
    if "://" in raw or lowered.startswith(("http:", "https:", "data:", "blob:")):
        return "", ["unsafe_path:url_not_storage_relative"]
    if _has_windows_drive_prefix(raw) or raw.startswith("/"):
        return "", ["unsafe_path:absolute"]
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return "", ["unsafe_path:traversal"]
    return "/".join(parts), notes


def _has_windows_drive_prefix(raw_path: str) -> bool:
    return len(raw_path) >= 3 and raw_path[0].isalpha() and raw_path[1:3] == ":/"


def _safe_glob_matches(root: Path, rel_glob: str) -> list[Path]:
    if "**" in rel_glob:
        return []
    base = (root / rel_glob).resolve()
    try:
        base.parent.relative_to(root)
    except ValueError:
        return []
    matches: list[Path] = []
    for raw_match in glob.glob(str(root / rel_glob)):
        path = Path(raw_match).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path.is_file():
            matches.append(path)
    return sorted(matches)


def _read_safe_json_sidecar(root: Path, rel_path: str) -> dict[str, Any] | None:
    safe_path, _notes = _normalize_reference_path(rel_path)
    if not safe_path:
        return None
    full_path = (root / safe_path).resolve()
    try:
        full_path.relative_to(root)
    except ValueError:
        return None
    if not full_path.exists() or not full_path.is_file():
        return None
    try:
        payload = json.loads(full_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _project_filter(project_id: int | str | None) -> dict[str, Any]:
    return {"id": project_id} if project_id not in (None, "") else {}


def _related_project_filter(project_id: int | str | None) -> dict[str, Any]:
    return {"project_id": project_id} if project_id not in (None, "") else {}


def _job_project_filter(project_id: int | str | None) -> dict[str, Any]:
    return {"project_id": project_id} if project_id not in (None, "") else {}


def _project_user_ids(project_id: int | str | None) -> set[int] | None:
    if project_id in (None, ""):
        return None
    return set(Project.objects.filter(id=project_id).exclude(user_id=None).values_list("user_id", flat=True))


def _dedupe_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for entry in entries:
        key = (
            entry["category"],
            entry["owner_model"],
            entry["owner_id"],
            entry["field"],
            entry["path"],
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def _summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_category = Counter(entry["category"] for entry in entries)
    by_criticality = Counter(entry["criticality"] for entry in entries)
    return {
        "total_references": len(entries),
        "existing_references": sum(1 for entry in entries if entry["exists"]),
        "missing_references": sum(1 for entry in entries if not entry["exists"]),
        "by_category": {category: by_category.get(category, 0) for category in CATEGORIES if by_category.get(category, 0)},
        "by_criticality": {
            criticality: by_criticality.get(criticality, 0)
            for criticality in CRITICALITIES
            if by_criticality.get(criticality, 0)
        },
    }


def _entry_sort_key(entry: dict[str, Any]) -> tuple[str, str, int, str, str]:
    owner_id = str(entry.get("owner_id") or "")
    try:
        owner_id_int = int(owner_id)
    except ValueError:
        owner_id_int = 0
    return (
        str(entry.get("category") or ""),
        str(entry.get("owner_model") or ""),
        owner_id_int,
        str(entry.get("field") or ""),
        str(entry.get("path") or ""),
    )
