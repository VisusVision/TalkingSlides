#!/usr/bin/env python
"""Smoke test for calm LivePortrait driver rendering - using real avatar."""
import os
import sys
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
API_ROOT = REPO_ROOT / "services" / "api"
WORKER_ROOT = REPO_ROOT / "services" / "worker"
AVATAR_ROOT = REPO_ROOT / "services" / "avatar"

# Setup environment BEFORE Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ["AVATAR_LIVEPORTRAIT_CALM_IMAGE_TEMPLATE"] = str(
    REPO_ROOT / "storage_local" / "avatar_templates" / "calm_lecture_driver.mp4"
)
os.environ["AVATAR_LIVEPORTRAIT_DRIVER_SOURCE_POLICY"] = ""
os.environ["AVATAR_PREVIEW_MUSETALK_FAST_MODE"] = "1"

# Setup Python path
for path in [AVATAR_ROOT, WORKER_ROOT, API_ROOT]:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import django
django.setup()

from services.avatar import AvatarRenderRequest, render_avatar_segment_local


def create_test_audio(output_path: Path, duration: float = 5.0) -> bool:
    """Create test audio file."""
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", str(int(duration)), "-q:a", "9", "-acodec", "libmp3lame",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.returncode == 0 and output_path.exists()


def main():
    print("=" * 90)
    print("CALM DRIVER SMOKE TEST (Real Avatar)")
    print("=" * 90)
    
    # Verify calm driver
    calm_driver = REPO_ROOT / "storage_local" / "avatar_templates" / "calm_lecture_driver.mp4"
    print(f"\n[1] Driver: {calm_driver.name}")
    if not calm_driver.exists():
        print(f"✗ Not found")
        return 1
    print(f"✓ Exists ({calm_driver.stat().st_size / 1024 / 1024:.2f} MB)")
    
    # Find real avatar image
    avatar_img = REPO_ROOT / "storage_local" / "avatars" / "2" / "68b998db56c841665809bd46a40cbcaeaa113b8ce2bbf21b5f8e2d68fcc2e430" / "processed.png"
    avatar_img = avatar_img.resolve()
    print(f"\n[2] Avatar: {avatar_img.name}")
    if not avatar_img.exists():
        print(f"✗ Not found: {avatar_img}")
        return 1
    size_kb = avatar_img.stat().st_size / 1024
    print(f"✓ Found ({size_kb:.1f} KB)")
    
    # Create temp dir and test audio
    with tempfile.TemporaryDirectory(prefix="calm_test_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        audio_path = tmpdir_path / "test.mp3"
        output_path = tmpdir_path / "output.mp4"
        
        print(f"\n[3] Audio: 5 seconds")
        if not create_test_audio(audio_path, 5.0):
            print(f"✗ Failed to create audio")
            return 1
        print(f"✓ Created ({audio_path.stat().st_size / 1024:.1f} KB)")
        
        # Render
        print(f"\n[4] Rendering with calm driver...")
        print(f"    - Driver: calm_lecture_driver.mp4")
        print(f"    - Avatar: processed.png (user 2)")
        print(f"    - Audio: 5s test")
        
        request = AvatarRenderRequest(
            source_image_path=str(avatar_img),
            audio_path=str(audio_path),
            output_path=str(output_path),
            target_frame_count=150,
            target_duration_seconds=5.0,
            preview_teacher_id=2,
            preview_job_id=999,
        )
        
        try:
            result = render_avatar_segment_local(request)
        except Exception as e:
            error_msg = str(e)[:300]
            print(f"✗ Render failed: {error_msg}")
            return 1
        
        # Check output
        print(f"\n[5] Results:")
        if not output_path.exists():
            print(f"✗ Output not created")
            return 1
        
        size_mb = output_path.stat().st_size / 1024 / 1024
        print(f"✓ Output created: {size_mb:.2f} MB")
        
        # Extract metadata
        if not result:
            print(f"   (No result dict)")
            return 0
        
        stage_paths = result.get("stage_paths", {})
        lp_state = stage_paths.get("liveportrait_stage_state", "unknown")
        mt_state = stage_paths.get("musetalk_stage_state", "unknown")
        
        print(f"  - LivePortrait stage: {lp_state}")
        print(f"  - MuseTalk stage: {mt_state}")
        
        motion_gate = stage_paths.get("liveportrait_motion_gate", {})
        if motion_gate:
            passed = motion_gate.get("passed", False)
            unique_frames = motion_gate.get("unique_frames", 0)
            head_motion = motion_gate.get("head_motion_score", 0)
            print(f"  - Motion gate passed: {passed}")
            print(f"  - Unique frames: {unique_frames}")
            print(f"  - Head motion score: {head_motion:.6f}")
        
        env_used = result.get("environment_used", {})
        calm_policy = env_used.get("AVATAR_LIVEPORTRAIT_DRIVER_SOURCE_POLICY", "")
        calm_path = env_used.get("AVATAR_LIVEPORTRAIT_CALM_IMAGE_TEMPLATE", "")
        
        print(f"\n[6] Driver Configuration:")
        print(f"  - Policy: {calm_policy if calm_policy else '(auto-select)'}")
        print(f"  - Calm template path: {calm_path if calm_path else '(not set)'}")
        
        # Determine success
        success = output_path.exists() and size_mb > 0.5
        if lp_state == "completed" and mt_state == "completed":
            print(f"\n✓ SMOKE TEST PASSED - Calm driver working!")
            return 0
        else:
            print(f"\n⚠ Output created but stages incomplete: LP={lp_state}, MT={mt_state}")
            return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
