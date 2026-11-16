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

Those satellites send random data bits: enough for stages 02 and 03.

For stages 04 and 05 there is a second kind (write_sky_recording): a receiver at
a known place under satellites on real GPS orbits. Each satellite sends a real
navigation message (bassanite/navigation.py's encoder) holding its ephemeris,
and its code, bits and carrier arrive with the delay of its true distance,
changing as it moves. The whole receiver can then be checked against a known
position.
"""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from bassanite.ca_code import ca_code
from bassanite.constants import CHIP_RATE, CODE_LENGTH, L1_HZ
from bassanite.navigation import BIT_S, FIELDS, SUBFRAME_S, encode_message
from bassanite.position import C_LIGHT, lla_to_ecef, predicted_doppler, signal_arriving
from bassanite.tracking import (MS_PER_BIT, SUMMARY_CSV, save_track, summarize,
                                summary_row, track_file_name, write_summary)

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


# --- a sky: satellites on real orbits above a receiver at a known place ------------------

SKY_RECEIVER = (51.4779, -0.0015, 46.0)   # lat, lon (deg), height (m): Greenwich
SKY_TOW = 221_400                 # s of the week at which a subframe 1 starts (Tuesday 13:30:00)
SKY_T_OE = 223_200                # reference time of the ephemerides (14:00:00)
SKY_WEEK = 2444                   # November 2026
SKY_LEAD_S = 0.64                 # the recording starts this long before SKY_TOW
# prn: sqrt(a) (sqrt m), eccentricity, inclination, node longitude, argument of
# perigee, mean anomaly (degrees), C/N0 (dB-Hz). All five are above Greenwich's
# horizon at SKY_TOW.
SKY_ORBITS = {5:  (5153.65, 0.0061, 55.2, -175.3148,   40.0,   21.0419, 49),
              12: (5153.65, 0.0089, 54.6,  157.8488, -110.0, -131.1827, 46),
              17: (5153.65, 0.0132, 55.9,  136.3576,   75.0,  -26.4903, 44),
              23: (5153.65, 0.0024, 54.9,  -22.2861,  160.0,  -22.0243, 41),
              30: (5153.65, 0.0105, 55.4, -109.1957,  -35.0,   60.8389, 39)}
SKY_BLOCK_S = 0.02                # generation block: the delay is interpolated inside it


def make_ephemeris(prn, orbit, rng, t_oe=SKY_T_OE, week=SKY_WEEK):
    """A complete ephemeris: the ellipse given by `orbit` (the first six numbers
    of a SKY_ORBITS entry), plus corrections and clock terms of realistic size.
    Every field is rounded to a whole number of its units, so that it is exactly
    what the message can carry."""
    sqrt_a, e, i_0, omega_0, omega, m_0 = orbit[:6]
    eph = dict(sqrt_a=sqrt_a, e=e, i_0=np.radians(i_0), omega_0=np.radians(omega_0),
               omega=np.radians(omega), m_0=np.radians(m_0), t_oe=t_oe, t_oc=t_oe,
               delta_n=rng.normal(4.5e-9, 4e-10), omega_dot=rng.normal(-8.0e-9, 4e-10),
               i_dot=rng.normal(0, 3e-10), c_uc=rng.normal(0, 4e-6), c_us=rng.normal(0, 4e-6),
               c_rc=rng.normal(250, 60), c_rs=rng.normal(0, 60), c_ic=rng.normal(0, 1e-7),
               c_is=rng.normal(0, 1e-7), a_f0=rng.normal(0, 2e-4), a_f1=rng.normal(0, 4e-12),
               a_f2=0.0, t_gd=rng.normal(0, 8e-9), week=week % 1024, ura=0, health=0,
               iodc=prn + 40, iode=prn + 40, iode_3=prn + 40)
    for name, _, _, _, unit_value, *_ in FIELDS:
        eph[name] = float(round(eph[name] / unit_value) * unit_value)
    return eph


def default_sky(seed=2024):
    """The ephemerides of the five SKY_ORBITS satellites: {prn: ephemeris}."""
    rng = np.random.default_rng(seed)
    return {prn: make_ephemeris(prn, orbit, rng) for prn, orbit in SKY_ORBITS.items()}


def sky_blocks(seconds, ephemerides, receiver=SKY_RECEIVER, cn0_dbhz=None, fs=FS,
               t_start=SKY_TOW - SKY_LEAD_S, seed=1, clock_drift=0.0):
    """The recording a receiver at `receiver` (lat, lon, height) makes from GPS time
    `t_start` (s of the week) on, as successive complex arrays of SKY_BLOCK_S.

    `cn0_dbhz` is {prn: dB-Hz} (default: the SKY_ORBITS values, 45 for other
    satellites). `clock_drift` is the relative error of the receiver's
    oscillator: its samples come (1 + clock_drift) times too fast and its tuning
    is off by the same fraction, as in a real front end.
    """
    rx = lla_to_ecef(*receiver)
    block = int(round(fs * SKY_BLOCK_S))
    n_blocks = int(seconds * fs) // block
    rate = fs * (1.0 + clock_drift)                         # true samples per second
    edges = np.arange(n_blocks + 1) * block / rate          # block boundaries, true seconds
    inside = np.arange(block) / block                       # position inside a block, 0 to 1
    t_message = SUBFRAME_S * (int(t_start - 1.0) // SUBFRAME_S)     # the message starts before
    n_subframes = int(np.ceil((t_start + seconds - t_message) / SUBFRAME_S)) + 1
    cn0_dbhz = cn0_dbhz or {}
    sats = []
    for prn, eph in sorted(ephemerides.items()):
        cn0 = cn0_dbhz.get(prn, SKY_ORBITS[prn][6] if prn in SKY_ORBITS else 45.0)
        lag, _ = signal_arriving(eph, t_start + edges, rx)
        levels = 1.0 - 2.0 * np.array(encode_message(eph, t_message, n_subframes), np.float32)
        sats.append((_code(prn).astype(np.float32), levels,
                     float(np.sqrt(10 ** ((cn0 - 10 * np.log10(fs)) / 10))), lag))
    rng = np.random.default_rng(seed)
    for b in range(n_blocks):
        x = (rng.standard_normal((block, 2), dtype=np.float32) * np.float32(np.sqrt(0.5))) \
            .view(np.complex64).ravel().astype(np.complex128)
        t_rx = edges[b] + inside * (block / rate)           # true time since the first sample
        for code, levels, amp, lag in sats:
            lag_now = lag[b] + (lag[b + 1] - lag[b]) * inside       # smooth: interpolate
            t_sv = (t_start - t_message) + t_rx - lag_now   # satellite time since the message start
            chip = (t_sv * CHIP_RATE).astype(np.int64) % CODE_LENGTH
            bit = (t_sv / BIT_S).astype(np.int64)
            # Carrier phase in turns: the delay, and the receiver tuned off by clock_drift.
            turns = -L1_HZ * (lag_now + clock_drift * t_rx)
            turns -= np.floor(turns)
            x += amp * code[chip] * levels[bit] * np.exp(2j * np.pi * turns)
        yield x


def sky_truth(seconds, ephemerides, receiver=SKY_RECEIVER, cn0_dbhz=None, fs=FS,
              t_start=SKY_TOW - SKY_LEAD_S, seed=1, clock_drift=0.0):
    """What is true about a sky recording (the content of its truth sidecar)."""
    rx = lla_to_ecef(*receiver)
    cn0_dbhz = cn0_dbhz or {}
    spms = fs * 1e-3
    satellites = []
    for prn, eph in sorted(ephemerides.items()):
        lag0, _ = signal_arriving(eph, t_start, rx)
        lag1, _ = signal_arriving(eph, t_start + 1.0, rx)
        rate = float(lag1 - lag0)                           # how fast the delay grows (s/s)
        doppler = -L1_HZ * (rate + clock_drift)
        chips_in = ((t_start - lag0) * CHIP_RATE) % CODE_LENGTH     # chip being received at sample 0
        samples_per_chip = fs / (CHIP_RATE * (1.0 - rate - clock_drift))
        code_phase_samp = ((CODE_LENGTH - chips_in) * samples_per_chip) % spms
        satellites.append({"prn": int(prn), "code_phase_chips": code_phase_samp * CODE_LENGTH / spms,
                           "code_phase_samp": code_phase_samp, "doppler_hz": doppler,
                           "cn0_dbhz": cn0_dbhz.get(prn, SKY_ORBITS[prn][6]
                                                    if prn in SKY_ORBITS else 45.0)})
    return {"fs": fs, "seconds": seconds, "seed": seed, "satellites": satellites,
            "cn0_dbhz": float(np.median([s["cn0_dbhz"] for s in satellites])),
            "receiver": dict(zip(("lat", "lon", "height"), map(float, receiver))),
            "gps_time_at_sample0": float(t_start), "clock_drift": clock_drift,
            "ephemerides": {str(prn): eph for prn, eph in sorted(ephemerides.items())}}


def write_sky_recording(path, seconds, ephemerides=None, receiver=SKY_RECEIVER,
                        cn0_dbhz=None, fs=FS, t_start=SKY_TOW - SKY_LEAD_S, seed=1,
                        clock_drift=0.0):
    """Write a sky recording (int8) and its truth sidecar, block by block. With
    the defaults, subframes 1, 2 and 3 are complete after 19 s. Returns the path."""
    path = Path(path)
    ephemerides = default_sky() if ephemerides is None else ephemerides
    with open(path, "wb") as f:
        for block in sky_blocks(seconds, ephemerides, receiver, cn0_dbhz, fs, t_start,
                                seed, clock_drift):
            to_int8(block).tofile(f)
    truth = sky_truth(seconds, ephemerides, receiver, cn0_dbhz, fs, t_start, seed, clock_drift)
    truth_path(path).write_text(json.dumps(truth, indent=2), encoding="utf-8")
    return path


# --- the same sky, as stage 03 would hand it over (no samples: fast) ---------------------

def sky_tracks(seconds, ephemerides=None, receiver=SKY_RECEIVER, fs=FS,
               t_start=SKY_TOW - SKY_LEAD_S, clock_drift=0.0, noise_m=0.0, seed=0,
               frequency_offset=None):
    """What tracking and bit sync would measure on a sky recording, computed
    directly from the geometry instead of from samples.

    Returns {prn: {code_phase_samp, doppler_hz, nav_bits, bit_offset}}:
    code_phase_samp[k] is the sample at which code period k starts,
    nav_bits the message from the first whole bit on (inverted for every other
    satellite, as a Costas loop would leave them), bit_offset the code period
    at which that bit starts. `noise_m` adds ranging noise (metres, 1 sigma) to
    the code timing. The receiver's oscillator is off by `clock_drift`; its
    tuning by `frequency_offset` (the same, unless given).
    """
    ephemerides = default_sky() if ephemerides is None else ephemerides
    rx = lla_to_ecef(*receiver)
    rng = np.random.default_rng(seed)
    t_message = SUBFRAME_S * (int(t_start - 1.0) // SUBFRAME_S)
    n_subframes = int(np.ceil((t_start + seconds - t_message) / SUBFRAME_S)) + 1
    tuning = clock_drift if frequency_offset is None else frequency_offset
    tracks = {}
    for i, (prn, eph) in enumerate(sorted(ephemerides.items())):
        lag0, _ = signal_arriving(eph, t_start, rx)
        first_ms = int(np.ceil((t_start - lag0) * 1000 + 1))        # a whole millisecond
        n_ms = int(seconds * 1000) - 3
        t_sv = (first_ms + np.arange(n_ms)) / 1000.0                # code period k leaves
        t_rx = t_sv + 0.07
        for _ in range(4):                       # when does it arrive?  t = t_sv + lag(t)
            t_rx = t_sv + signal_arriving(eph, t_rx, rx)[0]
        begin = (t_rx - t_start) * fs * (1.0 + clock_drift)
        begin = begin + rng.normal(0.0, noise_m / C_LIGHT * fs, n_ms)
        bit_offset = (-first_ms) % MS_PER_BIT    # bits start on multiples of 20 ms
        first_bit = int(round(((first_ms + bit_offset) / 1000.0 - t_message) / BIT_S))
        n_bits = (n_ms - bit_offset) // MS_PER_BIT
        bits = np.array(encode_message(eph, t_message, n_subframes)[first_bit:first_bit + n_bits],
                        dtype=np.int8)
        tracks[prn] = {"code_phase_samp": begin,
                       "doppler_hz": (predicted_doppler(eph, t_rx, rx) - L1_HZ * tuning)
                       .astype(np.float32),
                       "nav_bits": 1 - bits if i % 2 else bits, "bit_offset": int(bit_offset)}
    return tracks


def write_sky_tracking(folder, tracks, fs=FS):
    """Write `tracks` (sky_tracks()) as stage 03's result files: one
    track_PRNnn.npz per satellite and tracking_summary.csv, through tracking's
    own summarize() and save_track(). The prompt correlator is made noiseless."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for prn, t in sorted(tracks.items()):
        n = len(t["code_phase_samp"])
        prompt = np.zeros(n, np.float32)
        levels = np.repeat(1.0 - 2.0 * t["nav_bits"], MS_PER_BIT)
        # A 1 is sent as the level -1; summarize() reads a positive sum as 1, so the
        # stream comes out inverted: decoding does not care (see check_word).
        prompt[t["bit_offset"]:t["bit_offset"] + len(levels)] = -1000.0 * levels
        prompt[:t["bit_offset"]] = prompt[t["bit_offset"]] if n > t["bit_offset"] else 0.0
        track = {"i_prompt": prompt, "q_prompt": np.zeros(n, np.float32),
                 "doppler_hz": t["doppler_hz"], "code_phase_samp": t["code_phase_samp"]}
        result = summarize(track)
        seed = {"doppler_hz": float(t["doppler_hz"][0]),
                "code_phase_samp": float(t["code_phase_samp"][0])}
        save_track(folder / track_file_name(prn), prn, fs, seed, track, result)
        rows.append(summary_row(prn, track, result))
    write_summary(rows, folder / SUMMARY_CSV)
    return folder
