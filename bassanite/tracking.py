"""
tracking.py -- stage 03: lock onto each acquired satellite and follow it.

Acquisition gives a coarse (PRN, code phase, Doppler). Tracking keeps the local
replica aligned with two feedback loops, run once per 1 ms code period:

  * CARRIER loop (Costas PLL): follows carrier phase and Doppler. The
    discriminator is atan(Q/I), which is blind to the 180 deg flips the 50 bps
    navigation data puts on the carrier (atan2 would chase every bit edge).
  * CODE loop (DLL): Early/Prompt/Late correlators at +/-0.5 chip and the
    normalised (|E|-|L|)/(|E|+|L|) discriminator keep the prompt replica
    centred. Carrier-aided: the code rate follows the measured Doppler.

Once locked, the prompt correlator sampled every 1 ms is the despread signal;
its sign, summed over aligned 20 ms groups, gives the navigation bits.
See 03_tracking/NOTES.md for the full explanation and what made the loops lock.
"""
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bassanite.acquisition import read_results  # noqa: E402
from common.ca_code import CHIP_RATE, CODE_LENGTH, ca_code  # noqa: E402

L1_HZ = 1575.42e6                 # GPS L1 carrier (for carrier-aiding the code loop)
MS_PER_BIT = 20                   # 20 ms per 50 bps navigation bit
PROMPT_RATE_HZ = 1000             # one prompt sample per 1 ms code period
LOCK_QI_MAX = 0.3                 # |Q|/|I| below this = carrier phase-locked
DEFAULT_PLL_BW = 12.0
DEFAULT_DLL_BW = 1.0
PROGRESS_EVERY_MS = 1000          # how often track_prn reports progress
# Shortest signal that holds subframes 1, 2 and 3 (orbit and clock) each complete at
# least once, whatever the phase of the 30 s frame. A subframe is 300 bits (6 s). In
# the worst case the recording starts just after subframe 1 began, so subframe 1 is
# caught in the next frame: 30 s to its start + 6 s. (A 30 s recording, 1499 bits,
# never holds five complete subframes.) Pull-in time comes on top.
MIN_SECONDS_FOR_EPHEMERIS = 36.0
# Extra per-ms arrays track_prn(detail=True) returns (the loops' internals).
DETAIL_KEYS = ("i_early", "q_early", "i_late", "q_late", "carr_err", "code_err")


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


def costas_error(ip, qp):
    """Carrier-phase error in cycles from the prompt correlator (Costas).

    atan(Q/I), not atan2(Q, I): a 180 deg data flip turns (I, Q) into (-I, -Q),
    which leaves Q/I and so the error unchanged. The loop ignores the data bits.
    """
    ip_safe = ip if abs(ip) > 1e-12 else 1e-12
    return np.arctan(qp / ip_safe) / (2 * np.pi)


def dll_error(ie, qe, il, ql):
    """Code-phase error in chips from the Early and Late correlators.

    Normalised early-minus-late envelope: 0 when early and late are equally
    strong (prompt centred on the peak). The Early replica reads the code half a
    chip behind the prompt, so it matches best when our replica runs ahead of the
    incoming code: a positive error means "replica ahead", and track_prn slows
    the code NCO down (code_f = ... - code_nco).
    """
    e_mag, l_mag = np.hypot(ie, qe), np.hypot(il, ql)
    return (e_mag - l_mag) / (e_mag + l_mag + 1e-12)


