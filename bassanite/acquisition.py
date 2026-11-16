"""
acquisition.py -- stage 02: find which GPS satellites are in a recording.

For each PRN we search two unknowns: the CODE PHASE (where the satellite's 1 ms
code starts inside our 1 ms window, 0..1022 chips) and the DOPPLER (carrier
offset from satellite motion and the receiver's clock error). For each Doppler
bin we wipe off the carrier, correlate against the local code at every code
phase at once with the FFT, and non-coherently accumulate |corr|^2 over a few
ms. A cell far above the average means the satellite is there.

    corr = IFFT( FFT(signal_1ms * exp(-j2*pi*f*t)) * conj(FFT(code_1ms)) )

The detection metric is peak/mean of the accumulated surface (pure noise gives
~3 for 10 ms at 4 MSPS; the default threshold is 5). See
02_acquisition/NOTES.md for the full explanation and the verification method.
"""
import csv

import numpy as np

from bassanite.ca_code import upsample_code
from bassanite.constants import CHIP_RATE, CODE_LENGTH, L1_HZ  # noqa: F401  (L1_HZ re-exported)
from bassanite.iq_loader import load_iq, open_iq

DEFAULT_DOP_RANGE_HZ = 10000.0
DEFAULT_DOP_STEP_HZ = 250.0
DEFAULT_MS = 10
DEFAULT_THRESHOLD = 5.0
FINE_STEP_HZ = 10.0       # grid of the refined Doppler handed to tracking
# Signal the refinement sums over. Measured on capture.bin against the locked loops'
# Doppler: 10 ms misses by up to 74 Hz on a weak satellite (PRN 15, peak/mean 6.3),
# 40 ms by at most 29 Hz, which a 12 Hz carrier loop pulls in at once.
FINE_MS = 40

# scan_results.csv, the file stage 03 reads its seeds from, and its columns.
SCAN_CSV = "scan_results.csv"
# fine_doppler_hz is empty for undetected PRNs (and absent from older files).
CSV_FIELDS = ("prn", "doppler_hz", "fine_doppler_hz", "code_phase_samp",
              "code_phase_chips", "peak_over_mean", "detected")


def correlation_surface(x, fs, prn, dopplers, nms):
    """Accumulated correlation power for every (Doppler, code phase) cell.

    Returns an array of shape (len(dopplers), samples_per_ms): row i is the
    non-coherent sum over `nms` 1 ms periods of |corr|^2 at Doppler dopplers[i],
    for every code phase (in samples).
    """
    spms = int(round(fs * 1e-3))                  # samples per 1 ms code period
    code_conj = np.conj(np.fft.fft(upsample_code(prn, spms).astype(np.float64)))
    t = np.arange(spms) / fs
    surface = np.zeros((len(dopplers), spms))
    for i, dop in enumerate(dopplers):
        carrier = np.exp(-1j * 2 * np.pi * dop * t)    # carrier wipe-off for this bin
        for k in range(nms):
            seg = x[k * spms:(k + 1) * spms]
            if len(seg) < spms:
                break
            corr = np.fft.ifft(np.fft.fft(seg * carrier) * code_conj)
            surface[i] += np.abs(corr) ** 2              # non-coherent accumulation
    return surface


def doppler_bins(dop_min, dop_max, dop_step):
    return np.arange(dop_min, dop_max + 1, dop_step)


def acquire_prn(x, fs, prn, dop_min, dop_max, dop_step, nms, return_surface=False):
    """FFT parallel-code-phase acquisition of one PRN.

    Each Doppler bin is scored by peak/mean over its code phases; the best bin
    gives the estimates. Returns (code_phase_samples, doppler_hz, peak_over_mean),
    plus (dopplers, surface) when `return_surface` is True.
    """
    dopplers = doppler_bins(dop_min, dop_max, dop_step)
    surface = correlation_surface(x, fs, prn, dopplers, nms)
    best = (0, 0.0, 0.0)
    for i, dop in enumerate(dopplers):
        acc = surface[i]
        peak, mean = acc.max(), acc.mean()
        ratio = peak / mean if mean > 0 else 0.0
        if ratio > best[2]:
            best = (int(acc.argmax()), float(dop), float(ratio))
    return (*best, dopplers, surface) if return_surface else best


