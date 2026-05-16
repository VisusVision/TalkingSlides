#!/usr/bin/env python
"""Find existing processed avatar image and list what's available."""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

sys.path.insert(0, str(REPO_ROOT / "services" / "avatar"))
sys.path.insert(0, str(REPO_ROOT / "services" / "worker"))
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

import django
django.setup()

avatar_dir = REPO_ROOT / "storage_local" / "avatars"
print(f"Searching for avatar images in {avatar_dir}")
print()

count = 0
for img_path in avatar_dir.rglob("*.png"):
    if img_path.is_file():
        size_kb = img_path.stat().st_size / 1024
        rel_path = img_path.relative_to(REPO_ROOT)
        print(f"{count:3d}. {rel_path} ({size_kb:7.1f} KB)")
        count += 1
        if count >= 20:
            break

print(f"\nTotal found so far: {count}")
print("\nTrying to use first one for smoke test...")
