"""Private engineering API, broker/outbox faults and authenticated streaming.

Synthetic producer/deployment injection is exclusively test-local. Production
has neither an authenticated producer nor deployment readiness in this stage.
"""
from copy import deepcopy
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
from unittest.mock import patch
import uuid

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection, close_old_connections, transaction
from rest_framework.test import APIClient

from core.models import Job, VisualSegmentRun
from core import visual_segment_runs as controls
from core import visual_segment_dispatch as dispatch
from core import visual_segment_delivery as delivery
from core.visual_design.schemas import DesignError, content_hash
from core.visual_segment_artifacts import PrivateSegmentMedia
from core.visual_segment_validation import installed_runtime
from worker.visual_segment_celery import process_private_delivery
from tests.integration.test_visual_segment_runs import (lesson, native, load, reserve, claim, infer, revoke, protected, _race, _postgres)
from tests.integration.test_visual_segment_media import prepare, execute

pytestmark = pytest.mark.django_db


@pytest.fixture
def engineering(lesson,monkeypatch):
    cache.clear()
    runtime=installed_runtime(lesson.dependencies.verifier)
    lesson.dependencies.runtime=runtime
    lesson.dependencies.supported_runtime_hashes={content_hash("visual-segment-runtime.v1",runtime)}
    monkeypatch.setattr(dispatch,"_deployment_ready",lambda:True)
    lesson.sends=[]
    monkeypatch.setattr(dispatch,"_send",lambda message,task_id:lesson.sends.append((deepcopy(message),task_id)))
    lesson.client=APIClient(); lesson.client.force_authenticate(lesson.owner)
    return lesson


def url(lesson,run=None,tail=""):
    return f"/api/v1/projects/{lesson.project.pk}/visual-segments/"+(str(run.pk)+"/" if run else "")+tail


def payload(lesson):
    row=lesson.snapshot
    return {"snapshot_id":str(row.pk),"expected_snapshot_hash":row.snapshot_hash,"expected_revision_hash":row.revision_hash,
        "page_key":lesson.pages[0].page_key,"producer_receipt_id":load("audio_proof_0.json")["receipt_id"]}


def submit(lesson):
    return lesson.client.post(url(lesson),payload(lesson),format="json")


def completed(lesson,inferred=False):
    if inferred:
        infer(lesson); run,_=reserve(lesson); attempt=claim(lesson,run,lease_seconds=300)
    else: run,attempt=prepare(lesson)
    execute(run,attempt); run.refresh_from_db()
    return run


def media_bytes(lesson,run,role="mp4"):
    return PrivateSegmentMedia(lesson.owner.pk,lesson.project.pk).read(
        PrivateSegmentMedia(lesson.owner.pk,lesson.project.pk).reference(run.completed_receipt["outputs"][role],"segment" if role=="mp4" else role))


def consume(response):
    try: return b"".join(response.streaming_content)
    finally: response.close()


def test_owner_reservation_commit_handoff_and_exact_retry(engineering,django_capture_on_commit_callbacks):
    lesson=engineering; before=protected(lesson)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response=submit(lesson)
        assert not lesson.sends  # Commit precedes broker publication.
    assert response.status_code==201 and len(callbacks)==1 and len(lesson.sends)==1
    run=VisualSegmentRun.objects.get(pk=response.data["id"])
    message,task_id=lesson.sends[0]
    assert message=={"run_id":str(run.pk),"execution_hash":run.execution_hash,"attempt_generation":0}
    assert task_id==str(run.dispatch_task_id) and not run.job.celery_task_id
    assert protected(lesson)==before and run.pending_dispatch
    with django_capture_on_commit_callbacks(execute=True): again=submit(lesson)
    assert again.status_code==200 and again.data["reused"] and len(lesson.sends)==1
    status=lesson.client.get(url(lesson,run))
    assert status.status_code==200 and status.data["is_current"] and not status.data["result_available"]
    assert lesson.client.get(url(lesson)).data["runs"][0]["id"]==str(run.pk)
    raw=json.dumps(status.data)
    for forbidden in ("input_pins","request_payload","claim_token","/private","expected_proof","native"):
        assert forbidden not in raw
    for item in (response,status):
        assert item["Cache-Control"]=="private, no-store" and item["X-Content-Type-Options"]=="nosniff"


