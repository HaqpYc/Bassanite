"""Tests for the Track tab: the loop discriminators, bit sync, running track.py
as a separate process, reading its results back, and the PLL try-it. All on
synthetic signals; the real results/ folder is never touched."""
import asyncio
import time

import numpy as np
import pytest
from nicegui import ui
from nicegui.testing import User

import tabs.acquisition as acq
import tabs.tracking as trk
from bassanite.acquisition import code_drift_chips_per_s
from bassanite.tracking import (DETAIL_KEYS, MS_PER_BIT, bit_sync, bit_sync_scores,
                                costas_error, dll_error)
from state import IQSource, state
from bassanite.synthetic import CODE_PHASE_SAMP, DOPPLER, FS, PRN, synthetic_gps, write_int8

SECONDS = 0.6


# --- discriminators and bit sync ---------------------------------------------------------------

def test_costas_error_ignores_data_flips():
    for ip, qp in ((100.0, 20.0), (50.0, -30.0), (-80.0, 5.0)):
        assert costas_error(ip, qp) == pytest.approx(costas_error(-ip, -qp))
    assert costas_error(100.0, 0.0) == 0.0
    assert costas_error(1.0, 1.0) == pytest.approx(1 / 8)        # 45 degrees, in cycles
    assert costas_error(0.0, 5.0) == pytest.approx(0.25, abs=1e-6)  # no division by 0


def test_dll_error_is_zero_when_balanced_and_scales_with_misalignment():
    assert dll_error(3.0, 4.0, 4.0, 3.0) == pytest.approx(0.0)   # |E| = |L| = 5
    # Ideal triangle, peak delta chips off: |E| = 0.5 + delta, |L| = 0.5 - delta
    for delta in (0.05, 0.1, 0.2):
        assert dll_error(0.5 + delta, 0, 0.5 - delta, 0) == pytest.approx(2 * delta)


def test_bit_sync_finds_the_bit_edge():
    rng = np.random.default_rng(4)
    offset = 7
    bits = rng.choice([-1.0, 1.0], size=60)
    ip = np.repeat(bits, MS_PER_BIT)[MS_PER_BIT - offset:] * 10 + rng.normal(0, 3, 1200 - 13)
    scores = bit_sync_scores(ip)
    assert int(np.argmax(scores)) == offset
    off, symbols, out = bit_sync(ip)
    assert off == offset
    assert np.array_equal(out[:20], (bits[1:21] > 0).astype(np.int8))


# --- running track.py, reading results -----------------------------------------------------------

@pytest.fixture(scope="module")
def tracked(tmp_path_factory):
    """Scan and track a synthetic recording through the real command-line script."""
    tmp = tmp_path_factory.mktemp("track")
    src = IQSource(write_int8(tmp / "synthetic.bin", synthetic_gps(SECONDS, cn0_dbhz=46.0)),
                   "int8", FS)
    seed_csv = tmp / "scan_results.csv"
    acq.run_scan(src, acq.ScanSettings(dop_range_hz=3000, ms=10), csv_path=seed_csv)
    out = tmp / "03_tracking"
    run = trk.TrackingRun.create(src, 0.0, SECONDS, trk.TrackSettings(), seed_csv, out)
    run.start()
    deadline = time.monotonic() + 120
    while run.poll() is None and time.monotonic() < deadline:
        time.sleep(0.2)
    assert run.succeeded(), run.log_text()
    frac, text = run.progress()
    moved = run.finish()
    run.cleanup()
    assert not run.out_dir.exists()
    return {"src": src, "out": out, "moved": moved, "progress": (frac, text),
            "seed_csv": seed_csv}


def test_run_moves_every_result_and_reports_progress(tracked):
    assert set(tracked["moved"]) == {"track_PRN07.npz", "track_PRN07.png",
                                     "tracking_summary.csv", "tracking_summary.meta.json"}
    frac, text = tracked["progress"]
    assert text.startswith(f"PRN {PRN} (1 of 1)") and 0 <= frac <= 1
    with np.load(tracked["out"] / "track_PRN07.npz") as d:
        assert all(k in d.files for k in DETAIL_KEYS)


