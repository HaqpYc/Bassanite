#!/usr/bin/env python3
"""
solve.py -- GPS positioning: from the code timing (stage 03) and the decoded
messages (stage 04) to latitude, longitude, height and time.

WHERE THIS SITS IN THE PIPELINE
-------------------------------
This is the last stage. At any instant of the recording, each satellite's signal
shows the time on that satellite's clock when it left (the message gives the
coarse time, the code timing the last microsecond). The signals travelled for
different times, so the clocks read differently:

    c (t_receiver - t_satellite_i) = distance to satellite i + c x (receiver clock error)

The ephemeris says where each satellite was. Four unknowns (x, y, z and the
receiver's clock error), so four satellites, solved by least squares. One
position is computed every 100 ms of the recording; the answer is their mean,
and their scatter shows the precision.

The algorithms live in ../bassanite/position.py (importable, also used by the web
UI); this script is their command line.

HOW IT CONNECTS TO THE OTHER STAGES (the file is the interface)
---------------------------------------------------------------
  * FROM stage 03: results/<recording>/tracking/track_PRNnn.npz (where each code
    period starts in the recording, and the Doppler for the cross-check).
  * FROM stage 04: results/<recording>/decode/navigation.json (ephemerides and
    the satellite time of code period 0).
  * OUT: results/<recording>/position/fix.json (the answer), fixes.csv (one
    position per instant), fix.meta.json and fix.png.

Example:
  python 05_positioning/solve.py --iq recordings/capture_20261110_143115.bin
  python 05_positioning/solve.py --iq <file> --without earth_rotation   # what is it worth?
  python 05_positioning/solve.py --iq <file> --truth 51.4779,-0.0015,46   # print the error
"""
import argparse
import os
import sys
from dataclasses import fields, replace

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

from bassanite.navigation import full_week, load_navigation, recording_date, utc_from_gps
from bassanite.position import (DEFAULT_STEP_S, SolveOptions, doppler_check, east_north_up,
                                enu_scatter, fix_series, lla_to_ecef, load_measurements,
                                satellite_rows, save_fix, summarize)
from bassanite.provenance import read_meta, write_meta
from bassanite.results import fix_json, fixes_csv, navigation_json, tracking_dir
from bassanite.synthetic import read_truth

CORRECTIONS = [f.name for f in fields(SolveOptions)]


def write_plot(path, series, summary, rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    enu = enu_scatter(series, summary)
    fig = plt.figure(figsize=(11, 4))
    fig.suptitle(f"Stage 05 position: {summary['lat']:.6f}°, {summary['lon']:.6f}°, "
                 f"{summary['height']:.0f} m  ({summary['n_fixes']} positions, "
                 f"{len(summary['satellites'])} satellites)")
    ax = fig.add_subplot(1, 3, 1)
    ax.scatter(enu[:, 0], enu[:, 1], s=5, alpha=0.4)
    ax.plot(0, 0, "+", color="k", ms=12)
    ax.set_aspect("equal"); ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)")
    ax.set_title("Positions around their mean")
    ax = fig.add_subplot(1, 3, 2)
    for j, name in enumerate(("east", "north", "up")):
        ax.plot(series["t"], enu[:, j], lw=0.8, label=name)
    ax.set_xlabel("time in the recording (s)"); ax.set_ylabel("m"); ax.legend(fontsize=8)
    ax.set_title("Each position against time")
    ax = fig.add_subplot(1, 3, 3, projection="polar")
    ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
    for r in rows:
        ax.plot(np.radians(r["azimuth"]), 90 - r["elevation"], "o", ms=9)
        ax.annotate(f"PRN {r['prn']}", (np.radians(r["azimuth"]), 90 - r["elevation"]),
                    textcoords="offset points", xytext=(6, 4), fontsize=8)
    ax.set_rlim(0, 90); ax.set_rticks([0, 30, 60, 90], ["90°", "60°", "30°", "0°"], fontsize=7)
    ax.set_title("The sky (centre = straight up)", fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=110)
    plt.close(fig)
    print(f"wrote {path}")