@pytest.mark.parametrize("extra",["svg","audio_path","candidate_id","request_payload","ffmpeg_args","provider","trust_hash","allow_synthetic","publishing_allowed","attempt_generation","receipt"])
def test_unknown_writable_fields_cannot_override_trust_or_receipts(engineering,extra):
    data=payload(engineering); data[extra]="forged"
    assert engineering.client.post(url(engineering),data,format="json").status_code==400
    assert not VisualSegmentRun.objects.exists() and not engineering.sends


@pytest.mark.parametrize("which",["producer","deployment"])
def test_production_gates_fail_closed_without_fake_jobs(engineering,monkeypatch,settings,which):
    if which=="producer": monkeypatch.setattr(controls,"_producer_dependencies",lambda:None)
    else:
        monkeypatch.setattr(dispatch,"_deployment_ready",lambda:False)
        settings.VISUAL_SEGMENT_DEPLOYMENT_READY=True  # Not a bypass.
    result=submit(engineering)
    assert result.status_code==503
    assert result.data["error"]["code"]==("audio_provenance_unavailable" if which=="producer" else "deployment_not_ready")
    assert not VisualSegmentRun.objects.exists() and not engineering.sends


@pytest.mark.parametrize("actor",["anonymous","foreign","staff"])
def test_no_foreign_project_or_run_disclosure(engineering,actor):
    run,_=reserve(engineering)
    client=APIClient()
    if actor!="anonymous": client.force_authenticate(User.objects.create_user(username=actor,is_staff=actor=="staff"))
    for path in (url(engineering),url(engineering,run),url(engineering,run,"media/mp4/")):
        assert client.get(path).status_code in (401,403,404)
    assert client.post(url(engineering),payload(engineering),format="json").status_code in (401,403,404)
    assert client.post(url(engineering,run,"cancel/"),{"reason":"foreign"},format="json").status_code in (401,403,404)


@pytest.mark.parametrize("field",["expected_snapshot_hash","expected_revision_hash","page_key","producer_receipt_id"])
def test_wrong_saved_identity_rejected_without_jobs(engineering,field):
    data=payload(engineering)
    data[field]="missing" if field=="page_key" else str(uuid.uuid4()) if field=="producer_receipt_id" else "sha256:"+"f"*64
    response=engineering.client.post(url(engineering),data,format="json")
    assert response.status_code==(503 if field=="producer_receipt_id" else 409) and not VisualSegmentRun.objects.exists()


@pytest.mark.parametrize("field",["narration_text","source_slide_index","split_index"])
def test_current_source_edits_block_old_reservation(engineering,field):
    page=engineering.pages[0]; setattr(page,field,"New narration" if field=="narration_text" else 3); page.save()
    assert submit(engineering).status_code==409 and not VisualSegmentRun.objects.exists()


def test_status_history_and_cancel_preserve_lesson_and_receipt(engineering):
    run=completed(engineering); before=protected(engineering); old=deepcopy(run.completed_receipt)
    detail=engineering.client.get(url(engineering,run))
    assert detail.data["result_available"] and detail.data["media_roles"]==["mp4","srt","vtt"]
    response=engineering.client.post(url(engineering,run,"cancel/"),{"reason":"Owner stops delivery"},format="json")
    assert response.status_code==200 and response.data["cancelled"] and not response.data["result_available"]
    run.refresh_from_db(); assert run.completed_receipt==old and run.job.status=="done" and protected(engineering)==before
    assert engineering.client.get(url(engineering,run,"media/mp4/")).status_code==403
    assert engineering.client.post(url(engineering,run,"cancel/"),{"reason":"repeat"},format="json").status_code==200


