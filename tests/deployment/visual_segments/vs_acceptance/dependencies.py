"""Explicit test-only injection, mounted read-only into isolated test processes.

No production code imports this package. Expected receipt digests come from
committed anchors, runtime support from controller-observed built-image evidence
frozen BEFORE reservation, never from a caller or the running render attempt.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

CONTROL = Path('/engineering-control')

def install():
    from django.conf import settings
    if settings.SETTINGS_MODULE != 'vs_acceptance.settings' or settings.DATABASES['default']['NAME'] != 'visual_segments_engineering':
        raise RuntimeError('Engineering fixture isolation failed')
    from core import visual_segment_runs as controls, visual_segment_dispatch as dispatch
    from core.visual_design.schemas import content_hash
    from core.visual_segment_artifacts import NativeAudioValidator
    from tests.integration.test_visual_segment_artifacts import FixtureTrust, load
    from tests.integration.test_visual_segment_runs import _engineering_pin, _engineering_proof
    path = CONTROL/'runtime.json'
    if not path.exists():
        return  # Observation/migrations only; no reservation authority yet.
    expected = json.loads(path.read_text())
    verifier = NativeAudioValidator(ffmpeg='/usr/bin/ffmpeg', ffprobe='/usr/bin/ffprobe',
        expected_ffmpeg_hash=expected['ffmpeg_hash'], expected_ffprobe_hash=expected['ffprobe_hash'])
    trust = FixtureTrust()
    proofs = {load(f'audio_proof_{i}.json')['receipt_id']: load(f'audio_proof_{i}.json') for i in range(3)}
    anchors = load('control_trust_anchors.json')
    for i,(key,value) in enumerate(proofs.items()):
        value['synthesis']['snapshot_tts_settings_hash'] = anchors['snapshot_tts_settings_hash']
        trust.records[key] = replace(trust.records[key], expected_proof_hash=anchors['audio_proof_hashes'][i])
    deps = SimpleNamespace(trusted_source=trust, proof_lookup=lambda key: deepcopy(proofs[key]), verifier=verifier,
        runtime=expected['runtime'], supported_runtime_hashes={content_hash('visual-segment-runtime.v1',expected['runtime'])})
    controls._producer_dependencies = lambda: deps
    controls._proof_from_source = _engineering_proof
    controls._pin_audio = _engineering_pin
    # Explicit test dependency only. This does NOT attest production readiness.
    dispatch._deployment_ready = lambda: True
    _install_fault_barriers()


def _install_fault_barriers():
    """Controller-controlled process barriers; no new HTTP endpoint or task.

    Real Docker kill/restart is used by the host driver. These tiny wrappers only
    signal exact test boundaries; no receipt/claim/media validation is bypassed.
    """
    import time
    from core import visual_segment_runs as controls
    root = Path('/engineering-private/test-controls')
    root.mkdir(mode=0o700,exist_ok=True)
    def wrap(module,name,stage):
        original = getattr(module,name)
        def bounded(*args,**kwargs):
            result = original(*args,**kwargs)
            target = root/('pause-'+stage)
            if target.is_file():
                marker=root/('reached-'+stage); marker.write_text('reached'); marker.chmod(0o600)
                deadline=time.monotonic()+45
                while target.exists() and time.monotonic()<deadline: time.sleep(.1)
                if target.exists(): raise TimeoutError('engineering boundary deadline')
            return result
        setattr(module,name,bounded)
    from core import visual_segment_dispatch as dispatch
    send=dispatch._send
    def wait(stage):
        target=root/('pause-'+stage)
        if target.is_file():
            marker=root/('reached-'+stage);marker.write_text('reached');marker.chmod(0o600)
            deadline=time.monotonic()+45
            while target.exists() and time.monotonic()<deadline:time.sleep(.1)
            if target.exists():raise TimeoutError('engineering dispatch barrier deadline')
    def controlled_send(*args,**kwargs):
        wait('send');result=send(*args,**kwargs);wait('sent');return result
    dispatch._send=controlled_send
    wrap(controls,'claim_attempt','claim')
    wrap(controls,'accept_verified_segment','accepted')
    # Import after Django models and app registry are populated.
    from worker import visual_segment_tasks as media
    wrap(media,'create_slide_video','media')
    wrap(media,'_pin_output','pin')
    import os, signal, subprocess
    original_popen = subprocess.Popen
    def controlled_popen(*args,**kwargs):
        process=original_popen(*args,**kwargs)
        command=args[0] if args else kwargs.get('args')
        target=root/'pause-native'
        if (target.is_file() and isinstance(command,list) and command
                and command[0]=='/usr/bin/ffmpeg' and str(command[-1]).endswith('/segment.mp4')):
            os.kill(process.pid,signal.SIGSTOP)
            marker=root/'reached-native';marker.write_text('reached');marker.chmod(0o600)
            deadline=time.monotonic()+45
            while target.exists() and time.monotonic()<deadline:time.sleep(.1)
            if target.exists():
                process.kill();process.wait();raise TimeoutError('engineering native barrier deadline')
            os.kill(process.pid,signal.SIGCONT)
        return process
    subprocess.Popen=controlled_popen
