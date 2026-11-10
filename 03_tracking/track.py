#!/usr/bin/env python3
"""
track.py -- GPS tracking: lock onto each acquired satellite and follow it for the
whole recording, producing the 50 bps navigation data stream stage 04 will decode.

WHERE THIS SITS IN THE PIPELINE
-------------------------------
Stage 02 (acquisition) answered "which satellites are up there, and roughly at what
code phase + Doppler?" -- a coarse, one-shot 2-D search. But the satellite keeps
moving: its Doppler drifts and its code phase slides continuously. *Tracking* is the
closed-loop stage that **keeps** the local replica aligned, sample by sample, with
two feedback loops running once per 1 ms code period:

  * CARRIER loop (PLL)  -- a Costas phase-locked loop that follows the carrier phase
                           and Doppler. Its discriminator is atan(Q/I), not atan2:
                           the 50 bps nav data flips the carrier 180 deg every 20 ms
                           and atan(Q/I) is blind to that flip.
  * CODE loop (DLL)     -- a delay-locked loop using Early/Prompt/Late correlators
                           (+/-0.5 chip) to keep the prompt replica centred on the
                           code. Carrier-aided: the code rate is slaved to the
                           measured Doppler (the code stretches/compresses with the
                           same line-of-sight dynamics as the carrier).

Once locked, the *prompt* correlator output, sampled once per 1 ms, IS the despread
signal: a real bipolar waveform whose sign carries the navigation bits. Summing it in
aligned 20 ms groups (bit-edge sync) yields the **50 bps** symbol stream.

The loops themselves live in ../bassanite/tracking.py (importable, also used by the
web UI); this script is their command line.

HOW IT CONNECTS TO THE OTHER STAGES (the file is the interface)
---------------------------------------------------------------
  * FROM stage 01: samples come through bassanite/iq_loader.open_iq, which applies
    the same conditioning as stage 02's load_iq but reads the file block by block --
    so a bundled recording and a fresh live `hackrf_transfer` capture are handled
    identically (just pass --iq/--format/--fs), whatever their length.
  * FROM stage 02: the per-PRN seeds (Doppler + code phase) are read from
    results/<recording>/acquisition/scan_results.csv. We seed from
    `code_phase_chips` (sample-rate independent) and convert to samples at our fs.
  * TO stage 04: per PRN we write track_PRN<nn>.npz holding the dense 1 kHz prompt
    stream AND the derived 50 bps symbols, plus a tracking_summary.csv (with
    tracking_summary.meta.json: which recording and settings) and a diagnostic PNG,
    into results/<recording>/tracking/. Stage 04 starts from these at frame sync.

This productionizes the proven prototype in
issues/clock_drift/solution_clean_capture/mini_gps_rx.py (its track()), reusing the
validated Gold-code generator in ../bassanite/ca_code.py instead of a private copy.

Example:
  python 03_tracking/track.py                       # all detected PRNs in capture.bin
  python 03_tracking/track.py --prn 20 --seconds 5  # one PRN, quick window
"""
import argparse
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

from bassanite.provenance import write_meta
from bassanite.results import scan_csv, tracking_dir
from bassanite.tracking import (DEFAULT_DLL_BW, DEFAULT_PLL_BW,
                                MS_PER_BIT, PROMPT_RATE_HZ, SUMMARY_CSV, read_seeds,
                                save_track, summarize, summary_row, track_file_name,
                                track_prn, write_summary)
from bassanite.ca_code import CODE_LENGTH
from bassanite.iq_loader import open_iq


