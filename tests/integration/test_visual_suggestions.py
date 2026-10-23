"""Real DB/API regression tests for private preview-only Studio suggestions."""
from copy import deepcopy
import socket
from time import perf_counter
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as ModelValidationError
from django.test import override_settings
import pytest
from rest_framework.test import APIClient

from core.drafts import build_draft_from_active_project, save_project_draft_data
from core.models import Job, Project, TranscriptPage, UserProfile, VisualDesignSuggestion
from core.visual_design import VisualPlan, VisualPreview
from core.visual_design_source import build_studio_visual_source

pytestmark = pytest.mark.django_db


@pytest.fixture
def lesson():
    owner = User.objects.create_user(username="visual-owner", password="pass")
    UserProfile.objects.create(user=owner, role="publisher")
    project = Project.objects.create(user=owner, title="Real saved lesson", status="ready", is_published=True, moderation_status="approved")
    page = TranscriptPage.objects.create(project=project, order=7, source_slide_index=0, split_index=1,
        page_key="s1-p2", original_text="Ölçüm & kayıt", narration_text="Protected narration stays exactly as authored.",
        editor_document={"version": 1, "paragraphs": [{"text": "Ölçüm & kayıt"}]})
    Job.objects.create(project=project, job_type="video_export", status="done", result_url="published.mp4")
    client = APIClient()
    client.force_authenticate(owner)
    return owner, project, page, client


def endpoint(project):
    return f"/api/v1/projects/{project.pk}/visual-suggestions/"


def generate(lesson):
    _, project, page, client = lesson
    source = client.get(endpoint(project), {"page_key": page.page_key})
    assert source.status_code == 200
    result = client.post(endpoint(project), {"page_key": page.page_key, "expected_source_hash": source.data["source"]["source_hash"]}, format="json")
    assert result.status_code in (200, 201), result.data
    return result


def test_real_page_snapshot_preserves_zero_split_key_and_exact_text(lesson):
    _, project, page, _ = lesson
    source = build_studio_visual_source(project, page.page_key)
    data = source.snapshot.to_dict()
    assert data["project_id"] == str(project.pk)
    assert data["page_key"] == "s1-p2"
    assert data["source_slide_index"] == 0
    assert data["split_index"] == 1
    assert data["original_display_text"] == page.original_text
    assert data["protected_narration"] == page.narration_text
    assert data["evidence"] == []
    assert source.source_mode == "active"
    assert source == build_studio_visual_source(project, page.page_key)


def test_saved_draft_precedence_and_temporary_id_is_not_identity(lesson):
    _, project, page, _ = lesson
    draft = build_draft_from_active_project(project)
    draft["transcript_pages"][0].update(id=-71, original_text="Private draft display", narration_text="Private draft narration")
    save_project_draft_data(project, draft)
    before = deepcopy(project.draft_data)
    source = build_studio_visual_source(project, page.page_key)
    assert source.source_mode == "draft"
    assert source.snapshot.to_dict()["original_display_text"] == "Private draft display"
    assert source.snapshot.to_dict()["protected_narration"] == "Private draft narration"
    assert source.snapshot.identity["source_slide_index"] == 0
    assert source.snapshot.identity["split_index"] == 1
    assert project.draft_data == before
    page.refresh_from_db()
    assert page.original_text == "Ölçüm & kayıt"
    response = generate(lesson)
    assert response.data["suggestion"]["source_mode"] == "draft"
    assert response.data["suggestion"]["page_key"] == "s1-p2"


def test_client_text_evidence_and_unsaved_payloads_are_not_accepted(lesson):
    _, project, page, client = lesson
    source = build_studio_visual_source(project, page.page_key)
    for extra in ({"pages": [{"original_text": "Forged"}]}, {"draft_data": {}}, {"evidence": [{"verified": True}]}, {"source": "active"}):
        response = client.post(endpoint(project), {"page_key": page.page_key, "expected_source_hash": source.source_hash, **extra}, format="json")
        assert response.status_code == 400
    assert VisualDesignSuggestion.objects.count() == 0
    response = client.post(endpoint(project), {"page_key": str(page.pk), "expected_source_hash": source.source_hash}, format="json")
    assert response.status_code == 404


