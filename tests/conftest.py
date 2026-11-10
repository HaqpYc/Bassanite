"""Tests of the bassanite package (the receiver itself, no UI). Run from the repo
root with the project's environment, where bassanite is installed (pip install -e .):

    python -m pytest tests
"""
import pytest


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
