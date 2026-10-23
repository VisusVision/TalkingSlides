"""Private proposal/review endpoints, separate from video and ordinary drafts."""
from django.db import OperationalError
from rest_framework import serializers
from rest_framework.response import Response

from core.models import VisualEvidenceProposal
from core.visual_design_views import OwnerVisualView, GenerateRequest, VisualGenerationThrottle
from core.visual_design_source import build_studio_visual_source
from core.visual_drafts import VisualDraftConflict
from core.visual_evidence import model_available, propose_evidence, proposal_metadata, review_proposal


class VisualProposalListView(OwnerVisualView):
    throttle_classes = [VisualGenerationThrottle]

    def get(self, request, project_id):
        project = self.project(request, project_id)
        page_key = request.query_params.get("page_key")
        if not page_key or len(page_key) > 64:
            raise serializers.ValidationError({"page_key": "Select a stable page key."})
        source = self.current_source(project, page_key)
        rows = VisualEvidenceProposal.objects.filter(project=project, owner=request.user, page_key=page_key)[:20]
        return Response({"model_available": model_available(), "source": source.metadata() if source else None,
                         "proposals": [proposal_metadata(row, source) for row in rows], "limit": 20})

    def post(self, request, project_id):
        project = self.project(request, project_id)
        serializer = GenerateRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        source = build_studio_visual_source(project, serializer.validated_data["page_key"])
        if source.source_hash != serializer.validated_data["expected_source_hash"]:
            raise VisualDraftConflict()
        proposal, fallback = propose_evidence(project, request.user, source)
        project.refresh_from_db()
        current = self.current_source(project, source.page_key)
        return Response({"proposal": proposal_metadata(proposal, current) if proposal else None,
                         "fallback": fallback, "applied": False})


class ReviewRequest(serializers.Serializer):
    action = serializers.ChoiceField(choices=["confirm", "reject"])
    expected_source_hash = serializers.RegexField(r"^sha256:[0-9a-f]{64}$")

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) != set(self.fields) or any(type(value) is not str for value in data.values()):
            raise serializers.ValidationError({"non_field_errors": ["Only action and expected_source_hash are accepted."]})
        return super().to_internal_value(data)


class VisualProposalReviewView(OwnerVisualView):
    def post(self, request, project_id, proposal_id):
        self.project(request, project_id)
        serializer = ReviewRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            data = review_proposal(project_id, request.user, proposal_id, **serializer.validated_data)
        except OperationalError as exc:
            if any(term in str(exc).lower() for term in ("locked", "deadlock", "could not serialize")):
                raise VisualDraftConflict() from exc
            raise
        return Response(data)