def test_nonstring_page_keys_are_rejected_and_invalid_legacy_keys_are_unsupported(lesson):
    _, project, page, client = lesson
    response = client.post(endpoint(project), {"page_key": page.pk, "expected_source_hash": "sha256:" + "a" * 64}, format="json")
    assert response.status_code == 400
    page.page_key = "legacy key"
    page.save()
    result = generate(lesson).data["suggestion"]
    assert result["status"] == "unsupported"
    assert result["page_key"] == "legacy key"


def test_no_saved_draft_is_created_by_reading_or_generation(lesson):
    _, project, _, _ = lesson
    before = list(Project.objects.filter(pk=project.pk).values())
    generate(lesson)
    assert list(Project.objects.filter(pk=project.pk).values()) == before
    project.refresh_from_db()
    assert project.draft_data == {}


def test_generation_is_repeatable_and_reuses_identical_candidate(lesson):
    first, second = generate(lesson), generate(lesson)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.data["reused"] is True
    assert first.data["suggestion"]["id"] == second.data["suggestion"]["id"]
    candidate = VisualDesignSuggestion.objects.get()
    assert candidate.status == "ready"
    assert candidate.plan_payload["strategy"] == "typography_only"
    assert VisualPlan(candidate.plan_payload).content_hash == candidate.plan_hash
    assert VisualPreview(candidate.preview_payload).content_hash == candidate.preview_hash
    assert first.data["suggestion"]["schema_version"] == "visual-suggestion.v1"
    assert first.data["suggestion"]["renderer_version"] == "svg-educational.v2"


