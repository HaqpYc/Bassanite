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
                           and Doppler. Costas (atan2) because the 50 bps nav data
                           flips the carrier 180 deg every 20 ms and a Costas
                           discriminator is blind to that flip.
  * CODE loop (DLL)     -- a delay-locked loop using Early/Prompt/Late correlators
                           (+/-0.5 chip) to keep the prompt replica centred on the
                           code. Carrier-aided: the code rate is slaved to the
                           measured Doppler (the code stretches/compresses with the
                           same line-of-sight dynamics as the carrier).

Once locked, the *prompt* correlator output, sampled once per 1 ms, IS the despread
signal: a real bipolar waveform whose sign carries the navigation bits. Summing it in
aligned 20 ms groups (bit-edge sync) yields the **50 bps** symbol stream.

HOW IT CONNECTS TO THE OTHER STAGES (the file is the interface)
---------------------------------------------------------------
  * FROM stage 01: samples are loaded only through common/iq_loader.load_iq, exactly
    like stage 02 -- so a bundled recording and a fresh live `hackrf_transfer`
    capture are handled identically (just pass --iq/--format/--fs).
  * FROM stage 02: the per-PRN seeds (Doppler + code phase) are read from
    02_acquisition/scan_results.csv. We seed from `code_phase_chips` (sample-rate
    independent) and convert to samples at our own fs.
  * TO stage 04: per PRN we write track_PRN<nn>.npz holding the dense 1 kHz prompt
    stream AND the derived 50 bps symbols, plus a tracking_summary.csv and a
    diagnostic PNG. Stage 04 starts from these at frame/preamble sync.

This productionizes the proven prototype in
issues/clock_drift/solution_clean_capture/mini_gps_rx.py (its track()), reusing the
validated Gold-code generator in ../common/ca_code.py instead of a private copy.

Example:
  python 03_tracking/track.py                       # all detected PRNs in capture.bin
  python 03_tracking/track.py --prn 20 --seconds 5  # one PRN, quick window
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

from common.ca_code import ca_code, CODE_LENGTH, CHIP_RATE     # noqa: E402
from common.iq_loader import load_iq                           # noqa: E402

L1_HZ = 1575.42e6                 # GPS L1 carrier (for carrier-aiding the code loop)
MS_PER_BIT = 20                   # 20 ms per 50 bps navigation bit
PROMPT_RATE_HZ = 1000             # one prompt sample per 1 ms code period


def calc_loop_coef(bw, zeta, k):
    """2nd-order loop-filter coefficients (tau1, tau2) for a given noise bandwidth.

    The standard textbook design (e.g. Kaplan; Borre's SoftGNSS): pick the natural
    frequency from the desired loop bandwidth `bw`, damping `zeta`, gain `k`.
    """
    wn = bw * 8.0 * zeta / (4.0 * zeta ** 2 + 1.0)
    tau1 = k / (wn * wn)
    tau2 = 2.0 * zeta / wn
    return tau1, tau2


def fine_doppler(x, fs, ca, code_phase_samp, dop_center, span=200.0, step=25.0,
                 nms=10):
    """Refine the acquisition Doppler to within ~step/2 Hz before closing the loop.

    Acquisition bins are coarse (250 Hz), so the residual carrier error can exceed a
    narrow PLL's pull-in range. We correlate the prompt replica over the first few
    ms at the seed code phase across a fine Doppler grid (non-coherent across ms, so
    the 20 ms data flips don't matter) and return the best Doppler.
    """
    spms = int(round(fs * 1e-3))
    n0 = int(code_phase_samp)
    cph = np.arange(spms) * CHIP_RATE / fs
    prompt = ca[(np.floor(cph).astype(int)) % CODE_LENGTH]
    n = np.arange(spms)
    best_dop, best_pow = float(dop_center), -1.0
    for dop in np.arange(dop_center - span, dop_center + span + 1, step):
        wipe = np.exp(-1j * 2 * np.pi * dop * n / fs)
        acc = 0.0
        for k in range(nms):
            seg = x[n0 + k * spms:n0 + (k + 1) * spms]
            if len(seg) < spms:
                break
            acc += abs((seg * wipe) @ prompt)
        if acc > best_pow:
            best_pow, best_dop = acc, float(dop)
    return best_dop