def test_historical_status_does_not_authorize_stale_media(engineering):
    run=completed(engineering)
    page=engineering.pages[0]; page.narration_text="Later source B"; page.save()
    response=engineering.client.get(url(engineering,run))
    assert response.status_code==200 and response.data["is_historical"] and not response.data["result_available"]
    assert engineering.client.get(url(engineering,run,"media/mp4/")).status_code==409


def test_broker_failure_and_send_before_ack_loss_replay_same_identity(engineering,monkeypatch):
    run,_=reserve(engineering)
    def unavailable(*args): raise ConnectionError("credentials must never leak")
    monkeypatch.setattr(dispatch,"_send",unavailable)
    assert dispatch.dispatch_pending(engineering.project.pk,engineering.owner,run.pk)=="broker_unavailable"
    run.refresh_from_db(); assert run.pending_dispatch and run.job.status=="pending"
    assert "credentials" not in json.dumps(engineering.client.get(url(engineering,run)).data)
    now=controls._now(); monkeypatch.setattr(controls,"_now",lambda:now+timedelta(seconds=31))
    monkeypatch.setattr(dispatch,"_send",lambda m,t:engineering.sends.append((deepcopy(m),t)))
    original=controls._write_run
    def ack_lost(row,**values):
        if set(values)=={"dispatch_error"}: raise RuntimeError("simulated crash after broker ack")
        return original(row,**values)
    with patch.object(controls,"_write_run",ack_lost):
        with pytest.raises(RuntimeError): dispatch.dispatch_pending(engineering.project.pk,engineering.owner,run.pk)
    monkeypatch.setattr(controls,"_now",lambda:now+timedelta(seconds=62))
    dispatch.reconcile_pending(run_id=run.pk)
    assert len(engineering.sends)==2 and engineering.sends[0]==engineering.sends[1]
    assert dispatch.reconcile_pending(run_id=run.pk)[0]["result"]=="cooldown"
    run.refresh_from_db(); assert run.dispatch_attempts==3 and run.completed_receipt is None


def test_commit_before_send_reconciliation_and_bounded_sends(engineering,monkeypatch):
    run,_=reserve(engineering); assert run.pending_dispatch and not engineering.sends
    for i in range(3):
        assert dispatch.reconcile_pending(run_id=run.pk)[0]["result"]=="sent"
        now=controls._now(); monkeypatch.setattr(controls,"_now",lambda now=now:now+timedelta(seconds=31))
    assert dispatch.reconcile_pending(run_id=run.pk)[0]["result"]=="dispatch_limit"
    assert len(engineering.sends)==3 and len({x[1] for x in engineering.sends})==1
    out=StringIO(); call_command("reconcile_visual_segments",run_id=str(run.pk),stdout=out)
    assert "dispatch_limit" in out.getvalue()


def test_real_media_through_worker_handoff_duplicate_and_stale_messages(engineering):
    run,_=reserve(engineering)
    dispatch.dispatch_pending(engineering.project.pk,engineering.owner,run.pk)
    message,task_id=engineering.sends[0]
    result=process_private_delivery(**message,delivery_id=task_id)
    assert result["status"]=="done" and not result["reused"]
    assert process_private_delivery(**message,delivery_id=task_id)["reused"]
    run.refresh_from_db(); assert run.attempt_count==1 and run.job.status=="done" and not run.job.celery_task_id
    with pytest.raises(DesignError): process_private_delivery(**{**message,"attempt_generation":9},delivery_id=task_id)
    with pytest.raises(DesignError): process_private_delivery(**message,delivery_id=str(uuid.uuid4()))


