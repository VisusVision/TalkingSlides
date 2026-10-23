"""Bounded private reuse-only segment adapter. Deliberately NOT a Celery task.

No dispatch, public endpoint, synthesis, latest-job lookup or lesson finalizer.
A server-owned Run and an already claimed, unexpired generation/token are required.
Production producer dependencies remain unconfigured in core.visual_segment_runs.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from django.shortcuts import get_object_or_404
from core.models import VisualSegmentRun
from core import visual_segment_runs as controls
from core.visual_design.schemas import DesignError, require, digest
from core.visual_design.segment_contracts import LIMITS, _uuid, _integer
from core.visual_segment_artifacts import PrivateSegmentMedia, _descend, _open_media, CHUNK_BYTES
from core.visual_segment_validation import (installed_runtime, caption_bytes, validate_segment_outputs)
from core.visual_render_assets import PrivateRenderAssets
from worker.completed_scene import compose_frozen_frame
from scripts.ffmpeg_helpers import create_slide_video


@contextmanager
def _attempt_directory(store, run, generation):
    # Every invocation owns a fresh namespace, including duplicate deliveries.
    parts = ("segments-v1", store.owner_id, store.project_id, "attempts", str(run.pk))
    with _descend(store.root, parts, create=True) as parent:
        name = f"g{generation}-" + uuid.uuid4().hex
        os.mkdir(name, 0o700, dir_fd=parent)
        with _descend(store.root, (*parts, name), create=False) as directory:
            yield store.root.joinpath(*parts, name), directory
    # Retain failed/finished attempt files for later reference-aware cleanup.
    # They are private and never indicate completion or authorization.


def _write(directory, name, data):
    fd = os.open(name, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, "wb") as file:
        file.write(data); file.flush(); os.fsync(file.fileno())


def _copy_audio(store, pin, directory):
    with store.open_verified(pin) as fd:
        out = os.open("narration.m4a", os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(out, "wb") as file:
            total = 0; hasher = hashlib.sha256()
            while True:
                block = os.read(fd, CHUNK_BYTES)
                if not block: break
                total += len(block); hasher.update(block); file.write(block)
                require(total <= pin["asset"]["size"], "artifact_integrity", "Audio copy grew")
            file.flush(); os.fsync(file.fileno())
            require(total == pin["asset"]["size"] and "sha256:"+hasher.hexdigest() == pin["asset"]["sha256"], "artifact_integrity", "Audio copy changed")


def _pin_output(store, directory, name, role):
    fd = _open_media(directory, name)
    try:
        maximum = LIMITS["max_mp4_bytes"] if role == "segment" else LIMITS["max_caption_bytes"]
        size = os.fstat(fd).st_size
        require(0 < size <= maximum, "asset_limit", "Output size exceeds role limit")
        hasher = hashlib.sha256(); total = 0
        while True:
            data = os.read(fd, CHUNK_BYTES)
            if not data: break
            total += len(data); hasher.update(data)
            require(total <= size, "artifact_integrity", "Output grew during pin")
        require(total == size, "artifact_integrity", "Output truncated")
        asset = {"sha256":"sha256:"+hasher.hexdigest(),"size":size,"extension":"mp4" if role=="segment" else role}
        os.lseek(fd,0,os.SEEK_SET)
        return store.pin_from_fd(fd, asset, role)
    finally:
        os.close(fd)


def execute_private_segment(run_id, execution_hash, attempt_generation, *, claim_token):
    """Direct internal engineering entry point; no arbitrary paths/settings.

    Claiming/reservation are separate server operations. This function never
    takes a broker message as source authority and registers no worker task.
    All test producer injection is test-local; production fails closed.
    """
    _uuid(str(run_id)); digest(execution_hash); _integer(attempt_generation,1); _uuid(str(claim_token))
    run = get_object_or_404(VisualSegmentRun, pk=run_id)
    owner, project_id = run.owner, run.project_id
    require(run.execution_hash == execution_hash, "run_integrity", "Wrong execution identity")
    if run.completed_receipt is not None:
        decision = controls.private_result_access_policy(project_id,owner,run_id)
        require(decision.allowed and run.completed_claim_token == uuid.UUID(str(claim_token))
                and run.completed_receipt["attempt_generation"] == attempt_generation,
                "stale_attempt", "Completed delivery is not this authorized attempt")
        return run.completed_receipt
    # Closed preflight gate checks current controls and exact claim. It cannot
    # itself approve media; only independent validation below produces a witness.
    started = time.monotonic()
    def remaining():
        left = LIMITS["execution_timeout_seconds"]-(time.monotonic()-started)
        require(left > 0, "execution_timeout", "Private attempt exceeded execution deadline")
        return left
    try:
        controls.prepare_receipt_acceptance(project_id,owner,run_id,execution_hash=execution_hash,
            generation=attempt_generation,claim_token=claim_token)
        prepared,frozen,deps,proof,association,request = controls._prepare_run(project_id,owner,run_id)
        require(installed_runtime(deps.verifier) == request.to_dict()["recipe"]["runtime"], "runtime", "Unsupported installed runtime")
        store = PrivateSegmentMedia(owner.pk,project_id)
        # Count ALL immutable inputs before outputs, using one attempt budget.
        for role,pin in prepared.input_pins["media"].items():
            with store.budget.claim(("input",role,pin["asset"]["sha256"]),pin["asset"]["size"]): pass
        for ref in prepared.input_pins["visual"]:
            with store.budget.claim(("visual",ref["sha256"],ref["extension"]),ref["size"]): pass
        # Reserve a conservative maximum staging budget before native writes;
        # final CAS pins are charged separately by the same attempt budget.
        narration_size = prepared.input_pins["media"]["narration"]["asset"]["size"]
        with store.budget.claim(("temporary_audio",), narration_size): pass
        with store.budget.claim(("temporary_mp4",), LIMITS["max_mp4_bytes"]): pass
        with controls._InputGuard(owner.pk,project_id,prepared.input_pins) as guard:
            with store.open_verified(prepared.input_pins["media"]["narration"]) as fd:
                deps.verifier.validate(fd,proof.to_dict())
            assets = PrivateRenderAssets(project_id)
            frame = compose_frozen_frame(frozen,run.page_key,assets,ffmpeg_path=deps.verifier.ffmpeg,timeout_seconds=min(20,remaining()))
            frame_bytes = assets.read(frame["frame_asset"])
            with store.budget.claim(("temporary_frame",),len(frame_bytes)): pass
            with store.budget.claim(("frame",frame["frame_asset"]["sha256"]),frame["frame_asset"]["size"]): pass
            with _attempt_directory(store,run,attempt_generation) as (directory,dirfd):
                _write(dirfd,"frame.png",frame_bytes)
                _copy_audio(store,prepared.input_pins["media"]["narration"],dirfd)
                for role in ("srt","vtt"):
                    caption = caption_bytes(request,role)
                    with store.budget.claim(("temporary_caption",role),len(caption)): pass
                    _write(dirfd,"captions."+role,caption)
                _write(dirfd,"segment.mp4",b"")
                create_slide_video(str(directory/"frame.png"),str(directory/"narration.m4a"),str(directory/"segment.mp4"),
                    duration_sec=request.to_dict()["subtitles"]["target_duration_ms"]/1000,
                    verified_aac_copy=True,ffmpeg_path=deps.verifier.ffmpeg,timeout_seconds=remaining())
                pins = {role:_pin_output(store,dirfd,"segment.mp4" if role=="mp4" else "captions."+role,
                    "segment" if role=="mp4" else role) for role in ("mp4","srt","vtt")}
                remaining(); guard.check()
                witness = validate_segment_outputs(request,frame_asset=frame["frame_asset"],pins=pins,
                    verifier=deps.verifier,generation=attempt_generation,claim_token=claim_token)
                remaining(); guard.check()
                accepted,_ = controls.accept_verified_segment(project_id,owner,run_id,witness=witness)
                return accepted.completed_receipt
    except (OSError, subprocess.TimeoutExpired) as exc:
        import errno
        transient = isinstance(exc, subprocess.TimeoutExpired) or getattr(exc, "errno", None) in {errno.EAGAIN, errno.EIO, errno.ENOSPC, errno.ETIMEDOUT}
        if not transient:
            try:
                controls.record_terminal_failure(project_id,owner,run_id,execution_hash=execution_hash,
                    generation=attempt_generation,claim_token=claim_token,reason_code="invalid_or_corrupt_artifact")
            except DesignError: pass
            raise DesignError("invalid_or_corrupt_artifact", "Private input/output is missing or unsafe") from exc
        # Explicit retry eligibility only, no scheduling or fresh inputs. At most
        # three claims are possible, including lease takeovers and failed work.
        exhausted = False
        try:
            latest = VisualSegmentRun.objects.get(pk=run_id)
            if latest.attempt_count >= 3:
                exhausted = True
                controls.record_terminal_failure(project_id,owner,run_id,execution_hash=execution_hash,
                    generation=attempt_generation,claim_token=claim_token,reason_code="retry_exhausted")
            else:
                controls.release_claim(project_id,owner,run_id,execution_hash=execution_hash,
                    generation=attempt_generation,claim_token=claim_token)
        except (DesignError, VisualSegmentRun.DoesNotExist): pass  # Already fenced/deleted.
        if exhausted:
            raise DesignError("retry_exhausted", "Private media attempt limit exhausted") from exc
        raise DesignError("transient_media_failure", "Private media attempt failed; explicit bounded retry required") from exc
    except (DesignError, RuntimeError, ValueError, KeyError) as exc:
        # No failure may turn into synthesis, original fallback or publication.
        try:
            controls.record_terminal_failure(project_id,owner,run_id,execution_hash=execution_hash,
                generation=attempt_generation,claim_token=claim_token,reason_code="validation_failed")
        except DesignError: pass
        raise
