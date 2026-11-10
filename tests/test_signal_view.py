"""Tests for bassanite/signal_view.py (the Signal tab's numbers)."""
import numpy as np
import pytest

from bassanite.signal_view import (adc_stats, code_histogram, psd, raw_samples,
                                   recording_seconds, waterfall)

FS = 4e6


def _write_int8(path, i, q):
    raw = np.empty(2 * len(i), dtype=np.int8)
    raw[0::2], raw[1::2] = i, q
    raw.tofile(path)


def test_psd_full_scale_tone_reads_0_dbfs_at_its_frequency():
    nfft = 1024
    k = 100                                    # put the tone exactly on bin 100
    f0 = k * FS / nfft
    n = np.arange(nfft * 16)
    x = np.exp(2j * np.pi * f0 * n / FS)
    freqs, p = psd(x, FS, nfft)
    assert freqs[np.argmax(p)] == pytest.approx(f0)
    assert p.max() == pytest.approx(0.0, abs=0.01)


def test_psd_noise_level_matches_theory():
    rng = np.random.default_rng(0)
    nfft, sigma2 = 1024, 1e-4                  # total complex noise power
    x = np.sqrt(sigma2 / 2) * (rng.standard_normal(nfft * 400)
                               + 1j * rng.standard_normal(nfft * 400))
    _, p = psd(x, FS, nfft)
    w = np.hanning(nfft)
    expected = 10 * np.log10(sigma2 * np.sum(w ** 2) / np.sum(w) ** 2)
    assert np.mean(p) == pytest.approx(expected, abs=0.3)


def test_psd_needs_enough_samples():
    with pytest.raises(ValueError):
        psd(np.zeros(10, complex), FS, 1024)


def test_raw_samples_returns_exact_adc_codes(tmp_path):
    f = tmp_path / "x.bin"
    i = np.array([-128, -1, 0, 5, 127] * 100, dtype=np.int8)
    q = np.array([3, 3, -7, 0, 1] * 100, dtype=np.int8)
    _write_int8(f, i, q)
    ri, rq = raw_samples(f, "int8", FS, seconds=None)
    assert np.array_equal(ri, i) and np.array_equal(rq, q)
    ri, _ = raw_samples(f, "int8", FS, seconds=None, remove_dc=True)
    assert ri.mean() == pytest.approx(0.0, abs=1e-9)


def test_adc_stats_counts_clipping_and_levels():
    i = np.array([127, 0, 1, 2], float)
    q = np.array([0, -128, 1, 2], float)
    s = adc_stats(i, q, "int8")
    assert s["clip_fraction"] == pytest.approx(0.5)
    assert s["levels_used"] == 5               # -128, 0, 1, 2, 127
    assert s["dc_i"] == pytest.approx(32.5)


def test_adc_stats_complex64_has_no_adc_fields():
    s = adc_stats(np.ones(4), np.zeros(4), "complex64")
    assert s["clip_fraction"] is None and s["levels_used"] is None
    assert s["rms_dbfs"] == pytest.approx(0.0)


def test_code_histogram():
    codes, counts = code_histogram([-128, 0, 0, 127])
    assert len(codes) == len(counts) == 256
    assert counts[codes == 0][0] == 2 and counts.sum() == 4


def test_waterfall_shape_and_duration(tmp_path):
    f = tmp_path / "x.bin"
    rng = np.random.default_rng(1)
    n = int(FS * 0.05)                         # 50 ms
    _write_int8(f, rng.integers(-5, 5, n), rng.integers(-5, 5, n))
    assert recording_seconds(f, "int8", FS) == pytest.approx(0.05)
    times, freqs, power = waterfall(f, "int8", FS, nfft=256, rows=10)
    assert power.shape == (10, 256) and len(times) == 10 and len(freqs) == 256
    assert times[0] == 0.0 and times[-1] < 0.05


def test_open_iq_matches_load_iq(tmp_path):
    from bassanite.iq_loader import load_iq, open_iq
    rng = np.random.default_rng(1)
    raw = rng.integers(-60, 60, size=2 * 50_000).astype(np.int8)
    raw[0::2] += 5                                          # a DC offset to remove
    path = tmp_path / "rec.bin"
    raw.tofile(path)
    for kw in ({}, {"seconds": 0.004, "skip_seconds": 0.002}, {"remove_dc": False}):
        full = load_iq(path, "int8", FS, **kw)
        lazy = open_iq(path, "int8", FS, **kw)
        assert len(lazy) == lazy.size == len(full)
        assert np.allclose(lazy[:], full, atol=1e-6)
        assert np.allclose(lazy[100:4100], full[100:4100], atol=1e-6)
        assert lazy[len(full) - 3:len(full) + 10].shape == (3,)   # clipped at the end
    path64 = tmp_path / "rec.cf32"
    load_iq(path, "int8", FS).tofile(path64)
    assert np.allclose(open_iq(path64, "complex64", FS)[:], load_iq(path64, "complex64", FS),
                       atol=1e-6)