def track_prn(x, fs, prn, code_phase_samp, doppler_hz,
              pll_bw=12.0, dll_bw=1.0, max_ms=None):
    """Track one PRN with a Costas PLL + carrier-aided DLL.

    Runs one iteration per 1 ms code period. Returns a dict of per-ms arrays
    (i_prompt, q_prompt, doppler_hz, code_phase_samp) truncated to the number of
    epochs actually tracked.

    Parameters
    ----------
    code_phase_samp : float   acquisition code phase, in samples at this fs
    doppler_hz      : float   acquisition Doppler estimate (Hz)
    pll_bw          : float   carrier (PLL) loop noise bandwidth (Hz)
    dll_bw          : float   code (DLL) loop noise bandwidth (Hz); 0 freezes the
                              code rate (carrier-aiding only)
    max_ms          : int     cap on epochs (None = until the samples run out)
    """
    ca = ca_code(prn)                                  # +/-1 replica, length 1023
    spms_nominal = fs * 1e-3
    n_ms = int(len(x) / spms_nominal) + 1
    if max_ms is not None:
        n_ms = min(n_ms, max_ms)

    PDI = 1e-3                                          # integration time per epoch
    SPACING = 0.5                                       # Early/Late spacing (chips)
    # Loop-filter coefficients (Kaplan/Borre damping 0.7).
    tau1_c, tau2_c = calc_loop_coef(pll_bw, 0.7, 0.25)
    use_dll = dll_bw > 0.0
    tau1_d, tau2_d = calc_loop_coef(dll_bw, 0.7, 1.0) if use_dll else (1.0, 0.0)

    # Sharpen the coarse acquisition Doppler so the PLL can pull in.
    carr_freq_basis = fine_doppler(x, fs, ca, code_phase_samp, doppler_hz)
    carr_f = carr_freq_basis              # current carrier-Doppler estimate (Hz)
    carr_phase = 0.0                      # carrier NCO phase carried across epochs
    carr_nco = 0.0; old_carr_err = 0.0    # PLL filter state
    code_nco = 0.0; old_code_err = 0.0    # DLL filter state (chips/s correction)

    i_prompt = np.zeros(n_ms, dtype=np.float32)
    q_prompt = np.zeros(n_ms, dtype=np.float32)
    dopp = np.zeros(n_ms, dtype=np.float32)
    code_start = np.zeros(n_ms, dtype=np.float64)

    # Code phase is tracked as an integer sample pointer (n0) plus a fractional
    # leftover in CHIPS (rem_code_phase). Each epoch we process exactly enough
    # samples to finish one 1023-chip period -- so n0 stays integer (no fractional
    # sample-start error) and no per-period rounding can accumulate into a drift.
    n0 = int(round(code_phase_samp))
    rem_code_phase = 0.0
    m = 0
    while m < n_ms:
        # Code NCO: nominal chip rate, carrier-aided, trimmed by the DLL.
        code_f = CHIP_RATE * (1.0 + carr_f / L1_HZ) - code_nco
        code_step = code_f / fs                         # chips advanced per sample
        blksize = int(np.ceil((CODE_LENGTH - rem_code_phase) / code_step))
        if n0 + blksize + 2 >= len(x):
            break

        n = np.arange(blksize)
        # Carrier wipe-off (phase continuous across epochs).
        bb = x[n0:n0 + blksize] * np.exp(-1j * (2 * np.pi * carr_f * n / fs + carr_phase))
        # Code phase (chips) at each sample; Early/Prompt/Late at -/0/+0.5 chip.
        tcode = rem_code_phase + n * code_step
        early = ca[(np.floor(tcode - SPACING).astype(int)) % CODE_LENGTH]
        prompt = ca[(np.floor(tcode).astype(int)) % CODE_LENGTH]
        late = ca[(np.floor(tcode + SPACING).astype(int)) % CODE_LENGTH]
        ie, qe = np.real(bb @ early), np.imag(bb @ early)
        ip, qp = np.real(bb @ prompt), np.imag(bb @ prompt)
        il, ql = np.real(bb @ late), np.imag(bb @ late)

        i_prompt[m] = ip
        q_prompt[m] = qp
        dopp[m] = carr_f
        code_start[m] = n0 - rem_code_phase / code_step   # fractional code-start sample

        # Advance the carrier NCO phase with the frequency ACTUALLY used to wipe
        # off this block (before the loop filter changes it) -- otherwise the phase
        # reference and the wipe-off disagree, which spins the prompt phasor.
        carr_phase = (carr_phase + 2 * np.pi * carr_f * blksize / fs) % (2 * np.pi)

        # Carrier loop: Costas atan(Q/I) discriminator (cycles) -- blind to the
        # 180 deg flips of the 50 bps data, unlike atan2. Updates carr_f for next.
        ip_safe = ip if abs(ip) > 1e-12 else 1e-12
        carr_err = np.arctan(qp / ip_safe) / (2 * np.pi)
        carr_nco += (tau2_c / tau1_c) * (carr_err - old_carr_err) + carr_err * (PDI / tau1_c)
        old_carr_err = carr_err
        carr_f = carr_freq_basis + carr_nco

        # Code loop: normalized early-minus-late power discriminator (chips).
        if use_dll:
            e_mag, l_mag = np.hypot(ie, qe), np.hypot(il, ql)
            code_err = (e_mag - l_mag) / (e_mag + l_mag + 1e-12)
            code_nco += ((tau2_d / tau1_d) * (code_err - old_code_err)
                         + code_err * (PDI / tau1_d))
            old_code_err = code_err

        # Carry the fractional chip overshoot into the next period; step n0.
        rem_code_phase += blksize * code_step - CODE_LENGTH
        n0 += blksize
        m += 1

    return {
        "i_prompt": i_prompt[:m],
        "q_prompt": q_prompt[:m],
        "doppler_hz": dopp[:m],
        "code_phase_samp": code_start[:m],
    }


