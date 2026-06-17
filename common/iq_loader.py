#!/usr/bin/env python3
"""
iq_loader.py -- load a raw IQ recording into a numpy complex array.

The SDR writes the signal as a stream of complex samples (in-phase I + quadrature
Q). Two formats show up in this project:

  * "int8"     -- HackRF / `hackrf_transfer` native format: interleaved signed
                  8-bit bytes  I0,Q0, I1,Q1, ...  (2 bytes per complex sample).
                  This is what the bundled `recordings/capture.bin` is.
  * "complex64"-- gr_complex / GNU Radio float format: interleaved 32-bit floats
                  (8 bytes per complex sample). This is what GNSS-SDR `dump`s and
                  what mini_gps_rx.py read.

This loader is the small "conditioning" step of the pipeline: it converts either
on-disk format to a single in-memory representation (`np.complex64`, scaled to
roughly +/-1) and removes the DC offset (a constant I/Q bias that SDR front-ends,
especially clones, always add and which would otherwise sit as a fake spike at
0 Hz).

No dependencies beyond numpy.
"""
import numpy as np

BYTES_PER_SAMPLE = {"int8": 2, "complex64": 8}
_ALIASES = {"i8": "int8", "hackrf": "int8", "fc32": "complex64",
            "gr_complex": "complex64", "complex": "complex64"}


def normalize_format(fmt):
    fmt = fmt.lower()
    fmt = _ALIASES.get(fmt, fmt)
    if fmt not in BYTES_PER_SAMPLE:
        raise ValueError(f"unknown format {fmt!r}; use 'int8' or 'complex64'")
    return fmt


def load_iq(path, fmt="int8", fs=4e6, seconds=None, skip_seconds=0.0,
            remove_dc=True):
    """Load IQ samples from `path`.

    Parameters
    ----------
    path : str         file to read
    fmt  : str         "int8" (HackRF) or "complex64" (gr_complex); aliases ok
    fs   : float       sample rate in Hz (only used to interpret seconds/skip)
    seconds : float    how much to read; None = whole file
    skip_seconds : float   how much to skip from the start
    remove_dc : bool   subtract the mean (DC) from the loaded block

    Returns
    -------
    np.ndarray of np.complex64, scaled to roughly +/-1.
    """
    fmt = normalize_format(fmt)
    fs = float(fs)

    if fmt == "int8":
        # 2 int8 items per complex sample.
        skip_items = int(round(skip_seconds * fs)) * 2
        count_items = -1 if seconds is None else int(round(seconds * fs)) * 2
        raw = np.fromfile(path, dtype=np.int8, count=count_items,
                          offset=skip_items)
        raw = raw[: (raw.size // 2) * 2]            # drop a dangling byte
        x = raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)
        x /= 128.0                                  # int8 full-scale -> ~[-1,1]
    else:  # complex64
        skip_items = int(round(skip_seconds * fs))     # offset is in items here
        count_items = -1 if seconds is None else int(round(seconds * fs))
        x = np.fromfile(path, dtype=np.complex64, count=count_items,
                        offset=skip_items * 8)

    x = x.astype(np.complex64)
    if remove_dc and x.size:
        x = x - x.mean()
    return x


if __name__ == "__main__":
    import argparse
    import os
    _default_iq = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "recordings", "capture.bin")
    ap = argparse.ArgumentParser(description="Inspect an IQ recording.")
    ap.add_argument("--iq", default=_default_iq)
    ap.add_argument("--format", default="int8")
    ap.add_argument("--fs", type=float, default=4e6)
    ap.add_argument("--seconds", type=float, default=0.2)
    a = ap.parse_args()
    x = load_iq(a.iq, a.format, a.fs, seconds=a.seconds)
    rms = float(np.sqrt(np.mean(np.abs(x) ** 2))) if x.size else 0.0
    print(f"loaded {x.size} samples ({x.size / a.fs:.3f}s) from {a.iq}")
    print(f"  format={a.format}  fs={a.fs:.0f}  RMS={rms:.4f}  "
          f"mean(after DC removal)={x.mean():.2e}")
