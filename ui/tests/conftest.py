import sys
from pathlib import Path

import pytest

# The UI's own modules (state, stages, tabs, ...) are top-level modules of ui/, as
# when ui/app.py runs; the bassanite package is installed (pip install -e .).
_UI = Path(__file__).resolve().parent.parent
if str(_UI) not in sys.path:
    sys.path.insert(0, str(_UI))

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture(autouse=True)
def results_root(tmp_path, monkeypatch):
    """Tests never touch the real results/: every stage writes under tmp_path."""
    import bassanite.results
    root = tmp_path / "results"
    monkeypatch.setattr(bassanite.results, "RESULTS_ROOT", root)
    return root


@pytest.fixture(autouse=True)
def no_hackrf(monkeypatch):
    """Tests never touch a real HackRF: the command-line tools are 'not found'."""
    import bassanite.hackrf
    monkeypatch.setattr(bassanite.hackrf, "find_tool", lambda name: None)