def bit_sync(i_prompt):
    """Find the 20 ms navigation-bit boundary and demodulate the 50 bps symbols.

    The prompt-I stream is at 1 kHz; each nav bit spans exactly 20 ms. We don't know
    which of the 20 possible 1 ms offsets starts a bit, so we pick the offset whose
    aligned 20 ms sums have the most energy (a misaligned grouping straddles sign
    flips and partially cancels). Frame/preamble sync is left to stage 04.

    Returns (bit_offset, nav_symbols_soft, nav_bits_01).
    """
    best_off, best_score = 0, -1.0
    for off in range(MS_PER_BIT):
        usable = (len(i_prompt) - off) // MS_PER_BIT * MS_PER_BIT
        if usable <= 0:
            continue
        seg = i_prompt[off:off + usable].reshape(-1, MS_PER_BIT)
        score = float(np.mean(np.abs(seg.sum(axis=1))))
        if score > best_score:
            best_score, best_off = score, off

    usable = (len(i_prompt) - best_off) // MS_PER_BIT * MS_PER_BIT
    seg = i_prompt[best_off:best_off + usable].reshape(-1, MS_PER_BIT)
    symbols = seg.sum(axis=1).astype(np.float32)        # soft 20 ms sums
    bits = (symbols > 0).astype(np.int8)                # hard 0/1
    return best_off, symbols, bits


