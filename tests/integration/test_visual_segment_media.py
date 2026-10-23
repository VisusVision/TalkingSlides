"""Real private MP4/SRT/VTT acceptance and fenced control integration.

No synthetic production flag: only the existing test-local producer fixture is
injected. VISUAL_MEDIA_REQUIRE=1 makes native tooling mandatory in hosted CI.
"""
from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw
from django.db import connection
from core import visual_segment_runs as runs
from core.models import VisualSegmentRun, Job, PRIVATE_SEGMENT_JOB, VisualDraftSelection
from core.visual_design.schemas import DesignError, content_hash
from core.visual_segment_artifacts import PrivateSegmentMedia
from core.visual_segment_validation import installed_runtime, validate_segment_outputs, caption_bytes, validate_captions
from core.visual_drafts import decide_visual_draft
from worker import visual_segment_tasks as media
from tests.integration.test_visual_segment_runs import (lesson, native, reserve, claim, freeze, select_all, infer,
    revoke, protected, _race, _postgres)

pytestmark = pytest.mark.django_db
EXAMPLES = json.loads((Path(__file__).parents[1]/"fixtures/visual_segments/media_examples.json").read_text())


def prepare(lesson, kind="flowchart"):
    # Runtime is explicitly engineering-supported on this host, separately from
    # producer receipt trust. Existing pure request/receipt goldens are unchanged.
    runtime = installed_runtime(lesson.dependencies.verifier)
    lesson.dependencies.runtime = runtime
    lesson.dependencies.supported_runtime_hashes = {content_hash("visual-segment-runtime.v1",runtime)}
    page = lesson.pages[0]; page.original_text = EXAMPLES[kind]
    if kind == "preserve_original":
        path = Path(lesson.source).parents[3]/"public"/str(lesson.project.pk)/"original.png"
        path.parent.mkdir(parents=True,exist_ok=True)
        image = Image.new("RGB",(1920,1080),"#f4f7fb"); draw = ImageDraw.Draw(image)
        draw.rectangle((160,300,780,800),fill="#147d92"); draw.ellipse((1080,350,1530,800),fill="#5268b5")
        image.save(path)
        page.editor_document={"scene":{"background_mode":"original","original_background_path":f"{lesson.project.pk}/original.png"}}
    page.save()
    if kind != "preserve_original": select_all(lesson)
    else:
        old = VisualDraftSelection.objects.get(project=lesson.project,page_key=page.page_key)
        decide_visual_draft(lesson.project.pk,lesson.owner,{"page_key":page.page_key,"action":"restore","expected_source_hash":
            __import__('core.visual_design_source',fromlist=['build_studio_visual_source']).build_studio_visual_source(lesson.project,page.page_key).source_hash,
            "expected_version":old.version})
    lesson.snapshot=freeze(lesson)
    run,_=reserve(lesson); attempt=claim(lesson,run,lease_seconds=300)
    return run,attempt


def execute(run,attempt):
    return media.execute_private_segment(str(run.pk),run.execution_hash,attempt.attempt_generation,claim_token=attempt.claim_token)


def witness_for(lesson,run):
    run.refresh_from_db()
    prepared,frozen,deps,proof,assoc,request = runs._prepare_run(lesson.project.pk,lesson.owner,run.pk)
    value=run.completed_receipt
    store=PrivateSegmentMedia(lesson.owner.pk,lesson.project.pk)
    pins={k:store.reference(v,"segment" if k=="mp4" else k) for k,v in value["outputs"].items()}
    return validate_segment_outputs(request,frame_asset=value["inputs"]["frame_asset"],pins=pins,verifier=deps.verifier,
        generation=value["attempt_generation"],claim_token=run.completed_claim_token)