def write_plot(out_dir, prn, fs, track, cn0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ip, qp = track["i_prompt"], track["q_prompt"]
    t = np.arange(len(ip)) / PROMPT_RATE_HZ
    nominal = fs * 1e-3
    code_resid = track["code_phase_samp"] - np.arange(len(ip)) * nominal

    fig, ax = plt.subplots(2, 2, figsize=(11, 7))
    fig.suptitle(f"Stage 03 tracking — PRN {prn}")

    step = max(1, len(ip) // 4000)
    ax[0, 0].scatter(ip[::step], qp[::step], s=2, alpha=0.3)
    ax[0, 0].set_title("Prompt I/Q (locks to the I axis; sign = data bits)")
    ax[0, 0].set_xlabel("I"); ax[0, 0].set_ylabel("Q"); ax[0, 0].axhline(0, lw=0.5)

    show = slice(0, min(len(ip), 2000))           # first 2 s shows the 20 ms flips
    ax[0, 1].plot(t[show], ip[show], lw=0.6)
    ax[0, 1].set_title("Prompt I vs time (20 ms bit flips)")
    ax[0, 1].set_xlabel("s"); ax[0, 1].set_ylabel("I_P")

    ax[1, 0].plot(t, track["doppler_hz"], lw=0.8, label="Doppler")
    ax[1, 0].plot(t, code_resid, lw=0.8, color="tab:orange", label="code-phase resid (samp)")
    ax[1, 0].set_title("Doppler & code-phase drift (smooth = real)")
    ax[1, 0].set_xlabel("s"); ax[1, 0].legend(fontsize=8)

    tb = np.arange(len(cn0)) * (MS_PER_BIT / 1000.0)
    ax[1, 1].plot(tb, cn0, lw=0.8, color="tab:green")
    ax[1, 1].set_title("C/N0 (NWPR)"); ax[1, 1].set_xlabel("s")
    ax[1, 1].set_ylabel("dB-Hz")

    fig.tight_layout(rect=(0, 0, 1, 0.97))
    path = os.path.join(out_dir, f"track_PRN{prn:02d}.png")
    fig.savefig(path, dpi=110)
    plt.close(fig)
    print(f"wrote {path}")


def main():
    ap = argparse.ArgumentParser(
        description="Track acquired GPS PRNs (carrier PLL + code DLL) -> 50 bps.")
    ap.add_argument("--iq", default=os.path.join(_ROOT, "recordings", "capture.bin"),
                    help="raw IQ recording (default: the bundled outdoor capture)")
    ap.add_argument("--format", default="int8",
                    help="int8 (HackRF) or complex64 (gr_complex)")
    ap.add_argument("--fs", type=float, default=4e6, help="sample rate (Hz)")
    ap.add_argument("--seconds", type=float, default=None,
                    help="how much of the file to track (default: whole file)")
    ap.add_argument("--skip-seconds", type=float, default=0.0,
                    help="skip this many seconds from the start")
    ap.add_argument("--from-csv", default=None,
                    help="stage-02 acquisition results to seed from (default: "
                         "results/<recording>/acquisition/scan_results.csv)")
    ap.add_argument("--prn", type=int, default=None,
                    help="track only this PRN (seed from CSV unless --doppler given)")
    ap.add_argument("--prns", default=None,
                    help="track only these detected PRNs, comma-separated (e.g. 5,20,22)")
    ap.add_argument("--doppler", type=float, default=None,
                    help="override Doppler seed (Hz) for --prn")
    ap.add_argument("--code-phase-chips", type=float, default=None,
                    help="override code-phase seed (chips) for --prn")
    ap.add_argument("--all", action="store_true",
                    help="track every PRN in the CSV, not just detected ones")
    ap.add_argument("--pll-bw", type=float, default=DEFAULT_PLL_BW,
                    help="PLL bandwidth (Hz)")
    ap.add_argument("--dll-bw", type=float, default=DEFAULT_DLL_BW,
                    help="DLL bandwidth (Hz)")
    ap.add_argument("--out-dir", default=None,
                    help="where to write outputs (default: results/<recording>/tracking/)")
    ap.add_argument("--no-plot", action="store_true", help="skip diagnostic PNGs")
    ap.add_argument("--progress", action="store_true",
                    help="print 'PROGRESS <k> <n> <prn> <done_ms> <total_ms>' lines "
                         "(for the web UI)")
    a = ap.parse_args()
    if a.from_csv is None:
        a.from_csv = str(scan_csv(a.iq))
    if a.out_dir is None:
        a.out_dir = str(tracking_dir(a.iq))
    os.makedirs(a.out_dir, exist_ok=True)

    # --- assemble the list of (prn, doppler, code-phase) seeds ------------------
    spms = int(round(a.fs * 1e-3))
    if a.prn is not None and a.doppler is not None and a.code_phase_chips is not None:
        seeds = [{"prn": a.prn, "doppler_hz": a.doppler,
                  "code_phase_samp": a.code_phase_chips * spms / CODE_LENGTH,
                  "peak_over_mean": 0.0}]
    else:
        if not os.path.exists(a.from_csv):
            sys.exit(f"no acquisition results at {a.from_csv}; run stage 02 first "
                     f"(python 02_acquisition/scan_prns.py)")
        seeds = read_seeds(a.from_csv, a.fs, only_detected=not a.all)
        if a.prns:
            wanted = {int(p) for p in a.prns.split(",") if p.strip()}
            seeds = [s for s in seeds if s["prn"] in wanted]
        if a.prn is not None:
            seeds = [s for s in seeds if s["prn"] == a.prn]
            if not seeds:
                sys.exit(f"PRN {a.prn} not found in {a.from_csv} "
                         f"(use --doppler/--code-phase-chips to track it anyway)")
    if not seeds:
        sys.exit("no PRNs to track (no detected satellites in the CSV; try --all)")

    # --- open the IQ (same conditioning as stage 02's load_iq). The file is read
    # block by block as the loops advance, so memory does not grow with its length.
    x = open_iq(a.iq, a.format, a.fs, seconds=a.seconds, skip_seconds=a.skip_seconds)
    first = x[:int(0.1 * a.fs)]                            # RMS of the first 0.1 s
    rms = float(np.sqrt(np.mean(np.abs(first) ** 2))) if first.size else 0.0
    print(f"opened {x.size} samples ({x.size / a.fs:.2f}s) from {a.iq}  (RMS={rms:.4f})")
    print(f"tracking {len(seeds)} PRN(s): {[s['prn'] for s in seeds]}  "
          f"(PLL {a.pll_bw:.0f} Hz, DLL {a.dll_bw:.1f} Hz)")

    max_ms = None if a.seconds is None else int(a.seconds * 1000)
    total_ms = int(x.size / a.fs * 1000)
    if max_ms is not None:
        total_ms = min(total_ms, max_ms)
    summary = []
    for k, s in enumerate(seeds, start=1):
        prn = s["prn"]

        def report(done, total, k=k, prn=prn):
            print(f"PROGRESS {k} {len(seeds)} {prn} {done} {total}", flush=True)

        if a.progress:
            report(0, total_ms)
        tr = track_prn(x, a.fs, prn, s["code_phase_samp"], s["doppler_hz"],
                       pll_bw=a.pll_bw, dll_bw=a.dll_bw, max_ms=max_ms, detail=True,
                       progress=report if a.progress else None,
                       refine=not s.get("refined", False))
        r = summarize(tr)
        if r is None:
            print(f"  PRN {prn:>2}: too few epochs ({len(tr['i_prompt'])}), skipped")
            continue
        save_track(os.path.join(a.out_dir, track_file_name(prn)), prn, a.fs, s, tr, r)
        if not a.no_plot:
            write_plot(a.out_dir, prn, a.fs, tr, r["cn0"])
        summary.append(summary_row(prn, tr, r))
        lock_txt = "LOCK" if r["locked"] else "weak"
        print(f"  PRN {prn:>2}: {len(tr['i_prompt']):>5} ms  "
              f"Doppler->{tr['doppler_hz'][-1]:>7.0f} Hz  "
              f"CN0~{r['mean_cn0']:4.1f} dB-Hz  |Q|/|I|={r['qi']:.2f} [{lock_txt}]  "
              f"bit_off={r['bit_offset']}  {len(r['bits'])} bits")

    if summary:
        path = os.path.join(a.out_dir, SUMMARY_CSV)
        write_summary(summary, path)
        print(f"wrote {path}")
        write_meta(path, "tracking", a.iq, a.format, a.fs,
                   {"seconds": a.seconds, "skip_seconds": a.skip_seconds,
                    "pll_bw_hz": a.pll_bw, "dll_bw_hz": a.dll_bw,
                    "from_csv": os.path.abspath(a.from_csv),
                    "prns": [row["prn"] for row in summary]})


if __name__ == "__main__":
    main()
