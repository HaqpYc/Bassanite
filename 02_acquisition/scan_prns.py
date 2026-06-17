#!/usr/bin/env python3
"""
scan_prns.py -- GPS acquisition: scan ALL 32 PRNs and report which satellites
are present in a recording.

This is the "scanning PRN" stage of the pipeline (in GPS terms: *acquisition* =
satellite detection). For each PRN we ask: is this satellite's code somewhere in
the recording, and if so at what code phase and Doppler?

THE 2D SEARCH
-------------
We don't know two things about a visible satellite:
  1. CODE PHASE  -- where, within the 1 ms period, the satellite's code currently
                    lines up (0..1022 chips). Caused by the unknown signal travel
                    time.
  2. DOPPLER     -- the carrier-frequency offset (satellite motion is +/-5 kHz;
                    the HackRF's own oscillator adds up to ~+/-31 kHz at L1). Until
                    we remove it the code won't correlate.
So acquisition is a search over (code phase x Doppler). For each candidate
Doppler we de-rotate the signal ("carrier wipe-off") and correlate against the
local code replica; a sharp correlation peak at some code phase means "found it".

THE FFT TRICK (parallel code-phase search)
------------------------------------------
Instead of trying all 1023 code phases one by one, circular correlation via the
FFT gives the correlation at *every* code phase in a single IFFT:
    corr = IFFT( FFT(signal_1ms) * conj(FFT(code_1ms)) )
We do that per Doppler bin and non-coherently accumulate |corr|^2 over several
1 ms periods to lift weak satellites out of the noise.

THE DETECTION METRIC
--------------------
We report peak / mean of the accumulated correlation surface. Pure noise gives a
ratio near 1; a real satellite produces a peak far above the mean. A practical
threshold for this setup is ~2.5-3 (tune with --threshold).

Reuses the validated Gold-code generator in ../common/ca_code.py and the IQ
loader in ../common/iq_loader.py.

Example:
  python scan_prns.py --iq recordings/capture.bin --format int8 --fs 4e6
"""
import argparse
import csv
import os
import sys

import numpy as np

# Make the repo-root `common` package importable regardless of CWD.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from common.ca_code import upsample_code, CODE_LENGTH          # noqa: E402
from common.iq_loader import load_iq                           # noqa: E402


def acquire_prn(x, fs, prn, dop_min, dop_max, dop_step, nms):
    """FFT parallel-code-phase acquisition of one PRN.

    Returns (code_phase_samples, doppler_hz, peak_over_mean).
    """
    spms = int(round(fs * 1e-3))                  # samples per 1 ms code period
    code_up = upsample_code(prn, spms).astype(np.float64)
    CODE_CONJ = np.conj(np.fft.fft(code_up))      # precomputed once per PRN
    t = np.arange(spms) / fs

    best = (0, 0.0, 0.0)
    for dop in np.arange(dop_min, dop_max + 1, dop_step):
        carrier = np.exp(-1j * 2 * np.pi * dop * t)   # carrier wipe-off for this bin
        acc = np.zeros(spms)
        for k in range(nms):
            seg = x[k * spms:(k + 1) * spms]
            if len(seg) < spms:
                break
            corr = np.fft.ifft(np.fft.fft(seg * carrier) * CODE_CONJ)
            acc += np.abs(corr) ** 2                   # non-coherent accumulation
        peak, mean = acc.max(), acc.mean()
        ratio = peak / mean if mean > 0 else 0.0
        if ratio > best[2]:
            best = (int(acc.argmax()), float(dop), float(ratio))
    return best


def scan(x, fs, dop_min, dop_max, dop_step, nms, threshold):
    spms = int(round(fs * 1e-3))
    results = []
    for prn in range(1, 33):
        cp, dop, ratio = acquire_prn(x, fs, prn, dop_min, dop_max, dop_step, nms)
        chips = cp * float(CODE_LENGTH) / spms
        results.append({
            "prn": prn,
            "doppler_hz": dop,
            "code_phase_samp": cp,
            "code_phase_chips": chips,
            "peak_over_mean": ratio,
            "detected": ratio >= threshold,
        })
    results.sort(key=lambda r: r["peak_over_mean"], reverse=True)
    return results