@pytest.mark.parametrize("kind",EXAMPLES)
def test_five_real_private_segments_exact_aac_and_cues(lesson,kind):
    run,attempt=prepare(lesson,kind); before=protected(lesson)
    # Adapter imports none of the normal task/finalizer/provider stack.
    with patch("core.drafts.promote_project_draft",side_effect=AssertionError("No promotion")), patch("core.views._celery_app.send_task",side_effect=AssertionError("No broker")):
        receipt=execute(run,attempt)
    run.refresh_from_db(); run.job.refresh_from_db()
    assert run.job.status=="done" and run.completed_receipt==receipt and run.completed_receipt_hash
    assert not run.job.result_url and not run.job.srt_url and not run.job.celery_task_id
    assert receipt["publishing_allowed"] is False and protected(lesson)==before
    assert receipt["streams"]["video"]["frame_count"]>0
    assert abs(receipt["streams"]["video"]["duration_ms"]-2535)<=80
    assert receipt["streams"]["audio"]["duration_ms"]==2335
    assert receipt["streams"]["audio"]["packet_payload_hash"]==run.request_payload["audio_proof"]["audio"]["packet_payload_hash"]
    assert receipt["streams"]["caption_end_ms"]==2180
    assert receipt["streams"]["video"]["duration_ms"]>receipt["streams"]["audio"]["duration_ms"]
    assert runs.private_result_access_policy(lesson.project.pk,lesson.owner,run.pk).allowed
    store=PrivateSegmentMedia(lesson.owner.pk,lesson.project.pk)
    for role,asset in receipt["outputs"].items():
        assert store.verify(store.reference(asset,"segment" if role=="mp4" else role))
    raw=lesson.snapshot.payload
    if kind=="chart":
        layout=raw["scenes"][0]["design"]["plan"]["layout"]
        assert layout["unit"]=="cm" and [p["value"] for p in layout["points"]]==[12,18]
    assert raw["state"]["pages"][0]["source_slide_index"]==0 and raw["state"]["pages"][0]["split_index"]==1
    # Optional reproducible engineering artifact export remains under the already
    # configured private test root; only a JSON report is suitable for committing.
    report=os.environ.get("VISUAL_SEGMENT_EXAMPLE_REPORT")
    if report:
        from core.visual_segment_artifacts import _configured_root, _root_fd
        path=_configured_root(report); path.mkdir(mode=0o700,parents=True,exist_ok=True)
        with _root_fd(path,create=False): pass
        for role,asset in receipt["outputs"].items():
            (path/(kind+"."+role)).write_bytes(store.read(store.reference(asset,"segment" if role=="mp4" else role)))
        (path/(kind+".json")).write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n")
        for file in path.glob(kind+".*"): file.chmod(0o600)


def test_duplicate_delivery_and_callback_are_idempotent_without_composition(lesson,monkeypatch):
    run,attempt=prepare(lesson); first=execute(run,attempt)
    witness=witness_for(lesson,run)
    monkeypatch.setattr(media,"create_slide_video",lambda *a,**kw:pytest.fail("duplicate render"))
    assert execute(run,attempt)==first
    result,reused=runs.accept_verified_segment(lesson.project.pk,lesson.owner,run.pk,witness=witness)
    assert reused and result.completed_receipt==first
    assert VisualSegmentRun.objects.count()==1 and Job.objects.filter(job_type=PRIVATE_SEGMENT_JOB).count()==1