def test_private_preview_delivery_and_metadata_do_not_expose_svg_or_paths(lesson):
    _, project, page, client = lesson
    response = generate(lesson)
    metadata = response.data["suggestion"]
    assert "<svg" not in str(metadata)
    assert "protected_narration" not in metadata
    assert metadata["applied"] is False and metadata["can_apply"] is False
    preview = client.get(metadata["preview_url"])
    assert preview.status_code == 200
    assert preview["Content-Type"].startswith("image/svg+xml")
    assert preview["Cache-Control"] == "private, no-store"
    assert "Authorization" in preview["Vary"] and "Cookie" in preview["Vary"]
    assert preview["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in preview["Content-Security-Policy"]
    assert "script-src 'none'" in preview["Content-Security-Policy"]
    assert preview["X-Visual-Source-Stale"] == "false"
    assert b"&amp;" in preview.content
    assert page.narration_text.encode() not in preview.content
    detail = client.get(endpoint(project) + metadata["id"] + "/")
    assert detail.status_code == 200
    assert detail.data["preview_hash"] == metadata["preview_hash"]
    assert detail["Cache-Control"] == "private, no-store"


def test_anonymous_other_owner_staff_and_cross_project_access_are_denied(lesson):
    _, project, page, _ = lesson
    metadata = generate(lesson).data["suggestion"]
    other = User.objects.create_user(username="outsider", is_staff=True, is_superuser=True)
    foreign_project = Project.objects.create(user=other, title="Foreign project")
    for user in (None, other):
        client = APIClient()
        if user:
            client.force_authenticate(user)
        expected = 404 if user else 401
        for url in (endpoint(project) + "?page_key=" + page.page_key, endpoint(project) + metadata["id"] + "/", metadata["preview_url"]):
            assert client.get(url).status_code == expected
        assert client.post(endpoint(project), {}, format="json").status_code == expected
    outsider = APIClient()
    outsider.force_authenticate(other)
    assert outsider.get(endpoint(foreign_project) + metadata["id"] + "/").status_code == 404
    assert outsider.get(endpoint(foreign_project) + metadata["id"] + "/preview/").status_code == 404


def test_owner_transfer_does_not_disclose_previous_owners_candidates(lesson):
    _, project, page, _ = lesson
    metadata = generate(lesson).data["suggestion"]
    new_owner = User.objects.create_user(username="new-owner")
    project.user = new_owner
    project.save()
    client = APIClient()
    client.force_authenticate(new_owner)
    assert client.get(endpoint(project), {"page_key": page.page_key}).data["suggestions"] == []
    assert client.get(metadata["preview_url"]).status_code == 404


@pytest.mark.parametrize("change", ["display", "narration", "split", "reorder", "editor", "saved_draft", "delete", "restore", "source"])
def test_stale_candidates_after_saved_source_changes(lesson, change):
    _, project, page, client = lesson
    metadata = generate(lesson).data["suggestion"]
    if change == "saved_draft":
        draft = build_draft_from_active_project(project)
        draft["transcript_pages"][0]["narration_text"] = "Saved private edit"
        save_project_draft_data(project, draft)
    elif change == "source":
        page.source_slide_index = 4
        page.save()
    else:
        if change == "display": page.original_text = "New display"
        if change == "narration": page.narration_text = "New narration"
        if change == "split": page.split_index = 2
        if change == "reorder": page.order = 2
        if change == "editor": page.editor_document = {"scene": {"text_scale": 1.2}}
        if change in ("delete", "restore"): page.is_active = False
        page.save()
        if change == "restore":
            page.is_active = True
            page.save()
    detail = client.get(endpoint(project) + metadata["id"] + "/")
    assert detail.status_code == 200
    assert detail.data["is_stale"] is True
    historical = client.get(metadata["preview_url"])
    assert historical.status_code == 200
    assert historical["X-Visual-Source-Stale"] == "true"
    if change != "delete":
        next_candidate = generate(lesson).data["suggestion"]
        assert next_candidate["id"] != metadata["id"]
        assert next_candidate["is_stale"] is False


def test_draft_merge_delete_and_discard_use_stable_keys(lesson):
    _, project, page, client = lesson
    draft = build_draft_from_active_project(project)
    draft["transcript_pages"].append({**deepcopy(draft["transcript_pages"][0]), "id": -2, "page_key": "s1-p3", "split_index": 2})
    save_project_draft_data(project, draft)
    metadata = generate(lesson).data["suggestion"]
    draft["transcript_pages"] = draft["transcript_pages"][1:]
    save_project_draft_data(project, draft)
    assert client.get(endpoint(project), {"page_key": page.page_key}).data["source"] is None
    assert client.get(endpoint(project) + metadata["id"] + "/").data["is_stale"] is True
    project.draft_data = {}
    project.save()
    assert generate(lesson).data["suggestion"]["source_mode"] == "active"


def test_source_hash_precondition_prevents_generation_from_outdated_listing(lesson):
    _, project, page, client = lesson
    source = build_studio_visual_source(project, page.page_key)
    page.original_text = "A newer saved page"
    page.save()
    response = client.post(endpoint(project), {"page_key": page.page_key, "expected_source_hash": source.source_hash}, format="json")
    assert response.status_code == 409
    assert VisualDesignSuggestion.objects.count() == 0


def test_real_prose_and_editor_claims_never_become_verified_evidence(lesson):
    _, project, page, _ = lesson
    page.original_text = "First A, then B. A has 12 kg. B has 18 kg. B is better."
    page.editor_document = {"evidence": [{"kind": "numeric", "verified": True}], "table": [[12, 18]]}
    page.save()
    source = build_studio_visual_source(project, page.page_key)
    assert source.snapshot.to_dict()["evidence"] == []
    assert generate(lesson).data["suggestion"]["strategy"] == "typography_only"


@pytest.mark.parametrize("text", ['<script>alert(1)</script>', 'unsupported 😀', 'word ' * 500])
def test_unsupported_legacy_content_is_explicit_and_persisted_without_rewrite(lesson, text):
    _, project, page, _ = lesson
    page.original_text = text
    page.save()
    result = generate(lesson).data["suggestion"]
    assert result["status"] == "unsupported"
    assert result["errors"]
    assert result["preview_url"] is None
    page.refresh_from_db()
    assert page.original_text == text


def test_original_asset_preserves_visual_and_inplace_changes_invalidate(lesson, tmp_path):
    _, project, page, client = lesson
    asset = tmp_path / str(project.pk) / "images" / "original.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"synthetic original")
    page.editor_document = {"scene": {"original_background_path": f"{project.pk}/images/original.png"}}
    page.save()
    with override_settings(STORAGE_ROOT=str(tmp_path)):
        source = build_studio_visual_source(project, page.page_key)
        assert source.snapshot.to_dict()["original_asset_ref"].startswith("asset:")
        response = generate(lesson).data["suggestion"]
        assert response["strategy"] == "preserve_original"
        assert response["status"] == "preserved" and response["preview_url"] is None
        asset.write_bytes(b"different synthetic original")
        assert build_studio_visual_source(project, page.page_key).source_hash != source.source_hash
        assert client.get(endpoint(project) + response["id"] + "/").data["is_stale"] is True


def test_foreign_or_traversal_asset_is_not_trusted(lesson, tmp_path):
    _, project, page, _ = lesson
    for raw in ("../foreign.png", "99999/images/x.png", "https://example.com/x.png"):
        page.editor_document = {"scene": {"original_background_path": raw}}
        page.save()
        with override_settings(STORAGE_ROOT=str(tmp_path)):
            assert "original_asset_ref" not in build_studio_visual_source(project, page.page_key).snapshot.to_dict()


def test_immutable_candidates_and_read_only_detail_methods(lesson):
    _, project, _, client = lesson
    metadata = generate(lesson).data["suggestion"]
    item = VisualDesignSuggestion.objects.get(pk=metadata["id"])
    item.status = "failed"
    with pytest.raises(ModelValidationError):
        item.save()
    for method in (client.patch, client.delete, client.post):
        assert method(endpoint(project) + metadata["id"] + "/", {}, format="json").status_code == 405


def test_incompatible_and_tampered_stored_preview_is_never_served(lesson):
    _, project, _, client = lesson
    metadata = generate(lesson).data["suggestion"]
    item = VisualDesignSuggestion.objects.get()
    damaged = deepcopy(item.preview_payload)
    damaged["svg"] = damaged["svg"].replace("</svg>", "<script>alert(1)</script></svg>")
    # Simulate out-of-band DB corruption; application save deliberately disallows it.
    VisualDesignSuggestion.objects.filter(pk=item.pk).update(preview_payload=damaged)
    assert client.get(metadata["preview_url"]).status_code == 409
    VisualDesignSuggestion.objects.filter(pk=item.pk).update(schema_version="visual-suggestion.v999")
    detail = client.get(endpoint(project) + metadata["id"] + "/").data
    assert detail["compatible"] is False and detail["is_stale"] is True
    assert detail["preview_url"] is None
    assert client.get(metadata["preview_url"]).status_code == 404


def test_no_project_page_draft_job_or_video_mutation_or_network_dispatch(lesson):
    _, project, page, client = lesson
    draft = build_draft_from_active_project(project)
    draft["transcript_pages"][0]["original_text"] = "Saved private display"
    save_project_draft_data(project, draft)
    before = {"projects": list(Project.objects.values()), "pages": list(TranscriptPage.objects.values()), "jobs": list(Job.objects.values())}
    with patch.object(socket, "socket", side_effect=AssertionError("Network forbidden")), patch("core.views._celery_app.send_task", side_effect=AssertionError("Render dispatch forbidden")):
        result = generate(lesson)
        assert client.get(result.data["suggestion"]["preview_url"]).status_code == 200
    assert {"projects": list(Project.objects.values()), "pages": list(TranscriptPage.objects.values()), "jobs": list(Job.objects.values())} == before


def test_candidate_history_is_bounded_and_generation_is_fast(lesson):
    started = perf_counter()
    for _ in range(5): generate(lesson)
    elapsed = perf_counter() - started
    assert elapsed < 3, f"Five bounded local deterministic API requests took {elapsed:.3f}s"
    _, project, page, client = lesson
    result = client.get(endpoint(project), {"page_key": page.page_key})
    assert result.data["limit"] == 20
    assert len(result.data["suggestions"]) == 1
