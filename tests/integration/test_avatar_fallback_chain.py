import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

sys.modules.pop("avatar", None)
canonical_adapters = importlib.import_module("avatar.canonical_adapters")  # noqa: E402
bootstrap_musetalk = importlib.import_module("worker.bootstrap_musetalk")  # noqa: E402
CANONICAL_ENGINE = canonical_adapters.CANONICAL_ENGINE
get_avatar_engine_configuration_report = canonical_adapters.get_avatar_engine_configuration_report
normalize_avatar_engine = canonical_adapters.normalize_avatar_engine


def test_avatar_engine_report_requires_liveportrait_and_musetalk(monkeypatch):
    monkeypatch.delenv("AVATAR_ALLOW_MUSETALK_ONLY_FAST_MODE", raising=False)
    monkeypatch.delenv("AVATAR_ENGINE", raising=False)
    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_CMD", raising=False)
    monkeypatch.delenv("AVATAR_MUSETALK_CMD", raising=False)

    report = get_avatar_engine_configuration_report()

    assert report["selected_engine"] == CANONICAL_ENGINE
    assert report["configured"] == {}
    assert report["missing"][CANONICAL_ENGINE] == ["AVATAR_LIVEPORTRAIT_CMD", "AVATAR_MUSETALK_CMD"]


def test_legacy_engine_names_normalize_to_canonical_engine(monkeypatch):
    monkeypatch.delenv("AVATAR_ALLOW_MUSETALK_ONLY_FAST_MODE", raising=False)

    assert normalize_avatar_engine("musetalk") == CANONICAL_ENGINE
    assert normalize_avatar_engine("liveportrait+musetalk") == CANONICAL_ENGINE
    assert normalize_avatar_engine("") == CANONICAL_ENGINE


def test_musetalk_only_fast_mode_requires_explicit_flag(monkeypatch):
    monkeypatch.setenv("AVATAR_ALLOW_MUSETALK_ONLY_FAST_MODE", "1")
    monkeypatch.setenv("AVATAR_ENGINE", "musetalk")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_CMD", raising=False)

    report = get_avatar_engine_configuration_report()

    assert normalize_avatar_engine("musetalk") == "musetalk"
    assert report["selected_engine"] == "musetalk"
    assert report["configured"] == {"musetalk": ["AVATAR_MUSETALK_CMD"]}
    assert report["missing"] == {}
    assert report["active_chain"] == ["musetalk"]
    assert report["musetalk_only_fast_mode_enabled"] is True
    assert bootstrap_musetalk._selected_avatar_engine() == "musetalk"