def track_prn(x, fs, prn, code_phase_samp, doppler_hz,
              pll_bw=DEFAULT_PLL_BW, dll_bw=DEFAULT_DLL_BW, max_ms=None,
              detail=False, progress=None):
    """Track one PRN with a Costas PLL + carrier-aided DLL.

    Runs one iteration per 1 ms code period. Returns a dict of per-ms arrays
    (i_prompt, q_prompt, doppler_hz, code_phase_samp) truncated to the number of
    epochs actually tracked.

    `x` is the complex baseband signal: an array, or an IQReader
    (common/iq_loader.open_iq), since the loop only slices it and takes len().

    Parameters
    ----------
    code_phase_samp : float   acquisition code phase, in samples at this fs
    doppler_hz      : float   acquisition Doppler estimate (Hz)
    pll_bw          : float   carrier (PLL) loop noise bandwidth (Hz)
    dll_bw          : float   code (DLL) loop noise bandwidth (Hz); 0 freezes the
                              code rate (carrier-aiding only)
    max_ms          : int     cap on epochs (None = until the samples run out)
    detail          : bool    also return the Early/Late correlators and both
                              discriminator outputs per ms (for plots)
    progress        : callable(done_ms, total_ms), called every second of signal
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
    if detail:
        el = {k: np.zeros(n_ms, dtype=np.float32) for k in DETAIL_KEYS}

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
        carr_err = costas_error(ip, qp)
        carr_nco += (tau2_c / tau1_c) * (carr_err - old_carr_err) + carr_err * (PDI / tau1_c)
        old_carr_err = carr_err
        carr_f = carr_freq_basis + carr_nco

        # Code loop: normalized early-minus-late power discriminator (chips).
        code_err = 0.0
        if use_dll:
            code_err = dll_error(ie, qe, il, ql)
            code_nco += ((tau2_d / tau1_d) * (code_err - old_code_err)
                         + code_err * (PDI / tau1_d))
            old_code_err = code_err

        if detail:
            el["i_early"][m], el["q_early"][m] = ie, qe
            el["i_late"][m], el["q_late"][m] = il, ql
            el["carr_err"][m], el["code_err"][m] = carr_err, code_err

        # Carry the fractional chip overshoot into the next period; step n0.
        rem_code_phase += blksize * code_step - CODE_LENGTH
        n0 += blksize
        m += 1
        if progress is not None and m % PROGRESS_EVERY_MS == 0:
            progress(m, n_ms)

    out = {
        "i_prompt": i_prompt[:m],
        "q_prompt": q_prompt[:m],
        "doppler_hz": dopp[:m],
        "code_phase_samp": code_start[:m],
    }
    if detail:
        out.update({k: v[:m] for k, v in el.items()})
    return out


def bit_sync_scores(i_prompt):
    """Energy of the 20 ms sums for each of the 20 possible bit-edge offsets.

    Score of offset o = mean |sum of 20 prompt values| over the bits that start
    at o, 20 + o, 40 + o, ... A grouping aligned with the real bit edges never
    straddles a sign flip, so its sums are largest. -1 where no full bit fits.
    """
    scores = np.full(MS_PER_BIT, -1.0)
    for off in range(MS_PER_BIT):
        usable = (len(i_prompt) - off) // MS_PER_BIT * MS_PER_BIT
        if usable <= 0:
            continue
        seg = i_prompt[off:off + usable].reshape(-1, MS_PER_BIT)
        scores[off] = float(np.mean(np.abs(seg.sum(axis=1))))
    return scores


def bit_sync(i_prompt):
    """Find the 20 ms navigation-bit boundary and demodulate the 50 bps symbols.

    The prompt-I stream is at 1 kHz; each nav bit spans exactly 20 ms. We don't know
    which of the 20 possible 1 ms offsets starts a bit, so we pick the offset whose
    aligned 20 ms sums have the most energy (a misaligned grouping straddles sign
    flips and partially cancels). Frame/preamble sync is left to stage 04.

    Returns (bit_offset, nav_symbols_soft, nav_bits_01).
    """
    best_off = int(np.argmax(bit_sync_scores(i_prompt)))   # first best, as before

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


def summarize(track):
    """Bits and quality figures for one tracked PRN (a track_prn() result).

    Returns None when fewer than one bit's worth of epochs was tracked, else a
    dict: bit_offset, symbols, bits, cn0 and lock_cos (per 20 ms), qi (median
    |Q|/|I|), mean_cn0 (median C/N0, dB-Hz) and locked (qi < LOCK_QI_MAX).
    """
    ip, qp = track["i_prompt"], track["q_prompt"]
    if len(ip) < MS_PER_BIT:
        return None
    bit_off, symbols, bits = bit_sync(ip)
    cn0, lock = estimate_cn0(ip, qp, offset=bit_off)
    qi = float(np.median(np.abs(qp)) / (np.median(np.abs(ip)) + 1e-9))
    return {"bit_offset": bit_off, "symbols": symbols, "bits": bits,
            "cn0": cn0, "lock_cos": lock, "qi": qi,
            "mean_cn0": float(np.median(cn0)) if cn0.size else float("nan"),
            "locked": qi < LOCK_QI_MAX}


def read_seeds(csv_path, fs, only_detected=True):
    """Read stage-02 acquisition results as tracking seeds.

    Converts the sample-rate-independent `code_phase_chips` to samples at this fs.
    Returns a list of dicts {prn, doppler_hz, code_phase_samp, peak_over_mean}.
    """
    spms = int(round(fs * 1e-3))
    seeds = [{"prn": r["prn"], "doppler_hz": r["doppler_hz"],
              "code_phase_samp": r["code_phase_chips"] * spms / CODE_LENGTH,
              "peak_over_mean": r["peak_over_mean"]}
             for r in read_results(csv_path) if r["detected"] or not only_detected]
    seeds.sort(key=lambda s: s["prn"])
    return seeds


# --- result files: the hand-over to stage 04 (written and read only here) -----------
#
#   track_PRNnn.npz        one per satellite: the 1 kHz loop outputs, the 50 bps bits
#                          and the 50 Hz quality figures (see save_track)
#   tracking_summary.csv   one row per satellite (SUMMARY_FIELDS)

SUMMARY_CSV = "tracking_summary.csv"
SUMMARY_FIELDS = ("prn", "ms_tracked", "final_doppler_hz", "mean_cn0_dbhz",
                  "pll_lock_qi", "bit_offset", "nbits")


def track_file_name(prn):
    return f"track_PRN{prn:02d}.npz"


def save_track(path, prn, fs, seed, track, result):
    """Write one satellite's track_prn() output and its summarize() result."""
    np.savez(
        path,
        # dense 1 kHz streams
        i_prompt=track["i_prompt"], q_prompt=track["q_prompt"],
        doppler_hz=track["doppler_hz"], code_phase_samp=track["code_phase_samp"],
        # loop internals at 1 kHz: Early/Late correlators and both discriminators
        **{k: track[k] for k in DETAIL_KEYS if k in track},
        # derived 50 bps stream
        nav_symbols=result["symbols"], nav_bits=result["bits"],
        # 50 Hz block-rate quality
        cn0_dbhz=result["cn0"], lock_cos=result["lock_cos"],
        # scalars / metadata
        prn=prn, fs=fs, prompt_rate_hz=PROMPT_RATE_HZ, ms_per_bit=MS_PER_BIT,
        bit_offset=result["bit_offset"],
        seconds_tracked=len(track["i_prompt"]) / PROMPT_RATE_HZ,
        doppler_seed_hz=seed["doppler_hz"],
        code_phase_samp_seed=seed["code_phase_samp"],
    )
    return path


