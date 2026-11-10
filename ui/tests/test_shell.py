"""Tests for the UI's non-visual logic. Run from the repo root:

    python -m pytest ui/tests
"""
import sys
from pathlib import Path

import markdown2
import pytest

UI_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UI_DIR))

from explain import (EXPLAIN_DIR, fill, function_source, load_panel,  # noqa: E402
                     load_story, panel_names, placeholders)
from explain_values import explain_values  # noqa: E402
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


def test_each_recording_has_its_own_results(tmp_path, results_root):
    import stages
    from bassanite.provenance import write_meta
    stage = stages.STAGE_BY_KEY["acquisition"]
    rec = tmp_path / "new.bin"
    rec.write_bytes(bytes(8))
    other = tmp_path / "other.bin"
    other.write_bytes(bytes(8))
    assert stage_status(stage, IQSource(rec)).summary == "Not run yet"

    results = results_root / "new" / stage.output_files[0]
    results.parent.mkdir(parents=True)
    results.write_text("prn,doppler_hz,code_phase_samp,code_phase_chips,detected\n"
                       "5,250.0,10,2.5575,True\n")
    status = stage_status(stage, IQSource(rec))       # no provenance: the folder's own
    assert status.state == "done" and "1 satellites detected" in status.summary
    assert stage_status(stage, IQSource(other)).summary == "Not run yet"   # its own folder
    assert stages.output_paths(stage, IQSource(rec)) == [results]

    # Provenance names the exact recording: a same-named file elsewhere is flagged.
    write_meta(results, "acquisition", rec, "int8", 4e6, {})
    assert stage_status(stage, IQSource(rec)).state == "done"
    namesake = tmp_path / "elsewhere" / "new.bin"
    namesake.parent.mkdir()
    namesake.write_bytes(bytes(8))
    status = stage_status(stage, IQSource(namesake))
    assert status.state == "attention" and "same name" in status.summary

    [past] = stages.results_history()
    assert past.name == "new" and past.stages == (stage,)
    assert past.source.path == rec.resolve()


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
    # latex2mathml raises on LaTeX it can't convert (placeholders filled first).
    html = markdown2.markdown(fill(panel.math, explain_values()), extras=MD_EXTRAS)
    assert "<math" in html
    markdown2.markdown(fill(panel.intuition, explain_values()), extras=MD_EXTRAS)


@pytest.mark.parametrize("stage_key,name", _all_panels())
@pytest.mark.parametrize("fs", [4e6, 2e6, 10e6])
def test_every_placeholder_has_a_value(stage_key, name, fs):
    """U2: a figure written {{name}} must be computable for any recording."""
    panel = load_panel(stage_key, name)
    values = explain_values(fs, ms=5, dop_range_hz=5000, dop_step_hz=500, threshold=6)
    for text in (panel.intuition, panel.math):
        assert placeholders(text) <= set(values), placeholders(text) - set(values)
        assert "{{" not in fill(text, values)


def test_values_follow_the_recording():
    v4, v2 = explain_values(4e6), explain_values(2e6)
    assert (v4["spms"], v4["cells"], v4["dop_bins"]) == (4000, 324_000, 81)
    assert round(v4["chip_per_sample"], 3) == 0.256 and round(v4["m_per_sample"]) == 75
    assert round(v4["snr_ratio"]) == 200 and round(v4["gain_db"]) == 36
    assert v2["spms"] == 2000 and v2["cells"] == 162_000
    assert fill("{{cells:,}} cells, {{fs_msps:g}} MSPS", v2) == "162,000 cells, 2 MSPS"
    with pytest.raises(KeyError):
        fill("{{nope}}", v2)


@pytest.mark.parametrize("stage_key,name", _all_panels())
def test_code_reference_points_at_a_real_function(stage_key, name):
    refs = load_panel(stage_key, name).code_refs
    if not refs:
        pytest.skip("panel has no code yet")
    for ref in refs:
        assert function_source(ref).startswith("def ")


def test_function_source_errors():
    with pytest.raises(FileNotFoundError):
        function_source("nope.py::f")
    with pytest.raises(LookupError):
        function_source("common/ca_code.py::does_not_exist")
    with pytest.raises(LookupError):
        function_source("bassanite/tracking.py::NoSuchClass.update")


def test_function_source_reads_methods():
    src = function_source("bassanite/tracking.py::LoopFilter.update")
    assert src.startswith("def update(self, err):")
    assert function_source("bassanite/tracking.py::LoopFilter").startswith("class LoopFilter")
