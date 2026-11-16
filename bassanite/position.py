"""
position.py -- stage 05: from the code timing and the message to a position.

Every satellite broadcasts, in effect, "this is the time on my clock". Stage 03
times the code to a few nanoseconds and stage 04 labels it with the satellite's
time, so at any instant of the recording we can read each satellite's clock as it
shows in the signal arriving just then (satellite_time). The signals took
different times to arrive, so the clocks read differently: those differences
are the measurement.

    pseudorange_i = c (t_rx - t_sv_i) = |s_i - r| + c dt

with s_i the satellite's position when the signal left (sat_position, from the
decoded ephemeris), r our position and dt the error of our own clock, which
nobody set. Four unknowns (x, y, z, dt), so four satellites; solve_position
finds them by Newton's method with least squares.

Corrections, each of which can be switched off to see what it is worth
(SolveOptions): the satellite's clock error (subframe 1), the relativistic and
group-delay terms, the Earth's rotation during the 70 ms flight, and the delay
of the lower atmosphere. The ionosphere is not corrected: its model is sent
once per 12.5 minutes, longer than our recordings. See 05_positioning/NOTES.md.
"""
import csv
import json
from dataclasses import asdict, dataclass

from pathlib import Path

import numpy as np

from bassanite.constants import L1_HZ
from bassanite.navigation import CODE_PERIOD_S, WEEK_S
from bassanite.tracking import load_track, track_file_name

C_LIGHT = 299_792_458.0       # m/s
MU = 3.986005e14              # m^3/s^2, Earth's gravitational constant (fixed by the specification)
OMEGA_E = 7.2921151467e-5     # rad/s, rotation rate of the Earth
F_REL = -4.442807633e-10      # s/sqrt(m), constant of the relativistic clock correction
EARTH_A = 6378137.0           # m, WGS 84 equatorial radius
EARTH_E2 = 6.69437999014e-3   # WGS 84 eccentricity squared

FIRST_TRAVEL_S = 0.070        # the receiver clock is first set by assuming this flight time
SETTLE_S = 2.0                # skipped at the start of a track, while the loops pull in
DEFAULT_STEP_S = 0.1          # one position every 100 ms


def wrap_week(dt):
    """A time difference brought back to +/- half a week (times of week restart
    at 0 every Sunday at midnight)."""
    return (dt + WEEK_S / 2) % WEEK_S - WEEK_S / 2


# --- where a satellite is, and what its clock shows -----------------------------------------

def sat_position(eph, t):
    """Position of a satellite (ECEF, metres) at GPS time `t` (seconds of the week),
    from its ephemeris (IS-GPS-200, table 20-IV). ECEF axes turn with the Earth:
    the position is given in the axes as they are at time `t`. Also returns the
    eccentric anomaly, which the clock correction needs. `t` may be an array:
    the position then has shape (3, n)."""
    a = eph["sqrt_a"] ** 2
    tk = wrap_week(t - eph["t_oe"])                               # time since the reference time
    M = eph["m_0"] + (np.sqrt(MU / a ** 3) + eph["delta_n"]) * tk     # mean anomaly: constant rate
    E = M
    for _ in range(10):                                           # Kepler's equation M = E - e sin E
        E = M + eph["e"] * np.sin(E)
    nu = np.arctan2(np.sqrt(1 - eph["e"] ** 2) * np.sin(E), np.cos(E) - eph["e"])   # true anomaly
    phi = nu + eph["omega"]                                       # angle from the ascending node
    s2, c2 = np.sin(2 * phi), np.cos(2 * phi)
    u = phi + eph["c_us"] * s2 + eph["c_uc"] * c2                 # corrected angle,
    r = a * (1 - eph["e"] * np.cos(E)) + eph["c_rs"] * s2 + eph["c_rc"] * c2     # radius
    i = eph["i_0"] + eph["i_dot"] * tk + eph["c_is"] * s2 + eph["c_ic"] * c2     # and inclination
    node = (eph["omega_0"] + (eph["omega_dot"] - OMEGA_E) * tk
            - OMEGA_E * eph["t_oe"])                              # node, in the turning frame
    x, y = r * np.cos(u), r * np.sin(u)                           # position in the orbit's plane
    return np.array([x * np.cos(node) - y * np.cos(i) * np.sin(node),
                     x * np.sin(node) + y * np.cos(i) * np.cos(node),
                     y * np.sin(i)]), E


