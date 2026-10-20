from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest
from django.contrib.auth.models import User
from django.test import override_settings

from core.models import Project, TranscriptPage, UserProfile, VoiceProfile
from core.render_planner import (
    DEFAULT_RENDER_RESOLUTION,
    REASON_AVATAR_CHANGED,
    REASON_AVATAR_POSITION_CHANGED,
    REASON_AVATAR_VISIBILITY_CHANGED,
    REASON_CORRUPT_ARTIFACT,
    REASON_IMAGE_CHANGED,
    REASON_LANGUAGE_CHANGED,
    REASON_MISSING_ARTIFACT,
    REASON_MISSING_BASELINE,
    REASON_NEW_SLIDE,
    REASON_PIPELINE_VERSION_CHANGED,
    REASON_PROVIDER_CHANGED,
    REASON_REMOVED_SLIDE,
    REASON_RENDER_RESOLUTION_CHANGED,
    REASON_TRANSCRIPT_CHANGED,
    REASON_VOICE_CHANGED,
    build_project_render_planner_manifest,
    build_render_planner_manifest,
    canonical_json,
    manifest_from_playback_sidecar,
    plan_render_dirty_slides,
    render_fingerprint,
)


def _slides() -> list[dict]:
    return [
        {
            "slide_number": 1,
            "page_key": "s1-p1",
            "narration_text": "Slide one transcript.",
            "display_text": "Slide one transcript.",
            "subtitle_chunks": ["Slide one transcript."],
            "image_hash": "image-one",
            "duration_seconds": 3.0,
            "pause_seconds": 1.0,
        },
        {
            "slide_number": 2,
            "page_key": "s2-p1",
            "narration_text": "Slide two transcript.",
            "display_text": "Slide two transcript.",
            "subtitle_chunks": ["Slide two transcript."],
            "image_hash": "image-two",
            "duration_seconds": 4.0,
            "pause_seconds": 1.0,
        },
        {
            "slide_number": 3,
            "page_key": "s3-p1",
            "narration_text": "Slide three transcript.",
            "display_text": "Slide three transcript.",
            "subtitle_chunks": ["Slide three transcript."],
            "image_hash": "image-three",
            "duration_seconds": 5.0,
            "pause_seconds": 1.0,
        },
    ]


def _voice(**overrides) -> dict:
    payload = {"provider": "xtts_v2", "voice_id": "voice-a", "language": "en", "speed": 1.0, "pitch": 1.0}
    payload.update(overrides)
    return payload


def _avatar(**overrides) -> dict:
    payload = {
        "enabled": True,
        "source_hash": "avatar-source-a",
        "model_version": "liveportrait+musetalk:v1",
        "motion_preset": "natural",
        "quality_preset": "high",
        "lipsync_engine": "liveportrait+musetalk",
        "publisher_layout": {"position": "top-right", "size": "medium", "visible": True},
    }
    payload.update(overrides)
    return payload


