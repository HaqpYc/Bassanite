"""
acquisition.py -- stage 02: find which GPS satellites are in a recording.

For each PRN we search two unknowns: the CODE PHASE (where the satellite's 1 ms
code starts inside our 1 ms window, 0..1022 chips) and the DOPPLER (carrier
offset from satellite motion and the receiver's clock error). For each Doppler
bin we wipe off the carrier, correlate against the local code at every code
phase at once with the FFT, and non-coherently accumulate |corr|^2 over a few
ms. A cell far above the average means the satellite is there.

    corr = IFFT( FFT(signal_1ms * exp(-j2*pi*f*t)) * conj(FFT(code_1ms)) )

The detection metric is peak/mean of the accumulated surface (pure noise gives
~3 for 10 ms at 4 MSPS; the default threshold is 5). See
02_acquisition/NOTES.md for the full explanation and the verification method.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.ca_code import CODE_LENGTH, upsample_code  # noqa: E402

DEFAULT_DOP_RANGE_HZ = 10000.0
DEFAULT_DOP_STEP_HZ = 250.0
DEFAULT_MS = 10
DEFAULT_THRESHOLD = 5.0


def correlation_surface(x, fs, prn, dopplers, nms):
    """Accumulated correlation power for every (Doppler, code phase) cell.

    Returns an array of shape (len(dopplers), samples_per_ms): row i is the
    non-coherent sum over `nms` 1 ms periods of |corr|^2 at Doppler dopplers[i],
    for every code phase (in samples).
    """
    spms = int(round(fs * 1e-3))                  # samples per 1 ms code period
    code_conj = np.conj(np.fft.fft(upsample_code(prn, spms).astype(np.float64)))
    t = np.arange(spms) / fs
    surface = np.zeros((len(dopplers), spms))
    for i, dop in enumerate(dopplers):
        carrier = np.exp(-1j * 2 * np.pi * dop * t)    # carrier wipe-off for this bin
        for k in range(nms):
            seg = x[k * spms:(k + 1) * spms]
            if len(seg) < spms:
                break
            corr = np.fft.ifft(np.fft.fft(seg * carrier) * code_conj)
            surface[i] += np.abs(corr) ** 2              # non-coherent accumulation
    return surface


def doppler_bins(dop_min, dop_max, dop_step):
    return np.arange(dop_min, dop_max + 1, dop_step)


def acquire_prn(x, fs, prn, dop_min, dop_max, dop_step, nms, return_surface=False):
    """FFT parallel-code-phase acquisition of one PRN.

    Each Doppler bin is scored by peak/mean over its code phases; the best bin
    gives the estimates. Returns (code_phase_samples, doppler_hz, peak_over_mean),
    plus (dopplers, surface) when `return_surface` is True.
    """
    dopplers = doppler_bins(dop_min, dop_max, dop_step)
    surface = correlation_surface(x, fs, prn, dopplers, nms)
    best = (0, 0.0, 0.0)
    for i, dop in enumerate(dopplers):
        acc = surface[i]
        peak, mean = acc.max(), acc.mean()
        ratio = peak / mean if mean > 0 else 0.0
        if ratio > best[2]:
            best = (int(acc.argmax()), float(dop), float(ratio))
    return (*best, dopplers, surface) if return_surface else best


def scan(x, fs, dop_min, dop_max, dop_step, nms, threshold, prns=range(1, 33),
         progress=None):
    """Acquire every PRN in `prns`. Returns one dict per PRN, strongest first.

    `progress(done, total)` is called after each PRN, if given.
    """
    spms = int(round(fs * 1e-3))
    prns = list(prns)
    results = []
    for n, prn in enumerate(prns, start=1):
        cp, dop, ratio = acquire_prn(x, fs, prn, dop_min, dop_max, dop_step, nms)
        results.append({
            "prn": prn,
            "doppler_hz": dop,
            "code_phase_samp": cp,
            "code_phase_chips": cp * float(CODE_LENGTH) / spms,
            "peak_over_mean": ratio,
            "detected": ratio >= threshold,
        })
        if progress is not None:
            progress(n, len(prns))
    results.sort(key=lambda r: r["peak_over_mean"], reverse=True)
    return results


def seconds_needed(nms):
    """How many seconds of signal a scan integrating `nms` ms reads (+5 ms margin)."""
    return (nms + 5) / 1000.0
