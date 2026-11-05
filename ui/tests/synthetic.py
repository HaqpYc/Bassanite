"""A synthetic GPS recording for tests: satellites with a known PRN, code phase
and Doppler, 50 bps data, in complex white noise of unit power."""
import numpy as np

from bassanite.acquisition import L1_HZ
from common.ca_code import CHIP_RATE, CODE_LENGTH, ca_code

FS = 4e6
PRN, CODE_PHASE_SAMP, DOPPLER = 7, 1234, 1500.0


def synthetic_gps(seconds, cn0_dbhz=48.0, seed=0, fs=FS,
                  sats=((PRN, CODE_PHASE_SAMP, DOPPLER),)):
    """Each satellite in `sats` is (prn, code phase in samples at t=0, Doppler Hz).

    Like a real signal, the code is stretched by the same Doppler as the carrier
    (chip rate x (1 + f_D / f_L1)), so its code phase drifts over time.
    """
    rng = np.random.default_rng(seed)
    n = int(fs * seconds)
    t = np.arange(n) / fs
    amp = np.sqrt(10 ** ((cn0_dbhz - 10 * np.log10(fs)) / 10))   # per-sample SNR
    x = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)
    for prn, code_phase, doppler in sats:
        chip_rate = CHIP_RATE * (1 + doppler / L1_HZ)
        chips = np.floor((np.arange(n) - code_phase) * chip_rate / fs).astype(int)
        code = ca_code(prn)[chips % CODE_LENGTH]
        bits = rng.choice([-1.0, 1.0], size=int(seconds * 50) + 2)
        data = bits[((np.arange(n) - code_phase) // int(fs * 0.02)).clip(0)]
        x += amp * code * data * np.exp(2j * np.pi * doppler * t)
    return x


def write_int8(path, x, sigma_codes=8.0):
    """Store `x` the way hackrf_transfer does: interleaved int8 I/Q."""
    raw = np.empty(2 * len(x), dtype=np.int8)
    raw[0::2] = np.clip(np.round(x.real * sigma_codes / np.sqrt(0.5)), -128, 127)
    raw[1::2] = np.clip(np.round(x.imag * sigma_codes / np.sqrt(0.5)), -128, 127)
    raw.tofile(path)
    return path