def sat_clock_error(eph, t, E, relativity=True, group_delay=True):
    """How far the satellite's clock is ahead of GPS time (s): the polynomial of
    subframe 1, the relativistic term (the clock runs faster or slower along the
    slightly elliptical orbit) and the delay of the L1 signal inside the satellite."""
    dt = wrap_week(t - eph["t_oc"])
    error = eph["a_f0"] + eph["a_f1"] * dt + eph["a_f2"] * dt ** 2
    if relativity:
        error = error + F_REL * eph["e"] * eph["sqrt_a"] * np.sin(E)
    if group_delay:
        error = error - eph["t_gd"]
    return error


def turn_with_earth(pos, angle):
    """Coordinates of a fixed point of space after the Earth-fixed axes have turned
    by `angle` (rad)."""
    return np.array([pos[0] * np.cos(angle) + pos[1] * np.sin(angle),
                     -pos[0] * np.sin(angle) + pos[1] * np.cos(angle), pos[2]])


def signal_arriving(eph, t, rx_pos):
    """The signal of one satellite that arrives at `rx_pos` (ECEF) at GPS time `t`
    (scalar or array). Returns its lag: how far behind `t` the satellite's clock
    was when that signal left (travel time minus the satellite's clock error),
    and where the satellite was then, in the Earth-fixed frame of the arrival
    time. This is the forward model: the synthetic recordings are made with it,
    and it predicts the Doppler a receiver at a known place should see."""
    travel = 0.075
    for _ in range(5):                                  # the travel time depends on itself
        pos, E = sat_position(eph, t - travel)
        pos = turn_with_earth(pos, OMEGA_E * travel)    # the Earth turns while the signal flies
        travel = np.linalg.norm(pos.T - rx_pos, axis=-1) / C_LIGHT
    return travel - sat_clock_error(eph, t - travel, E), pos


def predicted_doppler(eph, t, rx_pos, dt=0.5):
    """Doppler shift (Hz) a perfect receiver at `rx_pos` sees at GPS time `t`: the
    lag shrinks when the satellite approaches, and the signal is seen in fast
    motion. A real receiver adds a shift common to all satellites, from the error
    of its own oscillator."""
    before, _ = signal_arriving(eph, t - dt, rx_pos)
    after, _ = signal_arriving(eph, t + dt, rx_pos)
    return -L1_HZ * (after - before) / (2 * dt)


# --- the Earth -----------------------------------------------------------------------------------

def lla_to_ecef(lat, lon, h):
    """Latitude and longitude (degrees) and height above the ellipsoid (m) -> ECEF (m)."""
    lat, lon = np.radians(lat), np.radians(lon)
    n = EARTH_A / np.sqrt(1 - EARTH_E2 * np.sin(lat) ** 2)
    return np.array([(n + h) * np.cos(lat) * np.cos(lon), (n + h) * np.cos(lat) * np.sin(lon),
                     (n * (1 - EARTH_E2) + h) * np.sin(lat)])


def ecef_to_lla(p):
    """ECEF (m) -> latitude, longitude (degrees), height above the WGS 84 ellipsoid (m)."""
    lon, pr = np.arctan2(p[1], p[0]), np.hypot(p[0], p[1])
    lat = np.arctan2(p[2], pr * (1 - EARTH_E2))
    h = 0.0
    for _ in range(8):
        n = EARTH_A / np.sqrt(1 - EARTH_E2 * np.sin(lat) ** 2)
        h = pr / np.cos(lat) - n
        lat = np.arctan2(p[2], pr * (1 - EARTH_E2 * n / (n + h)))
    return float(np.degrees(lat)), float(np.degrees(lon)), float(h)


def east_north_up(lat, lon):
    """Matrix that turns an ECEF vector into (east, north, up) at a place (degrees)."""
    lat, lon = np.radians(lat), np.radians(lon)
    return np.array([[-np.sin(lon), np.cos(lon), 0],
                     [-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)],
                     [np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)]])


