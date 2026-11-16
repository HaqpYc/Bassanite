"""refine_doppler computes only the 3 code-phase columns it needs; it must give the
same Doppler as reading them off the full correlation surface (the old method)."""
import numpy as np
import pytest

from bassanite import acquisition
from bassanite.iq_loader import open_iq
from bassanite.synthetic import FS, synthetic_gps, write_int8

SPAN, STEP = 250.0, 10.0


def reference(x, fs, prn, doppler, code_phase, nms, span_hz, step_hz):
    """The old implementation: a full surface, then 3 columns."""
    dops = np.arange(doppler - span_hz, doppler + span_hz + step_hz / 2, step_hz)
    surf = acquisition.correlation_surface(x, fs, prn, dops, min(nms, len(x) // int(round(fs * 1e-3))))
    cols = (int(round(code_phase)) + np.arange(-1, 2)) % surf.shape[1]
    return float(dops[surf[:, cols].max(axis=1).argmax()])


CASES = [  # prn, code phase (samples), true Doppler, C/N0
    (7, 1234, 2130.0, 48.0),
    (7, 3999, -1500.0, 42.0),     # code phase at the end: columns wrap past spms
    (13, 0, 3610.0, 45.0),        # ... and at the start
    (22, 2500, -3995.0, 40.0),
]


@pytest.mark.parametrize("prn, cp, dop, cn0", CASES)
def test_matches_the_surface_method_on_an_array(prn, cp, dop, cn0):
    x = synthetic_gps(0.045, cn0_dbhz=cn0, sats=((prn, cp, dop),))
    search = round(dop / 250) * 250.0              # where a 250 Hz search grid would put it
    got = acquisition.refine_doppler(x, FS, prn, search, cp, 40, SPAN, STEP)
    assert got == reference(x, FS, prn, search, cp, 40, SPAN, STEP)
    assert isinstance(got, float)
    assert abs(got - dop) <= 30                    # and it is actually close to the truth


@pytest.mark.parametrize("prn, cp, dop, cn0", CASES[:2])
def test_matches_the_surface_method_on_a_reader(tmp_path, prn, cp, dop, cn0):
    x = synthetic_gps(0.045, cn0_dbhz=cn0, sats=((prn, cp, dop),))
    reader = open_iq(write_int8(tmp_path / "s.bin", x), "int8", FS)
    search = round(dop / 250) * 250.0
    assert (acquisition.refine_doppler(reader, FS, prn, search, cp, 40, SPAN, STEP)
            == reference(reader, FS, prn, search, cp, 40, SPAN, STEP))


def test_short_signal_uses_what_there_is():
    x = synthetic_gps(0.015, cn0_dbhz=48.0, sats=((7, 1234, 2130.0),))
    assert (acquisition.refine_doppler(x, FS, 7, 2000.0, 1234, 40, SPAN, STEP)
            == reference(x, FS, 7, 2000.0, 1234, 40, SPAN, STEP))


def test_scan_seconds_leaves_room_for_the_refinement_at_the_end():
    # a 1 s recording: the last window starts early enough to hold FINE_MS
    start = acquisition.window_start(1.0, 5.0, 10)
    assert start + acquisition.scan_seconds(10) == pytest.approx(1.0)
    assert acquisition.window_start(0.02, 5.0, 10) == 0.0     # too short: start at 0
