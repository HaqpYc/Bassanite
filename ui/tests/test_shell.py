"""Tests for the UI's non-visual logic. Run from the repo root:

    python -m pytest ui/tests
"""
import sys
from pathlib import Path

import markdown2
import pytest

UI_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UI_DIR))

from explain import (EXPLAIN_DIR, function_source, load_panel,  # noqa: E402
                     load_story, panel_names)
from stages import STAGES, stage_status  # noqa: E402
from state import IQSource, guess_format  # noqa: E402

from components import MD_EXTRAS  # noqa: E402


# --- IQ source -------------------------------------------------------------------

def test_int8_duration(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(bytes(8000))                 # 4000 complex int8 samples
    src = IQSource(f, "int8", fs=4e6)
    assert src.n_samples == 4000
    assert src.duration_s == pytest.approx(1e-3)
    assert src.problems() == []


def test_complex64_size_mismatch_is_reported(tmp_path):
    f = tmp_path / "x.dat"
    f.write_bytes(bytes(12))                   # not a multiple of 8 bytes
    assert "multiple of 8" in IQSource(f, "complex64").problems()[0]


def test_missing_file_and_bad_rate(tmp_path):
    assert "not found" in IQSource(tmp_path / "nope.bin").problems()[0]
    f = tmp_path / "x.bin"
    f.write_bytes(bytes(4))
    assert IQSource(f, fs=0).problems() == ["Sample rate must be positive."]


@pytest.mark.parametrize("name,fmt", [("a.bin", "int8"), ("a.DAT", "complex64"),
                                      ("a.cf32", "complex64"), ("a.xyz", "int8")])
def test_guess_format(name, fmt):
    assert guess_format(name) == fmt


# --- stage status ------------------------------------------------------------------

def test_status_without_source():
    assert stage_status(STAGES[0], None).state == "locked"


def test_results_for_another_recording_are_flagged(tmp_path):
    from stages import STAGE_BY_KEY, _results_from_other_recording
    from state import DEFAULT_RECORDING
    stage = STAGE_BY_KEY["acquisition"]
    rec = tmp_path / "new.bin"
    rec.write_bytes(bytes(8))
    assert _results_from_other_recording(stage, IQSource(rec))
    assert not _results_from_other_recording(stage, IQSource(DEFAULT_RECORDING))
    assert not _results_from_other_recording(stage, None)
    assert not _results_from_other_recording(STAGE_BY_KEY["signal"], IQSource(rec))


def test_unbuilt_stages_are_locked():
    for stage in STAGES:
        if not stage.built:
            assert stage_status(stage, None).summary == "Not built yet"


# --- explanations ------------------------------------------------------------------

@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.key)
def test_every_stage_has_story_and_panel(stage):
    assert load_story(stage.key)
    assert panel_names(stage.key)


def _all_panels():
    return [(d.name, n) for d in sorted(EXPLAIN_DIR.iterdir()) if d.is_dir()
            for n in panel_names(d.name)]


@pytest.mark.parametrize("stage_key,name", _all_panels())
def test_panel_parses_and_math_renders(stage_key, name):
    panel = load_panel(stage_key, name)
    assert panel.intuition and panel.math
    # latex2mathml raises on LaTeX it can't convert.
    html = markdown2.markdown(panel.math, extras=MD_EXTRAS)
    assert "<math" in html


@pytest.mark.parametrize("stage_key,name", _all_panels())
def test_code_reference_points_at_a_real_function(stage_key, name):
    ref = load_panel(stage_key, name).code_ref
    if ref is None:
        pytest.skip("panel has no code yet")
    assert function_source(ref).startswith("def ")


def test_function_source_errors():
    with pytest.raises(FileNotFoundError):
        function_source("nope.py::f")
    with pytest.raises(LookupError):
        function_source("common/ca_code.py::does_not_exist")
