"""Owner-private, bounded delivery. No public media path or token resolver."""
from contextlib import ExitStack
import os
import re
import time

from django.db import DatabaseError
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException

from core.models import Project, VisualSegmentRun
from core import visual_segment_runs as controls
from core.visual_design.schemas import DesignError, require
from core.visual_segment_dispatch import require_ready
from core.visual_segment_artifacts import PrivateSegmentMedia

ROLES = {"mp4": ("segment", "video/mp4"), "srt": ("srt", "text/plain; charset=utf-8"), "vtt": ("vtt", "text/vtt; charset=utf-8")}
CHUNK = 64 * 1024
RECHECK_BYTES = 256 * 1024
RECHECK_SECONDS = 1.0


def byte_range(header, size):
    require(type(header) is str and len(header) <= 128 and "," not in header, "invalid_range", "Only one bounded byte range is supported")
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    require(match is not None and any(match.groups()), "invalid_range", "Invalid byte range")
    left, right = match.groups()
    if not left:
        suffix = int(right)
        require(suffix > 0, "invalid_range", "Empty suffix range")
        return max(0,size-suffix), size-1
    start = int(left); end = min(int(right),size-1) if right else size-1
    require(start < size and start <= end, "invalid_range", "Unsatisfiable byte range")
    return start,end


class PrivateDelivery:
    """Verified descriptor + bounded live control checks; never a long DB txn.

    Recheck before opening, after opening, at first iteration and at most every
    256 KiB or one second before the next chunk. Each chunk also rechecks pinned
    inode/stat/confinement. Revocation ends the stream, not a retroactive recall
    of bytes already yielded/in-flight. WSGI cannot change headers midstream.
    """
    def __init__(self, project_id, owner, run_id, role):
        require(role in ROLES, "media_role", "Unsupported private output role")
        require_ready()
        self.project_id,self.owner,self.run_id,self.role = project_id,owner,run_id,role
        self.stack = ExitStack(); self.closed = False
        self.fd = None; self.sent_since_check = RECHECK_BYTES; self.checked_at = 0
        try:
            project = get_object_or_404(Project, pk=project_id, user=owner)
            before = controls._database_stamp(project_id,owner.pk)
            decision = controls.private_result_access_policy(project_id,owner,run_id)
            require(decision.allowed, decision.reason_code, "Private media is not authorized")
            self.prepared,self.frozen,self.dependencies,self.proof,self.association,self.request = controls._prepare_run(project_id,owner,run_id)
            self.receipt_hash = self.prepared.completed_receipt_hash
            self.source_stamp = controls._database_stamp(project_id,owner.pk)
            require(before == self.source_stamp, "revision_conflict", "Source changed while authorizing delivery")
            self.pin = PrivateSegmentMedia(owner.pk,project_id).reference(self.prepared.completed_receipt["outputs"][role],ROLES[role][0])
            self.size = self.pin["asset"]["size"]
            self._authorize()
            self.source_check = self.stack.enter_context(controls._source_files(self.frozen))
            all_pins = {"media":{**self.prepared.input_pins["media"],**{
                name:PrivateSegmentMedia(owner.pk,project_id).reference(asset,ROLES[name][0])
                for name,asset in self.prepared.completed_receipt["outputs"].items()}},
                "visual":[*self.prepared.input_pins["visual"],self.prepared.completed_receipt["inputs"]["frame_asset"]]}
            self.guard = self.stack.enter_context(controls._InputGuard(owner.pk,project_id,all_pins))
            store = PrivateSegmentMedia(owner.pk,project_id)
            self.fd = self.stack.enter_context(store.open_verified(self.pin))
            self._authorize(); self.guard.check(); self.source_check()
        except BaseException:
            self.close(); raise

    def _authorize(self):
        require_ready()
        with controls._locked_project(self.project_id,self.owner) as project:
            run,job = controls._locked_run(project,self.owner,self.run_id)
            reason = controls._terminal(run,job)
            require(reason == "completed_private" and run.completed_receipt_hash == self.receipt_hash,
                    reason or "unavailable", "Private result is no longer authorized")
            require(controls._database_stamp(self.project_id,self.owner.pk) == self.source_stamp,
                    "revision_conflict", "Saved source changed during delivery")
            controls._approval(project,self.owner,self.request)
            controls._producer_current(self.dependencies,self.proof,self.association)
        self.checked_at = time.monotonic(); self.sent_since_check = 0

    def range(self, start, end):
        os.lseek(self.fd,start,os.SEEK_SET)
        self.remaining = end-start+1
        # A response might wait before iteration begins. Reauthorize first yield.
        self.sent_since_check = RECHECK_BYTES
        return self

    def __iter__(self): return self

    def __next__(self):
        if self.closed: raise StopIteration
        try:
            if self.remaining <= 0:
                self.close(); raise StopIteration
            if self.sent_since_check >= RECHECK_BYTES or time.monotonic()-self.checked_at >= RECHECK_SECONDS:
                self._authorize()
            self.guard.check(); self.source_check()
            data = os.read(self.fd,min(CHUNK,self.remaining))
            require(bool(data), "artifact_integrity", "Private media truncated during streaming")
            self.guard.check(); self.source_check()
            self.remaining -= len(data); self.sent_since_check += len(data)
            return data
        except (DesignError,OSError,Http404,APIException,DatabaseError):
            self.close(); raise StopIteration

    def close(self):
        if not self.closed:
            self.closed = True
            try: self.stack.close()
            except (DesignError,OSError): pass  # No success/state mutation on disconnect.
