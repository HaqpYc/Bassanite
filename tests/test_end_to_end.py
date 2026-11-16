"""The whole receiver on a synthetic recording with a known answer: samples in,
position out, through the same functions as the command-line stages.

Slow (about 20 s of signal for five satellites is generated and tracked), so it
is skipped by default:

    python -m pytest tests -m slow
"""
import numpy as np
import pytest

from bassanite import navigation as nav
from bassanite import position as pos
from bassanite.acquisition import scan, scan_seconds
from bassanite.iq_loader import load_iq, open_iq
from bassanite.synthetic import SKY_RECEIVER, read_truth, write_sky_recording
from bassanite.tracking import summarize, track_prn

FS = 4e6
SECONDS = 20.0            # subframes 1, 2 and 3 are complete after 19 s
CLOCK_DRIFT = -3e-7       # the receiver's oscillator, 0.3 ppm slow


@pytest.mark.slow
def test_from_samples_to_the_known_position(tmp_path):
    path = write_sky_recording(tmp_path / "sky.bin", SECONDS, clock_drift=CLOCK_DRIFT)
    truth = read_truth(path)

    # Stage 02: all five satellites, each near its true Doppler.
    found = [r for r in scan(load_iq(str(path), "int8", FS, seconds=scan_seconds(10)),
                             FS, -10000, 10000, 250, 10, 5.0) if r["detected"]]
    true = {s["prn"]: s for s in truth["satellites"]}
    assert sorted(r["prn"] for r in found) == sorted(true)
    for r in found:
        assert abs(r["fine_doppler_hz"] - true[r["prn"]]["doppler_hz"]) < 40

    # Stages 03 and 04: track, then read each message.
    x = open_iq(str(path), "int8", FS)
    satellites = {}
    for r in found:
        track = track_prn(x, FS, r["prn"], r["code_phase_samp"], r["fine_doppler_hz"],
                          refine=False)
        result = summarize(track)
        assert result["locked"], r["prn"]
        sat = nav.decode_satellite(result["bits"], result["bit_offset"])
        assert nav.usable(sat), (r["prn"], sat["problems"])
        assert sat["ephemeris"] == truth["ephemerides"][str(r["prn"])]
        satellites[r["prn"]] = {"begin": track["code_phase_samp"],
                                "doppler_hz": track["doppler_hz"],
                                "period0_time": sat["period0_time"],
                                "ephemeris": sat["ephemeris"]}

    # Stage 05: the position, the time and the oscillator error. The synthetic
    # sky has no atmosphere, so that correction is off.
    options = pos.SolveOptions(troposphere=False)
    s = pos.summarize(pos.fix_series(satellites, FS, options=options))
    east, north, up = pos.east_north_up(*SKY_RECEIVER[:2]) @ (
        np.array(s["ecef"]) - pos.lla_to_ecef(*SKY_RECEIVER))
    print(f"\nerror of the mean position: {east:+.2f} m east, {north:+.2f} m north, "
          f"{up:+.2f} m up; one position scatters by {s['horizontal_rms']:.1f} m "
          f"horizontally, {s['up_std']:.1f} m up; HDOP {s['dop']['hdop']:.1f}")
    assert np.hypot(east, north) < 5.0 and abs(up) < 8.0
    assert s["gps_time_at_sample0"] == pytest.approx(truth["gps_time_at_sample0"], abs=50e-9)
    assert s["clock_drift"] == pytest.approx(CLOCK_DRIFT, abs=3e-8)
    check = pos.doppler_check(satellites, s, FS)
    assert check["spread"] < 1.0