def test_results_read_back(tracked):
    rows, meta = trk.saved_tracking(tracked["src"], tracked["out"])
    assert [r["prn"] for r in rows] == [PRN] and rows[0]["locked"]
    assert meta["settings"]["skip_seconds"] == 0.0
    other = IQSource(tracked["seed_csv"].with_name("synthetic.bin"), "int8", 2e6)
    assert trk.saved_tracking(other, tracked["out"]) is not None   # same file, same size
    assert trk.read_summary(trk.summary_path(tracked["out"]))[0]["nbits"] > 20


def test_track_analysis(tracked):
    tr = trk.load_track(trk.track_file(PRN, tracked["out"]), 0.0)
    assert tr.prn == PRN and tr.n_ms > 500 and tr.detail is not None
    late = slice(300, tr.n_ms)
    # The carrier is locked: energy on I, small |Q|/|I| per bit
    assert np.median(trk.qi_per_bit(tr.i, tr.q, tr.bit_offset)[10:]) < 0.3
    # The code is centred: early and late balanced
    e, p, l_ = trk.epl(tr, late)
    assert e == pytest.approx(l_, rel=0.1) and p > e
    # The code slides at the rate the Doppler predicts
    drift = trk.code_drift_chips(tr)
    rate = np.polyfit(np.arange(tr.n_ms)[late] / 1000, drift[late], 1)[0]
    assert rate == pytest.approx(float(code_drift_chips_per_s(DOPPLER)), abs=0.1)
    # Windows and bits
    assert trk.window_slice(tr.n_ms, 50, 200) == slice(0, 50)
    assert trk.window_slice(tr.n_ms, 10_000, 200) == slice(tr.n_ms - 200, tr.n_ms)
    assert trk.bit_at(tr, tr.bit_offset + 45) == 2
    y, k = trk.decimate(np.arange(10.0), 3)
    assert k == 4 and y.tolist() == [1.5, 5.5]


def test_figures_use_title_objects(tracked):
    tr = trk.load_track(trk.track_file(PRN, tracked["out"]), 0.0)
    sl = trk.window_slice(tr.n_ms, 400, 200)
    figs = [trk.iq_figure(tr.i[sl], tr.q[sl], 100.0), trk.epl_figure(trk.epl(tr, sl), 100.0),
            trk.prompt_figure(tr, sl, 100.0), trk.bits_figure(tr, 5),
            trk.sync_figure(bit_sync_scores(tr.i), tr.bit_offset),
            trk.curve_figure(tr, tr.doppler, "Doppler (Hz)", (0.2, 0.4)),
            trk.quality_figure(tr, (0.2, 0.4)), trk.discriminator_figure(tr, sl)]
    for fig in figs:
        for axis in ("xaxis", "yaxis"):
            if "title" in fig["layout"].get(axis, {}):
                assert isinstance(fig["layout"][axis]["title"], dict)
    # 200 ms window = 10 bit edges, each bit's value written on top
    assert len(trk.prompt_figure(tr, sl, 100.0)["layout"]["annotations"]) >= 9


def test_one_shared_run_at_a_time_and_cancel_leaves_nothing(tracked, tmp_path):
    target = tmp_path / "target"
    run = trk.start_tracking(tracked["src"], 0.0, SECONDS, trk.TrackSettings(),
                             tracked["seed_csv"], target)
    try:
        assert trk.current_run() is run
        with pytest.raises(RuntimeError, match="already running"):
            trk.start_tracking(tracked["src"], 0.0, SECONDS, trk.TrackSettings(),
                               tracked["seed_csv"], target)
        run.cancel()
        outcome = None
        for _ in range(100):
            outcome = trk.settle_run()
            if outcome is not None:
                break
            time.sleep(0.1)
        assert outcome[0] == "info" and "cancelled" in outcome[1]
        assert trk.current_run() is None
        assert not run.out_dir.exists() and not target.exists()
    finally:
        trk.stop_all_runs()
        trk._current = None


