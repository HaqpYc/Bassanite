import sys
from pathlib import Path

import pytest

# Make the ui/ modules (state, stages, ...) importable from the tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture(autouse=True)
def no_hackrf(monkeypatch):
    """Tests never touch a real HackRF: the command-line tools are 'not found'."""
    import state  # noqa: F401  (puts the repo root on sys.path)
    import common.hackrf
    monkeypatch.setattr(common.hackrf, "find_tool", lambda name: None)