def _artifact_set(tmp_path, page_key: str) -> dict:
    artifacts = {}
    for name in ("composed_segment", "slide_image", "tts_audio"):
        path = tmp_path / f"{page_key}-{name}.bin"
        path.write_bytes(f"{page_key}:{name}".encode("utf-8"))
        artifacts[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return artifacts


def _artifacts(tmp_path, slides: list[dict]) -> dict:
    return {slide["page_key"]: _artifact_set(tmp_path, slide["page_key"]) for slide in slides}


def _manifest(tmp_path, *, slides=None, voice=None, language="en", provider="xtts_v2", avatar=None, **kwargs) -> dict:
    page_rows = _slides() if slides is None else slides
    return build_render_planner_manifest(
        project_id=42,
        slides=page_rows,
        voice=_voice() if voice is None else voice,
        language=language,
        tts_provider=provider,
        avatar=_avatar() if avatar is None else avatar,
        artifacts_by_page_key=_artifacts(tmp_path, page_rows),
        **kwargs,
    )


def _plan(previous, current, tmp_path, **kwargs) -> dict:
    return plan_render_dirty_slides(
        previous_manifest=previous,
        current_manifest=current,
        storage_root=tmp_path,
        **kwargs,
    )


def test_transcript_dirty_marks_only_changed_slide(tmp_path):
    previous = _manifest(tmp_path)
    slides = _slides()
    slides[1]["narration_text"] = "Edited slide two transcript."
    slides[1]["subtitle_chunks"] = ["Edited slide two transcript."]
    current = _manifest(tmp_path, slides=slides)

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == [2]
    assert plan["reusable_slides"] == [1, 3]
    assert plan["reasons"]["2"] == [REASON_TRANSCRIPT_CHANGED]


def test_image_dirty_marks_only_changed_slide(tmp_path):
    previous = _manifest(tmp_path)
    slides = _slides()
    slides[0]["image_hash"] = "image-one-v2"
    current = _manifest(tmp_path, slides=slides)

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == [1]
    assert plan["reasons"]["1"] == [REASON_IMAGE_CHANGED]


def test_voice_dirty_invalidates_every_slide(tmp_path):
    previous = _manifest(tmp_path)
    current = _manifest(tmp_path, voice=_voice(voice_id="voice-b"))

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == [1, 2, 3]
    assert set(plan["global_reasons"]) == {REASON_VOICE_CHANGED}
    assert all(reasons == [REASON_VOICE_CHANGED] for reasons in plan["reasons"].values())


def test_language_dirty_invalidates_every_slide(tmp_path):
    previous = _manifest(tmp_path, language="en")
    current = _manifest(tmp_path, language="tr")

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == [1, 2, 3]
    assert plan["global_reasons"] == [REASON_LANGUAGE_CHANGED]


def test_provider_dirty_invalidates_every_slide(tmp_path):
    previous = _manifest(tmp_path, provider="xtts_v2")
    current = _manifest(tmp_path, provider="gtts")

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == [1, 2, 3]
    assert plan["global_reasons"] == [REASON_PROVIDER_CHANGED]


def test_avatar_dirty_invalidates_every_slide(tmp_path):
    previous = _manifest(tmp_path)
    current = _manifest(tmp_path, avatar=_avatar(source_hash="avatar-source-b"))

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == [1, 2, 3]
    assert plan["global_reasons"] == [REASON_AVATAR_CHANGED]


def test_avatar_visibility_dirty_invalidates_every_slide(tmp_path):
    previous = _manifest(tmp_path)
    current = _manifest(
        tmp_path,
        avatar=_avatar(publisher_layout={"position": "top-right", "size": "medium", "visible": False}),
    )

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == [1, 2, 3]
    assert plan["global_reasons"] == [REASON_AVATAR_VISIBILITY_CHANGED]


def test_avatar_position_dirty_invalidates_every_slide(tmp_path):
    previous = _manifest(tmp_path)
    current = _manifest(
        tmp_path,
        avatar=_avatar(publisher_layout={"position": "bottom-left", "size": "large", "visible": True}),
    )

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == [1, 2, 3]
    assert plan["global_reasons"] == [REASON_AVATAR_POSITION_CHANGED]


def test_missing_artifact_marks_slide_dirty_without_exposing_path(tmp_path):
    previous = _manifest(tmp_path)
    previous["pages"]["s2-p1"]["artifacts"]["composed_segment"]["path"] = str(tmp_path / "missing.mp4")
    current = _manifest(tmp_path)

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == [2]
    assert plan["reasons"]["2"] == [REASON_MISSING_ARTIFACT]
    assert "missing.mp4" not in str(plan)


def test_corrupt_artifact_marks_slide_dirty_without_exposing_path(tmp_path):
    previous = _manifest(tmp_path)
    previous["pages"]["s1-p1"]["artifacts"]["tts_audio"]["sha256"] = "0" * 64
    current = _manifest(tmp_path)

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == [1]
    assert plan["reasons"]["1"] == [REASON_CORRUPT_ARTIFACT]
    assert "s1-p1-tts_audio.bin" not in str(plan)


def test_same_fingerprint_reuses_all_slides(tmp_path):
    previous = _manifest(tmp_path)
    current = _manifest(tmp_path)

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == []
    assert plan["reusable_slides"] == [1, 2, 3]
    assert plan["reasons"] == {}


def test_different_fingerprint_when_render_input_changes(tmp_path):
    previous = _manifest(tmp_path)
    slides = _slides()
    slides[2]["pause_seconds"] = 2.0
    current = _manifest(tmp_path, slides=slides)

    assert previous["pages"]["s3-p1"]["fingerprint"] != current["pages"]["s3-p1"]["fingerprint"]
    plan = _plan(previous, current, tmp_path)
    assert plan["dirty_slides"] == [3]


def test_global_invalidation_for_resolution_and_pipeline_version(tmp_path):
    previous = _manifest(tmp_path, render_resolution={"width": 1600, "height": 900}, pipeline_version="pipeline:v1")
    current = _manifest(tmp_path, render_resolution={"width": 1920, "height": 1080}, pipeline_version="pipeline:v2")

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == [1, 2, 3]
    assert plan["global_reasons"] == [REASON_PIPELINE_VERSION_CHANGED, REASON_RENDER_RESOLUTION_CHANGED]
    assert all(
        reasons == [REASON_PIPELINE_VERSION_CHANGED, REASON_RENDER_RESOLUTION_CHANGED]
        for reasons in plan["reasons"].values()
    )


def test_unrelated_metadata_does_not_change_fingerprint_or_dirty_slides(tmp_path):
    previous = _manifest(tmp_path)
    slides = _slides()
    changed = deepcopy(slides)
    changed[1]["updated_at"] = "2026-07-27T12:00:00Z"
    changed[1]["theme"] = "dark"
    changed[1]["notifications"] = [{"id": "toast"}]
    changed[1]["editor_document"] = {
        "scene": {
            "background_mode": "",
            "updated_at": "2026-07-27T13:00:00Z",
            "sidebar_state": "open",
        }
    }
    current = _manifest(tmp_path, slides=changed)

    assert previous["pages"]["s2-p1"]["fingerprint"] == current["pages"]["s2-p1"]["fingerprint"]
    plan = _plan(previous, current, tmp_path)
    assert plan["dirty_slides"] == []
    assert plan["reusable_slides"] == [1, 2, 3]


def test_reordered_dictionaries_and_unicode_equivalent_text_hash_the_same():
    first = {
        "text": "Cafe\u0301",
        "settings": {"b": 2.0, "a": 1.0000004},
        "rows": [{"z": "last", "a": "first"}],
    }
    second = {
        "rows": [{"a": "first", "z": "last"}],
        "settings": {"a": 1.0, "b": 2},
        "text": "Caf\u00e9",
    }

    assert canonical_json(first) == canonical_json(second)
    assert render_fingerprint(first) == render_fingerprint(second)


def test_list_order_remains_render_significant():
    assert render_fingerprint({"chunks": ["first", "second"]}) != render_fingerprint({"chunks": ["second", "first"]})


def test_explicit_gtts_preference_dirties_all_and_auto_stays_distinct(tmp_path):
    previous = _manifest(tmp_path, provider="xtts_v2", tts_settings={"provider_preference": "auto"})
    current = _manifest(tmp_path, provider="xtts_v2", tts_settings={"provider_preference": "gtts"})

    assert previous["pages"]["s1-p1"]["inputs"]["tts_settings"]["provider_preference"] == "auto"
    assert current["pages"]["s1-p1"]["inputs"]["tts_settings"]["provider_preference"] == "gtts"
    plan = _plan(previous, current, tmp_path)
    assert plan["dirty_slides"] == [1, 2, 3]
    assert plan["global_reasons"] == [REASON_PROVIDER_CHANGED]


def test_default_render_resolution_matches_pipeline_default():
    manifest = build_render_planner_manifest(project_id=42, slides=_slides())

    assert DEFAULT_RENDER_RESOLUTION == {"width": 1920, "height": 1080}
    assert manifest["render_resolution"] == {"width": 1920, "height": 1080}


def test_inserted_deleted_and_reordered_slides_reuse_unchanged_parts(tmp_path):
    previous = _manifest(tmp_path)
    slides = [
        {**_slides()[1], "slide_number": 1},
        {**_slides()[0], "slide_number": 2},
        {**_slides()[2], "slide_number": 3},
        {
            "slide_number": 4,
            "page_key": "s4-p1",
            "narration_text": "Inserted slide transcript.",
            "display_text": "Inserted slide transcript.",
            "subtitle_chunks": ["Inserted slide transcript."],
            "image_hash": "image-four",
        },
    ]
    current = _manifest(tmp_path, slides=slides)

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == [4]
    assert plan["reusable_slides"] == [1, 2, 3]
    assert plan["reasons"]["4"] == [REASON_NEW_SLIDE]
    assert plan["sequence_changed"] is True

    deleted = _manifest(tmp_path, slides=_slides()[:2])
    delete_plan = _plan(previous, deleted, tmp_path)
    assert delete_plan["dirty_slides"] == [3]
    assert delete_plan["reasons"]["3"] == [REASON_REMOVED_SLIDE]
    assert delete_plan["sequence_changed"] is True


def test_insert_before_existing_pages_only_dirties_inserted_page(tmp_path):
    previous = _manifest(tmp_path)
    slides = [
        _slides()[0],
        {
            "slide_number": 2,
            "page_key": "s1-p2",
            "narration_text": "Inserted narration.",
            "display_text": "Inserted narration.",
            "subtitle_chunks": ["Inserted narration."],
            "image_hash": "image-inserted",
        },
        {**_slides()[1], "slide_number": 3},
        {**_slides()[2], "slide_number": 4},
    ]
    current = _manifest(tmp_path, slides=slides)

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == [2]
    assert plan["reusable_slides"] == [1, 3, 4]
    assert [row["page_key"] for row in plan["slides"] if row["status"] == "reusable"] == [
        "s1-p1",
        "s2-p1",
        "s3-p1",
    ]
    assert plan["sequence_changed"] is True


def test_duplicate_page_keys_are_canonicalized_without_colliding(tmp_path):
    slides = [
        {**_slides()[0], "page_key": "duplicate"},
        {**_slides()[1], "page_key": "duplicate"},
    ]

    manifest = _manifest(tmp_path, slides=slides)

    assert manifest["page_order"] == ["duplicate", "duplicate-2"]
    assert sorted(manifest["pages"]) == ["duplicate", "duplicate-2"]


def test_missing_and_old_manifest_mark_current_slides_missing_baseline(tmp_path):
    current = _manifest(tmp_path)

    missing_plan = _plan(None, current, tmp_path)
    old_plan = _plan({"version": 0, "pages": {}}, current, tmp_path)

    assert missing_plan["dirty_slides"] == [1, 2, 3]
    assert all(reasons == [REASON_MISSING_BASELINE] for reasons in missing_plan["reasons"].values())
    assert old_plan["dirty_slides"] == [1, 2, 3]
    assert all(reasons == [REASON_MISSING_BASELINE] for reasons in old_plan["reasons"].values())


def test_old_sidecar_manifest_key_is_accepted(tmp_path):
    manifest = _manifest(tmp_path)

    assert manifest_from_playback_sidecar({"industrial_render_manifest": manifest}) == manifest
    assert manifest_from_playback_sidecar({"render_planner_manifest": {"version": 0, "pages": {}}}) is None


def test_project_id_does_not_affect_slide_fingerprint(tmp_path):
    first = build_render_planner_manifest(project_id=1, slides=_slides())
    second = build_render_planner_manifest(project_id=999, slides=_slides())

    assert first["manifest_hash"] != second["manifest_hash"]
    assert first["pages"]["s1-p1"]["fingerprint"] == second["pages"]["s1-p1"]["fingerprint"]


def test_large_lesson_planning_keeps_one_row_per_slide(tmp_path):
    slides = [
        {
            "page_key": f"s{idx}-p1",
            "slide_number": idx,
            "narration_text": f"Slide {idx}",
            "image_hash": f"image-{idx}",
        }
        for idx in range(1, 501)
    ]
    previous = _manifest(tmp_path, slides=slides)
    current = _manifest(tmp_path, slides=slides)

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == []
    assert plan["reusable_slides"] == list(range(1, 501))


def test_future_slide_local_avatar_layout_changes_can_stay_slide_scoped(tmp_path):
    previous = _manifest(tmp_path)
    slides = _slides()
    slides[1]["editor_document"] = {"scene": {"avatar_layout": {"position": "bottom-left", "size": "large"}}}
    current = _manifest(tmp_path, slides=slides)

    plan = _plan(previous, current, tmp_path)

    assert plan["dirty_slides"] == [2]
    assert plan["reusable_slides"] == [1, 3]
    assert plan["reasons"]["2"] == [REASON_AVATAR_POSITION_CHANGED]
    assert plan["global_reasons"] == []


@pytest.mark.django_db
def test_project_avatar_payload_matches_current_enabled_visible_and_runtime_semantics(tmp_path, monkeypatch):
    monkeypatch.delenv("ENABLE_AVATAR", raising=False)
    teacher = User.objects.create_user(username="planner-avatar-teacher")
    UserProfile.objects.create(
        user=teacher,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_original="avatars/teacher/source.png",
        avatar_image_processed="avatars/teacher/processed.png",
        avatar_source_valid=True,
        avatar_overlay_default_position="top-left",
        avatar_overlay_size="large",
        avatar_overlay_visible=True,
        avatar_lipsync_engine="musetalk",
    )
    source_path = tmp_path / "avatars" / "teacher" / "processed.png"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"avatar-source")
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    teacher.profile.avatar_source_hash = source_hash
    teacher.profile.avatar_preview_source_hash = source_hash
    teacher.profile.save(update_fields=["avatar_source_hash", "avatar_preview_source_hash", "updated_at"])
    project = Project.objects.create(
        title="Planner avatar semantics",
        user=teacher,
        avatar_enabled_override=None,
        avatar_visible=True,
        draft_data={
            "metadata": {
                "avatar_runtime_settings": {
                    "motion_preset": "subtle_blink",
                    "restoration_enabled": True,
                    "liveportrait_enabled": False,
                },
                "updated_at": "ignored",
            }
        },
    )
    TranscriptPage.objects.create(project=project, order=0, page_key="s1-p1", narration_text="Hello")

    with override_settings(STORAGE_ROOT=str(tmp_path), ENABLE_AVATAR=True):
        manifest = build_project_render_planner_manifest(project)

    avatar = manifest["pages"]["s1-p1"]["inputs"]["avatar"]
    assert avatar["enabled"] is True
    assert avatar["avatar_visible"] is True
    assert avatar["source_hash"] == source_hash
    assert avatar["lipsync_engine"] == "liveportrait+musetalk"
    assert avatar["runtime"] == {
        "liveportrait_enabled": False,
        "motion_preset": "subtle_blink",
        "restoration_enabled": True,
    }
    assert avatar["layout"] == {"position": "top-left", "size": "large", "visible": True}


