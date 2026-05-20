"""pytest config — register custom markers and ensure backend on PYTHONPATH."""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: end-to-end tests that require LLM and MySQL")
