"""Tests for the bassanite package (stages 02-03) on a synthetic GPS signal:
one satellite with a known PRN, code phase and Doppler, 50 bps data, in noise."""
import json

import numpy as np
import pytest

from bassanite import acquisition, provenance, tracking
from synthetic import CODE_PHASE_SAMP, DOPPLER, FS, PRN, synthetic_gps


@pytest.fixture(scope="module")
def signal():
    return synthetic_gps(0.6)


def test_scan_finds_the_satellite_and_nothing_else(signal):
    calls = []
    results = acquisition.scan(signal, FS, -5000, 5000, 250, 10, 5.0,
                               prns=[PRN, 1, 2, 3], progress=lambda d, t: calls.append(d))
    best = results[0]
    assert best["prn"] == PRN and best["detected"]
    assert best["code_phase_samp"] == CODE_PHASE_SAMP
    assert best["doppler_hz"] == DOPPLER
    assert not any(r["detected"] for r in results[1:])
    assert calls == [1, 2, 3, 4]


def test_surface_matches_the_reported_peak(signal):
    cp, dop, ratio, dopplers, surface = acquisition.acquire_prn(
        signal, FS, PRN, -2000, 2000, 250, 10, return_surface=True)
    assert surface.shape == (len(dopplers), int(FS * 1e-3))
    i, j = np.unravel_index(surface.argmax(), surface.shape)
    assert (dopplers[i], j) == (dop, cp)
    assert ratio == pytest.approx(surface[i].max() / surface[i].mean())
    assert acquisition.acquire_prn(signal, FS, PRN, -2000, 2000, 250, 10) == (cp, dop, ratio)


def test_tracking_locks_and_reports_detail_and_progress(signal, monkeypatch):
    monkeypatch.setattr(tracking, "PROGRESS_EVERY_MS", 100)
    calls = []
    plain = tracking.track_prn(signal, FS, PRN, CODE_PHASE_SAMP, DOPPLER + 100)
    tr = tracking.track_prn(signal, FS, PRN, CODE_PHASE_SAMP, DOPPLER + 100,
                            detail=True, progress=lambda d, t: calls.append(d))
    for k in plain:                                    # detail changes no result
        assert np.array_equal(plain[k], tr[k])
    for k in ("i_early", "q_early", "i_late", "q_late", "carr_err", "code_err"):
        assert len(tr[k]) == len(tr["i_prompt"])
    r = tracking.summarize(tr)
    assert r["locked"] and r["mean_cn0"] == pytest.approx(48, abs=3)
    assert abs(tr["doppler_hz"][-100:].mean() - DOPPLER) < 10   # pulled in from +100 Hz
    # On lock, early and late are balanced (prompt centred on the code)
    e = np.hypot(tr["i_early"][-200:], tr["q_early"][-200:]).mean()
    l_ = np.hypot(tr["i_late"][-200:], tr["q_late"][-200:]).mean()
    assert e == pytest.approx(l_, rel=0.1)
    assert calls[:3] == [100, 200, 300]


def test_summarize_needs_one_bit():
    short = {"i_prompt": np.ones(10, np.float32), "q_prompt": np.zeros(10, np.float32)}
    assert tracking.summarize(short) is None


def test_scan_results_round_trip_and_seeds(signal, tmp_path):
    results = acquisition.scan(signal, FS, -2000, 2000, 250, 10, 5.0, prns=[PRN, 1])
    path = tmp_path / acquisition.SCAN_CSV
    acquisition.write_results(results, path)
    assert acquisition.read_results(path) == results
    seeds = tracking.read_seeds(path, 2 * FS)            # converted to this sample rate
    assert [s["prn"] for s in seeds] == [PRN]
    assert seeds[0]["code_phase_samp"] == pytest.approx(2 * CODE_PHASE_SAMP)
    assert len(tracking.read_seeds(path, FS, only_detected=False)) == 2


