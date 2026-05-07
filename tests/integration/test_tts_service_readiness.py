import importlib
import sys
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
HTTPException = fastapi.HTTPException


REPO_ROOT = Path(__file__).resolve().parents[2]
TTS_ROOT = REPO_ROOT / "services" / "tts_service"
if str(TTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TTS_ROOT))


def test_ready_endpoint_reflects_warmup_and_ready_states(monkeypatch):
    tts_main = importlib.import_module("main")

    # Prevent startup hook from triggering heavy preload behavior in tests.
    monkeypatch.setattr(tts_main, "XTTS_PRELOAD_ON_STARTUP", False, raising=False)

    # Not ready while warmup is in progress.
    monkeypatch.setattr(tts_main, "XTTS_ENABLED", True, raising=False)
    monkeypatch.setattr(tts_main, "_XTTS_MODEL", None, raising=False)
    monkeypatch.setattr(tts_main, "_XTTS_WARMUP_IN_PROGRESS", True, raising=False)
    monkeypatch.setattr(tts_main, "_XTTS_WARMUP_ERROR", None, raising=False)
    with pytest.raises(HTTPException) as exc:
        tts_main.ready()
    assert exc.value.status_code == 503

    # Ready once model is loaded.
    monkeypatch.setattr(tts_main, "_XTTS_WARMUP_IN_PROGRESS", False, raising=False)
    monkeypatch.setattr(tts_main, "_XTTS_MODEL", object(), raising=False)
    data = tts_main.ready()
    assert data["xtts_ready"] is True
    assert data["xtts_state"] == "ready"
