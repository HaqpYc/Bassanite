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
from common.ca_code import CHIP_RATE, CODE_LENGTH, ca_code  # noqa: E402

L1_HZ = 1575.42e6                 # GPS L1 carrier (for carrier-aiding the code loop)
MS_PER_BIT = 20                   # 20 ms per 50 bps navigation bit
PROMPT_RATE_HZ = 1000             # one prompt sample per 1 ms code period
LOCK_QI_MAX = 0.3                 # |Q|/|I| below this = carrier phase-locked
DEFAULT_PLL_BW = 12.0
DEFAULT_DLL_BW = 1.0
PROGRESS_EVERY_MS = 1000          # how often track_prn reports progress


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
              pll_bw=DEFAULT_PLL_BW, dll_bw=DEFAULT_DLL_BW, max_ms=None,
              detail=False, progress=None):
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
        el = {k: np.zeros(n_ms, dtype=np.float32)
              for k in ("i_early", "q_early", "i_late", "q_late", "carr_err", "code_err")}

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
        code_err = 0.0
        if use_dll:
            e_mag, l_mag = np.hypot(ie, qe), np.hypot(il, ql)
            code_err = (e_mag - l_mag) / (e_mag + l_mag + 1e-12)
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
