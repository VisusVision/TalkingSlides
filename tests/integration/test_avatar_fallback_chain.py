import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

sys.modules.pop("avatar", None)
canonical_adapters = importlib.import_module("avatar.canonical_adapters")  # noqa: E402
CANONICAL_ENGINE = canonical_adapters.CANONICAL_ENGINE
get_avatar_engine_configuration_report = canonical_adapters.get_avatar_engine_configuration_report
normalize_avatar_engine = canonical_adapters.normalize_avatar_engine


def test_avatar_engine_report_requires_liveportrait_and_musetalk(monkeypatch):
    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_CMD", raising=False)
    monkeypatch.delenv("AVATAR_MUSETALK_CMD", raising=False)

    report = get_avatar_engine_configuration_report()

    assert report["selected_engine"] == CANONICAL_ENGINE
    assert report["configured"] == {}
    assert report["missing"][CANONICAL_ENGINE] == ["AVATAR_LIVEPORTRAIT_CMD", "AVATAR_MUSETALK_CMD"]


def test_legacy_engine_names_normalize_to_canonical_engine():
    assert normalize_avatar_engine("musetalk") == CANONICAL_ENGINE
    assert normalize_avatar_engine("liveportrait+musetalk") == CANONICAL_ENGINE
    assert normalize_avatar_engine("") == CANONICAL_ENGINE
