"""Tests for the Decode and Position tabs, on the synthetic sky: five satellites on
GPS orbits above a receiver at Greenwich (bassanite/synthetic.py). The tracking
results are written directly (write_sky_tracking), so nothing slow runs, and the
real results/ folder is never touched."""
import asyncio
import json
import os
from datetime import datetime, timezone

import numpy as np
import pytest
from nicegui import ui
from nicegui.testing import User

import stages
import tabs.decode as dec
import tabs.position as pst
from bassanite import navigation as nav
from bassanite import position as pos
from bassanite.results import fix_json, fixes_csv, navigation_json, tracking_dir
from bassanite.synthetic import (FS, SKY_RECEIVER, default_sky, sky_tracks, sky_truth,
                                 truth_path, write_sky_tracking)
from state import IQSource, state

SECONDS = 20.0
NEAR = datetime(2026, 11, 10, tzinfo=timezone.utc)


def make_sky(tmp_path, name="sky", seconds=SECONDS, prns=None, noise_m=1.0) -> IQSource:
    """A recording (0.3 s of noise: these two tabs never read samples) with its
    truth file and its stage 03 results in the tests' results folder."""
    rec = tmp_path / f"{name}.bin"
    np.random.default_rng(0).integers(-8, 9, int(2 * FS * 0.3), dtype=np.int8).tofile(rec)
    ephs = {p: e for p, e in default_sky().items() if prns is None or p in prns}
    truth_path(rec).write_text(json.dumps(sky_truth(seconds, ephs)), encoding="utf-8")
    write_sky_tracking(tracking_dir(rec), sky_tracks(seconds, ephs, noise_m=noise_m))
    return IQSource(rec, "int8", FS)


@pytest.fixture
def sky(tmp_path):
    return make_sky(tmp_path)


# --- Decode: running, files, status -------------------------------------------------------

def test_run_decode_writes_what_the_command_line_writes(sky):
    satellites = dec.run_decode(sky)
    assert sorted(satellites) == [5, 12, 17, 23, 30]
    assert all(nav.usable(s) for s in satellites.values())
    saved = nav.load_navigation(navigation_json(sky.path))
    assert saved[12]["ephemeris"] == default_sky()[12] and saved[12]["usable"]
    assert dec.saved_decode(sky)
    status = stages.stage_status(stages.STAGE_BY_KEY["decode"], sky)
    assert status.state == "done" and "5 of 5 messages complete" in status.summary
    assert "5 of 5 satellites have a complete message" in dec.status_text(satellites)
    assert "go to step 5" in dec.status_text(satellites)


def test_a_short_recording_is_reported_not_decoded(tmp_path):
    src = make_sky(tmp_path, "short", seconds=9.0)       # subframe 1 only
    satellites = dec.run_decode(src)
    assert not any(nav.usable(s) for s in satellites.values())
    assert "no complete subframe 2, 3" in satellites[5]["problems"][0]
    status = stages.stage_status(stages.STAGE_BY_KEY["decode"], src)
    assert status.state == "attention" and "No satellite" in status.summary
    assert "A position needs 4" in dec.status_text(satellites)


def test_decode_results_older_than_tracking_are_stale(sky):
    dec.run_decode(sky)
    summary = tracking_dir(sky.path) / "tracking_summary.csv"
    later = navigation_json(sky.path).stat().st_mtime + 10
    os.utime(summary, (later, later))                    # tracking was run again
    assert not dec.saved_decode(sky)
    status = stages.stage_status(stages.STAGE_BY_KEY["decode"], sky)
    assert status.state == "attention" and "run this step again" in status.summary


# --- Decode: figures and texts ------------------------------------------------------------

