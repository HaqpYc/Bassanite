"""Tests for the Find satellites tab: Gold codes, the search grid, the Try-it
measurements and the persistence check, on synthetic signals (no real recording)."""
import asyncio
import json

import numpy as np
import pytest
from nicegui.testing import User

import tabs.acquisition as acq
from bassanite.acquisition import acquire_prn, cn0_from_peak, code_drift_chips_per_s
from common.ca_code import CODE_LENGTH, G2_TAPS, ca_code, register_states
from state import IQSource, state
from synthetic import CODE_PHASE_SAMP, DOPPLER, FS, PRN, synthetic_gps, write_int8


# --- meet the codes -------------------------------------------------------------------------

def test_register_states_produce_the_code():
    for prn in (1, 7, 20, 32):
        g1, g2, chips = register_states(prn)
        s1, s2 = G2_TAPS[prn]
        assert g1.shape == g2.shape == (CODE_LENGTH, 10)
        assert np.array_equal(chips, g1[:, 9] ^ g2[:, s1 - 1] ^ g2[:, s2 - 1])
        assert np.array_equal(1 - 2 * chips, ca_code(prn))
        assert np.array_equal(g1[1][1:], g1[0][:-1])              # bits shift right
        assert g1[1][0] == g1[0][2] ^ g1[0][9]                    # G1 feedback 3, 10


def test_gold_codes_are_three_valued():
    allowed = {-65, -1, 63}
    codes = {p: ca_code(p) for p in range(1, 33)}
    for a in codes:
        auto = acq.circular_correlation(codes[a], codes[a])
        assert auto[0] == CODE_LENGTH and set(auto[1:]) <= allowed
        for b in codes:
            if b != a:
                assert set(acq.circular_correlation(codes[a], codes[b])) <= allowed


def test_code_correlations_are_centred_on_zero_shift():
    lags, auto, cross = acq.code_correlations(20, 5)
    assert len(lags) == CODE_LENGTH and lags[0] == -511 and lags[-1] == 511
    assert auto[lags == 0][0] == CODE_LENGTH and abs(cross).max() <= 65


def test_lfsr_diagram_and_step_text():
    svg = acq.lfsr_svg(20, 0)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert svg.count("<rect") == 21                     # 2 x 10 stages + output chip
    assert "PRN 20: stages 4 ⊕ 7" in svg
    # PRN 1 starts 1100100000 (the textbook check)
    assert acq.chips_so_far(1, 9) == "1100100000"
    assert acq.chip_explanation(1, 0).endswith("chip = 1 ⊕ 0 = 1, sent as −1.")


def test_max_pool_keeps_the_peak():
    a = np.zeros((3, 4000))
    a[1, 1234] = 9.0
    pooled, k = acq.max_pool(a, 512)
    assert k == 8 and pooled.shape == (3, 500)
    assert pooled.max() == 9.0 and pooled[1].argmax() == 1234 // 8
    pooled, k = acq.max_pool(np.arange(10.0)[None], 3)   # 10 = 4 + 4 + 2 (padded)
    assert k == 4 and pooled.tolist() == [[3.0, 7.0, 9.0]]


# --- search, results on disk -------------------------------------------------------------------

@pytest.fixture(scope="module")
def signal():
    return synthetic_gps(0.06, cn0_dbhz=48.0)


@pytest.fixture
def recording(tmp_path):
    path = write_int8(tmp_path / "synthetic.bin", synthetic_gps(0.06, cn0_dbhz=48.0))
    return IQSource(path, "int8", FS)


def test_scan_writes_results_and_provenance(recording, tmp_path):
    csv_path = tmp_path / "scan_results.csv"
    settings = acq.ScanSettings(dop_range_hz=3000, ms=5)
    results = acq.run_scan(recording, settings, csv_path=csv_path)
    assert acq.detected_prns(results) == [PRN]
    best = results[0]
    assert best["code_phase_samp"] == CODE_PHASE_SAMP and best["doppler_hz"] == DOPPLER

    meta = json.loads(csv_path.with_name("scan_results.meta.json").read_text())
    assert meta["settings"]["ms"] == 5 and meta["settings"]["dop_range_hz"] == 3000
    saved, saved_settings = acq.saved_results(recording, csv_path)
    assert saved == results and saved_settings == settings

    other = IQSource(write_int8(tmp_path / "other.bin", synthetic_gps(0.03, seed=3)), "int8", FS)
    assert acq.saved_results(other, csv_path) is None


def test_scan_results_go_to_the_recordings_own_folder(recording, results_root):
    acq.run_scan(recording, acq.ScanSettings(dop_range_hz=1000, ms=2))
    assert acq.results_csv(recording) == results_root / "synthetic" / "acquisition" /         "scan_results.csv"
    assert acq.results_csv(recording).is_file()
    assert acq.saved_results(recording) is not None


def test_window_never_runs_past_the_end(recording):
    assert acq.window_start(recording, 10.0, 10) == pytest.approx(0.06 - 0.015)
    with pytest.raises(ValueError, match="too short"):
        acq.load_window(recording, 0.0, 100)