def azimuth_elevation(rx_pos, sat_pos):
    """Direction of a satellite seen from `rx_pos` (both ECEF): azimuth from north
    through east and elevation above the horizon, in degrees."""
    lat, lon, _ = ecef_to_lla(rx_pos)
    e, n, u = east_north_up(lat, lon) @ (np.asarray(sat_pos) - np.asarray(rx_pos))
    return (float(np.degrees(np.arctan2(e, n)) % 360),
            float(np.degrees(np.arcsin(u / np.sqrt(e * e + n * n + u * u)))))


def troposphere_delay(elevation_deg, height_m=0.0):
    """Extra path (m) through the lower atmosphere: about 2.4 m straight up at sea
    level, and more at low elevation where the path through the air is longer.
    A simple model, good to a few decimetres above 10 degrees."""
    scale = np.exp(-np.clip(height_m, -500.0, 9000.0) / 8000.0)       # thinner air higher up
    return 2.47 * scale / (np.sin(np.radians(max(elevation_deg, 2.0))) + 0.0121)


# --- reading the satellites' clocks ----------------------------------------------------------------

def satellite_time(begin, period0_time, sample):
    """What a satellite's clock showed when the signal now at `sample` (a position
    in the recording, in samples, scalar or array) left it.

    `begin[k]` is where code period k starts in the recording (tracking's
    code_phase_samp) and `period0_time` the satellite time of period 0 (stage
    04). Periods are 1 ms of satellite time apart; inside one, interpolate.
    """
    k = np.clip(np.searchsorted(begin, sample, side="right") - 1, 0, len(begin) - 2)
    fraction = (sample - begin[k]) / (begin[k + 1] - begin[k])
    return period0_time + (k + fraction) * CODE_PERIOD_S


# --- solving -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SolveOptions:
    """The corrections solve_position applies. All on is correct; switching one
    off shows what it is worth."""
    satellite_clock: bool = True    # a_f0, a_f1, a_f2: up to hundreds of km
    relativity: bool = True         # the eccentricity term: metres
    group_delay: bool = True        # t_gd: metres
    earth_rotation: bool = True     # the Earth turns during the flight: tens of metres
    troposphere: bool = True        # metres, mostly on the height


def transmit_time(eph, t_sv, options=SolveOptions()):
    """True GPS time at which a signal left, from the time its satellite's clock
    showed (`t_sv`): remove that clock's error. Returns (t_tx, clock error)."""
    if not options.satellite_clock:
        return t_sv, 0.0
    t_tx, error = t_sv, 0.0
    for _ in range(2):                  # the error is evaluated at t_tx: iterate once
        _, E = sat_position(eph, t_tx)
        error = sat_clock_error(eph, t_tx, E, options.relativity, options.group_delay)
        t_tx = t_sv - error
    return t_tx, error


def solve_position(t_sv, ephemerides, t_rx, start=None, iterations=8,
                   options=SolveOptions()):
    """Position and receiver clock error from the satellite clocks read at one instant.

    `t_sv` is {prn: satellite time read in the signal} at an instant when the
    receiver's own clock shows `t_rx`; `ephemerides` is {prn: ephemeris}.
    Newton's method: start from a guess (`start`: x, y, z, c*dt in metres; the
    centre of the Earth by default), replace each range equation by its tangent,
    solve the linear system by least squares, correct, repeat.

    Returns a dict: position (ECEF, m), clock_error (s, receiver clock minus GPS
    time), history (the successive estimates), geometry (one row [-u, 1] per
    satellite, u the unit vector towards it), and satellites {prn: position,
    t_tx, clock_error, pseudorange, range, residual (m), azimuth, elevation
    (deg), troposphere (m)}.
    """
    prns = sorted(t_sv)
    estimate = np.zeros(4) if start is None else np.array(start, dtype=float)
    history = []
    sats = {}
    for prn in prns:                     # time and place of transmission: fixed by the signal
        t_tx, clock = transmit_time(ephemerides[prn], t_sv[prn], options)
        pos, _ = sat_position(ephemerides[prn], t_tx)
        sats[prn] = {"t_tx": float(t_tx), "clock_error": float(clock), "tx_position": pos,
                     "pseudorange": float(C_LIGHT * (t_rx - t_sv[prn]))}
    rows = np.zeros((len(prns), 4))
    for _ in range(iterations):
        misfit = np.zeros(len(prns))
        on_earth = 6.0e6 < np.linalg.norm(estimate[:3]) < 6.6e6
        height = ecef_to_lla(estimate[:3])[2] if on_earth else 0.0
        for j, prn in enumerate(prns):
            s = sats[prn]
            travel = (t_rx - estimate[3] / C_LIGHT) - s["t_tx"]
            pos = (turn_with_earth(s["tx_position"], OMEGA_E * travel)
                   if options.earth_rotation else s["tx_position"])
            distance = np.linalg.norm(pos - estimate[:3])
            tropo = 0.0
            if on_earth:
                s["azimuth"], s["elevation"] = azimuth_elevation(estimate[:3], pos)
                if options.troposphere:
                    tropo = float(troposphere_delay(s["elevation"], height))
            rows[j] = np.r_[(estimate[:3] - pos) / distance, 1.0]
            misfit[j] = C_LIGHT * (t_rx - s["t_tx"]) - (distance + tropo + estimate[3])
            s.update(position=pos, range=float(distance), troposphere=tropo,
                     residual=float(misfit[j]))
        estimate = estimate + np.linalg.lstsq(rows, misfit, rcond=None)[0]
        history.append(estimate.copy())
    for s in sats.values():
        s.setdefault("azimuth", float("nan"))
        s.setdefault("elevation", float("nan"))
        del s["tx_position"]
    return {"position": estimate[:3].copy(), "clock_error": float(estimate[3] / C_LIGHT),
            "history": history, "geometry": rows.copy(), "satellites": sats}


