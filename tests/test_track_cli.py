"""03_tracking/track.py must carry on without matplotlib (an optional extra)."""
import importlib.util
import sys
from pathlib import Path

TRACK_PY = Path(__file__).resolve().parents[1] / "03_tracking" / "track.py"


def _load_track_module():
    spec = importlib.util.spec_from_file_location("track_cli_under_test", TRACK_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_matplotlib_skips_the_plots_with_one_note(monkeypatch, capsys):
    track = _load_track_module()
    monkeypatch.setitem(sys.modules, "matplotlib", None)      # makes the import fail
    assert track.plots_available() is False
    out = capsys.readouterr().out
    assert out.count("matplotlib is not installed") == 1
    assert '.[plots]' in out


def test_plots_are_available_when_matplotlib_imports(monkeypatch, capsys):
    track = _load_track_module()
    monkeypatch.setitem(sys.modules, "matplotlib", object())
    assert track.plots_available() is True
    assert capsys.readouterr().out == ""
