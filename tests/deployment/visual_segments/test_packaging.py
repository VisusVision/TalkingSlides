"""Fast packaging/guard tests. These are NOT built-container acceptance."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[3]


def test_explicit_numeric_identity_and_private_scope():
    for name in ('api','worker'):
        text=(ROOT/f'infra/dockerfiles/Dockerfile.{name}').read_text()
        assert 'groupadd --gid 1000 appuser' in text and 'useradd --uid 1000 --gid 1000' in text
    text=(ROOT/'infra/docker-compose.yml').read_text()
    assert text.count('      - visual_private:/var/lib/talkingslides-private')==2  # Only API + render in ordinary service discovery
    assert text.count('VISUAL_RENDER_PRIVATE_ROOT: /var/lib/talkingslides-private')==2


def test_harness_settings_refuse_normal_environment():
    path=Path(__file__).parent/'vs_acceptance/settings.py'
    result=subprocess.run([sys.executable,str(path)],env={'PATH':os.environ['PATH']},capture_output=True,text=True,timeout=5)
    assert result.returncode!=0 and 'Refusing engineering' in result.stderr


def test_production_readiness_and_producer_remain_closed():
    from core.visual_segment_dispatch import _deployment_ready
    from core.visual_segment_runs import _producer_dependencies
    assert _deployment_ready() is False and _producer_dependencies() is None


def test_volume_inspection_never_repairs_populated_or_public_paths(tmp_path,monkeypatch):
    from core.visual_private_volume import inspect_volume
    path=tmp_path/'private';path.mkdir(mode=0o755);(path/'keep').write_text('keep')
    with pytest.raises(ValueError):inspect_volume(path,provision=True)
    assert (path/'keep').read_text()=='keep' and path.stat().st_mode&0o777==0o755
    link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises((OSError,ValueError)):inspect_volume(link)
    monkeypatch.setenv('STORAGE_ROOT',str(tmp_path))
    with pytest.raises(ValueError,match='overlap'):inspect_volume(path)


def test_worker_python310_digest_compatibility(monkeypatch):
    import hashlib
    import io
    from core.visual_segment_artifacts import _hash_stream,NativeAudioValidator
    monkeypatch.delattr(hashlib,'file_digest',raising=False)
    data=b'bounded native identity'*10000
    assert _hash_stream(io.BytesIO(data)).hexdigest()==hashlib.sha256(data).hexdigest()
    expected='sha256:'+hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest()
    assert NativeAudioValidator._tool(str(Path(sys.executable).resolve()),expected)


def test_legacy_public_repair_never_follows_private_symlink(tmp_path):
    public=tmp_path/'public';public.mkdir()
    private=tmp_path/'private';private.mkdir(mode=0o700)
    (public/'701').symlink_to(private)
    normal=public/'702';normal.mkdir(mode=0o700)
    result=subprocess.run(['sh',str(ROOT/'infra/dockerfiles/local-storage-entrypoint.sh'),'true'],
        env={**os.environ,'STORAGE_ROOT':str(public),'APP_RUN_USER':'no-such-engineering-user',
            'VISUAL_RENDER_PRIVATE_ROOT':'','LOCAL_STORAGE_PERMISSIVE_CHMOD':'1'},capture_output=True,text=True,timeout=5)
    assert result.returncode==0
    assert private.stat().st_mode&0o777==0o700
    assert normal.stat().st_mode&0o777==0o777


def test_runtime_comparison_ignores_only_aslr_display_addresses():
    import runpy
    identity=runpy.run_path(str(Path(__file__).parent/'run.py'))['runtime_identity']
    base={'runtime':{'rasterizer_build_hash':'sha256:a'},'unsupported':None,'ffmpeg_hash':'a',
        'ffprobe_hash':'b','fonts':{'regular':'c','bold':'d'},'uid':1000,'gid':1000,
        'ffmpeg_version':'version','linked_libraries':'libc => /lib/libc (0x1234)'}
    changed={**base,'linked_libraries':'libc => /lib/libc (0x9999)'}
    assert identity(changed)==identity(base)
    assert identity({**changed,'runtime':{'rasterizer_build_hash':'sha256:b'}})!=identity(base)
    with pytest.raises(ValueError):identity({**base,'unknown_render_dependency':'x'})