@pytest.mark.parametrize("event",["cancel","revoke","expiry","takeover","source_edit","producer_revoke"])
def test_live_controls_during_real_composition_fence_acceptance(lesson,event,monkeypatch):
    if event=="revoke":
        infer(lesson)
        runtime=installed_runtime(lesson.dependencies.verifier); lesson.dependencies.runtime=runtime
        lesson.dependencies.supported_runtime_hashes={content_hash("visual-segment-runtime.v1",runtime)}
        run,_=reserve(lesson); attempt=claim(lesson,run,lease_seconds=300)
    else: run,attempt=prepare(lesson)
    original=media.create_slide_video
    def compose(*args,**kwargs):
        if event in ("cancel","revoke"):
            from concurrent.futures import ThreadPoolExecutor
            from threading import Event
            started=Event(); popen=subprocess.Popen
            def launched(*a,**kw):
                process=popen(*a,**kw); started.set(); return process
            with patch.object(subprocess,"Popen",launched), ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(original,*args,**kwargs)
                assert started.wait(timeout=10) and not future.done()
                if event=="cancel": runs.cancel_run(lesson.project.pk,lesson.owner,run.pk,reason="Cancel during FFmpeg")
                else: revoke(lesson)
                output=future.result(timeout=30)
        else: output=original(*args,**kwargs)
        if event in ("expiry","takeover"):
            monkeypatch.setattr(runs,"_now",lambda:attempt.lease_expires_at+timedelta(seconds=1))
            if event=="takeover": claim(lesson,run)
        if event=="source_edit":
            page=lesson.pages[0]; page.narration_text="Later B"; page.save()
        if event=="producer_revoke": lesson.trust.authorized=False
        return output
    monkeypatch.setattr(media,"create_slide_video",compose)
    with pytest.raises(DesignError): execute(run,attempt)
    run.refresh_from_db(); assert run.completed_receipt is None and run.job.status!="done"


@pytest.mark.parametrize("when",["cancel","revoke"])
def test_later_controls_preserve_accepted_identity_but_deny_access(lesson,when):
    if when=="revoke":
        infer(lesson); runtime=installed_runtime(lesson.dependencies.verifier); lesson.dependencies.runtime=runtime
        lesson.dependencies.supported_runtime_hashes={content_hash("visual-segment-runtime.v1",runtime)}
        run,_=reserve(lesson); attempt=claim(lesson,run,lease_seconds=300)
    else: run,attempt=prepare(lesson)
    value=execute(run,attempt); run.refresh_from_db(); receipt_hash=run.completed_receipt_hash
    if when=="revoke": revoke(lesson)
    else: runs.cancel_run(lesson.project.pk,lesson.owner,run.pk,reason="withdraw delivery")
    assert not runs.private_result_access_policy(lesson.project.pk,lesson.owner,run.pk).allowed
    run.refresh_from_db(); assert run.completed_receipt==value and run.completed_receipt_hash==receipt_hash and run.job.status=="done"


def test_arbitrary_receipt_never_completes_a_run(lesson):
    run,attempt=prepare(lesson)
    with pytest.raises(DesignError): runs.accept_verified_segment(lesson.project.pk,lesson.owner,run.pk,witness={"accepted":True})
    run.refresh_from_db(); assert run.completed_receipt is None and run.job.status=="running"


@pytest.mark.parametrize("attack",["audio","svg","font","runtime","caption","source","split","missing_original"])
def test_invalid_input_never_produces_accepted_output(lesson,attack,monkeypatch):
    run,attempt=prepare(lesson,"preserve_original" if attack=="missing_original" else "flowchart")
    if attack=="runtime":
        actual=installed_runtime(lesson.dependencies.verifier); actual["build_id"]="unsupported"
        monkeypatch.setattr(media,"installed_runtime",lambda verifier:actual)
    elif attack in ("audio","svg","font","missing_original"):
        if attack=="audio":
            store=PrivateSegmentMedia(lesson.owner.pk,lesson.project.pk); parts,name=store._location(run.input_pins["media"]["narration"])
            path=store.root.joinpath(*parts,name)
        else:
            raw=lesson.snapshot.payload
            ref=raw["fonts"]["bold"] if attack=="font" else raw["scenes"][0]["original"]["assets"]["original"] if attack=="missing_original" else raw["scenes"][0]["design"]["asset"]
            from core.visual_render_assets import PrivateRenderAssets
            path=PrivateRenderAssets(lesson.project.pk).path(ref)
        path.write_bytes(b"corrupt")
    else:
        page=lesson.pages[0]
        if attack=="caption": page.chunk_timeline=[{"start":0,"end":5,"text":"Invalid caption"}]
        if attack=="source": page.source_slide_index=2
        if attack=="split": page.split_index=4
        page.save()
    with pytest.raises((DesignError,OSError)): execute(run,attempt)
    run.refresh_from_db(); assert run.completed_receipt is None and run.job.status!="done"