def estimate_cn0(i_prompt, q_prompt, block_ms=MS_PER_BIT, offset=0):
    """Carrier-to-noise density via the Narrowband/Wideband Power Ratio (NWPR).

    Returns (cn0_dbhz_per_block, lock_cos_per_block). One value per `block_ms`
    block. Lock indicator is the carrier-lock cosine |sum I| / |sum I+jQ| in [0,1].
    `offset` aligns the blocks to the bit boundary so a block never straddles a
    180 deg data flip (which would cancel the narrowband power).
    """
    i_prompt, q_prompt = i_prompt[offset:], q_prompt[offset:]
    n = len(i_prompt) // block_ms * block_ms
    if n == 0:
        return np.zeros(0, np.float32), np.zeros(0, np.float32)
    ib = i_prompt[:n].reshape(-1, block_ms)
    qb = q_prompt[:n].reshape(-1, block_ms)
    i_sum, q_sum = ib.sum(axis=1), qb.sum(axis=1)
    nbp = i_sum ** 2 + q_sum ** 2                       # narrowband power
    wbp = (ib ** 2 + qb ** 2).sum(axis=1)               # wideband power
    np_ratio = np.clip(nbp / (wbp + 1e-12), 1.0 + 1e-6, block_ms - 1e-6)
    # NWPR estimate; T = 1 ms is each accumulation's integration time.
    cn0 = 10.0 * np.log10((1.0 / 1e-3) * (np_ratio - 1.0) / (block_ms - np_ratio))
    lock_cos = np.abs(i_sum) / (np.hypot(i_sum, q_sum) + 1e-12)
    return cn0.astype(np.float32), lock_cos.astype(np.float32)


def read_seeds(csv_path, fs, only_detected=True):
    """Read stage-02 acquisition results as tracking seeds.

    Converts the sample-rate-independent `code_phase_chips` to samples at this fs.
    Returns a list of dicts {prn, doppler_hz, code_phase_samp, peak_over_mean}.
    """
    spms = int(round(fs * 1e-3))
    seeds = []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            detected = str(row.get("detected", "")).strip().lower() == "true"
            if only_detected and not detected:
                continue
            chips = float(row["code_phase_chips"])
            seeds.append({
                "prn": int(row["prn"]),
                "doppler_hz": float(row["doppler_hz"]),
                "code_phase_samp": chips * spms / CODE_LENGTH,
                "peak_over_mean": float(row.get("peak_over_mean", 0.0)),
            })
    seeds.sort(key=lambda s: s["prn"])
    return seeds


def save_npz(out_dir, prn, fs, seed, track, bit_offset, symbols, bits, cn0, lock):
    path = os.path.join(out_dir, f"track_PRN{prn:02d}.npz")
    np.savez(
        path,
        # dense 1 kHz streams
        i_prompt=track["i_prompt"], q_prompt=track["q_prompt"],
        doppler_hz=track["doppler_hz"], code_phase_samp=track["code_phase_samp"],
        # derived 50 bps stream
        nav_symbols=symbols, nav_bits=bits,
        # 50 Hz block-rate quality
        cn0_dbhz=cn0, lock_cos=lock,
        # scalars / metadata
        prn=prn, fs=fs, prompt_rate_hz=PROMPT_RATE_HZ, ms_per_bit=MS_PER_BIT,
        bit_offset=bit_offset, seconds_tracked=len(track["i_prompt"]) / PROMPT_RATE_HZ,
        doppler_seed_hz=seed["doppler_hz"],
        code_phase_samp_seed=seed["code_phase_samp"],
    )
    return path


