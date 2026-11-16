"""Stage 05 (bassanite/position.py) against a sky where everything is known: five
satellites on GPS orbits and a receiver at Greenwich (bassanite/synthetic.py).

These tests work on measurements (when each code period arrives), not on samples:
the whole chain from samples is tests/test_end_to_end.py, which is slow.
"""
from dataclasses import replace

import numpy as np
import pytest

from bassanite import navigation as nav
from bassanite import position as pos
from bassanite.constants import L1_HZ
from bassanite.synthetic import SKY_RECEIVER, SKY_TOW, default_sky, sky_tracks

FS = 4e6
EPHS = default_sky()
TRUE_POS = pos.lla_to_ecef(*SKY_RECEIVER)
NO_ATMOSPHERE = replace(pos.SolveOptions(), troposphere=False)   # the test sky has no air


def readings(t, rx=TRUE_POS):
    """What each satellite's clock shows in the signal arriving at `rx` at GPS time t."""
    return {prn: float(t - pos.signal_arriving(eph, t, rx)[0]) for prn, eph in EPHS.items()}


def measurements(seconds=12.0, clock_drift=-2e-7, noise_m=0.0, frequency_offset=None):
    """What stages 03 and 04 would hand over for the test sky (sky_tracks): per
    satellite, the sample at which each code period starts (begin), the satellite
    time of period 0, the ephemeris and the Doppler. The receiver's oscillator is
    off by `clock_drift`, so its samples come (1 + clock_drift) times too fast."""
    t0 = SKY_TOW + 3.0                               # true GPS time of sample 0
    tracks = sky_tracks(seconds, EPHS, fs=FS, t_start=t0, clock_drift=clock_drift,
                        noise_m=noise_m, frequency_offset=frequency_offset)
    sats = {}
    for prn, t in tracks.items():
        sat = nav.decode_satellite(t["nav_bits"], t["bit_offset"])
        sats[prn] = {"begin": t["code_phase_samp"], "period0_time": sat["period0_time"],
                     "ephemeris": EPHS[prn], "doppler_hz": t["doppler_hz"]}
    return sats, t0, None


# --- orbits and the Earth -----------------------------------------------------------------

def test_satellites_are_on_gps_orbits_above_the_receiver():
    for prn, eph in EPHS.items():
        p, _ = pos.sat_position(eph, SKY_TOW)
        assert 26.0e6 < np.linalg.norm(p) < 27.2e6, prn       # 20 200 km above the ground
        lag, where = pos.signal_arriving(eph, SKY_TOW, TRUE_POS)
        assert 0.060 < lag + eph["a_f0"] < 0.090               # 18 000 to 27 000 km away
        assert pos.azimuth_elevation(TRUE_POS, where)[1] > 5, prn


def test_an_orbit_takes_half_a_sidereal_day_and_times_wrap_with_the_week():
    eph = EPHS[12]
    period = 2 * np.pi / (np.sqrt(pos.MU / eph["sqrt_a"] ** 6) + eph["delta_n"])
    assert period == pytest.approx(43082, abs=40)              # half a sidereal day
    # A reference time just before the week ends, used just after it has restarted:
    # 46 s later, not a week earlier.
    late = dict(eph, t_oe=604800 - 16, t_oc=604800 - 16)
    wrapped, E = pos.sat_position(late, 30.0)
    unwrapped, _ = pos.sat_position(late, 30.0 + 604800)
    assert np.allclose(wrapped, unwrapped)
    assert pos.sat_clock_error(late, 30.0, E) == pytest.approx(
        pos.sat_clock_error(late, 30.0 + 604800, E), abs=1e-15)
    assert pos.wrap_week(30.0 - (604800 - 16)) == 46.0


def test_coordinates_round_trip():
    for lla in (SKY_RECEIVER, (-33.9, 151.2, 0.0), (89.9, -120.0, 3000.0)):
        back = pos.ecef_to_lla(pos.lla_to_ecef(*lla))
        assert back == pytest.approx(lla, abs=1e-6)
    enu = pos.east_north_up(*SKY_RECEIVER[:2])
    assert np.allclose(enu @ enu.T, np.eye(3))
    up = pos.lla_to_ecef(SKY_RECEIVER[0], SKY_RECEIVER[1], 1000.0) - pos.lla_to_ecef(
        SKY_RECEIVER[0], SKY_RECEIVER[1], 0.0)
    assert enu @ up == pytest.approx([0, 0, 1000.0], abs=1e-6)


