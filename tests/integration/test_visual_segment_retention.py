"""Conservative report-only retention: no filesystem mutation or GC authority."""
from pathlib import Path
import pytest
from core import visual_segment_runs as controls
from core.visual_segment_retention import private_inventory
from core.visual_design.schemas import DesignError
from tests.integration.test_visual_segment_runs import lesson,native,reserve,protected
from tests.integration.test_visual_segment_media import prepare,execute

pytestmark=pytest.mark.django_db


def tree(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_inventory_retains_snapshots_runs_accepted_cancelled_history(lesson):
    run,attempt=prepare(lesson);execute(run,attempt)
    controls.cancel_run(lesson.project.pk,lesson.owner,run.pk,reason='Historical private result')
    before=protected(lesson)
    from django.conf import settings
    root=Path(settings.VISUAL_RENDER_PRIVATE_ROOT)
    orphan=root/'unreferenced-at-scan';orphan.write_bytes(b'concurrent pin may need this');orphan.chmod(0o600)
    files=tree(root);result=private_inventory()
    assert result['report_only'] and not result['deletion_enabled']
    assert all(item['deletion_allowed'] is False for item in result['files'])
    run.refresh_from_db();sha=run.completed_receipt['outputs']['mp4']['sha256'][7:]
    assert any(item['reference']=='retained_reference' and sha in item['relative_name'] for item in result['files'])
    assert any(item['reference']=='unreferenced_at_scan' for item in result['files'])
    assert tree(root)==files and protected(lesson)==before


def test_inventory_is_bounded_and_rejects_symlinks_without_deletion(lesson,tmp_path,settings):
    reserve(lesson);root=Path(settings.VISUAL_RENDER_PRIVATE_ROOT);files=tree(root)
    with pytest.raises(DesignError,match='budget'):private_inventory(limit=1)
    assert tree(root)==files
    target=tmp_path/'outside';target.write_bytes(b'outside')
    link=root/'escape';link.symlink_to(target)
    with pytest.raises(DesignError):private_inventory()
    assert link.is_symlink() and target.read_bytes()==b'outside'
    link.unlink();root.chmod(0o755)
    with pytest.raises(DesignError):private_inventory()
    root.chmod(0o700)
    assert tree(root)==files
