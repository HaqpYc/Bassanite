"""The numbers the explanation texts quote, computed for the current recording.

Panels in ui/explain/ write `{{key}}` or `{{key:format}}` (Python format spec, e.g.
`{{cells:,}}` or `{{chip_per_sample:.3f}}`) where a figure depends on the sample
rate or the search settings, and explain.fill() replaces them with these values.
Figures measured on one satellite of one recording stay as text, labelled as a
worked example on `capture.bin`.
"""
from __future__ import annotations

import math
from functools import lru_cache

from state import DEFAULT_FS, state

from bassanite.acquisition import (DEFAULT_DOP_RANGE_HZ, DEFAULT_DOP_STEP_HZ,
                                   DEFAULT_MS, DEFAULT_THRESHOLD, doppler_bins,
                                   noise_peak_over_mean)
from bassanite.provenance import read_meta
from bassanite.results import scan_csv

TIME_SAMPLES = 200          # samples in the Signal tab's time plot
STRONG_CN0_DBHZ = 43        # the strongest satellite of the bundled recording
SPECTRUM_NFFT = 1024        # the Signal tab's default FFT size
SPEED_OF_LIGHT = 299_792_458.0


@lru_cache(maxsize=32)
def _floor(spms: int, n_dopplers: int, nms: int) -> float:
    return noise_peak_over_mean(spms, n_dopplers, nms)


def explain_values(fs: float = DEFAULT_FS, ms: int = DEFAULT_MS,
                   dop_range_hz: float = DEFAULT_DOP_RANGE_HZ,
                   dop_step_hz: float = DEFAULT_DOP_STEP_HZ,
                   threshold: float = DEFAULT_THRESHOLD) -> dict:
    """Every placeholder value, for a sample rate and acquisition settings."""
    spms = int(round(fs * 1e-3))
    bins = len(doppler_bins(-dop_range_hz, dop_range_hz, dop_step_hz))
    fs_db = 10 * math.log10(fs)
    return {
        "fs_msps": fs / 1e6,
        "fs_db": fs_db,
        "spms": spms,                                    # samples per 1 ms code period
        "chip_per_sample": 1023 / spms,
        "m_per_sample": SPEED_OF_LIGHT / fs,            # range covered by one sample
        "ms": ms,
        "dop_bins": bins,
        "dop_range_hz": dop_range_hz,
        "dop_step_hz": dop_step_hz,
        "threshold": threshold,
        "cells": bins * spms,
        "noise_floor": _floor(spms, bins, ms),           # peak/mean of noise, these settings
        "floor_1": _floor(spms, bins, 1),
        "floor_5": _floor(spms, bins, 5),
        "floor_10": _floor(spms, bins, 10),
        "floor_20": _floor(spms, bins, 20),
        "row_floor_1": _floor(spms, 1, 1),
        "row_floor_10": _floor(spms, 1, 10),
        "row_floor_20": _floor(spms, 1, 20),
        "time_samples": TIME_SAMPLES,
        "time_us": TIME_SAMPLES / fs * 1e6,
        "spectrum_nfft": SPECTRUM_NFFT,
        "bin_khz": fs / SPECTRUM_NFFT / 1e3,
        "strong_cn0": STRONG_CN0_DBHZ,
        "snr_db": STRONG_CN0_DBHZ - fs_db,
        "snr_ratio": 10 ** ((fs_db - STRONG_CN0_DBHZ) / 10),
        "gain_db": 10 * math.log10(spms),
    }


def current_values() -> dict:
    """The values for the loaded recording, and the settings of its last scan."""
    src = state.source
    if src is None or src.fs < 1e3:
        return explain_values()
    saved = (read_meta(scan_csv(src.path)) or {}).get("settings", {})
    try:
        return explain_values(
            src.fs, int(saved.get("ms", DEFAULT_MS)),
            float(saved.get("dop_range_hz", DEFAULT_DOP_RANGE_HZ)),
            float(saved.get("dop_step_hz", DEFAULT_DOP_STEP_HZ)),
            float(saved.get("threshold", DEFAULT_THRESHOLD)))
    except (TypeError, ValueError, ZeroDivisionError):
        return explain_values(src.fs)
