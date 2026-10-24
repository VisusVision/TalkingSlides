#!/usr/bin/env python3
"""Real Docker/PostgreSQL/Redis/Gunicorn/Celery acceptance. No mock broker.

Requires an isolated Docker host and already built images (see README). A missing
runtime fails the command; it is never a passing skip. Keeps no speech/video in
Git. Reports contain image/runtime/media identities, never auth tokens.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

if not __debug__:raise RuntimeError('Acceptance checks must not run with Python optimization')

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def container_json(output):
    # NVIDIA's inherited worker entrypoint emits a CUDA banner before commands.
    # Command return codes are checked independently; the final line is our
    # fixed internal operation's explicit single-line JSON, never a trust grant.
    return json.loads(output.strip().splitlines()[-1])


def runtime_identity(value):
    # ldd's display addresses are randomized diagnostics, not build identities.
    # The existing runtime contract hashes every linked library's actual bytes.
    names={'runtime','unsupported','ffmpeg_hash','ffprobe_hash','fonts','uid','gid','ffmpeg_version'}
    if set(value)!=names|{'linked_libraries'}:raise ValueError('Unknown runtime observation fields')
    return {key:value[key] for key in names}


class SecurityInvariantError(AssertionError):
    pass


class Deployment:
    def __init__(self,report):
        self.report=report;self.report.mkdir(parents=True,exist_ok=True)
        self.control=Path(tempfile.mkdtemp(prefix='vs-engineering-'))
        self.control.chmod(0o755)  # Contains public runtime evidence, never tokens.
        (self.control/'container-only.marker').write_text('ISOLATED ENGINEERING ONLY\n')
        self.env={**os.environ,'VISUAL_ACCEPTANCE_CONTROL':str(self.control)}
        self.project='vsengineering'+uuid.uuid4().hex[:10]
        self.command=['docker','compose','-p',self.project,'-f',str(HERE/'compose.yml')]
        self.results={};self.scenarios=[];self.tokens={};self.port=None

    def cmd(self,*args,check=True,timeout=90):
        value=subprocess.run([*self.command,*args],env=self.env,capture_output=True,text=True,timeout=timeout)
        if check and value.returncode:
            raise RuntimeError('Container operation failed: '+str(args[:3])+'\n'+value.stderr[-3000:])
        return value

    def ops(self,action,*args,service='api'):
        value=self.cmd('run','--rm','--no-deps',service,'python','-m','vs_acceptance.ops',action,*map(str,args),timeout=90)
        return container_json(value.stdout)

    def wait(self,callback,*,seconds=60):
        deadline=time.monotonic()+seconds;last=None
        while time.monotonic()<deadline:
            try:
                last=callback()
                if last:return last
            except (RuntimeError,urllib.error.URLError,ConnectionError):pass
            time.sleep(.3)
        raise AssertionError('Acceptance deadline exceeded; last='+str(last)[:300])

    def http(self,method,path,data=None,*,other=False,headers=None):
        head={'Authorization':'Token '+self.tokens['other_token' if other else 'token'],**(headers or {})}
        if data is not None:head['Content-Type']='application/json'
        request=urllib.request.Request('http://127.0.0.1:'+self.port+path,
            data=json.dumps(data).encode() if data is not None else None,method=method,headers=head)
        try:response=urllib.request.urlopen(request,timeout=60)
        except urllib.error.HTTPError as exc:response=exc
        with response:return response.status,dict(response.headers),response.read()

    @property
    def base(self):return '/api/v1/projects/701/visual-segments/'

    def api_ready(self):
        def published():
            result=self.cmd('port','api','8000',check=False)
            address=result.stdout.strip()
            if result.returncode or not address:return False
            host,port=address.rsplit(':',1)
            if host!='127.0.0.1':raise SecurityInvariantError('API port is not loopback-only')
            self.port=str(int(port))
            return True
        self.wait(published,seconds=20)
        self.wait(lambda:self.http('GET','/health/')[0]==200)

    def reset(self,inferred=False):
        for service in ('worker','api'):self.cmd('kill','-s','SIGKILL',service,check=False)
        self.cmd('rm','-sf','worker','api',check=False)
        self.cmd('exec','-T','vs-redis','redis-cli','FLUSHALL')  # Dedicated broker instance only.
        self.cmd('run','--rm','--no-deps','api','python','-m','django','flush','--noinput')
        self.ops('reset_controls')
        self.tokens=self.ops('seed',*(['inferred'] if inferred else []))
        self.cmd('up','-d','--no-deps','api')
        self.api_ready()

    def worker(self):
        self.cmd('up','-d','--no-deps','worker')

    def submit(self):
        response=self.http('POST',self.base,self.tokens['payload'])
        assert response[0] in (200,201),response
        return json.loads(response[2])

    def summary(self):return self.ops('summary')

    def done(self):
        result=self.wait(lambda:(value if (value:=self.summary())['receipt_hash'] else None),seconds=65)
        assert result['status']=='done' and result['public_fields']==['','','']
        assert result['receipt']['publishing_allowed'] is False
        assert result['receipt']['streams']['audio']['packet_payload_hash']=='sha256:05c9c0e0b1cfe5b55f5c72a697f62a74aa900da42ed94b1ea9d86195ddc44d91'
        return result

    def reached(self,stage):self.wait(lambda:self.ops('reached',stage),seconds=30)

    def prepare(self):
        self.cmd('up','-d','--wait','vs-postgres','vs-redis',timeout=120)
        self.cmd('run','--rm','init')
        observed=self.ops('runtime',service='worker')
        assert observed['runtime'] is not None,'Worker native runtime is unsupported'
        (self.control/'runtime.json').write_text(json.dumps(observed))
        actual=self.ops('runtime')
        self.results['worker_runtime']=observed;self.results['api_runtime']=actual
        print('NATIVE_IDENTITY '+json.dumps({'worker':runtime_identity(observed),'api':runtime_identity(actual)}),flush=True)
        assert runtime_identity(observed)==runtime_identity(actual),'Matched API variant and worker native runtime diverged'
        assert (actual['uid'],actual['gid'])==(1000,1000)
        self.results['runtime']=actual
        raw=subprocess.run(['docker','run','--rm','--user','1000:1000',
            '--mount','type=bind,src='+str(HERE)+',dst=/acceptance,readonly',
            '-e','PYTHONPATH=/acceptance:/app','vs-default-api:acceptance','python','-c',
            'from vs_acceptance.ops import runtime;import json;print(json.dumps(runtime()))'],
            capture_output=True,text=True,timeout=60,check=True)
        self.results['default_slim_api_runtime']=container_json(raw.stdout)
        self.results['default_slim_matches_worker']=runtime_identity(self.results['default_slim_api_runtime'])==runtime_identity(actual)
        self.cmd('run','--rm','--no-deps','api','python','-m','django','migrate','--noinput',timeout=120)
        self.results['images']={}
        for name in ('vs-worker:acceptance','vs-private-api:acceptance','vs-default-api:acceptance'):
            result=subprocess.run(['docker','image','inspect',name,'--format','{{.Id}}'],capture_output=True,text=True,check=True)
            self.results['images'][name]=result.stdout.strip()
        self.results['gates']={'D1':'pending','D2':'PASS_MATCHED_VARIANT','D3':'pending','D4':'pending',
            'D5':'pending','D6':'pending','D7':'pending','D8':'REPORT_ONLY','D9':'pending','D10':'HOSTED_REGRESSION_REQUIRED'}

    def storage(self):
        # Actual numeric process identity, cross-process writes and restart.
        probe="import os,pathlib,json;from core.visual_private_volume import inspect_volume; p=pathlib.Path('/engineering-private/PROBE');p.write_bytes(b'private shared bytes');p.chmod(0o600);print(json.dumps(inspect_volume('/engineering-private')))"
        out=self.cmd('run','--rm','--no-deps','api','python','-c',probe).stdout
        api_mount=container_json(out);assert api_mount['runtime_uid']==1000
        read="from pathlib import Path;assert Path('/engineering-private/PROBE').read_bytes()==b'private shared bytes'"
        self.cmd('run','--rm','--no-deps','worker','python','-c',read)
        worker_mount=container_json(self.cmd('run','--rm','--no-deps','worker','python','-c',probe.replace('private shared bytes','worker shared bytes')).stdout)
        assert all(api_mount[k]==worker_mount[k] for k in ('device','inode','uid','gid','mode'))
        self.results['shared_mount']={'api':api_mount,'worker':worker_mount}
        self.cmd('run','--rm','--no-deps','api','python','-c',read.replace('private shared bytes','worker shared bytes'))
        bad=self.cmd('run','--rm','--no-deps','--user','1001:1001','api','python','-c',read,check=False)
        assert bad.returncode!=0
        dropped=[]
        for image in ('vs-default-api:acceptance','vs-worker:acceptance'):
            probe="import os,json;assert (os.geteuid(),os.getegid())==(1000,1000);print(json.dumps({'uid':os.geteuid(),'gid':os.getegid()}))"
            result=subprocess.run(['docker','run','--rm','--user','0:0',
                '--mount','type=bind,src='+str(ROOT/'infra/dockerfiles/local-storage-entrypoint.sh')+',dst=/entrypoint.sh,readonly',
                '-e','VISUAL_RENDER_PRIVATE_ROOT=/var/lib/talkingslides-private',
                image,'sh','/entrypoint.sh','python','-c',probe],capture_output=True,text=True,timeout=30,check=True)
            dropped.append({'image':image,'process':container_json(result.stdout)})
        self.results['root_entrypoint_processes']=dropped
        self.results['gates']['D1']='PASS_BUILT_CROSS_PROCESS_1000_1000'

    def processes(self):
        rows=[]
        for service,match in [('api','gunicorn'),('worker','celery')]:
            identity=self.cmd('ps','-q',service).stdout.strip()
            result=subprocess.run(['docker','top',identity,'-eo','pid,uid,gid,args'],capture_output=True,text=True,timeout=10,check=True)
            found=[]
            for line in result.stdout.splitlines()[1:]:
                parts=line.split(None,3)
                if len(parts)==4 and match in parts[3]:
                    if parts[1:3]!=['1000','1000']:raise SecurityInvariantError('Wrong numeric serving/worker process identity')
                    found.append({'pid':parts[0],'uid':parts[1],'gid':parts[2],'command':parts[3]})
            assert found,'Actual '+match+' process not found'
            rows.append({'service':service,'processes':found})
        self.results['serving_processes']=rows

    def disconnect(self,path):
        def count():
            result=self.cmd('exec','-T','api','python','-m','vs_acceptance.ops','private_descriptors')
            return container_json(result.stdout)['private_descriptors']
        baseline=count()
        for _ in range(3):
            connection=socket.create_connection(('127.0.0.1',int(self.port)),timeout=10)
            connection.sendall(('GET '+path+' HTTP/1.1\r\nHost: localhost\r\nAuthorization: Token '+self.tokens['token']+'\r\nConnection: close\r\n\r\n').encode())
            assert connection.recv(128)
            connection.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
            connection.close()
        self.wait(lambda:count()<=baseline,seconds=10)
        self.results['disconnect']={'client_resets':3,'baseline_private_fds':baseline,'after_private_fds':count(),
            'scope':'direct Gunicorn, fixture-sized output; no production proxy/slow-stream claim'}

    def media(self):
        self.reset();self.worker();self.submit();run=self.done();receipt=run['receipt_hash']
        self.processes()
        self.results['example']={k:run[k] for k in ('id','execution_hash','receipt_hash','receipt')}
        path=self.base+run['id']+'/media/'
        status,headers,mp4=self.http('GET',path+'mp4/')
        assert status==200 and headers['Cache-Control']=='private, no-store' and headers['X-Content-Type-Options']=='nosniff'
        assert headers['Content-Type']=='video/mp4' and len(mp4)==run['receipt']['outputs']['mp4']['size']
        for header,start,end in [('bytes=0-10',0,10),('bytes=10-',10,len(mp4)-1),('bytes=-11',len(mp4)-11,len(mp4)-1)]:
            status,h,body=self.http('GET',path+'mp4/',headers={'Range':header})
            assert status==206 and body==mp4[start:end+1] and int(h['Content-Length'])==len(body)
        status,h,body=self.http('HEAD',path+'mp4/');assert status==200 and not body and int(h['Content-Length'])==len(mp4)
        for bad in ('bytes=999999999-','bytes=0-1,3-4','bytes=-0'):
            assert self.http('GET',path+'mp4/',headers={'Range':bad})[0]==416
        for role in ('srt','vtt'):
            status,h,body=self.http('GET',path+role+'/');assert status==200
            assert 'sha256:'+hashlib.sha256(body).hexdigest()==run['receipt']['outputs'][role]['sha256']
        if self.http('GET',path+'mp4/',other=True)[0]!=404:raise SecurityInvariantError('Cross-owner media access')
        for route in ('/media/','/static/','/api/v1/stream/guessed-private-hash/'):
            assert self.http('GET',route+run['receipt']['outputs']['mp4']['sha256'][7:]+'.mp4')[0] in (400,403,404)
        self.disconnect(path+'mp4/')
        signed=self.ops('public_token')
        for route in ('/api/v1/stream/'+signed['token']+'/', '/api/v1/jobs/'+str(signed['job_id'])+'/',
                      '/api/v1/projects/701/jobs/'+str(signed['job_id'])+'/'):
            if self.http('GET',route)[0] not in (403,404):raise SecurityInvariantError('Private result escaped through ordinary token/Job route')
        self.ops('replay',0);time.sleep(2);assert self.summary()['receipt_hash']==receipt and self.summary()['attempts']==1
        self.cmd('restart','api','worker');self.api_ready()
        assert self.http('GET',path+'mp4/')[0]==200
        self.ops('cancel');assert self.http('GET',path+'mp4/')[0]==403
        inventory=self.cmd('run','--rm','--no-deps','api','python','-m','django','visual_segment_inventory').stdout
        report=container_json(inventory);assert not report['deletion_enabled']
        assert any(item['reference']=='retained_reference' for item in report['files'])
        self.results['retention_inventory']={'files':len(report['files']),'bytes':report['bytes'],'deletion_enabled':False}
        self.results['gates'].update(D3='PASS_EXTERNAL_REDIS_SEPARATE_WORKER',D4='PASS_REAL_AAC_COPY_MP4_SRT_VTT',D7='PASS_TESTED_ROUTES')

    def crash_dispatch(self,stage):
        self.reset();self.ops('pause',stage)
        with ThreadPoolExecutor(max_workers=1) as pool:
            call=pool.submit(self.submit)
            self.reached(stage);before=self.summary()
            self.cmd('kill','-s','SIGKILL','api')
            try:call.result(timeout=5)
            except Exception:pass  # Actual HTTP connection is killed after DB commit.
        self.ops('resume',stage);self.cmd('up','-d','--no-deps','api')
        self.api_ready()
        self.ops('cooldown');self.ops('reconcile');self.worker()
        after=self.done();assert before['id']==after['id'] and before['execution_hash']==after['execution_hash']
        assert after['attempts']==1
        self.scenarios.append('API kill after '+stage+', reconcile same identity')

    def worker_crash(self,stage):
        self.reset();self.ops('pause',stage);self.worker();self.submit();self.reached(stage)
        before=self.summary();self.cmd('kill','-s','SIGKILL','worker');self.ops('resume',stage)
        if stage=='accepted':
            self.worker();self.ops('replay',0);time.sleep(2)
            after=self.done();assert after['receipt_hash']==before['receipt_hash'] and after['attempts']==1
        else:
            assert not before['receipt_hash'];self.ops('expire');self.ops('reconcile','retry');self.worker()
            after=self.done();assert after['execution_hash']==before['execution_hash'] and after['attempts']==2
            self.ops('replay',0);time.sleep(1);assert self.summary()['receipt_hash']==after['receipt_hash']
        self.scenarios.append('real worker kill at '+stage)

    def authorization(self,action):
        self.reset(inferred=action=='revoke');self.ops('pause','native');self.worker();self.submit();self.reached('native')
        self.ops(action);self.ops('resume','native')
        # Cancellation/revocation can mark the Job failed before FFmpeg returns.
        # Observe the executor's exit as well, so a later acceptance is not hidden
        # by killing the worker when the next scenario resets its database.
        self.wait(lambda:self.ops('execution_finished'),seconds=40)
        result=self.summary();assert result['status']=='failed'
        if result['receipt_hash']:raise SecurityInvariantError('Unauthorized attempt accepted')
        self.scenarios.append(action+' during real FFmpeg')

    def worker_before_claim(self):
        self.reset();self.worker();time.sleep(2)
        self.cmd('kill','-s','SIGKILL','worker')
        self.submit();before=self.summary();assert before['attempts']==0
        self.worker();after=self.done();assert after['attempts']==1
        self.scenarios.append('real worker restart before claim; pending Redis delivery retained')

    def broker_reconnect(self):
        self.reset();self.worker();time.sleep(2)
        self.cmd('stop','vs-redis');self.cmd('up','-d','--wait','vs-redis');time.sleep(3)
        self.submit();after=self.done();assert after['attempts']==1
        self.scenarios.append('existing worker reconnects after Redis restart')

    def broker_persisted(self):
        self.reset();self.submit();before=self.summary()
        assert before['pending'] and before['attempts']==0
        self.cmd('kill','-s','SIGKILL','vs-redis')
        self.cmd('up','-d','--wait','vs-redis');self.worker()
        after=self.done()
        assert before['execution_hash']==after['execution_hash'] and after['attempts']==1
        self.scenarios.append('Redis killed with pending delivery; AOF survives without republishing')

    def broker(self):
        self.reset();self.cmd('stop','vs-redis')
        self.submit();before=self.summary();assert before['pending'] and not before['receipt_hash']
        self.cmd('up','-d','--wait','vs-redis');self.ops('cooldown');self.ops('reconcile');self.worker();after=self.done()
        assert before['execution_hash']==after['execution_hash'] and after['attempts']==1
        self.scenarios.append('Redis unavailable/send failure/restart/reconcile')

    def post_authorization(self,action):
        self.reset(inferred=action=='revoke');self.worker();self.submit();run=self.done()
        before=run['receipt_hash'];self.ops(action)
        code=self.http('GET',self.base+run['id']+'/media/mp4/')[0]
        assert code in (401,403,404,409) and self.summary()['receipt_hash']==before
        self.scenarios.append(action+' after accepted receipt denies HTTP delivery')

    def producer_boundaries(self):
        self.reset();self.ops('producer_lost')
        assert self.http('POST',self.base,self.tokens['payload'])[0]==503
        self.scenarios.append('producer disabled before HTTP reservation; dispatch denied')
        self.reset();self.submit();self.ops('producer_lost');self.worker()
        self.wait(lambda:self.ops('delivery_finished'),seconds=40)
        run=self.summary()
        if run['attempts'] or run['receipt_hash']:raise SecurityInvariantError('Disabled producer acquired a claim')
        self.scenarios.append('producer disabled after send; real worker claim denied')

    def faults(self):
        results=[]
        for mode,options in [('disk','rw,size=1048576,nr_inodes=128,uid=1000,gid=1000,mode=0700'),
                ('inode','rw,size=1048576,nr_inodes=12,uid=1000,gid=1000,mode=0700'),
                ('root_owner','rw,size=1048576,uid=0,gid=0,mode=0700'),
                ('world','rw,size=1048576,uid=1000,gid=1000,mode=0755'),
                ('readonly','ro,size=1048576,uid=1000,gid=1000,mode=0700'),
                ('symlink','rw,size=1048576,uid=1000,gid=1000,mode=0700'),
                ('quota','rw,size=1048576,uid=1000,gid=1000,mode=0700'),
                ('missing_mount','rw,size=1048576,uid=1000,gid=1000,mode=0700'),
                ('runtime_drift','rw,size=1048576,uid=1000,gid=1000,mode=0700')]:
            raw=subprocess.run(['docker','run','--rm','--user','1000:1000',
                '--tmpfs','/fault:'+options,'--mount','type=bind,src='+str(HERE)+',dst=/acceptance,readonly',
                '-e','PYTHONPATH=/acceptance:/app/api:/app','vs-private-api:acceptance',
                'python','-m','vs_acceptance.faults',mode],capture_output=True,text=True,timeout=30,check=True)
            results.append(container_json(raw.stdout))
        self.results['storage_faults']=results
        probe="import os;os.environ['DJANGO_SETTINGS_MODULE']='config.settings_test';import django;django.setup();from core.visual_segment_runs import _producer_dependencies;from core.visual_segment_dispatch import _deployment_ready;assert _producer_dependencies() is None and _deployment_ready() is False;print('production gates remain closed')"
        subprocess.run(['docker','run','--rm','-e','VISUAL_SEGMENT_DEPLOYMENT_READY=true',
            'vs-default-api:acceptance','python','-c',probe],capture_output=True,text=True,timeout=30,check=True)

    def finish(self):
        self.results['scenarios']=self.scenarios
        for name,value in self.results.get('gates',{}).items():
            if value=='pending':self.results['gates'][name]='NOT_COMPLETED'
        self.results.setdefault('classification','PHASE3B_DEPLOYMENT_BLOCKED')
        self.results['production_enabled']=False
        self.results['remaining']=['No authenticated production producer',
            'Closed production readiness: no enrolled expiring cross-container attestation',
            'Production reverse proxy and full GPU/avatar image variant not exercised',
            'Retention is report-only; destructive cleanup remains disabled']
        (self.report/'acceptance.json').write_text(json.dumps(self.results,indent=2))
        print('DEPLOYMENT_GATE_REPORT '+json.dumps(self.results),flush=True)

    def run(self):
        try:
            print('DEPLOYMENT_STAGE prepare/storage/faults/media',flush=True)
            self.prepare();self.storage();self.faults();self.media()
            for stage in ('send','sent'):
                print('DEPLOYMENT_STAGE dispatch-crash '+stage,flush=True);self.crash_dispatch(stage)
            for stage in ('claim','native','pin','accepted'):
                print('DEPLOYMENT_STAGE worker-crash '+stage,flush=True);self.worker_crash(stage)
            self.worker_before_claim();self.broker();self.broker_reconnect();self.broker_persisted()
            for action in ('cancel','revoke','source_edit','producer_lost'):
                print('DEPLOYMENT_STAGE authorization '+action,flush=True);self.authorization(action)
            for action in ('revoke','source_edit','owner_lost','producer_lost'):self.post_authorization(action)
            self.producer_boundaries()
            self.results['gates']['D5']='PASS_ENUMERATED_REAL_PROCESS_FAILURES'
            self.results['gates']['D6']='PASS_TESTED_CONTROL_BOUNDARIES'
            self.results['gates']['D9']='PARTIAL_CLOSED_PRODUCTION_GATE; SEE_UNIT_FAULT_TESTS'
        except BaseException as exc:
            if isinstance(exc,SecurityInvariantError):self.results['classification']='PHASE3B_SECURITY_REWORK_REQUIRED'
            self.results['failure']=type(exc).__name__+': '+str(exc)[:3000]
            raise
        finally:
            try:
                logs=self.cmd('logs','--no-color',check=False,timeout=20)
                (self.report/'containers.log').write_text(logs.stdout+logs.stderr)
            except Exception as exc:self.results['log_capture_failure']=type(exc).__name__
            self.finish()
            try:self.cmd('down','--volumes','--remove-orphans',check=False,timeout=60)
            finally:shutil.rmtree(self.control)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir',type=Path,required=True)
    args=parser.parse_args()
    if shutil.which('docker') is None:
        args.report_dir.mkdir(parents=True,exist_ok=True)
        (args.report_dir/'acceptance.json').write_text(json.dumps({'classification':'PHASE3B_DEPLOYMENT_BLOCKED','reason':'Docker unavailable','gates':{f'D{i}':'NOT_EXECUTED' for i in range(1,11)}}))
        raise SystemExit('Docker unavailable: built-container acceptance NOT EXECUTED')
    Deployment(args.report_dir).run()