def test_bitstream_figure_marks_preambles_parity_and_chance_matches(sky):
    sat = dec.run_decode(sky)[12]
    cls = dec.bit_classes(sat)
    start = sat["subframes"][0]["start_bit"]
    assert list(cls[start:start + 8]) == [3] * 8                  # preamble
    assert list(cls[start + 8:start + 24]) == [1] * 16            # data
    assert list(cls[start + 24:start + 30]) == [2] * 6            # parity
    assert cls[:start].max() == 0                                 # before the first subframe
    fig = dec.bitstream_figure(sat)
    z = np.array(fig["data"][0]["z"], dtype=float)
    assert z.shape == (-(-len(sat["bits"]) // 300), 300)
    assert np.nanmax(z) <= 7 and np.isnan(z[-1, -1])              # the last row is not full
    names = [d.get("name") for d in fig["data"]]
    assert ("preamble by chance" in names) == bool(sat["lookalikes"])
    assert fig["layout"]["xaxis"]["title"] == {"text": "bit in the row (300 bits = 6 s per row)"}


def test_anatomy_colours_each_field_and_the_table_lists_them(sky):
    sat = dec.run_decode(sky)[12]
    sf1 = next(sf for sf in sat["subframes"] if sf["number"] == 1)
    cat, names, labels = dec.anatomy(sf1)
    assert names[:5] == ["not used by a position", "parity", "preamble", "time of week",
                         "subframe number"]
    assert names[cat[270]] == "a_f0" and names[cat[291]] == "a_f0"     # bits 271 to 292
    assert names[cat[82]] == names[cat[210]] == "iodc"                  # a field in two parts
    assert sum(1 for _, _, name in labels if name == "iodc") == 2
    assert list(cat[294:300]) == [1] * 6
    fig = dec.anatomy_figure(sf1)
    assert np.array(fig["data"][0]["z"]).shape == (10, 30)
    assert fig["layout"]["yaxis"]["ticktext"][0] == "word 1 ✓"
    rows = {r["name"]: r for r in dec.field_rows(sf1)}
    assert set(rows) == {"week", "ura", "health", "iodc", "t_gd", "t_oc", "a_f2", "a_f1",
                         "a_f0"}
    assert rows["week"]["value"] == "396 weeks" and len(rows["a_f0"]["bits"]) == 22
    sf4 = {**sf1, "number": 4}
    assert dec.field_rows(sf4) == []


def test_time_text_reads_the_clock(sky):
    sat = dec.run_decode(sky)[5]
    sf = sat["subframes"][0]
    text = dec.time_text(sat, sf, NEAR)
    assert "**221,400 s**" in text and "Tuesday 13:30:00" in text
    assert "week 2444 since 1980" in text and "2026-11-10 13:29:42 UTC" in text
    assert f"code period {sat['bit_offset'] + 20 * sf['start_bit']:,}" in text
    assert dec.time_of_week_text(0) == "Sunday 00:00:00"
    assert dec.subframes_text(sat) == "1 2 3"
    rows = dec.handover_rows({5: sat})
    assert rows[0]["ok"] == "✅" and rows[0]["a"] == "26,560"


# --- Position: running, files, status ------------------------------------------------------

def test_run_solve_finds_the_receiver(sky):
    dec.run_decode(sky)
    doc = pst.run_solve(sky)
    s = doc["summary"]
    assert (s["lat"], s["lon"]) == pytest.approx(SKY_RECEIVER[:2], abs=3e-5)   # ~3 m
    assert s["satellites"] == [5, 12, 17, 23, 30] and s["n_fixes"] > 100
    assert doc["options"]["troposphere"] is False          # a synthetic sky has no air
    assert np.hypot(doc["truth"]["error_east"], doc["truth"]["error_north"]) < 3.0
    assert doc["utc_at_sample0"].startswith("2026-11-10T13:29:41")
    assert doc["doppler_check"]["spread"] < 0.1
    assert fixes_csv(sky.path).is_file() and pst.saved_fix(sky)
    status = stages.stage_status(stages.STAGE_BY_KEY["position"], sky)
    assert status.state == "done" and "51.47790° N, 0.00150° W, 46 m" in status.summary


def test_fewer_than_four_satellites_give_no_position(tmp_path):
    src = make_sky(tmp_path, "three", prns=(5, 12, 17))
    dec.run_decode(src)
    with pytest.raises(ValueError, match="needs 4 satellites"):
        pst.run_solve(src)
    status = stages.stage_status(stages.STAGE_BY_KEY["decode"], src)
    assert status.state == "attention" and "a fix needs 4" in status.summary


def test_position_older_than_decode_is_stale(sky):
    dec.run_decode(sky)
    pst.run_solve(sky)
    later = fix_json(sky.path).stat().st_mtime + 10
    os.utime(navigation_json(sky.path), (later, later))
    assert not pst.saved_fix(sky)
    assert stages.stage_status(stages.STAGE_BY_KEY["position"], sky).state == "attention"


# --- Position: figures and texts -----------------------------------------------------------

@pytest.fixture
def solved(sky):
    dec.run_decode(sky)
    return sky, pst.run_solve(sky)


def test_figures_from_the_saved_files(solved):
    src, doc = solved
    fixes = pos.load_fixes(fixes_csv(src.path))
    enu = pst.enu_of(doc, fixes)
    assert enu.shape == (doc["summary"]["n_fixes"], 3)
    assert abs(enu.mean(axis=0)).max() < 0.01              # around their mean
    fig = pst.scatter_figure(enu, [1.0, -2.0])
    assert [d["name"] for d in fig["data"]] == ["one position", "their mean",
                                                "the true position"]
    assert fig["layout"]["yaxis"]["scaleanchor"] == "x"
    assert len(pst.series_figure(fixes["t_s"], enu)["data"]) == 3
    sky_fig = pst.sky_figure(doc["satellites"], used=[5, 12, 17, 23])
    assert sky_fig["data"][0]["text"] == ["PRN 5", "PRN 12", "PRN 17", "PRN 23", "PRN 30"]
    assert sky_fig["data"][0]["marker"]["color"][-1] == pst.NOISE     # PRN 30 left out
    assert all(0 <= r <= 90 for r in sky_fig["data"][0]["r"])


def test_iterations_start_at_the_centre_of_the_earth(solved):
    _, doc = solved
    fig = pst.iterations_figure(doc["history"])
    y = fig["data"][0]["y"]
    assert y[0] == pytest.approx(6.365e6, rel=0.01) and y[-1] < 1e-2
    assert fig["layout"]["yaxis"]["type"] == "log"
    assert pst.iterations_text(doc["history"]).startswith(
        "Distance from the final position: 6,36")


def test_tables_and_texts(solved):
    _, doc = solved
    rows = pst.range_rows(doc)
    assert [r["prn"] for r in rows] == [5, 12, 17, 23, 30]
    assert all(60 < float(r["travel"]) < 90 for r in rows)             # milliseconds
    assert len(pst.doppler_rows(doc["doppler_check"])) == 5
    assert pst.doppler_text(doc["doppler_check"]).startswith("✅")
    assert pst.doppler_text({**doc["doppler_check"], "spread": 7.0}).startswith("⚠️")
    headline, details = pst.result_text(doc)
    assert headline == "51.47790° N, 0.00150° W, 46 m"
    assert "5 satellites" in details and "WGS 84 ellipsoid" in details
    assert "synthetic recording" in pst.truth_text(doc)
    assert pst.truth_text({**doc, "truth": None}) == ""


def test_try_it_shows_what_a_correction_and_a_satellite_are_worth(solved):
    src, doc = solved
    reference = pst.try_solve(src, [5, 12, 17, 23, 30], ())
    no_clock = pst.try_solve(src, [5, 12, 17, 23, 30], ("satellite_clock",))
    text = pst.shift_text(reference, no_clock, [5, 12, 17, 23, 30], ("satellite_clock",))
    assert text.startswith("Without the satellite's clock error") and " km " in text
    four = pst.try_solve(src, [5, 12, 17, 23], ())
    assert four["dop"]["pdop"] > reference["dop"]["pdop"]
    assert "With only PRN 5, 12, 17, 23" in pst.shift_text(reference, four, [5, 12, 17, 23], ())
    assert pst.try_solve(src, [5, 12, 17], ()) is None
    assert "fewer equations than unknowns" in pst.shift_text(reference, None, [5, 12, 17], ())


# --- the pages --------------------------------------------------------------------------------

async def test_decode_then_position_from_the_page(user: User, sky) -> None:
    old_source = state.source
    state.source = sky
    try:
        await user.open("/")
        await user.should_see("Not decoded yet", retries=20)
        await user.should_see("First decode the satellites' messages", retries=20)
        user.find(kind=ui.button, content="Decode the messages").click()
        await user.should_see("5 of 5 satellites have a complete message", retries=40)
        await user.should_see("One subframe, field by field")
        await user.should_see("Not computed yet", retries=20)
        user.find(kind=ui.button, content="Compute the position").click()
        for _ in range(100):
            if fix_json(sky.path).is_file():
                break
            await asyncio.sleep(0.1)
        await user.should_see("51.47790° N, 0.00150° W, 46 m", retries=40)
        await user.should_see("This is a synthetic recording", retries=20)
        await user.should_see("Everything is on", retries=20)
    finally:
        state.source = old_source


async def test_the_tabs_say_what_is_missing(user: User, tmp_path) -> None:
    rec = tmp_path / "bare.bin"
    np.random.default_rng(0).integers(-8, 9, int(2 * FS * 0.3), dtype=np.int8).tofile(rec)
    old_source = state.source
    state.source = IQSource(rec, "int8", FS)
    try:
        await user.open("/")
        await user.should_see("First track the satellites of this recording", retries=20)
        await user.should_see("First decode the satellites' messages", retries=20)
    finally:
        state.source = old_source