def test_permitted_transient_retry_then_terminal_failure_is_not_success(lesson,monkeypatch):
    run,attempt=prepare(lesson)
    original=media.create_slide_video
    monkeypatch.setattr(media,"create_slide_video",lambda *a,**kw:(_ for _ in ()).throw(subprocess.TimeoutExpired("ffmpeg",1)))
    with pytest.raises(DesignError,match="explicit bounded retry"): execute(run,attempt)
    run.refresh_from_db(); assert run.job.status=="pending" and run.completed_receipt is None
    next_attempt=claim(lesson,run,lease_seconds=300)
    monkeypatch.setattr(media,"create_slide_video",original)
    assert execute(run,next_attempt)["attempt_generation"]==next_attempt.attempt_generation
    with pytest.raises(DesignError): execute(run,attempt)


def unaccepted_witness(lesson,run,attempt,monkeypatch):
    captured=[]
    original=runs.accept_verified_segment
    def intercept(*args,**kwargs):
        captured.append(kwargs["witness"])
        return run,False
    with monkeypatch.context() as local:
        local.setattr(runs,"accept_verified_segment",intercept)
        assert execute(run,attempt) is None
    run.refresh_from_db(); assert run.completed_receipt is None and run.job.status=="running"
    return captured[0]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("other",["duplicate","cancel","revoke"])
def test_postgres_simultaneous_verified_completion(lesson,monkeypatch,other):
    _postgres()
    if other=="revoke":
        infer(lesson); runtime=installed_runtime(lesson.dependencies.verifier); lesson.dependencies.runtime=runtime
        lesson.dependencies.supported_runtime_hashes={content_hash("visual-segment-runtime.v1",runtime)}
        run,_=reserve(lesson); attempt=claim(lesson,run,lease_seconds=300)
    else: run,attempt=prepare(lesson)
    witness=unaccepted_witness(lesson,run,attempt,monkeypatch)
    def complete():
        return runs.accept_verified_segment(lesson.project.pk,lesson.owner,run.pk,witness=witness)[1]
    if other=="duplicate":
        results=_race([complete,complete])
        assert sorted(results)==[False,True]
    else:
        control=(lambda:runs.cancel_run(lesson.project.pk,lesson.owner,run.pk,reason="competing cancel")) if other=="cancel" else (lambda:revoke(lesson))
        results=_race([complete,lambda:(control(),"controlled")[1]])
        assert results[0] in (False,"run_cancelled","evidence_revoked") and results[1]=="controlled"
        assert not runs.private_result_access_policy(lesson.project.pk,lesson.owner,run.pk).allowed
    run.refresh_from_db()
    if run.completed_receipt is not None: assert run.job.status=="done" and run.completed_receipt_hash
    else: assert run.job.status=="failed"


