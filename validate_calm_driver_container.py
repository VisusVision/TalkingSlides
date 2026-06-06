#!/usr/bin/env python
import json
import sys
from pathlib import Path
repo_root = Path('/app')
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
from services.scripts.liveportrait_runner import _probe_driving_clip_variation
p = Path('/app/storage_local/avatar_templates/calm_lecture_driver.mp4')
metrics = _probe_driving_clip_variation(p)
print(json.dumps(metrics, indent=2, default=str))
