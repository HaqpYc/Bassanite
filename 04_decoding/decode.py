#!/usr/bin/env python3
"""
decode.py -- GPS navigation-message decoding: turn each tracked satellite's 50 bit/s
stream into its orbit (ephemeris), its clock correction and the time it was sent.

WHERE THIS SITS IN THE PIPELINE
-------------------------------
Stage 03 (tracking) produced, per satellite, about 50 bits per second with no
marker of where anything starts, and possibly every bit inverted. This stage:

  * finds where the 6-second SUBFRAMES start (the preamble 10001011, confirmed by
    the parity of the words that follow),
  * checks every 30-bit word with its 6 parity bits,
  * unpacks the fields of subframes 1, 2 and 3: the satellite's clock correction
    and the 16 numbers that describe its orbit,
  * and reads the TIME OF WEEK, which labels one known instant of the recording
    with the time on the satellite's clock.

The algorithms live in ../bassanite/navigation.py (importable, also used by the web
UI); this script is their command line.

HOW IT CONNECTS TO THE OTHER STAGES (the file is the interface)
---------------------------------------------------------------
  * FROM stage 03: results/<recording>/tracking/ (tracking_summary.csv and one
    track_PRNnn.npz per satellite: its bits and where bit 0 starts).
  * TO stage 05: results/<recording>/decode/navigation.json, per satellite the
    ephemeris and `period0_time` (the satellite time of tracking's code period 0),
    plus navigation.meta.json (which recording).

Example:
  python 04_decoding/decode.py                                  # capture.bin
  python 04_decoding/decode.py --iq recordings/capture_20261110_143115.bin
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

from bassanite.navigation import (EPHEMERIS_SUBFRAMES, complete, decode_tracks, full_week,
                                  recording_date, save_navigation, usable, utc_from_gps)
from bassanite.provenance import read_meta, write_meta
from bassanite.results import navigation_json, tracking_dir
from bassanite.tracking import SUMMARY_CSV


def describe(prn, sat, near):
    """The lines printed for one satellite."""
    subframes = sat["subframes"]
    words = sum(sum(sf["parity_ok"]) for sf in subframes)
    lines = [f"PRN {prn:>2}: {sat['nbits']} bits, C/N0 {sat['cn0_dbhz']:.1f} dB-Hz"
             + ("" if sat["locked"] else " (not locked)")]
    if subframes:
        numbers = " ".join(str(sf["number"]) if complete(sf) else f"({sf['number']})"
                           for sf in subframes)
        lines.append(f"    subframes {numbers}   first at bit {subframes[0]['start_bit']}, "
                     f"time of week {subframes[0]['tow']} s   "
                     f"parity {words}/{10 * len(subframes)} words   "
                     f"bits {'inverted' if sat['inverted'] else 'upright'}")
    eph = sat["ephemeris"]
    if eph:
        week = full_week(int(eph["week"]), near)
        lines.append(f"    week {week} ({utc_from_gps(week, subframes[0]['tow']):%Y-%m-%d %H:%M:%S} "
                     f"UTC at the first subframe)   issue {eph['iode']:.0f}   "
                     f"orbit radius {eph['sqrt_a'] ** 2 / 1e3:,.0f} km, e = {eph['e']:.4f}   "
                     f"clock offset {eph['a_f0'] * 1e6:+.1f} µs")
    for problem in sat["problems"]:
        lines.append(f"    ✗ {problem}")
    return lines


def main():
    ap = argparse.ArgumentParser(
        description="Decode the GPS navigation message of every tracked satellite.")
    ap.add_argument("--iq", default=os.path.join(_ROOT, "recordings", "capture.bin"),
                    help="the recording whose tracking results to decode "
                         "(default: the bundled outdoor capture)")
    ap.add_argument("--tracking-dir", default=None,
                    help="stage-03 results (default: results/<recording>/tracking/)")
    ap.add_argument("--out", default=None,
                    help="where to write (default: results/<recording>/decode/navigation.json)")
    a = ap.parse_args()
    folder = a.tracking_dir or str(tracking_dir(a.iq))
    summary = os.path.join(folder, SUMMARY_CSV)
    if not os.path.exists(summary):
        sys.exit(f"no tracking results at {summary}; run stage 03 first "
                 f"(python 03_tracking/track.py --iq {a.iq})")
    out = a.out or str(navigation_json(a.iq))
    os.makedirs(os.path.dirname(out), exist_ok=True)

    satellites = decode_tracks(folder)
    near = recording_date(a.iq)
    for prn, sat in sorted(satellites.items()):
        print("\n".join(describe(prn, sat, near)))
    good = sorted(prn for prn, sat in satellites.items() if usable(sat))
    print(f"\n{len(good)} satellite(s) with subframes "
          f"{', '.join(map(str, EPHEMERIS_SUBFRAMES))} complete: PRN {good}"
          f"  (a position needs 4)")

    save_navigation(out, satellites)
    print(f"wrote {out}")
    source = (read_meta(summary) or {}).get("source", {})
    write_meta(out, "decode", a.iq, source.get("format", "int8"), source.get("fs", 4e6),
               {"tracking_dir": os.path.abspath(folder), "usable_prns": good})


if __name__ == "__main__":
    main()
