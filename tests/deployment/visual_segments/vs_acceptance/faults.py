"""Actual disposable tmpfs failure probes, never a lesson or media injection API."""
import errno
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from django.conf import settings
settings.configure(VISUAL_RENDER_PRIVATE_ROOT='/fault',STORAGE_ROOT='/public',MEDIA_ROOT='/media',STATIC_ROOT='/static')
from core.visual_design.schemas import DesignError
from core.visual_segment_artifacts import PrivateSegmentMedia,MediaPinBudget

mode=sys.argv[1]
root=Path('/fault')
if mode in ('disk','inode'):
    full=False
    for i in range(1000):
        try:
            file=root/('filler'+str(i))
            file.write_bytes(b'x'*65536 if mode=='disk' else b'')
            file.chmod(0o600)
        except OSError as exc:
            assert exc.errno==errno.ENOSPC
            full=True;break
    assert full,'Bounded tmpfs exhaustion not reached'
if mode=='symlink':(root/'segments-v1').symlink_to('/tmp')
if mode=='quota':
    budget=MediaPinBudget()
    with budget.claim('a',32*1024*1024),budget.claim('b',32*1024*1024):
        try:
            with budget.claim('c',1):pass
        except DesignError:print(json.dumps({'fault':mode,'rejected':True}));raise SystemExit(0)
    raise AssertionError('Quota bypass')
store=PrivateSegmentMedia('801','701')
data=b'private fault probe\n';asset={'sha256':'sha256:'+hashlib.sha256(data).hexdigest(),'size':len(data),'extension':'vtt'}
with tempfile.TemporaryFile() as source:
    source.write(data);source.seek(0)
    try:store.pin_from_fd(source.fileno(),asset,'vtt')
    except (OSError,DesignError):print(json.dumps({'fault':mode,'rejected':True}));raise SystemExit(0)
raise AssertionError('Unsafe/exhausted pin unexpectedly succeeded')