@pytest.mark.django_db
def test_project_level_avatar_visibility_and_source_changes_invalidate_globally(tmp_path, monkeypatch):
    monkeypatch.delenv("ENABLE_AVATAR", raising=False)
    teacher = User.objects.create_user(username="planner-avatar-global")
    profile = UserProfile.objects.create(
        user=teacher,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_original="avatars/global/source.png",
        avatar_image_processed="avatars/global/processed.png",
        avatar_source_valid=True,
    )
    source_path = tmp_path / "avatars" / "global" / "processed.png"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"avatar-source-a")
    profile.avatar_source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    profile.save(update_fields=["avatar_source_hash", "updated_at"])
    project = Project.objects.create(title="Planner avatar global", user=teacher, avatar_visible=True)
    for index, text in enumerate(["one", "two"]):
        TranscriptPage.objects.create(project=project, order=index, page_key=f"s{index + 1}-p1", narration_text=text)

    with override_settings(STORAGE_ROOT=str(tmp_path), ENABLE_AVATAR=True):
        previous = build_project_render_planner_manifest(project)
        project.avatar_visible = False
        project.save(update_fields=["avatar_visible", "updated_at"])
        hidden = build_project_render_planner_manifest(project)
        source_path.write_bytes(b"avatar-source-b")
        profile.avatar_source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        profile.save(update_fields=["avatar_source_hash", "updated_at"])
        source_changed = build_project_render_planner_manifest(project)

    hidden_plan = _plan(previous, hidden, tmp_path, required_artifacts=())
    source_plan = _plan(hidden, source_changed, tmp_path, required_artifacts=())

    assert hidden_plan["dirty_slides"] == [1, 2]
    assert hidden_plan["global_reasons"] == [REASON_AVATAR_VISIBILITY_CHANGED]
    assert source_plan["dirty_slides"] == [1, 2]
    assert source_plan["global_reasons"] == [REASON_AVATAR_CHANGED]


@pytest.mark.django_db
def test_project_tts_settings_are_canonical_in_project_manifest(tmp_path):
    teacher = User.objects.create_user(username="planner-tts-teacher")
    UserProfile.objects.create(user=teacher, role="teacher")
    VoiceProfile.objects.create(user=teacher, provider="xtts_v2", voice_id="voice-a", language="en")
    project = Project.objects.create(
        title="Planner TTS",
        user=teacher,
        tts_settings={
            "provider_preference": " GTTS ",
            "overrides": {"technical": {" ASP ": " ey es pi "}},
            "created_at": "ignored",
        },
    )
    TranscriptPage.objects.create(project=project, order=0, page_key="s1-p1", narration_text="ASP")

    manifest = build_project_render_planner_manifest(project)

    settings = manifest["pages"]["s1-p1"]["inputs"]["tts_settings"]
    assert settings["provider_preference"] == "gtts"
    assert settings["overrides"]["technical"] == {"ASP": "ey es pi"}
