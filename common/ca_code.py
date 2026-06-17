#!/usr/bin/env python3
"""
ca_code.py -- generate the GPS L1 C/A code (the "PRN") for any satellite.

WHAT IS A PRN?
--------------
Every GPS satellite continuously transmits its own unique 1023-chip pseudo-random
sequence called the *Coarse/Acquisition (C/A) code*, clocked at 1.023 Mchip/s, so
the whole sequence repeats every **1 ms**. "PRN" (Pseudo-Random Noise number) is
just the satellite's ID, 1..32, which selects *which* code it sends.

These codes are **Gold codes**: a family designed so that
  * each code is nearly uncorrelated with a time-shifted copy of itself
    (sharp autocorrelation -> we can find the exact alignment), and
  * any two different codes are nearly uncorrelated
    (low cross-correlation -> satellites don't drown each other out).
That is the whole trick behind CDMA: all 31+ satellites share the exact same
1575.42 MHz carrier, and we separate them purely by their codes.

HOW THE CODE IS BUILT
---------------------
A Gold code is the XOR of two 10-bit maximal-length LFSR (shift-register)
sequences, G1 and G2:
  * G1 has a fixed feedback (taps 3 and 10).
  * G2 has a fixed feedback (taps 2,3,6,8,9,10) but each PRN taps *two different
    stages* of G2 (the "phase selection") before XOR-ing with G1. Those per-PRN
    tap pairs are the G2_TAPS table below -- they are what makes PRN 5 different
    from PRN 6, etc.

The output is mapped 0/1 -> +1/-1 so it can be multiplied directly against a
baseband signal during correlation.

This generator is validated against the two textbook checks (see the self-test at
the bottom): PRN 1's first ten chips are 1100100000, and every C/A code is
*balanced* with exactly 512 ones and 511 zeros, i.e. the +/-1 sum is -1.

No dependencies beyond numpy.
"""
import numpy as np

# Per-PRN G2 phase-selection taps (1-indexed register stages).
# Standard GPS ICD table for PRN 1..32 (G2 delay expressed as a stage pair).
G2_TAPS = {1: (2, 6), 2: (3, 7), 3: (4, 8), 4: (5, 9), 5: (1, 9), 6: (2, 10),
           7: (1, 8), 8: (2, 9), 9: (3, 10), 10: (2, 3), 11: (3, 4), 12: (5, 6),
           13: (6, 7), 14: (7, 8), 15: (8, 9), 16: (9, 10), 17: (1, 4),
           18: (2, 5), 19: (3, 6), 20: (4, 7), 21: (5, 8), 22: (6, 9),
           23: (1, 3), 24: (4, 6), 25: (5, 7), 26: (6, 8), 27: (7, 9),
           28: (8, 10), 29: (1, 6), 30: (2, 7), 31: (3, 8), 32: (4, 9)}

CODE_LENGTH = 1023          # chips per C/A code period
CHIP_RATE = 1.023e6         # chips per second
CODE_PERIOD = 1e-3          # seconds (1 ms)


def ca_code(prn, as_bits=False):
    """Return the length-1023 C/A code for `prn`.

    By default returns it as +/-1 floats (ready for correlation). With
    as_bits=True it returns the raw 0/1 chips instead.
    """
    if prn not in G2_TAPS:
        raise ValueError(f"PRN must be 1..32, got {prn}")
    g1 = np.ones(10, dtype=int)
    g2 = np.ones(10, dtype=int)
    s1, s2 = G2_TAPS[prn]
    out = np.empty(CODE_LENGTH, dtype=int)
    for i in range(CODE_LENGTH):
        # Output chip = G1 last stage XOR (the two selected G2 stages).
        out[i] = g1[9] ^ (g2[s1 - 1] ^ g2[s2 - 1])
        # Maximal-length feedback for each register.
        fb1 = g1[2] ^ g1[9]
        fb2 = g2[1] ^ g2[2] ^ g2[5] ^ g2[7] ^ g2[8] ^ g2[9]
        g1 = np.roll(g1, 1); g1[0] = fb1
        g2 = np.roll(g2, 1); g2[0] = fb2
    if as_bits:
        return out
    return (1 - 2 * out).astype(np.float64)   # 0 -> +1, 1 -> -1


def upsample_code(prn, samples_per_ms):
    """Sample the 1023-chip code onto `samples_per_ms` samples (one 1 ms period).

    Used to build a local replica at the receiver's sample rate. Nearest-chip
    sampling is fine for acquisition.
    """
    code = ca_code(prn)
    idx = np.floor(np.arange(samples_per_ms) * float(CODE_LENGTH) / samples_per_ms)
    idx = idx.astype(int) % CODE_LENGTH
    return code[idx]


def _self_test():
    # Check 1: PRN 1's first 10 chips are the well-known 1100100000.
    bits = ca_code(1, as_bits=True)
    first10 = "".join(str(b) for b in bits[:10])
    assert first10 == "1100100000", f"PRN1 first chips {first10} != 1100100000"

    # Check 2: every C/A code is balanced (512 ones, 511 zeros -> +/-1 sum = -1).
    for prn in G2_TAPS:
        code = ca_code(prn)
        s = int(code.sum())
        assert s == -1, f"PRN{prn} balance {s} != -1"
        assert code.size == CODE_LENGTH

    print("ca_code self-test PASSED:")
    print(f"  PRN1 first 10 chips = {first10} (expected 1100100000)")
    print(f"  all 32 PRNs balanced (sum of +/-1 == -1, 512 ones / 511 zeros)")


if __name__ == "__main__":
    _self_test()