def dilution_of_precision(geometry, lat, lon):
    """How the geometry turns 1 m of ranging noise into position noise (m): the
    diagonal of (H^T H)^-1 in east/north/up. Returns {hdop, vdop, pdop, tdop,
    gdop}; infinite when the satellites don't determine a position."""
    try:
        q = np.linalg.inv(geometry.T @ geometry)
    except np.linalg.LinAlgError:
        return dict.fromkeys(("hdop", "vdop", "pdop", "tdop", "gdop"), float("inf"))
    enu = east_north_up(lat, lon)
    q_enu = enu @ q[:3, :3] @ enu.T
    hdop2, vdop2, tdop2 = q_enu[0, 0] + q_enu[1, 1], q_enu[2, 2], q[3, 3]
    return {"hdop": float(np.sqrt(hdop2)), "vdop": float(np.sqrt(vdop2)),
            "pdop": float(np.sqrt(hdop2 + vdop2)), "tdop": float(np.sqrt(tdop2)),
            "gdop": float(np.sqrt(hdop2 + vdop2 + tdop2))}


def measurement_window(satellites, fs, settle_s=SETTLE_S):
    """First and last sample at which every satellite's clock can be read: after
    the loops have settled, before the shortest track ends."""
    first = max(s["begin"][0] for s in satellites.values()) + settle_s * fs
    last = min(s["begin"][-2] for s in satellites.values())
    return float(first), float(last)


def receiver_clock_at_0(satellites, sample):
    """What the receiver's clock shows at sample 0. Its only clock is the sample
    counter, so it is set once, by assuming the nearest satellite's signal took
    FIRST_TRAVEL_S to arrive at `sample`. The error of this guess (up to ~15 ms)
    is the clock error the solver finds."""
    latest = max(float(satellite_time(s["begin"], s["period0_time"], sample))
                 for s in satellites.values())
    return latest + FIRST_TRAVEL_S