def test_troposphere_delay_is_metres_and_grows_towards_the_horizon():
    assert pos.troposphere_delay(90.0) == pytest.approx(2.44, abs=0.05)
    assert 4.5 < pos.troposphere_delay(30.0) < 5.2 < pos.troposphere_delay(10.0) < 15
    assert pos.troposphere_delay(90.0, 4000.0) < pos.troposphere_delay(90.0)


# --- one position ---------------------------------------------------------------------------

def test_the_solver_recovers_position_and_clock_from_the_centre_of_the_earth():
    t = SKY_TOW + 10.0
    fix = pos.solve_position(readings(t), EPHS, t + 0.0123, options=NO_ATMOSPHERE)
    assert np.linalg.norm(fix["position"] - TRUE_POS) < 0.01           # 1 cm
    assert fix["clock_error"] == pytest.approx(0.0123, abs=1e-10)
    assert max(abs(s["residual"]) for s in fix["satellites"].values()) < 0.01
    far = [np.linalg.norm(h[:3] - TRUE_POS) for h in fix["history"]]
    assert far[0] > 1e5 and far[4] < 1.0                               # Newton: fast
    for s in fix["satellites"].values():
        assert 0 < s["elevation"] < 90 and 0 <= s["azimuth"] < 360


def test_four_satellites_are_enough_and_three_are_not():
    t = SKY_TOW + 10.0
    four = {p: v for p, v in readings(t).items() if p != 30}
    fix = pos.solve_position(four, EPHS, t, options=NO_ATMOSPHERE)
    assert np.linalg.norm(fix["position"] - TRUE_POS) < 0.5     # weaker geometry
    sats, _, _ = measurements(3.0)
    assert pos.fix_series({p: sats[p] for p in (5, 12, 17)}, FS) is None


@pytest.mark.parametrize("name, low, high", [
    ("satellite_clock", 1e4, 1e6),        # a_f0 is hundreds of microseconds
    ("relativity", 1.0, 100.0),
    ("group_delay", 0.3, 30.0),
    ("earth_rotation", 5.0, 100.0),
])
def test_what_each_correction_is_worth(name, low, high):
    t = SKY_TOW + 10.0
    fix = pos.solve_position(readings(t), EPHS, t,
                             options=replace(NO_ATMOSPHERE, **{name: False}))
    assert low < np.linalg.norm(fix["position"] - TRUE_POS) < high


def test_the_troposphere_correction_removes_the_delay_it_models():
    t = SKY_TOW + 10.0
    clean = pos.solve_position(readings(t), EPHS, t, options=NO_ATMOSPHERE)
    delayed = {p: v - pos.troposphere_delay(clean["satellites"][p]["elevation"], 46.0)
               / pos.C_LIGHT for p, v in readings(t).items()}       # air: signals arrive later
    off = pos.solve_position(delayed, EPHS, t, options=NO_ATMOSPHERE)
    on = pos.solve_position(delayed, EPHS, t)
    assert np.linalg.norm(off["position"] - TRUE_POS) > 2.0
    assert np.linalg.norm(on["position"] - TRUE_POS) < 0.05


def test_dop_says_how_noise_is_amplified():
    t = SKY_TOW + 10.0
    fix = pos.solve_position(readings(t), EPHS, t, options=NO_ATMOSPHERE)
    dop = pos.dilution_of_precision(fix["geometry"], *SKY_RECEIVER[:2])
    assert 0.5 < dop["hdop"] < 5 and 0.5 < dop["vdop"] < 8
    assert dop["pdop"] == pytest.approx(np.hypot(dop["hdop"], dop["vdop"]))
    assert dop["gdop"] > dop["pdop"]
    flat = np.tile(fix["geometry"][0], (4, 1))                  # four times the same direction
    assert pos.dilution_of_precision(flat, 0, 0)["gdop"] == float("inf") \
        or pos.dilution_of_precision(flat, 0, 0)["gdop"] > 1e6