@pytest.mark.parametrize("damage",["extra_stream","no_audio","reencoded_audio","wrong_geometry","blank_video","wrong_crf","truncated"])
def test_independent_validator_rejects_real_malformed_media(lesson,damage,monkeypatch,tmp_path):
    run,attempt=prepare(lesson)
    witness=unaccepted_witness(lesson,run,attempt,monkeypatch)
    _,_,deps,_,_,request=runs._prepare_run(lesson.project.pk,lesson.owner,run.pk)
    store=PrivateSegmentMedia(lesson.owner.pk,lesson.project.pk)
    pins=json.loads(witness.pins_json)
    original=tmp_path/"input.mp4"; original.write_bytes(store.read(pins["mp4"])); original.chmod(0o600)
    output=tmp_path/"malformed.mp4"
    if damage=="truncated": output.write_bytes(original.read_bytes()[:200])
    else:
        args=[deps.verifier.ffmpeg,"-nostdin","-v","error","-y","-i",str(original)]
        if damage=="extra_stream": args += ["-map","0:v:0","-map","0:a:0","-map","0:a:0","-c","copy"]
        if damage=="no_audio": args += ["-map","0:v:0","-c","copy"]
        if damage=="reencoded_audio": args += ["-c:v","copy","-c:a","aac","-b:a","128k"]
        if damage=="wrong_crf": args += ["-c:v","libx264","-crf","30","-preset","medium","-threads","1","-c:a","copy"]
        if damage in ("wrong_geometry","blank_video"):
            args += ["-vf","scale=640:360" if damage=="wrong_geometry" else "drawbox=color=white:t=fill", "-c:v","libx264","-threads","1","-c:a","copy"]
        subprocess.run([*args,str(output)],check=True,timeout=20)
    output.chmod(0o600)
    with output.open("rb") as file:
        import hashlib
        raw=output.read_bytes()
        asset={"sha256":"sha256:"+hashlib.sha256(raw).hexdigest(),"size":len(raw),"extension":"mp4"}
        pins["mp4"]=store.pin_from_fd(file.fileno(),asset,"segment")
    with pytest.raises(DesignError):
        validate_segment_outputs(request,frame_asset=json.loads(witness.frame_json),pins=pins,verifier=deps.verifier,
            generation=attempt.attempt_generation,claim_token=attempt.claim_token)
    run.refresh_from_db(); assert run.completed_receipt is None and run.job.status=="running"


def test_sidecars_reject_rewriting_and_cross_project_pins(lesson,monkeypatch):
    run,attempt=prepare(lesson); witness=unaccepted_witness(lesson,run,attempt,monkeypatch)
    _,_,deps,_,_,request=runs._prepare_run(lesson.project.pk,lesson.owner,run.pk)
    for role in ("srt","vtt"):
        original=caption_bytes(request,role)
        for corrupt in (original.replace(b"Plants",b"Wrong"), original.replace(b"02",b"09"), original+b"\nextra"):
            with pytest.raises(DesignError): validate_captions(corrupt,request,role)
    pins=json.loads(witness.pins_json); pins["srt"]["project_id"]="999"
    with pytest.raises(DesignError):
        validate_segment_outputs(request,frame_asset=json.loads(witness.frame_json),pins=pins,verifier=deps.verifier,
            generation=attempt.attempt_generation,claim_token=attempt.claim_token)


def test_oversized_output_and_private_permissions(lesson,tmp_path):
    run,attempt=prepare(lesson)
    store=PrivateSegmentMedia(lesson.owner.pk,lesson.project.pk)
    with media._attempt_directory(store,run,attempt.attempt_generation) as (directory,fd):
        assert directory.stat().st_mode&0o077==0
        media._write(fd,"segment.mp4",b"x")
        with (directory/"segment.mp4").open("r+b") as file: file.truncate(32*1024*1024+1)
        assert (directory/"segment.mp4").stat().st_mode&0o077==0
        with pytest.raises(DesignError,match="size"): media._pin_output(store,fd,"segment.mp4","segment")
    run.refresh_from_db(); assert run.completed_receipt is None


def test_claim_limit_and_terminal_failure_have_no_success_shortcut(lesson):
    run,attempt=prepare(lesson)
    for i in range(3):
        runs.release_claim(lesson.project.pk,lesson.owner,run.pk,execution_hash=run.execution_hash,
            generation=attempt.attempt_generation,claim_token=attempt.claim_token)
        if i<2: attempt=claim(lesson,run)
    with pytest.raises(DesignError,match="three"): claim(lesson,run)
    run.refresh_from_db(); assert run.attempt_count==3 and run.completed_receipt is None


def test_private_adapter_has_no_production_task_or_provider_dependencies():
    import ast
    source=Path(media.__file__).read_text()
    tree=ast.parse(source)
    imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert not any(x.startswith(("worker.tasks","worker.celery_app","tts","core.drafts")) for x in imports)
    assert "@app.task" not in source and "send_task(" not in source
    for name in ("synthesize", "concat_and_finalize", "merge_and_finalize_segments", "promote_project_draft", "requests"):
        assert not hasattr(media,name)


