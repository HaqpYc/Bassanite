"""
synthetic.py -- a GPS recording made up from known satellites, to test against.

Each satellite has a known PRN, code phase and Doppler, broadcasts random 50 bit/s
data, and sits in complex white noise of unit power at a chosen C/N0. Like a real
signal, the code is stretched by the same Doppler as the carrier (chip rate x
(1 + f_D / f_L1)), so its code phase drifts over time.

The signal is generated in fixed BLOCK_S blocks, each with its own noise seed,
so a long recording never has to be in memory at once and any stretch comes out
the same however it is cut. write_recording() stores it the way hackrf_transfer
does (int8), with a <file>.truth.json sidecar giving the true values.

The data bits are random, not a navigation message: stages 04-05 need an
encoder before a synthetic recording can carry an ephemeris and a position.
"""
import json
import os
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassanite.acquisition import L1_HZ  # noqa: E402
from common.ca_code import CHIP_RATE, CODE_LENGTH, ca_code  # noqa: E402

FS = 4e6
# The one satellite synthetic_gps() puts in by default (code phase in samples at FS).
PRN, CODE_PHASE_SAMP, DOPPLER = 7, 1234, 1500.0
BLOCK_S = 0.1                    # generation block; part of the noise "seed"
SIGMA_CODES = 8.0                 # int8 noise level when written (like capture.bin x 8)
# A default sky: six satellites, code phase in CHIPS (independent of the sample
# rate) and Doppler in Hz, spread over the search range and off the 250 Hz grid.
DEFAULT_SATS = ((3, 120.4, -3210.0), (8, 377.0, -1130.0), (12, 655.2, 420.0),
                (17, 830.9, 1710.0), (24, 222.6, 2890.0), (31, 960.3, 4060.0))


def _block(n0, n1, fs, cn0_dbhz, sats, seed):
    """Samples n0..n1 of the recording (sats in samples), noise from block n0.

    Every random draw is prefix-stable (I and Q drawn as pairs, bits one by one),
    so a recording cut shorter is exactly the start of a longer one."""
    n = np.arange(n0, n1, dtype=np.float64)
    rng = np.random.default_rng([seed, int(round(n0 / (fs * BLOCK_S)))])
    iq = rng.standard_normal((n1 - n0, 2))
    x = (iq[:, 0] + 1j * iq[:, 1]) / np.sqrt(2)
    amp = np.sqrt(10 ** ((cn0_dbhz - 10 * np.log10(fs)) / 10))   # per-sample SNR
    samples_per_bit = int(fs * 0.02)
    for prn, code_phase, doppler in sats:
        n_bits = int((n1 - min(code_phase, 0)) // samples_per_bit) + 2
        bits = np.where(np.random.default_rng([seed, prn, 7]).random(n_bits) < 0.5,
                        -1.0, 1.0)
        chip_rate = CHIP_RATE * (1 + doppler / L1_HZ)
        chips = np.floor((n - code_phase) * chip_rate / fs).astype(np.int64)
        signal = (amp * _code(prn)[chips % CODE_LENGTH]
                  * bits[((n - code_phase) // samples_per_bit).clip(0).astype(np.int64)])
        # carrier e^{j 2 pi f n / fs} = (its value at n0) x (a phasor reused per block)
        start = np.exp(2j * np.pi * ((doppler * n0 / fs) % 1.0))
        x += signal * (start * _phasor(doppler, fs, n1 - n0))
    return x


@lru_cache(maxsize=64)
def _code(prn):
    """The +/-1 C/A code (generating it takes ~20 ms, so once per PRN)."""
    return ca_code(prn)


_phasors: dict = {}


def _phasor(doppler, fs, length):
    """e^{j 2 pi f k / fs} for k = 0..length-1, computed once per block size."""
    key = (doppler, fs)
    p = _phasors.get(key)
    if p is None or len(p) < length:
        p = np.exp(2j * np.pi * doppler * np.arange(max(length, int(round(fs * BLOCK_S))))
                   / fs)
        if len(_phasors) > 64:
            _phasors.clear()
        _phasors[key] = p
    return p[:length]


def synthetic_blocks(seconds, cn0_dbhz=48.0, seed=0, fs=FS, sats=()):
    """The recording as successive arrays of at most BLOCK_S seconds each.
    `sats` holds (prn, code phase in samples at t=0, Doppler Hz)."""
    total = int(fs * seconds)
    step = int(round(fs * BLOCK_S))
    for n0 in range(0, total, step):
        yield _block(n0, min(n0 + step, total), fs, cn0_dbhz, sats, seed)


def synthetic_gps(seconds, cn0_dbhz=48.0, seed=0, fs=FS,
                  sats=((PRN, CODE_PHASE_SAMP, DOPPLER),)):
    """The whole recording in memory (for short ones): complex128 samples."""
    blocks = list(synthetic_blocks(seconds, cn0_dbhz, seed, fs, sats))
    return np.concatenate(blocks) if blocks else np.zeros(0, np.complex128)


def to_int8(x, sigma_codes=SIGMA_CODES):
    """Interleaved int8 I/Q, as hackrf_transfer writes it."""
    raw = np.empty(2 * len(x), dtype=np.int8)
    raw[0::2] = np.clip(np.round(x.real * sigma_codes / np.sqrt(0.5)), -128, 127)
    raw[1::2] = np.clip(np.round(x.imag * sigma_codes / np.sqrt(0.5)), -128, 127)
    return raw


def write_int8(path, x, sigma_codes=SIGMA_CODES):
    """Store `x` the way hackrf_transfer does: interleaved int8 I/Q."""
    to_int8(x, sigma_codes).tofile(path)
    return path


def truth_path(iq_path):
    p = Path(iq_path)
    return p.with_name(p.name + ".truth.json")


def write_recording(path, seconds, cn0_dbhz=45.0, fs=FS, sats=DEFAULT_SATS, seed=0):
    """Write a synthetic int8 recording and its truth sidecar, block by block.

    `sats` holds (prn, code phase in chips at t=0, Doppler Hz). Returns the path.
    """
    path = Path(path)
    spms = fs * 1e-3                                       # samples per code period
    in_samples = [(prn, chips * spms / CODE_LENGTH, dop) for prn, chips, dop in sats]
    with open(path, "wb") as f:
        for block in synthetic_blocks(seconds, cn0_dbhz, seed, fs, in_samples):
            to_int8(block).tofile(f)
    truth = {"fs": fs, "seconds": seconds, "cn0_dbhz": cn0_dbhz, "seed": seed,
             "satellites": [{"prn": prn, "code_phase_chips": chips,
                             "code_phase_samp": samp, "doppler_hz": dop}
                            for (prn, chips, dop), (_, samp, _) in zip(sats, in_samples)]}
    truth_path(path).write_text(json.dumps(truth, indent=2), encoding="utf-8")
    return path


def read_truth(iq_path):
    """The truth sidecar of a synthetic recording, or None for a real one."""
    p = truth_path(iq_path)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (OSError, ValueError):
        return None
