# conftest.py — pytest configuration for AI_ACADEMY
import sys
from pathlib import Path

# Make scripts importable in all test sessions without installing the package
SCRIPTS_DIR = Path(__file__).resolve().parent / "services" / "scripts"
if SCRIPTS_DIR.exists() and str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: marks integration tests")