def test_scan_results_tolerate_other_columns(tmp_path):
    path = tmp_path / acquisition.SCAN_CSV
    path.write_text("prn,doppler_hz,code_phase_samp,code_phase_chips,detected,extra\n"
                    "5,250.0,10,2.5575,true,x\n")
    assert acquisition.read_results(path) == [
        {"prn": 5, "doppler_hz": 250.0, "fine_doppler_hz": None, "code_phase_samp": 10,
         "code_phase_chips": 2.5575, "peak_over_mean": 0.0, "detected": True}]
    [seed] = tracking.read_seeds(path, FS)        # an older file: tracking must refine
    assert seed["doppler_hz"] == 250.0 and not seed["refined"]


def test_scan_hands_over_a_refined_doppler(tmp_path):
    x = synthetic_gps(0.03, cn0_dbhz=50.0, sats=((PRN, CODE_PHASE_SAMP, DOPPLER + 90),))
    results = acquisition.scan(x, FS, -2000, 2000, 250, 10, 5.0, prns=[PRN, 1])
    found, other = results
    assert found["doppler_hz"] == DOPPLER                  # nearest 250 Hz bin
    # 1 ms coherent sums: a 20 Hz error costs ~0.1 % of the power, so +/-20 Hz is
    # what the refinement can do (the PLL pulls in the rest).
    assert abs(found["fine_doppler_hz"] - (DOPPLER + 90)) <= 20
    assert other["fine_doppler_hz"] is None
    path = tmp_path / acquisition.SCAN_CSV
    acquisition.write_results(results, path)
    [seed] = tracking.read_seeds(path, FS)
    assert seed["refined"] and seed["doppler_hz"] == found["fine_doppler_hz"]
    assert seed["coarse_doppler_hz"] == DOPPLER
    tr = tracking.track_prn(x, FS, PRN, seed["code_phase_samp"], seed["doppler_hz"],
                            refine=False)
    assert tr["doppler_hz"][0] == pytest.approx(seed["doppler_hz"])
    # The npz keeps both: where the loop started, and the search-grid Doppler.
    npz = tmp_path / tracking.track_file_name(PRN)
    tracking.save_track(npz, PRN, FS, seed, tr, tracking.summarize(tr))
    d = tracking.load_track(npz)
    assert (d["doppler_seed_hz"], d["doppler_coarse_hz"]) == (seed["doppler_hz"], DOPPLER)


def test_track_files_round_trip(signal, tmp_path):
    seed = {"prn": PRN, "doppler_hz": DOPPLER, "code_phase_samp": CODE_PHASE_SAMP}
    tr = tracking.track_prn(signal, FS, PRN, CODE_PHASE_SAMP, DOPPLER, detail=True)
    r = tracking.summarize(tr)
    path = tmp_path / tracking.track_file_name(PRN)
    assert path.name == "track_PRN07.npz"
    tracking.save_track(path, PRN, FS, seed, tr, r)
    d = tracking.load_track(path)
    assert (d["prn"], d["fs"], d["bit_offset"]) == (PRN, FS, r["bit_offset"])
    assert np.array_equal(d["i_prompt"], tr["i_prompt"])
    assert np.array_equal(d["nav_bits"], r["bits"])
    assert set(d["detail"]) == set(tracking.DETAIL_KEYS)
    assert d["doppler_seed_hz"] == DOPPLER

    row = tracking.summary_row(PRN, tr, r)
    summary = tmp_path / tracking.SUMMARY_CSV
    tracking.write_summary([row], summary)
    back = tracking.read_summary(summary)
    assert back == [{**row, "locked": True}]


def test_provenance_round_trip(tmp_path):
    rec = tmp_path / "rec.bin"
    rec.write_bytes(bytes(100))
    result = tmp_path / "scan_results.csv"
    path = provenance.write_meta(result, "acquisition", rec, "int8", 4e6, {"ms": 10})
    assert path.name == "scan_results.meta.json"
    meta = provenance.read_meta(result)
    assert meta["source"]["size_bytes"] == 100 and meta["settings"] == {"ms": 10}
    assert provenance.same_recording(meta, rec)
    other = tmp_path / "other.bin"
    other.write_bytes(bytes(100))
    assert not provenance.same_recording(meta, other)
    rec.write_bytes(bytes(50))                          # same name, different file
    assert not provenance.same_recording(meta, rec)
    assert provenance.read_meta(tmp_path / "none.csv") is None
    json.loads(path.read_text())                        # valid JSON