def test_prn_view_finds_the_satellite(signal):
    s = acq.ScanSettings(dop_range_hz=3000)
    v = acq.prn_view(signal, FS, PRN, s)
    assert (v.code_phase_samp, v.doppler_hz) == (CODE_PHASE_SAMP, DOPPLER)
    assert abs(v.fine_doppler_hz - DOPPLER) <= 20
    assert v.surface.shape == (len(v.dopplers), 500)
    assert v.code_slice.argmax() == CODE_PHASE_SAMP
    assert v.code_slice.max() == pytest.approx(v.peak_over_mean)
    i, j = np.unravel_index(v.surface.argmax(), v.surface.shape)
    assert v.dopplers[i] == DOPPLER
    assert abs(v.surface_chips[j] - CODE_PHASE_SAMP * CODE_LENGTH / 4000) < 2


def test_peak_gives_a_rough_cn0(signal):
    _, _, ratio = acquire_prn(signal, FS, PRN, -3000, 3000, 250, 10)
    assert cn0_from_peak(ratio) == pytest.approx(48.0, abs=2.0)


# --- try it ---------------------------------------------------------------------------------------

def test_doppler_error_follows_sinc_squared():
    x = synthetic_gps(0.03, cn0_dbhz=55.0)
    ref = acq.signal_power(acq.try_row(x, FS, PRN, DOPPLER, 10), CODE_PHASE_SAMP)
    for df, expected in ((250, 0.81), (500, 0.405), (1000, 0.0), (1430, 0.047)):
        row = acq.try_row(x, FS, PRN, DOPPLER + df, 10)
        left = acq.signal_power(row, CODE_PHASE_SAMP) / ref
        assert left == pytest.approx(expected, abs=0.03)
        assert float(acq.doppler_loss(df)) == pytest.approx(expected, abs=0.005)


def test_integration_calms_the_noise(signal):
    spikes = []
    for nms in (1, 10, 20):
        row = acq.try_row(signal, FS, PRN, DOPPLER, nms)
        spikes.append(acq.highest_noise(row, CODE_PHASE_SAMP, 8) / row.mean())
    assert spikes[0] > spikes[1] > spikes[2] and spikes[2] < 3


# --- persistence --------------------------------------------------------------------------------

def test_persistence_measures_the_drift_the_doppler_predicts(tmp_path):
    doppler = 4000.0                              # -2.6 chips/s
    x = synthetic_gps(1.0, cn0_dbhz=50.0, sats=((PRN, CODE_PHASE_SAMP, doppler),))
    src = IQSource(write_int8(tmp_path / "long.bin", x), "int8", FS)
    calls = []
    p = acq.persistence(src, acq.ScanSettings(dop_range_hz=5000), [PRN, 1], [PRN],
                        points=4, progress=lambda d, t: calls.append((d, t)))
    assert p["times"].tolist() == [0.0, 0.328, 0.657, 0.985]
    assert calls[-1] == (8, 8)
    d = p["drift"][PRN]
    assert d["predicted"] == pytest.approx(float(code_drift_chips_per_s(doppler)), abs=0.05)
    assert d["measured"] == pytest.approx(d["predicted"], abs=0.3)
    assert abs(d["doppler"] - doppler) <= 20
    assert 1 not in p["drift"]                    # no fit for undetected PRNs


# --- the page ----------------------------------------------------------------------------------

async def test_tab_steps_the_code_and_scans_without_touching_real_results(
        user: User, recording, tmp_path, monkeypatch) -> None:
    csv_path = acq.results_csv(recording)          # under the test's results root
    # A narrower Doppler search, so the scan takes a moment rather than seconds.
    monkeypatch.setattr(acq, "DEFAULT_SETTINGS", acq.ScanSettings(dop_range_hz=3000))
    old_source = state.source
    state.source = recording
    try:
        await user.open("/")
        await user.should_see("Meet the codes")
        await user.should_see("Chip 1 of 1023", retries=10)
        user.find("Next chip").click()
        await user.should_see("Chip 2 of 1023")
        await user.should_see("Not scanned yet", retries=20)

        user.find("Scan all 32 satellites").click()
        for _ in range(100):
            if csv_path.is_file():
                break
            await asyncio.sleep(0.1)
        assert csv_path.is_file()
        await user.should_see(f"1 satellite found: PRN {PRN}", retries=30)
        await user.should_see(f"PRN {PRN}: peak at code phase", retries=30)
    finally:
        state.source = old_source



def test_axis_titles_use_the_plotly_3_form():
    """Regression: plotly.js 3+ silently drops `title: "text"` (no axis titles)."""
    from plots import figure
    fig = figure([], xaxis={"title": "a"}, yaxis={"title": {"text": "b"}},
                 scene={"zaxis": {"title": "c"}, "camera": {}})
    assert fig["layout"]["xaxis"]["title"] == {"text": "a"}
    assert fig["layout"]["yaxis"]["title"] == {"text": "b"}
    assert fig["layout"]["scene"]["zaxis"]["title"] == {"text": "c"}
    v = acq.prn_view(synthetic_gps(0.02), FS, PRN, acq.ScanSettings(dop_range_hz=1000, ms=2))
    for three_d in (True, False):
        lay = acq.surface_figure(v, three_d)["layout"]
        axes = [lay["scene"][k] for k in ("xaxis", "yaxis", "zaxis")] if three_d \
            else [lay["xaxis"], lay["yaxis"]]
        assert all(isinstance(a["title"], dict) for a in axes)
