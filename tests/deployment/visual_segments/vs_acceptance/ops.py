"""Fixed engineering operations; CLI inside isolated containers, no HTTP route."""
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace


def emit(value): print(json.dumps(value,default=str))


def runtime():
    import subprocess
    from core.visual_segment_artifacts import NativeAudioValidator
    from core.visual_segment_validation import installed_runtime
    digest=lambda path:'sha256:'+hashlib.sha256(Path(path).read_bytes()).hexdigest()
    ffmpeg,ffprobe='/usr/bin/ffmpeg','/usr/bin/ffprobe'
    verifier=NativeAudioValidator(ffmpeg=ffmpeg,ffprobe=ffprobe,expected_ffmpeg_hash=digest(ffmpeg),expected_ffprobe_hash=digest(ffprobe))
    fonts={}
    for name,pattern in [('regular','DejaVu Sans:style=Book'),('bold','DejaVu Sans:style=Bold')]:
        path=subprocess.check_output(['fc-match','-f','%{file}',pattern],text=True)
        fonts[name]=digest(path)
    from core.visual_design.schemas import DesignError
    try: observed=installed_runtime(verifier);unsupported=None
    except DesignError as exc: observed=None;unsupported=exc.code
    return {'runtime':observed,'unsupported':unsupported,'ffmpeg_hash':digest(ffmpeg),'ffprobe_hash':digest(ffprobe),
        'fonts':fonts,'uid':os.geteuid(),'gid':os.getegid(),
        'ffmpeg_version':subprocess.check_output([ffmpeg,'-version'],text=True).splitlines()[0],
        'linked_libraries':subprocess.check_output(['ldd',ffmpeg],text=True)}


def lesson():
    from django.contrib.auth.models import User
    from core.models import Project,TranscriptPage,Job,VisualRenderSnapshot
    return SimpleNamespace(owner=User.objects.get(pk=801),project=Project.objects.get(pk=701),
        pages=list(TranscriptPage.objects.filter(project_id=701).order_by('order')),
        baseline=Job.objects.get(project_id=701,job_type='video_export'),
        snapshot=VisualRenderSnapshot.objects.filter(project_id=701).latest('created_at'),inferred_review=None)


def seed(inferred=False):
    from django.contrib.auth.models import User
    from rest_framework.authtoken.models import Token
    from core.models import Project,TranscriptPage,VoiceProfile,Job
    from tests.integration.test_visual_segment_runs import load,select_all,freeze,infer,PROCESS,TEXT,FIXTURES
    if User.objects.exists(): raise RuntimeError('Seed requires empty isolated database')
    source=Path('/engineering-producer/801/701/synthetic-baseline-run'); source.mkdir(mode=0o700,parents=True,exist_ok=True)
    for directory in (source.parents[1],source.parent,source): directory.chmod(0o700)
    for name,original in [('speech.m4a','narration.m4a'),('original-speech.wav','narration.wav')]:
        path=source/name;path.write_bytes((FIXTURES/original).read_bytes());path.chmod(0o600)
    owner=User.objects.create_user(pk=801,username='engineering-owner')
    other=User.objects.create_user(pk=802,username='engineering-other')
    project=Project.objects.create(pk=701,user=owner,title='Isolated segment acceptance',status='ready',is_published=True)
    VoiceProfile.objects.create(user=owner,provider='flite',voice_id='slt',language='en')
    pages=[]
    for i in range(3):
        proof=load(f'audio_proof_{i}.json')
        pages.append(TranscriptPage.objects.create(project=project,**proof['page'],order=i,original_text=PROCESS,
            narration_text=TEXT,subtitle_chunks=[TEXT],start_seconds=i*3,end_seconds=i*3+2.335,duration_seconds=2.335,
            chunk_timeline=[{'start':i*3+.243,'end':i*3+2.180,'text':TEXT}]))
    baseline=Job.objects.create(project=project,job_type='video_export',status='done',result_url='existing.mp4',srt_url='existing.srt')
    value=SimpleNamespace(owner=owner,project=project,pages=pages,baseline=baseline,inferred_review=None)
    select_all(value);value.snapshot=freeze(value)
    if inferred: infer(value)
    row=value.snapshot
    return {'token':Token.objects.create(user=owner).key,'other_token':Token.objects.create(user=other).key,
        'payload':{'snapshot_id':str(row.pk),'expected_snapshot_hash':row.snapshot_hash,'expected_revision_hash':row.revision_hash,
        'page_key':pages[0].page_key,'producer_receipt_id':load('audio_proof_0.json')['receipt_id']}}