@pytest.mark.parametrize("control",["cancel","revoke","held"])
def test_controls_prevent_duplicate_or_unauthorized_worker_claim(engineering,control):
    if control=="revoke": infer(engineering)
    run,_=reserve(engineering); dispatch.dispatch_pending(engineering.project.pk,engineering.owner,run.pk)
    message,task_id=engineering.sends[0]
    if control=="cancel": controls.cancel_run(engineering.project.pk,engineering.owner,run.pk,reason="before worker")
    if control=="revoke": revoke(engineering)
    if control=="held": claim(engineering,run)
    with pytest.raises(DesignError): process_private_delivery(**message,delivery_id=task_id)
    run.refresh_from_db(); assert run.completed_receipt is None


def test_expired_worker_requires_explicit_operator_retry(engineering,monkeypatch):
    run,_=reserve(engineering); dispatch.dispatch_pending(engineering.project.pk,engineering.owner,run.pk)
    attempt=claim(engineering,run)
    monkeypatch.setattr(controls,"_now",lambda:attempt.lease_expires_at+timedelta(seconds=1))
    assert dispatch.reconcile_pending()==[]
    with pytest.raises(DesignError): dispatch.reconcile_pending(retry_claimed=True)
    result=dispatch.reconcile_pending(run_id=run.pk,retry_claimed=True)
    assert result[0]["result"]=="sent"
    message,task_id=engineering.sends[-1]
    assert message["attempt_generation"]>attempt.attempt_generation
    assert process_private_delivery(**message,delivery_id=task_id)["status"]=="done"
    run.refresh_from_db(); assert run.attempt_count==2


def test_owner_get_head_and_exact_mp4_ranges(engineering):
    run=completed(engineering); expected=media_bytes(engineering,run)
    response=engineering.client.head(url(engineering,run,"media/mp4/"),HTTP_RANGE="bytes=0-0")
    assert response.status_code==200 and response.content==b"" and int(response["Content-Length"])==len(expected)
    for header,start,end in [(None,0,len(expected)-1),("bytes=0-10",0,10),("bytes=10-",10,len(expected)-1),("bytes=-11",len(expected)-11,len(expected)-1),("bytes=0-999999",0,len(expected)-1)]:
        response=engineering.client.get(url(engineering,run,"media/mp4/"),**({"HTTP_RANGE":header} if header else {}))
        assert response.status_code==(206 if header else 200)
        assert response["Content-Type"]=="video/mp4" and response["Cache-Control"]=="private, no-store"
        assert response["X-Content-Type-Options"]=="nosniff" and response["Accept-Ranges"]=="bytes"
        assert int(response["Content-Length"])==end-start+1
        if header: assert response["Content-Range"]==f"bytes {start}-{end}/{len(expected)}"
        assert consume(response)==expected[start:end+1]


def test_sidecars_and_invalid_range_policy(engineering):
    run=completed(engineering)
    for role,ctype in [("srt","text/plain; charset=utf-8"),("vtt","text/vtt; charset=utf-8")]:
        response=engineering.client.get(url(engineering,run,f"media/{role}/"))
        assert response["Content-Type"]==ctype and consume(response)==media_bytes(engineering,run,role)
        assert engineering.client.get(url(engineering,run,f"media/{role}/"),HTTP_RANGE="bytes=0-1").status_code==416
    for header in ("bytes=1-0","bytes=-0","bytes=99999999-","bytes=0-1,3-4","items=1-2","bytes=--","bytes=0-"+"9"*130):
        response=engineering.client.get(url(engineering,run,"media/mp4/"),HTTP_RANGE=header)
        assert response.status_code==416 and response["Content-Range"].startswith("bytes */")
    assert engineering.client.get(url(engineering,run,"media/unknown/")).status_code==404


