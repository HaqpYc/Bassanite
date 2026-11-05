#!/usr/bin/env python3
"""
signal_view.py -- look at a raw IQ recording before any GPS processing.

Stage 01 only records radio. These functions answer "what did we record?":

  * psd()          average power spectrum (where the energy is in frequency)
  * waterfall()    spectrum over time, across the whole recording
  * raw_samples()  I and Q as stored (ADC codes for HackRF int8 files)
  * adc_stats()    how the samples use the ADC: DC offset, spread, clipping
  * code_histogram() how often each ADC level occurs

GPS itself is invisible in all of these (it sits 15-20 dB below the noise); they
check that the recording is healthy: no clipping, no strong interference, sensible
gain. Used by the web UI's Signal tab.

No dependencies beyond numpy.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.iq_loader import BYTES_PER_SAMPLE, load_iq, normalize_format  # noqa: E402

INT8_MIN, INT8_MAX = -128, 127      # HackRF ADC codes, as written by hackrf_transfer


def psd(x, fs, nfft=1024):
    """Average power spectrum of `x` (Welch method: Hann window, no overlap).

    Returns (freq_offsets_hz, power_db). Frequencies are offsets from the tuned
    centre frequency, from -fs/2 to +fs/2. Power is in dBFS per bin: a full-scale
    complex sinusoid (amplitude 1) that falls exactly on a bin reads 0 dB.
    """
    nseg = len(x) // nfft
    if nseg == 0:
        raise ValueError(f"need at least {nfft} samples, got {len(x)}")
    window = np.hanning(nfft)
    segments = x[: nseg * nfft].reshape(nseg, nfft) * window
    spectrum = np.fft.fftshift(np.fft.fft(segments, axis=1), axes=1)
    power = np.mean(np.abs(spectrum) ** 2, axis=0) / np.sum(window) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / fs))
    return freqs, 10.0 * np.log10(power + 1e-20)


def recording_seconds(path, fmt, fs):
    """Duration of a recording file, from its size."""
    return os.path.getsize(path) // BYTES_PER_SAMPLE[normalize_format(fmt)] / fs


def waterfall(path, fmt="int8", fs=4e6, nfft=512, rows=150, ffts_per_row=8,
              remove_dc=True):
    """Spectrum against time over the whole recording.

    Reads `rows` short blocks spread evenly from start to end (each block is
    `ffts_per_row` FFTs long) and computes one psd() per block, so a 30 s file
    costs only a few MB of reading.

    Returns (times_s, freq_offsets_hz, power_db) with power_db of shape
    (rows, nfft).
    """
    block_s = nfft * ffts_per_row / fs
    total_s = recording_seconds(path, fmt, fs)
    if total_s < block_s:
        raise ValueError("recording shorter than one waterfall row")
    times = np.linspace(0.0, total_s - block_s, rows)
    power = np.empty((rows, nfft), dtype=np.float32)
    freqs = None
    for r, t in enumerate(times):
        x = load_iq(path, fmt, fs, seconds=block_s, skip_seconds=t,
                    remove_dc=remove_dc)
        freqs, power[r] = psd(x, fs, nfft)
    return times, freqs, power


def raw_samples(path, fmt="int8", fs=4e6, seconds=0.1, skip_seconds=0.0,
                remove_dc=False):
    """I and Q as stored in the file, without scaling.

    For int8 (HackRF) files these are the ADC codes, -128..127. For complex64
    files they are the stored float values. Optionally subtract the DC offset.
    """
    fmt = normalize_format(fmt)
    x = load_iq(path, fmt, fs, seconds=seconds, skip_seconds=skip_seconds,
                remove_dc=False).astype(np.complex128)
    if fmt == "int8":
        x = x * 128.0                     # undo load_iq's scaling: exact ADC codes
    if remove_dc and x.size:
        x = x - x.mean()
    return x.real, x.imag


def adc_stats(i, q, fmt="int8"):
    """How the samples use the ADC.

    Returns a dict with the DC offset and spread (standard deviation) of I and Q,
    the RMS level in dBFS, and, for int8 files, the fraction of samples where I
    or Q sits at the ADC's end codes (clipping) and how many of the 256 levels
    occur at all.
    """
    fmt = normalize_format(fmt)
    full_scale = 128.0 if fmt == "int8" else 1.0
    power = np.mean(i ** 2 + q ** 2) / full_scale ** 2
    stats = {
        "dc_i": float(np.mean(i)), "dc_q": float(np.mean(q)),
        "std_i": float(np.std(i)), "std_q": float(np.std(q)),
        "rms_dbfs": float(10 * np.log10(power + 1e-20)),
        "clip_fraction": None, "levels_used": None,
    }
    if fmt == "int8":
        ends = (INT8_MIN, INT8_MAX)
        clipped = np.isin(i, ends) | np.isin(q, ends)
        stats["clip_fraction"] = float(np.mean(clipped))
        stats["levels_used"] = int(np.unique(np.concatenate([i, q])).size)
    return stats


def block_power_db(x, fs, block_s=1e-3):
    """Mean power of each `block_s`-long block of `x`, in dBFS."""
    n = max(int(round(fs * block_s)), 1)
    nblocks = len(x) // n
    if nblocks == 0:
        return np.array([])
    p = np.mean(np.abs(x[: nblocks * n].reshape(nblocks, n)) ** 2, axis=1)
    return 10.0 * np.log10(p + 1e-20)


def burst_stats(x, fs, block_s=1e-3, threshold_db=2.0):
    """How bursty the level is: intermittent interference shows up as short
    blocks well above the usual (median) level, while steady noise doesn't.

    Returns {"burst_fraction": share of blocks more than `threshold_db` above
    the median, "burst_max_db": strongest block above the median}.
    """
    p = block_power_db(x, fs, block_s)
    if p.size < 2:
        return {"burst_fraction": 0.0, "burst_max_db": 0.0}
    base = np.median(p)
    return {"burst_fraction": float(np.mean(p > base + threshold_db)),
            "burst_max_db": float(p.max() - base)}


def code_histogram(values):
    """Count how often each int8 ADC code (-128..127) occurs.

    Returns (codes, counts), both of length 256.
    """
    codes = np.arange(INT8_MIN, INT8_MAX + 1)
    counts = np.bincount(np.asarray(values, dtype=np.int64) - INT8_MIN,
                         minlength=codes.size)
    return codes, counts


if __name__ == "__main__":
    import argparse
    _default_iq = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "recordings", "capture.bin")
    ap = argparse.ArgumentParser(description="Health check of an IQ recording.")
    ap.add_argument("--iq", default=_default_iq)
    ap.add_argument("--format", default="int8")
    ap.add_argument("--fs", type=float, default=4e6)
    ap.add_argument("--seconds", type=float, default=0.1)
    a = ap.parse_args()
    i, q = raw_samples(a.iq, a.format, a.fs, seconds=a.seconds)
    s = adc_stats(i, q, a.format)
    print(f"DC offset  I {s['dc_i']:+.2f}  Q {s['dc_q']:+.2f}")
    print(f"spread     I {s['std_i']:.2f}  Q {s['std_q']:.2f}   RMS {s['rms_dbfs']:.1f} dBFS")
    if s["clip_fraction"] is not None:
        print(f"clipped    {100 * s['clip_fraction']:.3f} %   "
              f"levels used {s['levels_used']} of 256")