def write_summary_csv(rows, path):
    fields = ["prn", "ms_tracked", "final_doppler_hz", "mean_cn0_dbhz",
              "pll_lock_qi", "bit_offset", "nbits"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path}")


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
    ap.add_argument("--from-csv",
                    default=os.path.join(_ROOT, "02_acquisition", "scan_results.csv"),
                    help="stage-02 acquisition results to seed from")
    ap.add_argument("--prn", type=int, default=None,
                    help="track only this PRN (seed from CSV unless --doppler given)")
    ap.add_argument("--doppler", type=float, default=None,
                    help="override Doppler seed (Hz) for --prn")
    ap.add_argument("--code-phase-chips", type=float, default=None,
                    help="override code-phase seed (chips) for --prn")
    ap.add_argument("--all", action="store_true",
                    help="track every PRN in the CSV, not just detected ones")
    ap.add_argument("--pll-bw", type=float, default=12.0, help="PLL bandwidth (Hz)")
    ap.add_argument("--dll-bw", type=float, default=1.0, help="DLL bandwidth (Hz)")
    ap.add_argument("--out-dir", default=_HERE, help="where to write outputs")
    ap.add_argument("--no-plot", action="store_true", help="skip diagnostic PNGs")
    a = ap.parse_args()

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
        if a.prn is not None:
            seeds = [s for s in seeds if s["prn"] == a.prn]
            if not seeds:
                sys.exit(f"PRN {a.prn} not found in {a.from_csv} "
                         f"(use --doppler/--code-phase-chips to track it anyway)")
    if not seeds:
        sys.exit("no PRNs to track (no detected satellites in the CSV; try --all)")

    # --- load IQ once (same loader as stage 02; live or recording, it's a file) -
    x = load_iq(a.iq, a.format, a.fs, seconds=a.seconds, skip_seconds=a.skip_seconds)
    rms = float(np.sqrt(np.mean(np.abs(x) ** 2))) if x.size else 0.0
    print(f"loaded {x.size} samples ({x.size / a.fs:.2f}s) from {a.iq}  (RMS={rms:.4f})")
    print(f"tracking {len(seeds)} PRN(s): {[s['prn'] for s in seeds]}  "
          f"(PLL {a.pll_bw:.0f} Hz, DLL {a.dll_bw:.1f} Hz)")

    max_ms = None if a.seconds is None else int(a.seconds * 1000)
    summary = []
    for s in seeds:
        prn = s["prn"]
        tr = track_prn(x, a.fs, prn, s["code_phase_samp"], s["doppler_hz"],
                       pll_bw=a.pll_bw, dll_bw=a.dll_bw, max_ms=max_ms)
        ip, qp = tr["i_prompt"], tr["q_prompt"]
        if len(ip) < MS_PER_BIT:
            print(f"  PRN {prn:>2}: too few epochs ({len(ip)}), skipped")
            continue
        bit_off, symbols, bits = bit_sync(ip)
        cn0, lock = estimate_cn0(ip, qp, offset=bit_off)
        qi = float(np.median(np.abs(qp)) / (np.median(np.abs(ip)) + 1e-9))
        mean_cn0 = float(np.median(cn0)) if cn0.size else float("nan")
        save_npz(a.out_dir, prn, a.fs, s, tr, bit_off, symbols, bits, cn0, lock)
        if not a.no_plot:
            write_plot(a.out_dir, prn, a.fs, tr, cn0)
        summary.append({
            "prn": prn, "ms_tracked": len(ip),
            "final_doppler_hz": round(float(tr["doppler_hz"][-1]), 1),
            "mean_cn0_dbhz": round(mean_cn0, 1), "pll_lock_qi": round(qi, 3),
            "bit_offset": bit_off, "nbits": len(bits),
        })
        lock_txt = "LOCK" if qi < 0.3 else "weak"
        print(f"  PRN {prn:>2}: {len(ip):>5} ms  Doppler->{tr['doppler_hz'][-1]:>7.0f} Hz  "
              f"CN0~{mean_cn0:4.1f} dB-Hz  |Q|/|I|={qi:.2f} [{lock_txt}]  "
              f"bit_off={bit_off}  {len(bits)} bits")

    if summary:
        write_summary_csv(summary, os.path.join(a.out_dir, "tracking_summary.csv"))


if __name__ == "__main__":
    main()
