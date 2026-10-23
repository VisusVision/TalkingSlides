"""Owner-only preview APIs; deliberately disconnected from scene/render APIs."""
from django.http import HttpResponse
from django.db import OperationalError
from django.shortcuts import get_object_or_404
from django.utils.cache import patch_vary_headers
from rest_framework import permissions, serializers
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from core.models import Project, VisualDesignSuggestion
from core.visual_design_source import build_studio_visual_source
from core.visual_suggestions import SUGGESTION_VERSION, generate_suggestion, suggestion_metadata
from core.visual_drafts import VisualDraftConflict, decide_visual_draft, verified_preview, visual_draft_metadata


class GenerateRequest(serializers.Serializer):
    page_key = serializers.CharField(max_length=64, trim_whitespace=False)
    expected_source_hash = serializers.RegexField(r"^sha256:[0-9a-f]{64}$")

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields) or any(type(data.get(key)) is not str for key in self.fields):
            raise serializers.ValidationError({"non_field_errors": ["Only page_key and expected_source_hash are accepted. Save unsaved edits separately."]})
        return super().to_internal_value(data)


class VisualGenerationThrottle(UserRateThrottle):
    scope = "visual_design"
    rate = "20/min"


class OwnerVisualView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    def project(self, request, project_id):
        # Same owner rule as _is_project_owner; intentionally no staff/review override.
        # Filtering by owner also avoids disclosing the existence of another project.
        return get_object_or_404(Project, pk=project_id, user=request.user)

    def candidate(self, project, request, suggestion_id):
        return get_object_or_404(VisualDesignSuggestion, pk=suggestion_id, project=project, owner=request.user)

    def current_source(self, project, page_key):
        try:
            return build_studio_visual_source(project, page_key)
        except (NotFound, serializers.ValidationError):
            return None

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        response["Referrer-Policy"] = "no-referrer"
        patch_vary_headers(response, ["Authorization", "Cookie"])
        return response


class VisualSuggestionListView(OwnerVisualView):
    def get(self, request, project_id):
        project = self.project(request, project_id)
        page_key = request.query_params.get("page_key")
        if not page_key or len(page_key) > 64:
            raise serializers.ValidationError({"page_key": "Select a stable page key."})
        source = self.current_source(project, page_key)
        candidates = VisualDesignSuggestion.objects.filter(project=project, owner=request.user, page_key=page_key)[:20]
        return Response({"schema_version": SUGGESTION_VERSION, "source": source.metadata() if source else None,
                         "suggestions": [suggestion_metadata(item, source) for item in candidates], "limit": 20})

    def post(self, request, project_id):
        self.throttle_classes = [VisualGenerationThrottle]
        self.check_throttles(request)
        project = self.project(request, project_id)
        payload = GenerateRequest(data=request.data)
        payload.is_valid(raise_exception=True)
        source = build_studio_visual_source(project, payload.validated_data["page_key"])
        if payload.validated_data["expected_source_hash"] != source.source_hash:
            return Response({"error": "Saved source changed; refresh suggestions before generating.", "source": source.metadata()}, status=409)
        candidate, created = generate_suggestion(project, request.user, source)
        # Refresh after generation to expose a racing save as stale, never apply it.
        project.refresh_from_db()
        current = self.current_source(project, candidate.page_key)
        return Response({"suggestion": suggestion_metadata(candidate, current), "reused": not created}, status=201 if created else 200)


class VisualSuggestionDetailView(OwnerVisualView):
    def get(self, request, project_id, suggestion_id):
        project = self.project(request, project_id)
        candidate = self.candidate(project, request, suggestion_id)
        return Response(suggestion_metadata(candidate, self.current_source(project, candidate.page_key)))


class VisualSuggestionPreviewView(OwnerVisualView):
    def get(self, request, project_id, suggestion_id):
        project = self.project(request, project_id)
        candidate = self.candidate(project, request, suggestion_id)
        metadata = suggestion_metadata(candidate, self.current_source(project, candidate.page_key))
        if candidate.status != "ready" or not metadata["compatible"]:
            raise NotFound("No supported generated preview is available.")
        try:
            data = verified_preview(candidate)
        except VisualDraftConflict:
            return Response({"error": "Stored preview failed validation."}, status=409)
        response = HttpResponse(data["svg"], content_type="image/svg+xml; charset=utf-8")
        response["Content-Security-Policy"] = "default-src 'none'; script-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; sandbox"
        response["Content-Disposition"] = 'inline; filename="visual-suggestion.svg"'
        response["Cross-Origin-Resource-Policy"] = "same-origin"
        response["X-Visual-Source-Stale"] = "true" if metadata["is_stale"] else "false"
        return response


class VisualDecisionRequest(serializers.Serializer):
    page_key = serializers.CharField(max_length=64, trim_whitespace=False)
    action = serializers.ChoiceField(choices=["select", "reject", "restore"])
    candidate_id = serializers.UUIDField(required=False)
    expected_source_hash = serializers.RegexField(r"^sha256:[0-9a-f]{64}$")
    expected_version = serializers.IntegerField(min_value=0, max_value=2**53 - 1)

    def to_internal_value(self, data):
        if (not isinstance(data, dict) or set(data) - set(self.fields)
            or type(data.get("expected_version")) is not int
            or any(type(data.get(key)) is not str for key in ("page_key", "action", "expected_source_hash"))
            or ("candidate_id" in data and type(data["candidate_id"]) is not str)):
            raise serializers.ValidationError({"non_field_errors": ["Only the explicit visual decision contract is accepted."]})
        return super().to_internal_value(data)

    def validate(self, data):
        if (data["action"] == "restore") == ("candidate_id" in data):
            raise serializers.ValidationError("Select/reject require candidate_id; restore must omit it.")
        return data


class VisualDraftView(OwnerVisualView):
    """Private storage only. No call into ordinary draft or render mutation APIs."""

    def get(self, request, project_id):
        project = self.project(request, project_id)
        page_key = request.query_params.get("page_key")
        if not page_key or len(page_key) > 64:
            raise serializers.ValidationError({"page_key": "Select a stable page key."})
        return Response(visual_draft_metadata(project, request.user, page_key))

    def post(self, request, project_id):
        self.project(request, project_id)
        payload = VisualDecisionRequest(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            data, reused = decide_visual_draft(project_id, request.user, payload.validated_data)
        except OperationalError as exc:
            # SQLite's write lock cannot serialize select_for_update in remote
            # tests; a busy writer is a conflict, never a successful partial save.
            if "locked" in str(exc).lower() or "deadlock" in str(exc).lower() or "could not serialize" in str(exc).lower():
                raise VisualDraftConflict() from exc
            raise
        return Response({**data, "reused": reused})
