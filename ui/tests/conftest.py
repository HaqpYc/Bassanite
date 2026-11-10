import sys
from pathlib import Path

import pytest

# Make the ui/ modules (state, stages, ...) and the repo-root packages
# (bassanite, common) importable from the tests, whichever test file runs first.
_UI = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_UI))
sys.path.insert(1, str(_UI.parent))

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture(autouse=True)
def results_root(tmp_path, monkeypatch):
    """Tests never touch the real results/: every stage writes under tmp_path."""
    import state  # noqa: F401  (puts the repo root on sys.path)
    import bassanite.results
    root = tmp_path / "results"
    monkeypatch.setattr(bassanite.results, "RESULTS_ROOT", root)
    return root


@pytest.fixture(autouse=True)
def no_hackrf(monkeypatch):
    """Tests never touch a real HackRF: the command-line tools are 'not found'."""
    import state  # noqa: F401  (puts the repo root on sys.path)
    import common.hackrf
    monkeypatch.setattr(common.hackrf, "find_tool", lambda name: None)