def fix_series(satellites, fs, step_s=DEFAULT_STEP_S, settle_s=SETTLE_S,
               options=SolveOptions(), prns=None):
    """A position at regular instants across the recording.

    `satellites` is {prn: {"begin": tracking's code_phase_samp, "period0_time":
    and "ephemeris": from stage 04}}; `prns` restricts the satellites used.
    Returns None with fewer than 4 satellites or no common stretch, else a dict
    of arrays, one entry per instant: sample, t (s since the first sample),
    ecef (n, 3), lat, lon, height, clock_error (s), residual_rms (m); plus
    rx_clock_at_0 (the receiver clock's reading at sample 0), prns, and `mid`,
    the full solve_position() result of the middle instant, solved from the
    centre of the Earth (the others start from the position before them).
    """
    sats = {p: s for p, s in satellites.items() if prns is None or p in prns}
    if len(sats) < 4:
        return None
    first, last = measurement_window(sats, fs, settle_s)
    samples = np.arange(first, last, step_s * fs)
    if len(samples) == 0:
        return None
    clock_at_0 = receiver_clock_at_0(sats, samples[0]) - samples[0] / fs
    ephs = {p: s["ephemeris"] for p, s in sats.items()}
    readings = {p: satellite_time(s["begin"], s["period0_time"], samples)
                for p, s in sats.items()}
    n = len(samples)
    out = {"sample": samples, "t": samples / fs, "ecef": np.zeros((n, 3)),
           "lat": np.zeros(n), "lon": np.zeros(n), "height": np.zeros(n),
           "clock_error": np.zeros(n), "residual_rms": np.zeros(n)}
    estimate, mid = None, None
    for i, sample in enumerate(samples):
        fix = solve_position({p: float(readings[p][i]) for p in sats}, ephs,
                             clock_at_0 + sample / fs, start=estimate,
                             iterations=8 if estimate is None else 3, options=options)
        estimate = np.r_[fix["position"], fix["clock_error"] * C_LIGHT]
        out["ecef"][i] = fix["position"]
        out["lat"][i], out["lon"][i], out["height"][i] = ecef_to_lla(fix["position"])
        out["clock_error"][i] = fix["clock_error"]
        out["residual_rms"][i] = np.sqrt(np.mean([s["residual"] ** 2
                                                  for s in fix["satellites"].values()]))
        if i == n // 2:
            mid = solve_position({p: float(readings[p][i]) for p in sats}, ephs,
                                 clock_at_0 + sample / fs, options=options)
            mid["sample"] = float(sample)
    out.update(rx_clock_at_0=clock_at_0, prns=sorted(sats), mid=mid)
    return out


def summarize(series):
    """The answer, from a fix_series(): the mean position and how much the single
    positions scatter around it.

    Returns a dict: lat, lon, height and ecef of the mean position; east_std,
    north_std, up_std and horizontal_rms (m), the scatter; n_fixes; satellites
    (PRNs used); dop (dilution_of_precision at the middle instant);
    clock_error_s at the first instant and clock_drift (s/s: how fast the
    receiver's clock gains on GPS time, i.e. the relative error of its
    oscillator); gps_time_at_sample0 (s of the week); residual_rms (m).
    """
    mean = series["ecef"].mean(axis=0)
    lat, lon, height = ecef_to_lla(mean)
    enu = (series["ecef"] - mean) @ east_north_up(lat, lon).T
    t = series["t"]
    if len(t) > 1:
        drift, offset = np.polyfit(t, series["clock_error"], 1)
    else:
        drift, offset = 0.0, float(series["clock_error"][0])
    return {"lat": lat, "lon": lon, "height": height, "ecef": [float(v) for v in mean],
            "east_std": float(enu[:, 0].std()), "north_std": float(enu[:, 1].std()),
            "up_std": float(enu[:, 2].std()),
            "horizontal_rms": float(np.sqrt((enu[:, 0] ** 2 + enu[:, 1] ** 2).mean())),
            "n_fixes": int(len(t)), "satellites": [int(p) for p in series["prns"]],
            "dop": dilution_of_precision(series["mid"]["geometry"], lat, lon),
            "clock_error_s": float(series["clock_error"][0]), "clock_drift": float(drift),
            "gps_time_at_sample0": float((series["rx_clock_at_0"] - offset) % WEEK_S),
            "residual_rms": float(np.sqrt(np.mean(series["residual_rms"] ** 2)))}


def enu_scatter(series, summary):
    """Each position of the series as (east, north, up) metres from the mean."""
    return (series["ecef"] - np.array(summary["ecef"])) \
        @ east_north_up(summary["lat"], summary["lon"]).T


