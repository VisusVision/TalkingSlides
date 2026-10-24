"""Bounded, report-only private inventory. No deletion authorization.

All snapshots and all Runs, including cancelled/revoked history, retain refs.
A scan is not a pin/GC transaction; an unreferenced-at-scan object may be being
pinned by another process. Nothing here can delete or issue cleanup permission.
"""
from collections import deque
import json
import os
import re
import stat

from django.conf import settings
from core.models import VisualRenderSnapshot, VisualSegmentRun
from core.visual_segment_artifacts import _configured_root, _root_fd
from core.visual_design.schemas import require


def private_inventory(*, limit=1000):
    require(type(limit) is int and 1 <= limit <= 2000, 'inventory_limit', 'Bounded inventory required')
    referenced=set(); total=0
    def collect(value):
        nonlocal total
        encoded=json.dumps(value)
        total+=len(encoded.encode())
        require(total<=8*1024*1024,'inventory_limit','Reference JSON budget exceeded')
        queue=deque([value])
        while queue:
            item=queue.popleft()
            if isinstance(item,dict):
                digest=item.get('sha256')
                if isinstance(digest,str) and re.fullmatch(r'sha256:[0-9a-f]{64}',digest):referenced.add(digest[7:])
                queue.extend(item.values())
            elif isinstance(item,list):queue.extend(item)
    for model,names in ((VisualRenderSnapshot,('payload',)),(VisualSegmentRun,('request_payload','input_pins','completed_receipt'))):
        rows=list(model.objects.order_by('pk').values(*names)[:limit+1])
        require(len(rows)<=limit,'inventory_limit','Reference row budget exceeded; no partial cleanup report')
        for row in rows:collect(row)
    root=_configured_root(getattr(settings,'VISUAL_RENDER_PRIVATE_ROOT',os.environ.get('VISUAL_RENDER_PRIVATE_ROOT')))
    files=[]; entries=0
    with _root_fd(root,create=False) as base:
        def scan(fd,parts):
            nonlocal entries
            require(len(parts)<=10,'inventory_limit','Directory depth budget exceeded')
            # scandir is lazy: never build an unbounded list of directory names.
            with os.scandir(fd) as children:
                for child in children:
                    entries+=1
                    require(entries<=limit,'inventory_limit','Filesystem entry budget exceeded')
                    value=child.stat(follow_symlinks=False)
                    require(not child.is_symlink() and value.st_uid==os.geteuid() and value.st_mode&0o077==0,
                        'private_storage','Unsafe private inventory entry')
                    if stat.S_ISDIR(value.st_mode):
                        nested=os.open(child.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                        try:scan(nested,(*parts,child.name))
                        finally:os.close(nested)
                    else:
                        require(stat.S_ISREG(value.st_mode),'private_storage','Unsupported private entry')
                        digest=child.name.split('.')[0]
                        files.append({'relative_name':'/'.join((*parts,child.name)),'size':value.st_size,
                            'reference':'retained_reference' if digest in referenced else 'unreferenced_at_scan',
                            'deletion_allowed':False})
        scan(base,())
    return {'schema_version':'visual-private-inventory.v1','report_only':True,'deletion_enabled':False,
        'files':files,'bytes':sum(f['size'] for f in files),'reference_hashes':len(referenced),
        'policy':'retain_all_snapshots_runs_and_receipts; no automatic cleanup'}