@pytest.mark.parametrize("event",["cancel","revoke","source","owner","producer","deployment"])
def test_live_stream_rechecks_and_closes_before_more_delivery(engineering,monkeypatch,event):
    run=completed(engineering,inferred=event=="revoke")
    monkeypatch.setattr(delivery,"CHUNK",1024); monkeypatch.setattr(delivery,"RECHECK_BYTES",1024)
    response=engineering.client.get(url(engineering,run,"media/mp4/")); assert response.status_code==200
    stream=iter(response.streaming_content); assert len(next(stream))==1024
    if event=="cancel": controls.cancel_run(engineering.project.pk,engineering.owner,run.pk,reason="during stream")
    if event=="revoke": revoke(engineering)
    if event=="source": page=engineering.pages[0]; page.narration_text="B"; page.save()
    if event=="owner": User.objects.filter(pk=engineering.owner.pk).update(is_active=False)
    if event=="producer": engineering.trust.authorized=False
    if event=="deployment": monkeypatch.setattr(dispatch,"_deployment_ready",lambda:False)
    with pytest.raises(StopIteration): next(stream)
    response.close(); run.refresh_from_db(); assert run.completed_receipt is not None


@pytest.mark.parametrize("attack",["corrupt","symlink","foreign"])
def test_unsafe_private_media_never_streams(engineering,tmp_path,attack):
    run=completed(engineering)
    store=PrivateSegmentMedia(engineering.owner.pk,engineering.project.pk)
    pin=store.reference(run.completed_receipt["outputs"]["mp4"],"segment")
    parts,name=store._location(pin); path=store.root.joinpath(*parts,name)
    if attack=="foreign":
        from core.models import Project
        other=Project.objects.create(user=engineering.owner,title="Other")
        response=engineering.client.get(f"/api/v1/projects/{other.pk}/visual-segments/{run.pk}/media/mp4/")
        assert response.status_code==404; return
    if attack=="corrupt": path.write_bytes(b"x"*path.stat().st_size)
    else:
        target=tmp_path/"foreign.mp4"; target.write_bytes(path.read_bytes()); target.chmod(0o600)
        path.unlink(); path.symlink_to(target)
    response=engineering.client.get(url(engineering,run,"media/mp4/"))
    assert response.status_code==409 and not response.streaming
    assert str(tmp_path) not in str(response.content)


@pytest.mark.django_db(transaction=True)
def test_postgres_simultaneous_reconciliation_and_claims(engineering):
    _postgres(); run,_=reserve(engineering)
    result=_race([lambda:dispatch.dispatch_pending(engineering.project.pk,engineering.owner,run.pk) for _ in range(2)])
    assert sorted(result)==["cooldown","sent"] and len(engineering.sends)==1
    message,task_id=engineering.sends[0]
    results=_race([lambda:process_private_delivery(**message,delivery_id=task_id)["status"] for _ in range(2)])
    assert "done" in results and all(value in ("done","claim_busy","stale_attempt","completed_private") for value in results)
    run.refresh_from_db(); assert run.attempt_count==1 and run.job.status=="done"