def load_track(path):
    """Read a track_PRNnn.npz back. Returns a dict: the 1 kHz arrays (i_prompt,
    q_prompt, doppler_hz, code_phase_samp), nav_bits, cn0_dbhz, the scalars prn,
    fs, bit_offset, doppler_seed_hz, code_phase_samp_seed, and `detail` (the
    DETAIL_KEYS arrays, or None for files saved without them)."""
    with np.load(path) as d:
        out = {k: d[k] for k in ("i_prompt", "q_prompt", "doppler_hz", "code_phase_samp",
                                 "nav_bits", "cn0_dbhz")}
        out.update(prn=int(d["prn"]), fs=float(d["fs"]), bit_offset=int(d["bit_offset"]),
                   doppler_seed_hz=float(d["doppler_seed_hz"]),
                   code_phase_samp_seed=float(d["code_phase_samp_seed"]))
        out["detail"] = ({k: d[k] for k in DETAIL_KEYS}
                         if all(k in d.files for k in DETAIL_KEYS) else None)
    return out


def summary_row(prn, track, result):
    """The tracking_summary.csv row for one satellite."""
    return {"prn": prn, "ms_tracked": len(track["i_prompt"]),
            "final_doppler_hz": round(float(track["doppler_hz"][-1]), 1),
            "mean_cn0_dbhz": round(result["mean_cn0"], 1),
            "pll_lock_qi": round(result["qi"], 3),
            "bit_offset": result["bit_offset"], "nbits": len(result["bits"])}


def write_summary(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(SUMMARY_FIELDS))
        w.writeheader()
        w.writerows({k: r[k] for k in SUMMARY_FIELDS} for r in rows)


def read_summary(path):
    """Read tracking_summary.csv. Each row also gets `locked` (|Q|/|I| test)."""
    with open(path, newline="") as f:
        rows = [{"prn": int(r["prn"]), "ms_tracked": int(r["ms_tracked"]),
                 "final_doppler_hz": float(r["final_doppler_hz"]),
                 "mean_cn0_dbhz": float(r["mean_cn0_dbhz"]),
                 "pll_lock_qi": float(r["pll_lock_qi"]),
                 "bit_offset": int(r["bit_offset"]), "nbits": int(r["nbits"])}
                for r in csv.DictReader(f)]
    for r in rows:
        r["locked"] = r["pll_lock_qi"] < LOCK_QI_MAX
    return rows
