"""Explicit empty-volume provisioning and read-only inspection; no app trust grant.

Standalone so a root init container needs neither Django nor database credentials.
Never recursively repairs ownership, follows symlinks or touches public storage.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import re

APP_UID = APP_GID = 1000


def inspect_volume(path, *, provision=False):
    root = Path(path)
    if not root.is_absolute() or root == Path('/') or '..' in root.parts:
        raise ValueError('explicit_private_root_required')
    resolved = root.resolve(strict=True)
    if root != resolved:
        raise ValueError('private_root_alias')
    for key in ('STORAGE_ROOT', 'MEDIA_ROOT', 'STATIC_ROOT'):
        public = os.environ.get(key)
        if public and (resolved.is_relative_to(Path(public).resolve()) or Path(public).resolve().is_relative_to(resolved)):
            raise ValueError('public_root_overlap')
    # Provision only a declared Linux mount, never a similarly named host folder.
    def unescape(value):return re.sub(r'\\([0-7]{3})',lambda match:chr(int(match.group(1),8)),value)
    mounts={unescape(line.split()[4]) for line in Path('/proc/self/mountinfo').read_text().splitlines()}
    if str(root) not in mounts:
        raise ValueError('explicit_private_mount_required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in root.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        value = os.fstat(fd)
        if provision and value.st_uid == 0 and not os.listdir(fd):
            if os.geteuid() != 0:
                raise ValueError('root_provisioner_required')
            # Empty declared volume only. Existing populated directories fail.
            os.fchown(fd, APP_UID, APP_GID)
            os.fchmod(fd, 0o700)
            os.fsync(fd)
            value = os.fstat(fd)
        if (value.st_uid, value.st_gid, stat.S_IMODE(value.st_mode)) != (APP_UID, APP_GID, 0o700):
            raise ValueError('private_volume_identity_or_mode')
        return {'uid': value.st_uid, 'gid': value.st_gid, 'mode': '0700',
                'device': value.st_dev, 'inode': value.st_ino,
                'runtime_uid': os.geteuid(), 'runtime_gid': os.getegid()}
    finally:
        os.close(fd)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('provision', 'inspect'))
    parser.add_argument('root')
    args = parser.parse_args()
    print(json.dumps(inspect_volume(args.root, provision=args.operation == 'provision')))