def main():
    import django
    django.setup()
    from core import visual_segment_runs as controls
    from core.models import VisualSegmentRun,VisualEvidenceReview
    action=sys.argv[1]
    if action=='runtime': return runtime()
    if action=='seed': return seed(len(sys.argv)>2 and sys.argv[2]=='inferred')
    if action=='public_token':
        from core.views import generate_media_token
        row=VisualSegmentRun.objects.get(project_id=701)
        return {'token':generate_media_token(row.job_id,'video'),'job_id':row.job_id}
    if action=='summary':
        row=VisualSegmentRun.objects.select_related('job').get(project_id=701)
        return {'id':str(row.pk),'execution_hash':row.execution_hash,'generation':row.attempt_generation,
            'attempts':row.attempt_count,'status':row.job.status,'receipt_hash':row.completed_receipt_hash,
            'receipt':row.completed_receipt,'pending':row.pending_dispatch,'lease':row.lease_expires_at,
            'task_id':str(row.dispatch_task_id),'cancelled':bool(row.cancelled_at),'failure':row.terminal_failure_reason,
            'public_fields':[row.job.result_url,row.job.srt_url,row.job.celery_task_id]}
    if action=='reserve':
        from tests.integration.test_visual_segment_runs import reserve
        row,_=reserve(lesson());return {'id':str(row.pk),'execution_hash':row.execution_hash}
    if action=='reconcile':
        from core.visual_segment_dispatch import reconcile_pending
        row=VisualSegmentRun.objects.get(project_id=701)
        return reconcile_pending(run_id=row.pk,retry_claimed=len(sys.argv)>2)
    if action=='replay':
        from core.visual_segment_dispatch import _send
        row=VisualSegmentRun.objects.get(project_id=701)
        _send({'run_id':str(row.pk),'execution_hash':row.execution_hash,'attempt_generation':int(sys.argv[2])},str(row.dispatch_task_id))
        return {'sent':True}
    if action=='cancel':
        row=VisualSegmentRun.objects.get(project_id=701)
        controls.cancel_run(row.project_id,row.owner,row.pk,reason='engineering cancellation');return {'cancelled':True}
    if action=='revoke':
        value=lesson();value.inferred_review=VisualEvidenceReview.objects.get(action='confirm')
        from tests.integration.test_visual_segment_runs import revoke
        revoke(value);return {'revoked':True}
    if action=='source_edit':
        value=lesson();page=value.pages[0];page.narration_text='A later saved source';page.save();return {'edited':True}
    if action=='owner_lost':
        value=lesson();value.owner.is_active=False;value.owner.save();return {'inactive':True}
    if action in ('pause','resume'):
        stage=sys.argv[2]
        if stage not in ('claim','native','media','pin','accepted','send','sent'):raise RuntimeError('invalid barrier')
        root=Path('/engineering-private/test-controls');root.mkdir(mode=0o700,exist_ok=True)
        if action=='pause':
            (root/('reached-'+stage)).unlink(missing_ok=True)
            p=root/('pause-'+stage);p.write_text('pause');p.chmod(0o600)
        else:(root/('pause-'+stage)).unlink(missing_ok=True)
        return {'barrier':stage,'action':action}
    if action=='reached':return (Path('/engineering-private/test-controls')/('reached-'+sys.argv[2])).exists()
    if action=='reset_controls':
        root=Path('/engineering-private/test-controls')
        for stage in ('claim','native','media','pin','accepted','send','sent'):
            for prefix in ('pause-','reached-'):(root/(prefix+stage)).unlink(missing_ok=True)
        return {'reset':True}
    if action=='cooldown':
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("UPDATE core_visualsegmentrun SET dispatch_next_at=NOW()-INTERVAL '1 second' WHERE project_id=701")
        return {'elapsed':True}
    if action=='expire':
        # Test-clock control via raw SQL in isolated DB, never a production API.
        # Does not change request/receipt identity; exercise actual expiry CAS.
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("UPDATE core_visualsegmentrun SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE project_id=701 AND completed_receipt IS NULL")
        return {'expired':True}
    raise RuntimeError('Unknown fixed engineering operation')


if __name__=='__main__': emit(main())
