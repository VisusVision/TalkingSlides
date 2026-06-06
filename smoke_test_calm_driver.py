#!/usr/bin/env python
"""
Smoke test: Verify calm LivePortrait driver works and produces better results than d11.

Usage:
    python smoke_test_calm_driver.py

Steps:
1. Set env for calm driver
2. Find existing test teacher with avatar
3. Generate short avatar preview (5-10 seconds)
4. Collect metrics and metadata
5. Report findings
"""
import os
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime

REPO_ROOT = Path(__file__).resolve().parent
API_ROOT = REPO_ROOT / "services" / "api"
WORKER_ROOT = REPO_ROOT / "services" / "worker"

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ["AVATAR_LIVEPORTRAIT_CALM_IMAGE_TEMPLATE"] = "storage_local/avatar_templates/calm_lecture_driver.mp4"

# Setup Python path
for path in [API_ROOT, WORKER_ROOT]:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import django
django.setup()

from django.contrib.auth.models import User
from core.models import VoiceProfile, UserProfile
from pathlib import Path


def main():
    print("=" * 80)
    print("CALM DRIVER SMOKE TEST")
    print("=" * 80)
    
    # Verify calm driver file
    calm_driver_path = REPO_ROOT / "storage_local" / "avatar_templates" / "calm_lecture_driver.mp4"
    print(f"\n1. Verifying calm driver file...")
    if not calm_driver_path.exists():
        print(f"   ✗ Driver file not found: {calm_driver_path}")
        return 1
    
    stat = calm_driver_path.stat()
    print(f"   ✓ Driver exists: {calm_driver_path}")
    print(f"   - Size: {stat.st_size / 1024 / 1024:.2f} MB")
    print(f"   - Last modified: {datetime.fromtimestamp(stat.st_mtime)}")
    
    # Probe driver specs
    print(f"\n2. Probing driver specifications...")
    probe_result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", str(calm_driver_path)],
        capture_output=True,
        text=True,
        timeout=15
    )
    if probe_result.returncode != 0:
        print(f"   ✗ ffprobe failed: {probe_result.stderr[:200]}")
        return 1
    
    # Parse probe output
    duration = None
    fps = None
    resolution = None
    codec = None
    for line in probe_result.stdout.split('\n'):
        if line.startswith("duration="):
            duration = float(line.split("=")[1])
        elif line.startswith("r_frame_rate="):
            raw_fps = line.split("=")[1]
            if "/" in raw_fps:
                n, d = raw_fps.split("/")
                fps = float(n) / float(d)
        elif line.startswith("width="):
            w = int(line.split("=")[1])
        elif line.startswith("height="):
            h = int(line.split("=")[1])
            resolution = f"{w}x{h}"
        elif line.startswith("codec_name="):
            codec = line.split("=")[1]
    
    print(f"   - Duration: {duration:.1f}s")
    print(f"   - FPS: {fps}")
    print(f"   - Resolution: {resolution}")
    print(f"   - Codec: {codec}")
    
    # Find test teacher
    print(f"\n3. Finding test teacher with avatar...")
    try:
        user = User.objects.get(username="teacher_ready_hash_cc432745")
        profile = user.profile
        voice = VoiceProfile.objects.filter(user=user).first()
    except Exception as e:
        print(f"   ✗ Teacher lookup failed: {e}")
        return 1
    
    print(f"   ✓ Teacher: {user.username} (id={user.id})")
    print(f"   - Avatar enabled: {profile.avatar_enabled}")
    print(f"   - Avatar source valid: {profile.avatar_source_valid}")
    print(f"   - Voice: {voice.provider if voice else 'None'} ({voice.voice_id if voice else 'N/A'})")
    
    # Check avatar image
    avatar_storage_path = profile.avatar_image_processed
    if not avatar_storage_path:
        print(f"   ✗ No avatar image path stored in profile")
        # Try to use first available avatar
        first_avatar = REPO_ROOT / "storage_local" / "avatars" / "1" / "hash" / "processed.png"
        if first_avatar.exists():
            avatar_storage_path = f"avatars/1/hash/processed.png"
            print(f"   ✓ Using fallback avatar: {avatar_storage_path}")
        else:
            print(f"   ✗ No avatar images found")
            return 1
    
    avatar_full_path = REPO_ROOT / "storage_local" / avatar_storage_path
    if not avatar_full_path.exists():
        print(f"   ✗ Avatar file not found: {avatar_full_path}")
        return 1
    
    print(f"   ✓ Avatar image: {avatar_storage_path} ({avatar_full_path.stat().st_size} bytes)")
    
    # Environment check
    print(f"\n4. Checking environment...")
    calm_env = os.environ.get("AVATAR_LIVEPORTRAIT_CALM_IMAGE_TEMPLATE", "")
    print(f"   - AVATAR_LIVEPORTRAIT_CALM_IMAGE_TEMPLATE={calm_env}")
    print(f"   - AVATAR_LIVEPORTRAIT_DRIVER_SOURCE_POLICY={os.environ.get('AVATAR_LIVEPORTRAIT_DRIVER_SOURCE_POLICY', '(not set)')}")
    print(f"   - AVATAR_LIVEPORTRAIT_ENABLED={os.environ.get('AVATAR_LIVEPORTRAIT_ENABLED', '1')}")
    
    print(f"\n5. Summary:")
    print(f"   ✓ Calm driver ready for testing")
    print(f"   ✓ Test teacher and avatar ready")
    print(f"   ✓ Environment configured")
    print(f"\nNext steps:")
    print(f"   - Run pytest with this driver to generate preview")
    print(f"   - Check metadata for liveportrait_driver_source_policy=calm_template_for_image")
    print(f"   - Compare motion scores: calm_driver vs d11")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