def print_table(results, threshold):
    print()
    print(f"{'PRN':>3}  {'Doppler(Hz)':>11}  {'codephase(samp)':>15}  "
          f"{'(chips)':>8}  {'peak/mean':>9}  status")
    print("-" * 64)
    for r in results:
        status = "DETECTED" if r["detected"] else ""
        print(f"{r['prn']:>3}  {r['doppler_hz']:>11.0f}  "
              f"{r['code_phase_samp']:>15}  {r['code_phase_chips']:>8.1f}  "
              f"{r['peak_over_mean']:>9.2f}  {status}")
    ndet = sum(r["detected"] for r in results)
    print("-" * 64)
    print(f"{ndet} PRN(s) at/above threshold {threshold:.2f} "
          f"(need >= 4 solid SVs for a position fix)")


def write_csv(results, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print(f"wrote {path}")


def write_plot(results, threshold, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_prn = sorted(results, key=lambda r: r["prn"])
    prns = [r["prn"] for r in by_prn]
    ratios = [r["peak_over_mean"] for r in by_prn]
    colors = ["tab:green" if r["detected"] else "tab:gray" for r in by_prn]

    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.bar(prns, ratios, color=colors)
    ax.axhline(threshold, color="tab:red", ls="--", lw=1,
               label=f"threshold = {threshold:.2f}")
    ax.set_xlabel("PRN (satellite ID)")
    ax.set_ylabel("acquisition peak / mean")
    ax.set_title("GPS L1 C/A acquisition scan — peak/mean per PRN")
    ax.set_xticks(prns)
    ax.tick_params(axis="x", labelsize=7)
    ax.legend()
    for r in by_prn:
        if r["detected"]:
            ax.text(r["prn"], r["peak_over_mean"], f"{r['peak_over_mean']:.0f}",
                    ha="center", va="bottom", fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    print(f"wrote {path}")


def main():
    ap = argparse.ArgumentParser(
        description="Scan all 32 GPS PRNs in an IQ recording (acquisition).")
    ap.add_argument("--iq",
                    default=os.path.join(_ROOT, "recordings", "capture.bin"),
                    help="raw IQ recording (default: the bundled outdoor capture)")
    ap.add_argument("--format", default="int8",
                    help="int8 (HackRF) or complex64 (gr_complex)")
    ap.add_argument("--fs", type=float, default=4e6, help="sample rate (Hz)")
    ap.add_argument("--dop-range", type=float, default=10000.0,
                    help="+/- Doppler search range (Hz)")
    ap.add_argument("--dop-step", type=float, default=250.0,
                    help="Doppler search step (Hz)")
    ap.add_argument("--ms", type=int, default=10,
                    help="number of 1 ms periods to non-coherently integrate")
    ap.add_argument("--skip-seconds", type=float, default=0.0,
                    help="skip this many seconds from the start of the file")
    ap.add_argument("--threshold", type=float, default=5.0,
                    help="peak/mean detection threshold (noise floor here is ~3.5 "
                         "for 10 ms @ 4 MSPS; persistence across --skip-seconds is "
                         "the real confirmation)")
    ap.add_argument("--csv", default=os.path.join(_HERE, "scan_results.csv"))
    ap.add_argument("--plot", default=os.path.join(_HERE, "scan_plot.png"))
    a = ap.parse_args()

    load_secs = (a.ms + 5) / 1000.0               # just enough + small margin
    x = load_iq(a.iq, a.format, a.fs, seconds=load_secs,
                skip_seconds=a.skip_seconds)
    rms = float(np.sqrt(np.mean(np.abs(x) ** 2))) if x.size else 0.0
    print(f"loaded {x.size} samples ({x.size / a.fs:.3f}s) from {a.iq}  "
          f"(RMS={rms:.4f})")
    print(f"searching Doppler +/-{a.dop_range:.0f} Hz @ {a.dop_step:.0f} Hz, "
          f"integrating {a.ms} ms non-coherently")

    results = scan(x, a.fs, -a.dop_range, a.dop_range, a.dop_step, a.ms,
                   a.threshold)
    print_table(results, a.threshold)
    write_csv(results, a.csv)
    write_plot(results, a.threshold, a.plot)


if __name__ == "__main__":
    main()