def doppler_check(satellites, summary, fs):
    """Compare the Doppler tracking measured with the Doppler the solution predicts.

    The carrier frequency was never used to compute the position, so this is an
    independent test: a wrong position, time or orbit gives predictions that are
    off by different amounts for different satellites (about 1 Hz per km of
    position error). `satellites[prn]` also needs "doppler_hz" (tracking's
    per-ms Doppler). The receiver's own frequency error shifts every measurement
    by the same amount, so only the spread of the differences counts.

    Returns a dict: satellites {prn: {measured, predicted, difference}} in Hz, at
    the middle of each track; common (the mean difference: the receiver's
    frequency offset at L1); spread (the largest deviation from it); and
    from_clock_drift, the offset expected from the drift of the receiver clock
    the position solution found (-f_L1 x clock_drift).
    """
    rx = np.array(summary["ecef"])
    rows = {}
    for prn in summary["satellites"]:
        s = satellites[prn]
        k = len(s["begin"]) // 2
        t_mid = summary["gps_time_at_sample0"] + s["begin"][k] / fs
        lo, hi = max(k - 500, 0), min(k + 500, len(s["doppler_hz"]))
        measured = float(np.mean(s["doppler_hz"][lo:hi]))
        predicted = float(predicted_doppler(s["ephemeris"], t_mid, rx))
        rows[int(prn)] = {"measured": measured, "predicted": predicted,
                          "difference": measured - predicted}
    common = float(np.mean([r["difference"] for r in rows.values()]))
    return {"satellites": rows, "common": common,
            "spread": float(max(abs(r["difference"] - common) for r in rows.values())),
            "from_clock_drift": float(-L1_HZ * summary["clock_drift"])}


def load_measurements(tracking_folder, navigation):
    """What the solver needs, from the files of stages 03 and 04.

    `navigation` is {prn: satellite} as load_navigation() or decode_tracks()
    return it. Returns (satellites, fs): for every usable satellite, begin
    (where each code period starts in the recording, in samples), doppler_hz,
    period0_time and ephemeris.
    """
    folder = Path(tracking_folder)
    sats, fs = {}, None
    for prn, nav in sorted(navigation.items()):
        if nav.get("ephemeris") is None or nav.get("period0_time") is None \
                or nav.get("problems"):
            continue
        path = folder / track_file_name(prn)
        if not path.is_file():
            continue
        track = load_track(path)
        fs = track["fs"]
        sats[int(prn)] = {"begin": track["code_phase_samp"], "doppler_hz": track["doppler_hz"],
                          "period0_time": nav["period0_time"], "ephemeris": nav["ephemeris"]}
    return sats, fs


# --- result files (written and read only here) --------------------------------------------------
#
#   fix.json     the summary (summarize), the options, one row per satellite, the
#                solver's iterations at the middle instant and the Doppler check
#   fixes.csv    one row per instant: t_s, lat, lon, height_m, x, y, z,
#                clock_error_s, residual_rms_m

FIX_JSON = "fix.json"
FIXES_CSV = "fixes.csv"
FIXES_FIELDS = ("t_s", "lat", "lon", "height_m", "x", "y", "z", "clock_error_s",
                "residual_rms_m")


def satellite_rows(series):
    """One dict per satellite at the middle instant, without numpy types."""
    rows = []
    for prn, s in sorted(series["mid"]["satellites"].items()):
        rows.append({"prn": int(prn), **{k: float(s[k]) for k in
                                         ("pseudorange", "range", "clock_error", "t_tx",
                                          "azimuth", "elevation", "troposphere", "residual")},
                     "position": [float(v) for v in s["position"]]})
    return rows


def save_fix(json_path, csv_path, series, summary, options=SolveOptions(), extra=None):
    """Write fix.json and fixes.csv. `extra` (a dict) is merged into the JSON."""
    doc = {"summary": summary, "options": asdict(options),
           "satellites": satellite_rows(series),
           # the solver's successive estimates at the middle instant: x, y, z, c*dt (m)
           "history": [[float(v) for v in h] for h in series["mid"]["history"]],
           **(extra or {})}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIXES_FIELDS)
        for i in range(len(series["t"])):
            w.writerow([f"{series['t'][i]:.4f}", f"{series['lat'][i]:.8f}",
                        f"{series['lon'][i]:.8f}", f"{series['height'][i]:.3f}",
                        *(f"{v:.3f}" for v in series["ecef"][i]),
                        f"{series['clock_error'][i]:.12f}",
                        f"{series['residual_rms'][i]:.4f}"])
    return json_path


def load_fix(json_path):
    """Read fix.json back (a dict: summary, options, satellites, ...)."""
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)


def load_fixes(csv_path):
    """Read fixes.csv back: {column: array}."""
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    return {k: np.array([float(r[k]) for r in rows]) for k in FIXES_FIELDS}