# --- from tracking's code timing to a series of positions -----------------------------------

def test_satellite_time_reads_the_clock_between_two_code_periods():
    begin = np.array([100.0, 4100.0, 8100.0, 12100.0])
    assert pos.satellite_time(begin, 50.0, 4100.0) == pytest.approx(50.001)
    assert pos.satellite_time(begin, 50.0, 6100.0) == pytest.approx(50.0015)
    assert np.allclose(pos.satellite_time(begin, 50.0, np.array([100.0, 9100.0])),
                       [50.0, 50.00225])


def test_fix_series_finds_the_position_the_time_and_the_oscillator_error():
    sats, t0, _ = measurements(clock_drift=-2e-7)
    series = pos.fix_series(sats, FS, options=NO_ATMOSPHERE)
    s = pos.summarize(series)
    assert np.linalg.norm(np.array(s["ecef"]) - TRUE_POS) < 0.05
    assert (s["lat"], s["lon"]) == pytest.approx(SKY_RECEIVER[:2], abs=1e-6)
    assert s["height"] == pytest.approx(SKY_RECEIVER[2], abs=0.05)
    assert s["clock_drift"] == pytest.approx(-2e-7, rel=1e-3)
    assert s["gps_time_at_sample0"] == pytest.approx(t0, abs=1e-9)
    assert s["n_fixes"] == len(series["t"]) > 50 and s["satellites"] == [5, 12, 17, 23, 30]
    assert s["horizontal_rms"] < 0.05 and s["residual_rms"] < 0.05


def test_ranging_noise_scatters_the_positions_as_the_dop_predicts():
    sats, _, _ = measurements(noise_m=1.0)
    s = pos.summarize(pos.fix_series(sats, FS, options=NO_ATMOSPHERE))
    # Interpolating between two noisy code periods averages them a little.
    assert 0.4 * s["dop"]["hdop"] < s["horizontal_rms"] < 1.3 * s["dop"]["hdop"]
    assert 0.4 * s["dop"]["vdop"] < s["up_std"] < 1.3 * s["dop"]["vdop"]
    assert np.linalg.norm(np.array(s["ecef"]) - TRUE_POS) < 1.5      # the mean is better


def test_the_doppler_check_agrees_only_at_the_right_place():
    sats, _, _ = measurements(clock_drift=-2e-7)
    s = pos.summarize(pos.fix_series(sats, FS, options=NO_ATMOSPHERE))
    good = pos.doppler_check(sats, s, FS)
    assert good["spread"] < 0.05
    assert good["common"] == pytest.approx(L1_HZ * 2e-7, abs=0.1)
    assert good["from_clock_drift"] == pytest.approx(good["common"], abs=0.1)
    wrong = dict(s, ecef=list(np.array(s["ecef"]) + [3000.0, 0.0, 0.0]))    # 3 km away
    assert pos.doppler_check(sats, wrong, FS)["spread"] > 0.5


def test_fix_files_round_trip(tmp_path):
    sats, _, _ = measurements(6.0)
    series = pos.fix_series(sats, FS, options=NO_ATMOSPHERE)
    s = pos.summarize(series)
    pos.save_fix(tmp_path / pos.FIX_JSON, tmp_path / pos.FIXES_CSV, series, s,
                 NO_ATMOSPHERE, {"week": 2444})
    doc = pos.load_fix(tmp_path / pos.FIX_JSON)
    assert doc["summary"]["lat"] == s["lat"] and doc["week"] == 2444
    assert doc["options"]["troposphere"] is False
    assert [r["prn"] for r in doc["satellites"]] == [5, 12, 17, 23, 30]
    fixes = pos.load_fixes(tmp_path / pos.FIXES_CSV)
    assert len(fixes["t_s"]) == s["n_fixes"]
    assert fixes["lat"][0] == pytest.approx(series["lat"][0], abs=1e-7)
