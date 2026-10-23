"""Real API grounding/review/selection, isolation and adversarial source checks."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection, close_old_connections
import pytest
from rest_framework.test import APIClient

from core.models import (Project, TranscriptPage, Job, VisualDesignSuggestion, VisualEvidenceProposal,
                         VisualEvidenceReview, VisualDraftSelection)
from core.visual_design_source import build_studio_visual_source
from core.visual_design.grounding import PROPOSAL_VERSION
from core.visual_design.schemas import canonical_json
from core.visual_suggestions import generate_suggestion
from core.visual_evidence import review_proposal
from core.visual_drafts import verified_preview, VisualDraftConflict

pytestmark = pytest.mark.django_db
BULLETS = "Data preparation\n- Collect sample\n- Record measurement\n- Check result"
PROCESS = "Measurement process\n1. Collect sample\n2. Record measurement\n3. Check result"


@pytest.fixture
def lesson():
    owner = User.objects.create_user(username="grounded-teacher")
    project = Project.objects.create(user=owner, title="Grounded lesson", status="ready", is_published=True)
    page = TranscriptPage.objects.create(project=project, page_key="slide-0-split-1", order=0,
        source_slide_index=0, split_index=1, original_text=BULLETS, narration_text="Protected narration")
    Job.objects.create(project=project, job_type="video_export", status="done", result_url="published.mp4")
    client = APIClient(); client.force_authenticate(owner)
    return owner, project, page, client


def proposal_payload(text=BULLETS):
    labels = [line[2:] for line in text.splitlines()[1:]]
    return {"schema_version": PROPOSAL_VERSION, "strategy": "flowchart", "rationale": "Proposed order; teacher must confirm.",
        "evidence": {"kind": "process", "data": {"steps": [{"id": f"step-{i+1}", "label": label} for i, label in enumerate(labels)],
                "order": [f"step-{i+1}" for i in range(len(labels))], "relationship": "sequence"}},
        "references": [{"start": 0, "end": len(text), "quote": text}]}


def source(lesson):
    return build_studio_visual_source(lesson[1], lesson[2].page_key)


def url(lesson, suffix="visual-proposals/"):
    return f"/api/v1/projects/{lesson[1].pk}/{suffix}"


def propose(lesson, value=None):
    with patch("core.visual_evidence.model_available", return_value=True), patch("core.visual_evidence.OllamaLessonIntelligenceProvider.generate_structured", return_value=canonical_json(value or proposal_payload())) as provider:
        response = lesson[3].post(url(lesson), {"page_key": lesson[2].page_key, "expected_source_hash": source(lesson).source_hash}, format="json")
    assert response.status_code == 200, response.data
    provider.assert_called_once()
    return response


def review(lesson, proposal_id, action="confirm", expected_hash=None):
    return lesson[3].post(url(lesson, f"visual-proposals/{proposal_id}/decision/"),
        {"action": action, "expected_source_hash": expected_hash or source(lesson).source_hash}, format="json")


def protected_state():
    return [list(model.objects.values()) for model in (Project, TranscriptPage, Job)]


def test_source_text_generates_flowchart_then_selects_without_lesson_or_video_mutations(lesson):
    owner, project, page, client = lesson
    page.original_text = PROCESS; page.save()
    initial = protected_state()
    with patch("core.views._celery_app.send_task", side_effect=AssertionError("No render dispatch")):
        current = source(lesson)
        candidate, created = generate_suggestion(project, owner, current)
        assert created and candidate.status == "ready"
        assert candidate.plan_payload["strategy"] == "flowchart"
        assert candidate.plan_payload["source_hash"] != current.source_hash
        assert verified_preview(candidate, current)["status"] == "valid"
        response = client.post(url(lesson, "visual-draft/"), {"page_key": page.page_key, "action": "select", "candidate_id": str(candidate.pk),
            "expected_source_hash": current.source_hash, "expected_version": 0}, format="json")
    assert response.status_code == 200, response.data
    assert protected_state() == initial
    assert source(lesson).source_hash == current.source_hash


def test_propose_review_generate_and_private_draft_reopen(lesson):
    _, project, page, client = lesson
    before = protected_state(); current = source(lesson)
    proposed = propose(lesson).data["proposal"]
    assert proposed["decision"] == "pending" and proposed["inferred"]
    assert not VisualDesignSuggestion.objects.exists()
    assert not VisualDraftSelection.objects.exists()
    confirmed = review(lesson, proposed["id"])
    assert confirmed.status_code == 200, confirmed.data
    result = confirmed.data["suggestion"]
    assert result["strategy"] == "flowchart" and result["status"] == "ready"
    assert result["evidence"]["origin"] == "teacher_confirmed"
    assert not VisualDraftSelection.objects.exists()  # confirmation is NOT selection
    response = client.post(url(lesson, "visual-draft/"), {"page_key": page.page_key, "action": "select", "candidate_id": result["id"],
        "expected_source_hash": current.source_hash, "expected_version": 0}, format="json")
    assert response.status_code == 200, response.data
    assert not client.get(url(lesson, "visual-draft/"), {"page_key": page.page_key}).data["selection"]["is_stale"]
    assert protected_state() == before
    assert source(lesson).source_hash == current.source_hash
    preview = client.get(url(lesson, f"visual-suggestions/{result['id']}/preview/"))
    assert preview.status_code == 200 and b"<script" not in preview.content
    assert preview["Content-Security-Policy"].endswith("sandbox")
    assert preview["Cache-Control"] == "private, no-store"
    assert "<ol" not in preview.content.decode()  # no executable DOM/render operations


def test_repeated_proposal_and_confirmation_reuse_immutable_rows(lesson):
    first = propose(lesson).data["proposal"]
    second = propose(lesson).data["proposal"]
    assert first["id"] == second["id"]
    confirmed = review(lesson, first["id"])
    again = review(lesson, first["id"])
    assert confirmed.status_code == again.status_code == 200
    assert again.data["reused"] is True
    assert VisualEvidenceReview.objects.count() == VisualEvidenceProposal.objects.count() == VisualDesignSuggestion.objects.count() == 1
    assert review(lesson, first["id"], "reject").status_code == 409


def test_rejection_is_private_and_cannot_generate_candidate(lesson):
    row = propose(lesson).data["proposal"]
    before = protected_state()
    result = review(lesson, row["id"], "reject")
    assert result.status_code == 200 and result.data["suggestion"] is None
    assert review(lesson, row["id"]).status_code == 409
    assert not VisualDesignSuggestion.objects.exists()
    assert protected_state() == before


@pytest.mark.parametrize("change", ["original_text", "narration_text", "split_index", "page_key", "is_active"])
def test_source_changes_invalidate_review_and_approved_selection(lesson, change):
    row = propose(lesson).data["proposal"]
    confirmed = review(lesson, row["id"])
    candidate = VisualDesignSuggestion.objects.get(pk=confirmed.data["suggestion"]["id"])
    page = lesson[2]
    setattr(page, change, {"split_index": 2, "page_key": "changed-key", "is_active": False}.get(change, "Changed text"))
    page.save()
    result = review(lesson, row["id"], expected_hash=row["source_hash"])
    assert result.status_code in (404, 409)
    if change not in ("page_key", "is_active"):
        with pytest.raises(VisualDraftConflict): verified_preview(candidate, source(lesson))
    listed = lesson[3].get(url(lesson), {"page_key": row["page_key"]})
    assert listed.data["proposals"][0]["is_stale"]


@pytest.mark.parametrize("failure", ["timeout", "unavailable", "malformed", "hallucination", "missing_reference", "oversized", "duplicate_key"])
def test_model_failure_never_creates_verified_evidence_or_blocks_deterministic_generation(lesson, failure):
    from core.lesson_intelligence import LessonIntelligenceProviderUnavailable
    value = proposal_payload()
    if failure == "hallucination": value["evidence"]["data"]["steps"][0]["label"] = "Invented step"
    if failure == "missing_reference": value["references"] = []
    raw = canonical_json(value)
    if failure == "malformed": raw = "not JSON"
    if failure == "oversized": raw = " " * 65537
    if failure == "duplicate_key": raw = raw[:-1] + ',"schema_version":"visual-proposal.v1"}'
    with patch("core.visual_evidence.model_available", return_value=True), patch("core.visual_evidence.OllamaLessonIntelligenceProvider.generate_structured",
            side_effect=TimeoutError() if failure == "timeout" else LessonIntelligenceProviderUnavailable() if failure == "unavailable" else None, return_value=raw):
        result = lesson[3].post(url(lesson), {"page_key": lesson[2].page_key, "expected_source_hash": source(lesson).source_hash}, format="json")
    assert result.status_code == 200 and result.data["fallback"]
    assert not VisualEvidenceProposal.objects.exists() and not VisualEvidenceReview.objects.exists()
    candidate, _ = generate_suggestion(lesson[1], lesson[0], source(lesson))
    assert candidate.status == "ready" and candidate.plan_payload["strategy"] == "typography_only"


def test_disabled_model_never_calls_provider_and_leaves_baseline_available(lesson):
    with patch("core.visual_evidence.model_available", return_value=False), patch("core.visual_evidence.OllamaLessonIntelligenceProvider.generate_structured") as provider:
        result = lesson[3].post(url(lesson), {"page_key": lesson[2].page_key, "expected_source_hash": source(lesson).source_hash}, format="json")
    provider.assert_not_called()
    assert result.data["fallback"] == "unavailable"


def test_proposals_and_reviews_are_owner_only_and_reject_unknown_write_fields(lesson):
    owner, project, page, client = lesson
    row = propose(lesson).data["proposal"]
    other = User.objects.create_user(username="other-grounded-owner")
    foreign = Project.objects.create(user=other, title="Foreign")
    foreign_client = APIClient(); foreign_client.force_authenticate(other)
    for client2 in (APIClient(), foreign_client):
        assert client2.get(url(lesson), {"page_key": page.page_key}).status_code in (401, 403, 404)
        assert client2.post(url(lesson, f"visual-proposals/{row['id']}/decision/"), {"action": "confirm", "expected_source_hash": row["source_hash"]}, format="json").status_code in (401, 403, 404)
    assert foreign_client.post(f"/api/v1/projects/{foreign.pk}/visual-proposals/{row['id']}/decision/",
        {"action": "confirm", "expected_source_hash": row["source_hash"]}, format="json").status_code == 404
    for extra in ({"evidence": []}, {"verified": True}, {"owner": owner.pk}, {"candidate_id": "forged"}):
        result = client.post(url(lesson, f"visual-proposals/{row['id']}/decision/"),
            {"action": "confirm", "expected_source_hash": row["source_hash"], **extra}, format="json")
        assert result.status_code == 400
    assert not VisualEvidenceReview.objects.exists()


@pytest.mark.parametrize("tamper", ["grounding", "proposal", "review", "preview"])
def test_confirmation_does_not_bypass_plan_preview_or_evidence_integrity(lesson, tamper):
    row = propose(lesson).data["proposal"]
    result = review(lesson, row["id"])
    candidate = VisualDesignSuggestion.objects.get(pk=result.data["suggestion"]["id"])
    if tamper == "grounding":
        data = deepcopy(candidate.grounding_payload); data["evidence"][0]["data"]["steps"][0]["label"] = "Fake"
        VisualDesignSuggestion.objects.filter(pk=candidate.pk).update(grounding_payload=data)
    if tamper == "proposal":
        data = proposal_payload(); data["evidence"]["data"]["steps"][0]["label"] = "Fake"
        VisualEvidenceProposal.objects.filter(pk=row["id"]).update(payload=data)
    if tamper == "review": VisualEvidenceReview.objects.filter(proposal_id=row["id"]).update(action="reject")
    if tamper == "preview": VisualDesignSuggestion.objects.filter(pk=candidate.pk).update(preview_hash="sha256:"+"0"*64)
    candidate.refresh_from_db()
    with pytest.raises(VisualDraftConflict): verified_preview(candidate, source(lesson))
    assert not VisualDraftSelection.objects.exists()


def test_model_proposal_is_not_renderable_without_explicit_teacher_confirmation(lesson):
    row = propose(lesson).data["proposal"]
    with pytest.raises(ValueError):
        generate_suggestion(lesson[1], lesson[0], source(lesson), proposal=VisualEvidenceProposal.objects.get(pk=row["id"]))
    assert not VisualDesignSuggestion.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_simultaneous_opposite_reviews_serialize_on_postgres(lesson):
    if connection.vendor != "postgresql": pytest.skip("PostgreSQL row-lock assertion runs in hosted CI")
    row = propose(lesson).data["proposal"]
    barrier = Barrier(2)
    def perform(action):
        close_old_connections()
        try:
            owner = User.objects.get(pk=lesson[0].pk)
            barrier.wait(timeout=5)
            try:
                review_proposal(lesson[1].pk, owner, row["id"], action, row["source_hash"])
                return 200
            except VisualDraftConflict:
                return 409
        finally: close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(perform, ["confirm", "reject"]))
    assert sorted(results) == [200, 409]
    assert VisualEvidenceReview.objects.count() == 1


def test_shared_ollama_transport_is_bounded_json_without_lesson_mutation():
    from io import BytesIO
    from core.lesson_intelligence import OllamaLessonIntelligenceProvider, LessonIntelligenceProviderUnavailable
    payload = canonical_json({"response": canonical_json(proposal_payload())}).encode()
    with patch("core.lesson_intelligence.urlopen", return_value=BytesIO(payload)) as request:
        response = OllamaLessonIntelligenceProvider().generate_structured("Bounded prompt")
    assert response == canonical_json(proposal_payload())
    assert request.call_args.kwargs["timeout"] <= 8
    sent = __import__('json').loads(request.call_args.args[0].data)
    assert sent["format"] == "json" and sent["stream"] is False and sent["options"]["temperature"] == 0
    with patch("core.lesson_intelligence.urlopen", return_value=BytesIO(b"x" * 65537)):
        with pytest.raises(LessonIntelligenceProviderUnavailable):
            OllamaLessonIntelligenceProvider().generate_structured("Bounded prompt")


def test_proposals_reviews_immutable_and_project_cleanup_cascades(lesson):
    from django.core.exceptions import ValidationError
    row = propose(lesson).data["proposal"]
    assert review(lesson, row["id"]).status_code == 200
    proposal = VisualEvidenceProposal.objects.get(pk=row["id"])
    with pytest.raises(ValidationError): proposal.save()
    decision = VisualEvidenceReview.objects.get(proposal=proposal)
    with pytest.raises(ValidationError): decision.save()
    lesson[1].delete()
    assert not VisualDesignSuggestion.objects.exists()
    assert not VisualEvidenceProposal.objects.exists()
    assert not VisualEvidenceReview.objects.exists()