@pytest.mark.parametrize("text",["Öğrenme ışık gerektirir.\nPlants need light.","النبات يحتاج إلى الضوء.","  Authored spacing stays.  "])
def test_caption_unicode_linebreaks_and_spacing_remain_verbatim(text):
    # Pure serializer exercise: authored captions need not be normalized spoken
    # text. No new producer association or audio eligibility is invented here.
    from tests.test_visual_segment_contracts import load, audio, TRUST
    from core.visual_design.render_snapshot import FrozenRenderSnapshot, revision_hash
    from core.visual_design.segment_contracts import SegmentRequest
    raw=load("snapshot.json"); page=raw["state"]["pages"][0]
    page["subtitle_chunks"]=[text]; page["chunk_timeline"][0]["text"]=text
    raw["revision_hash"]=revision_hash(raw); frozen=FrozenRenderSnapshot(raw)
    value=load("request_0.json"); value["snapshot"]["snapshot_hash"]=frozen.content_hash
    value["snapshot"]["authored_revision_hash"]=raw["revision_hash"]
    sub=value["subtitles"]; sub["authored_chunks"]=[text]; sub["cues"][0]["text"]=text
    sub["frozen_absolute"]["chunk_timeline"]=deepcopy(page["chunk_timeline"])
    sub["cues_hash"]=content_hash("visual-segment-cues.v1",{"page":sub["page"],"cues":sub["cues"]})
    sub["policy"]="result_allocator.v1"; sub["provenance"]["allocator_version"]="allocate_chunk_timings.v1"
    request=SegmentRequest(value,snapshot=frozen,snapshot_id=TRUST["snapshot_id"],expected_snapshot_hash=frozen.content_hash,
        audio_proof=audio(),supported_runtime_hashes={TRUST["runtime_hash"]})
    for role in ("srt","vtt"):
        data=caption_bytes(request,role); validate_captions(data,request,role)
        assert text.encode("utf-8") in data


def test_completed_private_job_cannot_become_public_playback(lesson):
    from rest_framework.test import APIClient
    from core import views
    from core.notifications import notify_render_completed
    run,attempt=prepare(lesson); execute(run,attempt); run.refresh_from_db()
    client=APIClient(); client.force_authenticate(lesson.owner)
    assert client.get(f"/api/v1/jobs/{run.job_id}/").status_code==404
    assert client.get(f"/api/v1/projects/{lesson.project.pk}/jobs/{run.job_id}/").status_code==404
    token=views.generate_media_token(run.job_id,"video")
    assert client.get(f"/api/v1/stream/{token}/").status_code in (403,404)
    assert views._latest_completed_video_export_job(lesson.project).pk==lesson.baseline.pk
    assert notify_render_completed(lesson.project,run.job) is None


def test_unconfigured_production_provenance_blocks_worker_before_media(lesson,monkeypatch):
    run,attempt=prepare(lesson)
    monkeypatch.setattr(runs,"_producer_dependencies",lambda:None)
    monkeypatch.setattr(media,"create_slide_video",lambda *a,**kw:pytest.fail("No unauthenticated execution"))
    with pytest.raises(DesignError) as error: execute(run,attempt)
    assert error.value.code=="audio_provenance_unavailable"
    run.refresh_from_db(); assert run.completed_receipt is None


def test_final_transient_attempt_is_terminal_and_never_succeeds(lesson,monkeypatch):
    run,attempt=prepare(lesson)
    monkeypatch.setattr(media,"create_slide_video",lambda *a,**kw:(_ for _ in ()).throw(subprocess.TimeoutExpired("ffmpeg",1)))
    for i in range(3):
        with pytest.raises(DesignError): execute(run,attempt)
        if i<2: attempt=claim(lesson,run,lease_seconds=300)
    run.refresh_from_db()
    assert run.job.status=="failed" and run.terminal_failure_reason=="retry_exhausted" and run.completed_receipt is None
    with pytest.raises(DesignError): claim(lesson,run)