def refine_doppler(x, fs, prn, doppler, code_phase, nms, span_hz, step_hz=FINE_STEP_HZ):
    """A finer Doppler estimate than the search grid: within +/-span_hz of
    `doppler`, the frequency where the correlation at `code_phase` (samples, +/-1)
    is strongest, summed non-coherently over `nms` ms (so data bit flips do not
    matter). Acquisition hands this to tracking: a narrow carrier loop cannot pull
    in the error of up to half a search bin on its own."""
    dops = np.arange(doppler - span_hz, doppler + span_hz + step_hz / 2, step_hz)
    spms = int(round(fs * 1e-3))
    nms_used = min(nms, len(x) // spms)
    # Only 3 code phases matter, so skip the FFTs of a full surface: correlate each
    # ms with the 3 shifted codes directly, all Dopplers in one matrix product.
    segs = np.asarray(x[:nms_used * spms]).reshape(nms_used, spms)
    code = upsample_code(prn, spms).astype(np.float64)
    cols = (int(round(code_phase)) + np.arange(-1, 2)) % spms
    rows = np.concatenate([segs * np.roll(code, c) for c in cols])   # (3 * nms_used, spms)
    carriers = np.exp(-2j * np.pi * (np.arange(spms) / fs)[:, None] * dops[None, :])
    power = np.abs(rows @ carriers) ** 2                             # (3 * nms_used, n_dops)
    power = power.reshape(len(cols), nms_used, len(dops)).sum(axis=1)  # sum over the ms
    return float(dops[power.max(axis=0).argmax()])


def scan(x, fs, dop_min, dop_max, dop_step, nms, threshold, prns=range(1, 33),
         progress=None):
    """Acquire every PRN in `prns`. Returns one dict per PRN, strongest first.

    Detected PRNs also get `fine_doppler_hz` (refine_doppler within one search
    step, over FINE_MS of `x` if it is that long: see scan_seconds); undetected
    ones get None. `progress(done, total)` is called after each PRN, if given.
    """
    spms = int(round(fs * 1e-3))
    prns = list(prns)
    results = []
    for n, prn in enumerate(prns, start=1):
        cp, dop, ratio = acquire_prn(x, fs, prn, dop_min, dop_max, dop_step, nms)
        detected = ratio >= threshold
        results.append({
            "prn": prn,
            "doppler_hz": dop,
            "fine_doppler_hz": (refine_doppler(x, fs, prn, dop, cp, FINE_MS, dop_step)
                                if detected else None),
            "code_phase_samp": cp,
            "code_phase_chips": cp * float(CODE_LENGTH) / spms,
            "peak_over_mean": ratio,
            "detected": detected,
        })
        if progress is not None:
            progress(n, len(prns))
    results.sort(key=lambda r: r["peak_over_mean"], reverse=True)
    return results


def seconds_needed(nms):
    """How many seconds of signal a scan integrating `nms` ms reads (+5 ms margin)."""
    return (nms + 5) / 1000.0


def scan_seconds(nms):
    """How much signal to load for `scan`: the search's `nms` ms, or FINE_MS for the
    Doppler refinement if that is longer."""
    return seconds_needed(max(nms, FINE_MS))


def despread(x, fs, prn, code_phase_samp, offset_chips=0.0):
    """Multiply `x` by the satellite's code, aligned at `code_phase_samp` (samples)
    plus `offset_chips`. Aligned, every +/-1 chip of the satellite's code meets the
    same chip in the replica and the code vanishes: what is left of the satellite is
    its carrier, a pure tone at its Doppler, now visible in a spectrum. Misaligned
    by a chip or more, the codes don't match and the signal stays spread."""
    spms = int(round(fs * 1e-3))
    shift = int(round(code_phase_samp + offset_chips * spms / CODE_LENGTH))
    code = np.roll(upsample_code(prn, spms).astype(np.float32), shift)
    reps = -(-len(x) // spms)                                   # ceil
    return x[:len(x)] * np.tile(code, reps)[:len(x)]


def noise_peak_over_mean(spms, n_dopplers, nms, trials=3, seed=0):
    """What peak/mean comes out with noise alone, for a grid of `n_dopplers` rows of
    `spms` code phases, each cell a sum of `nms` ms (simulated, median of `trials`).

    With noise only, each 1 ms correlation power is exponential, so a cell is a
    Gamma(nms) value; acquire_prn takes each row's max over its mean and keeps the
    best row. This is the floor a detection threshold has to clear.
    """
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(trials):
        cells = rng.gamma(nms, size=(n_dopplers, spms))
        out.append(float((cells.max(axis=1) / cells.mean(axis=1)).max()))
    return float(np.median(out))


def code_drift_chips_per_s(doppler_hz):
    """How fast the code phase should slide, given the carrier Doppler.

    Code and carrier are stretched by the same factor (1 + v/c), so a carrier
    offset f_D comes with a chip-rate offset of 1.023 MHz * f_D / f_L1. A
    positive Doppler (satellite approaching) means the code arrives earlier and
    earlier: its phase in our 1 ms window decreases.
    """
    return -CHIP_RATE * np.asarray(doppler_hz) / L1_HZ


def cn0_from_peak(peak_over_mean, coherent_s=1e-3):
    """Rough C/N0 in dB-Hz from an acquisition peak.

    A coherent sum over T seconds gives the aligned cell a signal-to-noise ratio of
    C/N0 * T, and the surface average is almost all noise, so
    peak/mean - 1 ~ C/N0 * T. Only meaningful for a detected satellite; expect
    +/-2 dB because the search grid rarely hits the peak exactly.
    """
    return 10 * np.log10(max(peak_over_mean - 1.0, 1e-12) / coherent_s)


# --- persistence: is a detection real? ------------------------------------------------

def window_start(duration_s, skip_s, nms):
    """`skip_s`, moved back if needed so what `scan` loads (scan_seconds: the search
    plus the 40 ms Doppler refinement) fits in a recording of `duration_s` seconds.
    A recording shorter than that starts at 0 and uses what there is."""
    return min(max(skip_s, 0.0), max(duration_s - scan_seconds(nms), 0.0))


def drift_tolerance(times):
    """How far measured and predicted drift may differ (chips/s): code phases are
    measured to one sample (~0.26 chip at 4 MSPS), so short spans fit loosely."""
    span = float(times[-1] - times[0]) if len(times) > 1 else 0.0
    return max(0.1, 0.5 / span) if span > 0 else float("inf")


def persistence(iq_path, fmt, fs, search, prns, detected, points=5, progress=None):
    """Re-acquire `prns` at `points` times spread over the recording.

    `search` is (dop_min, dop_max, dop_step, nms), as for acquire_prn. A real
    satellite comes back every time, with a code phase drifting at the rate its
    Doppler predicts (code_drift_chips_per_s); a noise peak lands somewhere new
    each time. Code phases are measured from the start of the recording (not of
    each window), so they line up even when a window starts mid-millisecond. For
    the `detected` PRNs the Doppler is refined and the code drift fitted.

    Returns {"times", "tracks": {prn: {"doppler", "chips", "ratio"}},
    "drift": {prn: {"measured", "predicted", "doppler"}}, "tolerance"}.
    """
    dop_min, dop_max, dop_step, nms = search
    duration = len(open_iq(iq_path, fmt, fs, remove_dc=False)) / fs
    last = max(duration - scan_seconds(nms), 0.0)     # the last window keeps its 40 ms
    times = np.unique(np.round(np.linspace(0.0, last, points), 3))
    tracks = {prn: {"doppler": [], "chips": [], "ratio": []} for prn in prns}
    total, done = len(times) * len(prns), 0
    ms_samples = fs * 1e-3
    for t in times:
        start = window_start(duration, float(t), nms)
        x = load_iq(iq_path, fmt, fs, seconds=scan_seconds(nms), skip_seconds=start)
        first = int(round(start * fs))
        for prn in prns:
            cp, dop, ratio = acquire_prn(x, fs, prn, dop_min, dop_max, dop_step, nms)
            if prn in detected:
                dop = refine_doppler(x, fs, prn, dop, cp, FINE_MS, dop_step)
            chips = ((first + cp) / ms_samples % 1.0) * CODE_LENGTH
            tracks[prn]["doppler"].append(dop)
            tracks[prn]["chips"].append(chips)
            tracks[prn]["ratio"].append(ratio)
            done += 1
            if progress is not None:
                progress(done, total)
    drift = {}
    for prn in detected:
        if prn not in tracks or len(times) < 2:
            continue
        chips = np.unwrap(np.array(tracks[prn]["chips"]), period=CODE_LENGTH)
        drift[prn] = {"measured": float(np.polyfit(times, chips, 1)[0]),
                      "predicted": float(code_drift_chips_per_s(
                          np.mean(tracks[prn]["doppler"]))),
                      "doppler": float(np.mean(tracks[prn]["doppler"]))}
    return {"times": times, "tracks": tracks, "drift": drift,
            "tolerance": drift_tolerance(times)}


# --- scan_results.csv: the hand-over to stage 03 (written and read only here) ---

def write_results(results, path):
    """Write scan results (as returned by `scan`) to a CSV file."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_FIELDS))
        w.writeheader()
        w.writerows({k: ("" if r.get(k) is None else r[k]) for k in CSV_FIELDS}
                    for r in results)


def read_results(path):
    """Read a scan_results.csv back into the dicts `scan` returns.

    Columns other than CSV_FIELDS are ignored, so older and newer files load.
    """
    with open(path, newline="") as f:
        return [{"prn": int(r["prn"]),
                 "doppler_hz": float(r["doppler_hz"]),
                 "fine_doppler_hz": (float(r["fine_doppler_hz"])
                                     if r.get("fine_doppler_hz") else None),
                 "code_phase_samp": int(float(r["code_phase_samp"])),
                 "code_phase_chips": float(r["code_phase_chips"]),
                 "peak_over_mean": float(r.get("peak_over_mean") or 0.0),
                 "detected": str(r.get("detected", "")).strip().lower() == "true"}
                for r in csv.DictReader(f)]
