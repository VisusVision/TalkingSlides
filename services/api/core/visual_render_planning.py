"""Snapshot-to-existing-planner adapter. Report only; never used by dispatch yet."""
from copy import deepcopy
from core.render_planner import build_render_planner_manifest, plan_render_dirty_slides
from core.visual_design.schemas import content_hash
from worker.partial_render_manifest import (build_expected_partial_render_manifest,
    classify_partial_render_changes, build_partial_render_plan, stable_hash)


def snapshot_planner_inputs(snapshot, *, visual_environment=None, synthesis_dependencies_by_page_key=None, caption_dependencies_by_page_key=None):
    value = snapshot.to_dict()
    state = value["state"]
    from core.visual_design.segment_dependencies import validate_visual_environment
    environment = validate_visual_environment(visual_environment) if visual_environment is not None else None
    if environment is None and (synthesis_dependencies_by_page_key is not None or caption_dependencies_by_page_key is not None):
        raise ValueError("Private segment planning requires an explicit visual environment")
    known_keys = {p["page_key"] for p in state["pages"]}
    for supplied in (synthesis_dependencies_by_page_key, caption_dependencies_by_page_key):
        if supplied is not None and (type(supplied) is not dict or not set(supplied) <= known_keys):
            raise ValueError("Private dependency keys must name frozen stable pages")
    voice = state["voice"] or {"voice_id": "", "provider": "", "language": "auto", "speed": 1, "pitch": 1}
    tts = {**state["tts_settings"], "voice_id": voice["voice_id"], "provider": voice["provider"],
           "language": voice["language"], "pitch": voice["pitch"], "speed": voice["speed"]}
    slides = []
    for position, (page, scene) in enumerate(zip(state["pages"], value["scenes"])):
        row = deepcopy(page)
        # Candidate/source/asset and renderer identities are explicit. Selection
        # history timestamps do not affect planner inputs.
        if scene["design"]:
            design = scene["design"]
            visual = {"candidate_id": design["candidate_id"], "source_hash": design["source_hash"],
                "design_source_hash": design["design_source_hash"], "plan_hash": design["plan_hash"],
                "preview_hash": design["preview_hash"], "asset": design["asset"],
                "renderer": design["preview_metadata"]["renderer_version"], "template": design["plan"]["template_version"],
                "contains_text": True, "scene_type": scene["type"]}
        else: visual = scene["original"]
        if environment is not None:
            visual = {"schema_version": "visual-segment-image-dependencies.v1", "scene": visual,
                "source": state["source_stamps"][position], "fonts": value["fonts"] if scene["design"] else {},
                "text_ownership": {key:scene[key] for key in ("type", "display_overlay", "narration_enabled", "subtitles_enabled")},
                "environment": environment}
        image_hash = content_hash("completed-scene-image.v1", visual)
        if synthesis_dependencies_by_page_key is not None and page["page_key"] in synthesis_dependencies_by_page_key:
            from core.visual_design.segment_dependencies import validate_synthesis_dependencies
            dependency = validate_synthesis_dependencies(synthesis_dependencies_by_page_key[page["page_key"]], project_id=value["project_id"], page_key=page["page_key"], page=page)
            if dependency["owner_id"] != value["owner_id"]:
                raise ValueError("Private dependency owner differs from frozen snapshot")
            row["private_synthesis_dependencies"] = dependency
        if caption_dependencies_by_page_key is not None and page["page_key"] in caption_dependencies_by_page_key:
            row["private_caption_dependencies"] = deepcopy(caption_dependencies_by_page_key[page["page_key"]])
        row["snapshot_voice_identity"] = deepcopy(voice)
        row["snapshot_timing_identity"] = {key: page[key] for key in ("start_seconds", "end_seconds", "duration_seconds", "chunk_timeline")}
        row.update(index=position, slide_num=position + 1, page_id=page["page_key"],
            image_hash=image_hash, image_token=image_hash, scene_background_mode=scene["type"],
            pause_seconds=tts.get("pause_seconds") if tts.get("pause_seconds") is not None else value["render_config"]["default_pause_seconds"], duration=page["duration_seconds"], tts_settings=tts)
        row["editor_document"] = deepcopy(row["editor_document"])
        row["editor_document"]["scene"] = {**row["editor_document"].get("scene", {}),
            "background_mode": scene["type"], "completed_design_identity": visual}
        slides.append(row)
    return {"project_id": value["project_id"], "slides": slides, "voice": voice,
        "language": voice["language"], "tts_provider": voice["provider"], "tts_settings": tts, "avatar": state["avatar"]}


def build_snapshot_planning_manifests(snapshot, artifacts_by_page_key=None, *, visual_environment=None,
        synthesis_dependencies_by_page_key=None, caption_dependencies_by_page_key=None):
    inputs = snapshot_planner_inputs(snapshot, visual_environment=visual_environment,
        synthesis_dependencies_by_page_key=synthesis_dependencies_by_page_key, caption_dependencies_by_page_key=caption_dependencies_by_page_key)
    core = build_render_planner_manifest(**inputs, artifacts_by_page_key=artifacts_by_page_key)
    partial = build_expected_partial_render_manifest(project_id=inputs["project_id"], slides=inputs["slides"],
        tts_settings=inputs["tts_settings"], effective_language=inputs["language"], avatar_options=inputs["avatar"])
    for key, artifacts in (artifacts_by_page_key or {}).items():
        if key in partial["pages"]:
            partial["pages"][key]["artifacts"] = {name: value.get("path", "") if isinstance(value, dict) else value for name, value in artifacts.items()}
    partial["manifest_hash"] = stable_hash({key: value for key, value in partial.items() if key != "manifest_hash"})
    return {"core": core, "partial": partial}


def compare_snapshot_plans(previous, current, *, required_artifacts=("tts_audio", "composed_segment", "slide_image"), **artifact_checks):
    core = plan_render_dirty_slides(previous_manifest=previous["core"], current_manifest=current["core"],
        required_artifacts=required_artifacts, **artifact_checks)
    expected = deepcopy(current["partial"])
    # The core planner already verifies existence and optional content hashes.
    # Feed its failed-artifact verdict into the established partial classifier.
    for row in core["slides"]:
        if {"MissingArtifact", "CorruptArtifact"} & set(row["reasons"]) and row["page_key"] in expected["pages"]:
            expected["pages"][row["page_key"]]["artifacts"] = {}
    classification = classify_partial_render_changes(old_manifest=previous["partial"], expected_manifest=expected,
        required_artifacts=required_artifacts)
    return {"core": core, "classification": classification, "plan": build_partial_render_plan(classification),
            "execution_enabled": False}
