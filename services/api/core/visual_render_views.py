"""Explicit engineering snapshot endpoints. No render/preview job endpoint."""
from django.db import OperationalError
from rest_framework import serializers
from rest_framework.response import Response
from core.visual_design_views import OwnerVisualView, VisualGenerationThrottle
from core.visual_design.schemas import DesignError
from core.visual_render_snapshots import (current_render_revision, freeze_render_snapshot,
    read_render_snapshot, RenderSnapshotConflict)


def metadata(row):
    return {"id": str(row.pk), "schema_version": row.schema_version, "snapshot_hash": row.snapshot_hash,
        "revision_hash": row.revision_hash, "created_at": row.created_at.isoformat(),
        "page_order": [p["page_key"] for p in row.payload["state"]["pages"]],
        "publishing_allowed": False, "job_created": False}


class VisualRenderSnapshotListView(OwnerVisualView):
    throttle_classes = [VisualGenerationThrottle]

    def get(self, request, project_id):
        self.project(request, project_id)
        try: revision = current_render_revision(project_id, request.user)
        except DesignError as exc: raise RenderSnapshotConflict(str(exc)) from exc
        return Response({"schema_version": "visual-render-snapshot.v1", "revision_hash": revision,
                         "publishing_allowed": False, "job_created": False})

    def post(self, request, project_id):
        self.project(request, project_id)
        data = request.data
        if type(data) is not dict or set(data) != {"expected_revision_hash"} or type(data.get("expected_revision_hash")) is not str:
            raise serializers.ValidationError({"expected_revision_hash": "Only an expected saved revision is accepted."})
        serializer = serializers.RegexField(r"^sha256:[0-9a-f]{64}$")
        expected = serializer.run_validation(data["expected_revision_hash"])
        try: row = freeze_render_snapshot(project_id, request.user, expected)
        except OperationalError as exc:
            if any(key in str(exc).lower() for key in ("locked", "deadlock", "could not serialize")):
                raise RenderSnapshotConflict() from exc
            raise
        return Response(metadata(row), status=201)


class VisualRenderSnapshotDetailView(OwnerVisualView):
    def get(self, request, project_id, snapshot_id):
        self.project(request, project_id)
        try: row, _ = read_render_snapshot(project_id, request.user, snapshot_id)
        except (DesignError, OSError, KeyError, TypeError, ValueError) as exc:
            raise RenderSnapshotConflict("Pinned snapshot failed integrity validation.") from exc
        return Response(metadata(row))
