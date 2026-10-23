"""Reproduce five ENGINEERING ONLY narrated examples using actual private Runs.

Requires existing test dependencies, FFmpeg/librsvg/fonts; no model download or
speech generation. Uses the committed Flite fixture and isolated synthetic trust.
Never uses production lesson records. The output root must be private.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root",type=Path,required=True)
    args=parser.parse_args()
    root=args.private_root
    if not root.is_absolute() or root.is_symlink() or ".." in root.parts:
        parser.error("An absolute nonsymlink private engineering directory is required")
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if root.stat().st_uid!=os.geteuid() or root.stat().st_mode & 0o077:
        parser.error("Output root must be owned by the current UID with mode 0700")
    # Do not export engineering media under any configured public media tree.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.settings_test")
    sys.path[:0]=[str(ROOT/"services/api"),str(ROOT/"services")]
    import django
    django.setup()
    from core.visual_segment_artifacts import _configured_root, _root_fd
    with _root_fd(_configured_root(root),create=False): pass
    report=root/"examples"
    command=[sys.executable,"-m","pytest","-q",
        "tests/integration/test_visual_segment_media.py::test_five_real_private_segments_exact_aac_and_cues",
        "--basetemp",str(root/"test-runtime")]
    env={**os.environ,"VISUAL_MEDIA_REQUIRE":"1","VISUAL_SEGMENT_EXAMPLE_REPORT":str(report)}
    subprocess.run(command,cwd=ROOT,env=env,check=True)
    receipts={path.stem:json.loads(path.read_text()) for path in sorted(report.glob("*.json"))}
    manifest={"scope":"synthetic engineering only; private, non-publishing", "examples":receipts}
    path=report/"manifest.json"
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w") as file: json.dump(manifest,file,ensure_ascii=False,indent=2); file.write("\n")
    print(f"Five validated private MP4/SRT/VTT examples: {report}")

if __name__=="__main__": main()