@pytest.mark.django_db(transaction=True)
def test_postgres_memory_broker_real_worker_and_real_media(engineering,monkeypatch):
    """Real Celery consumer over Kombu memory transport, NOT eager/apply/mock.

    PostgreSQL shares committed state across the actual worker thread. This does
    not claim external Redis, process-crash or built-container mount acceptance.
    """
    _postgres()
    from celery import Celery
    from celery.signals import task_postrun
    from celery.contrib.testing.worker import start_worker
    from worker.visual_segment_celery import generate_private_segment
    import time
    # Isolated test broker/app, inheriting the actual registered production task
    # implementation. Do not mutate a production app's cached Redis pools.
    app=Celery("private_segment_engineering",broker="memory://",backend="cache+memory://",set_as_current=False)
    actual=generate_private_segment._get_current_object() if hasattr(generate_private_segment,"_get_current_object") else generate_private_segment
    task_type=type("EngineeringPrivateTask",(type(actual),),{"__module__":__name__})
    app.register_task(task_type())
    app.conf.update(task_default_queue="render",task_routes={dispatch.TASK_NAME:{"queue":"render"}},task_always_eager=False)
    observed=[]
    def finished(sender=None,task_id=None,**kwargs):
        if getattr(sender,"name",None)==dispatch.TASK_NAME: observed.append(task_id)
    def publish(message,task_id):
        assert not connection.in_atomic_block
        app.send_task(dispatch.TASK_NAME,kwargs=message,task_id=task_id,queue="render",ignore_result=True,retry=False)
    monkeypatch.setattr(dispatch,"_send",publish)
    before=protected(engineering)
    task_postrun.connect(finished,weak=False)
    try:
        with start_worker(app,pool="solo",queues=["render"],perform_ping_check=False,loglevel="ERROR",shutdown_timeout=30):
            response=submit(engineering)
            assert response.status_code==201
            run=VisualSegmentRun.objects.get(pk=response.data["id"])
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                run.refresh_from_db()
                if run.completed_receipt is not None and observed: break
                time.sleep(.1)
            assert run.completed_receipt is not None and run.job.status=="done" and observed
            assert run.attempt_count==1 and protected(engineering)==before
            result=engineering.client.get(url(engineering,run,"media/mp4/"),HTTP_RANGE="bytes=0-15")
            assert result.status_code==206 and consume(result)==media_bytes(engineering,run)[:16]
            publish({"run_id":str(run.pk),"execution_hash":run.execution_hash,"attempt_generation":0},str(run.dispatch_task_id))
            deadline=time.monotonic()+15
            while len(observed)<2 and time.monotonic()<deadline: time.sleep(.1)
            assert len(observed)==2
            run.refresh_from_db(); assert run.attempt_count==1
    finally:
        task_postrun.disconnect(finished); app.close()


def test_closed_deployment_gate_cannot_be_enabled_through_settings(engineering,monkeypatch,settings):
    monkeypatch.setattr(dispatch,"_deployment_ready",lambda:False)
    settings.VISUAL_SEGMENT_DEPLOYMENT_READY=True
    settings.VISUAL_SEGMENT_ALLOW_SYNTHETIC=True
    assert submit(engineering).data["error"]["code"]=="deployment_not_ready"
    run,_=reserve(engineering)
    with pytest.raises(DesignError): process_private_delivery(str(run.pk),run.execution_hash,0,delivery_id=str(run.dispatch_task_id))
    assert run.completed_receipt is None


def test_owner_cannot_post_success_or_change_cancel_contract(engineering):
    run,_=reserve(engineering)
    assert engineering.client.post(url(engineering,run),{"receipt":{"success":True}},format="json").status_code==405
    assert engineering.client.post(url(engineering,run,"cancel/"),{"reason":"x","publishing_allowed":True},format="json").status_code==400
    assert engineering.client.post(url(engineering,run,"media/mp4/"),{},format="json").status_code==405
    run.refresh_from_db(); assert run.completed_receipt is None and run.job.status=="pending"


@pytest.mark.parametrize("change",["input_pin","elapsed_time"])
def test_active_stream_observes_input_files_and_time_bound(engineering,monkeypatch,change):
    run=completed(engineering)
    monkeypatch.setattr(delivery,"CHUNK",1024)
    response=engineering.client.get(url(engineering,run,"media/mp4/"))
    assert response.status_code==200
    stream=iter(response.streaming_content); assert len(next(stream))==1024
    if change=="input_pin":
        store=PrivateSegmentMedia(engineering.owner.pk,engineering.project.pk)
        pin=run.input_pins["media"]["narration"]
        parts,name=store._location(pin); path=store.root.joinpath(*parts,name)
        path.write_bytes(b"x"*path.stat().st_size)
    else:
        engineering.trust.authorized=False
        previous=delivery.time.monotonic()
        monkeypatch.setattr(delivery.time,"monotonic",lambda:previous+2)
    with pytest.raises(StopIteration): next(stream)
    response.close()