def main():
    ap = argparse.ArgumentParser(
        description="Solve the receiver's position from tracking and decoding results.")
    ap.add_argument("--iq", default=os.path.join(_ROOT, "recordings", "capture.bin"),
                    help="the recording (default: the bundled outdoor capture)")
    ap.add_argument("--step", type=float, default=DEFAULT_STEP_S,
                    help="seconds between two positions (default 0.1)")
    ap.add_argument("--prns", default=None,
                    help="use only these satellites, comma-separated (default: all usable)")
    ap.add_argument("--without", action="append", default=[], choices=CORRECTIONS,
                    help="switch one correction off, to see what it is worth (repeatable)")
    ap.add_argument("--truth", default=None, metavar="LAT,LON,HEIGHT",
                    help="the true position, to print the error (degrees, degrees, metres "
                         "above the ellipsoid). A synthetic recording's truth is used "
                         "automatically.")
    ap.add_argument("--out-dir", default=None,
                    help="where to write (default: results/<recording>/position/)")
    ap.add_argument("--no-plot", action="store_true", help="skip fix.png")
    a = ap.parse_args()

    nav_path = str(navigation_json(a.iq))
    if not os.path.exists(nav_path):
        sys.exit(f"no decoded messages at {nav_path}; run stage 04 first "
                 f"(python 04_decoding/decode.py --iq {a.iq})")
    navigation = load_navigation(nav_path)
    satellites, fs = load_measurements(tracking_dir(a.iq), navigation)
    prns = sorted(satellites) if a.prns is None else \
        sorted(int(p) for p in a.prns.split(",") if p.strip())
    missing = [p for p in prns if p not in satellites]
    if missing:
        sys.exit(f"PRN {missing} not usable (see {nav_path}); usable: {sorted(satellites)}")
    if len(prns) < 4:
        sys.exit(f"a position needs 4 satellites; only PRN {prns} have a complete message. "
                 f"Record at least 36 s outdoors with an open sky.")
    options = replace(SolveOptions(), **{name: False for name in a.without})

    series = fix_series(satellites, fs, step_s=a.step, options=options, prns=prns)
    if series is None:
        sys.exit("the tracks have no common stretch long enough to measure in")
    summary = summarize(series)
    rows = satellite_rows(series)
    check = doppler_check(satellites, summary, fs)
    week = full_week(int(satellites[prns[0]]["ephemeris"]["week"]), recording_date(a.iq))
    utc = utc_from_gps(week, summary["gps_time_at_sample0"])

    print(f"satellites used: PRN {prns}"
          + (f"   corrections off: {', '.join(a.without)}" if a.without else ""))
    print("PRN  azimuth  elevation   pseudorange      distance   sat. clock   troposphere")
    for r in rows:
        print(f"{r['prn']:>3}  {r['azimuth']:6.1f}°  {r['elevation']:7.1f}°  "
              f"{r['pseudorange'] / 1e3:10.3f} km  {r['range'] / 1e3:10.3f} km  "
              f"{r['clock_error'] * 1e6:+8.1f} µs  {r['troposphere']:6.2f} m")
    dop = summary["dop"]
    print(f"\nPOSITION   {summary['lat']:.6f}°, {summary['lon']:.6f}°, "
          f"height {summary['height']:.1f} m above the ellipsoid")
    print(f"           mean of {summary['n_fixes']} positions; one position scatters by "
          f"{summary['east_std']:.1f} m east, {summary['north_std']:.1f} m north, "
          f"{summary['up_std']:.1f} m up (1 sigma)")
    print(f"geometry   HDOP {dop['hdop']:.1f}, VDOP {dop['vdop']:.1f}: 1 m of ranging noise "
          f"gives {dop['hdop']:.1f} m horizontally and {dop['vdop']:.1f} m vertically")
    print(f"time       first sample taken at {utc:%Y-%m-%d %H:%M:%S.%f} UTC "
          f"(GPS week {week}, {summary['gps_time_at_sample0']:.6f} s)")
    print(f"clock      the receiver's oscillator is off by {summary['clock_drift'] * 1e6:+.3f} ppm")
    print(f"Doppler    measured minus predicted, per satellite: "
          + ", ".join(f"{r['difference']:+.2f}" for r in check["satellites"].values())
          + f" Hz\n           common part {check['common']:+.1f} Hz (the receiver's frequency "
            f"error; the clock drift predicts {check['from_clock_drift']:+.1f}); "
            f"spread {check['spread']:.2f} Hz")
    if len(prns) > 4:
        print(f"residuals  {summary['residual_rms']:.2f} m rms")
    else:
        print("residuals  none: with exactly 4 satellites the equations always have an "
              "exact solution, so the Doppler spread above is the consistency check")

    truth = None
    if a.truth:
        truth = [float(v) for v in a.truth.split(",")]
    else:
        synthetic = read_truth(a.iq) or {}
        if "receiver" in synthetic:
            truth = [synthetic["receiver"][k] for k in ("lat", "lon", "height")]
    extra = {"week": week, "utc_at_sample0": utc.isoformat(), "doppler_check": check}
    if truth:
        e, n, u = east_north_up(truth[0], truth[1]) @ (np.array(summary["ecef"])
                                                       - lla_to_ecef(*truth))
        print(f"error      {e:+.1f} m east, {n:+.1f} m north, {u:+.1f} m up of the true "
              f"position ({np.hypot(e, n):.1f} m horizontally)")
        extra["truth"] = {"lat": truth[0], "lon": truth[1], "height": truth[2],
                          "error_east": float(e), "error_north": float(n),
                          "error_up": float(u)}

    out_dir = a.out_dir or os.path.dirname(str(fix_json(a.iq)))
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, os.path.basename(str(fix_json(a.iq))))
    csv_path = os.path.join(out_dir, os.path.basename(str(fixes_csv(a.iq))))
    save_fix(json_path, csv_path, series, summary, options, extra)
    print(f"wrote {json_path}\nwrote {csv_path}")
    source = (read_meta(nav_path) or {}).get("source", {})
    write_meta(json_path, "position", a.iq, source.get("format", "int8"),
               source.get("fs", fs), {"step_s": a.step, "prns": prns,
                                      "without": sorted(a.without)})
    if not a.no_plot:
        try:
            write_plot(os.path.join(out_dir, "fix.png"), series, summary, rows)
        except ImportError:
            print('plot skipped: matplotlib is not installed (pip install -e ".[plots]")')


if __name__ == "__main__":
    main()
