"""Tests for the bassanite package (stages 02-03) on a synthetic GPS signal:
one satellite with a known PRN, code phase and Doppler, 50 bps data, in noise."""
import json

import numpy as np
import pytest

from bassanite import acquisition, provenance, tracking
from common.ca_code import CHIP_RATE, CODE_LENGTH, ca_code

FS = 4e6
PRN, CODE_PHASE_SAMP, DOPPLER = 7, 1234, 1500.0


def synthetic_gps(seconds, cn0_dbhz=48.0, seed=0):
    """One satellite: code delayed by CODE_PHASE_SAMP samples, carrier at DOPPLER,
    random data bits every 20 ms, complex white noise of unit power. Like a real
    signal, the code is stretched by the same Doppler as the carrier
    (chip rate x (1 + f_D / f_L1))."""
    rng = np.random.default_rng(seed)
    n = int(FS * seconds)
    t = np.arange(n) / FS
    chip_rate = CHIP_RATE * (1 + DOPPLER / tracking.L1_HZ)
    chips = np.floor((np.arange(n) - CODE_PHASE_SAMP) * chip_rate / FS).astype(int)
    code = ca_code(PRN)[chips % CODE_LENGTH]
    bits = rng.choice([-1.0, 1.0], size=int(seconds * 50) + 2)
    data = bits[((np.arange(n) - CODE_PHASE_SAMP) // int(FS * 0.02)).clip(0)]
    amp = np.sqrt(10 ** ((cn0_dbhz - 10 * np.log10(FS)) / 10))   # per-sample SNR
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)
    return amp * code * data * np.exp(2j * np.pi * DOPPLER * t) + noise


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