def test_estimate_and_command():
    src = IQSource("x.bin", "int8", 4e6)
    assert trk.estimate_minutes(30.0, 8) == pytest.approx(1.0)


def test_default_tracks_the_whole_recording(tmp_path):
    rec = tmp_path / "rec.bin"
    rec.write_bytes(bytes(2 * 4_000_000 * 3))                  # 3 s of int8 at 4 MSPS
    src = IQSource(rec, "int8", 4e6)
    assert trk.seconds_to_track(src, 0.5, trk.TrackSettings()) == pytest.approx(2.5)
    assert trk.seconds_to_track(src, 0.5, trk.TrackSettings(seconds=1.0)) == 1.0
    assert "36 s" in trk.too_short_for_decode(30.0)
    cmd = trk.track_command(src, 2.0, 30.0, trk.TrackSettings(prns=(5, 20)), "seeds.csv",
                            "out")
    assert cmd[cmd.index("--skip-seconds") + 1] == "2.0"
    assert cmd[cmd.index("--prns") + 1] == "5,20" and "--progress" in cmd


# --- try it ------------------------------------------------------------------------------------

def test_pll_bandwidth_too_wide_loses_lock():
    x = synthetic_gps(1.0, cn0_dbhz=45.0)
    good = trk.try_pll(x, FS, PRN, CODE_PHASE_SAMP, DOPPLER + 12, 12)
    wild = trk.try_pll(x, FS, PRN, CODE_PHASE_SAMP, DOPPLER + 12, 300)
    assert good["locked"] and not wild["locked"]
    assert np.std(wild["doppler"][500:]) > 10 * np.std(good["doppler"][500:])


def test_break_it_switches_break_the_lock():
    from bassanite.tracking import TrackOptions
    x = synthetic_gps(1.5, cn0_dbhz=45.0)          # 1500 Hz: code period 3999.996 samples
    good = trk.try_pll(x, FS, PRN, CODE_PHASE_SAMP, DOPPLER + 12)
    atan2 = trk.try_pll(x, FS, PRN, CODE_PHASE_SAMP, DOPPLER + 12,
                        options=TrackOptions(discriminator="atan2"))
    rounded = trk.try_pll(x, FS, PRN, CODE_PHASE_SAMP, DOPPLER + 12,
                          options=TrackOptions(integer_period=False))
    assert good["locked"] and not atan2["locked"] and not rounded["locked"]
    assert "the loop chases it" in trk.break_verdict(("atan2",), atan2)
    assert trk.break_verdict((), good).startswith("✅")


def test_loop_map_names_the_functions_and_marks_the_bug():
    svg = trk.loops_svg()
    for name in ("correlate()", "costas_error()", "dll_error()", "LoopFilter (PLL)",
                 "code_rate()", "carrier aiding"):
        assert name in svg
    assert "⚠" not in svg
    broken = trk.loops_svg(("atan2", "rounded"))
    assert "atan2: chases every bit flip" in broken and "rounded 4000-sample" in broken


# --- the page -------------------------------------------------------------------------------------

async def test_tab_runs_tracking_into_a_temporary_folder(user: User, tmp_path,
                                                          monkeypatch) -> None:
    src = IQSource(write_int8(tmp_path / "rec.bin", synthetic_gps(SECONDS, cn0_dbhz=46.0)),
                   "int8", FS)
    acq.run_scan(src, acq.ScanSettings(dop_range_hz=3000, ms=10))   # tests' results root
    old_source = state.source
    state.source = src
    try:
        await user.open("/")
        await user.should_see("Not tracked yet", retries=20)
        user.find(kind=ui.button, content="Track the satellites").click()
        for _ in range(600):
            if trk.summary_path(trk.track_dir(src)).is_file():
                break
            await asyncio.sleep(0.1)
        assert trk.summary_path(trk.track_dir(src)).is_file()
        await user.should_see("1 of 1 satellites locked", retries=30)
        await user.should_see(f"PRN {PRN} up close (locked)", retries=30)
    finally:
        state.source = old_source
