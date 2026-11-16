"""Position tab (stage 05): read every satellite's clock at the same instants, turn
the differences into distances, and solve for the receiver's position and time."""
from __future__ import annotations

from dataclasses import fields, replace

import numpy as np
from nicegui import run, ui

from components import explain_panel
from plots import DETECTED, HIGHLIGHT, LIMIT, NOISE, figure
from stages import (STAGE_BY_KEY, older_than_previous_step, position_text, rel_path,
                    result_is_for_other_recording)
from state import IQSource, state

from bassanite import navigation as nav
from bassanite import position as pos
from bassanite.provenance import write_meta
from bassanite.results import fix_json, fixes_csv, navigation_json, tracking_dir
from bassanite.synthetic import read_truth

MIN_SATS = 4
TRY_STEP_S = 1.0              # the Try-it solves one position per second: fast enough to play
MAP_POINTS = 150              # positions drawn on the map
SERIES_POINTS = 1500
ENU_COLOURS = ("#1976d2", "#ef6c00", "#2e7d32")      # east, north, up
CORRECTIONS = {
    "satellite_clock": "The satellite's clock error (subframe 1)",
    "relativity": "Relativity: the clock's rate along an elliptical orbit",
    "group_delay": "The delay of L1 inside the satellite",
    "earth_rotation": "The Earth's rotation during the flight",
    "troposphere": "The delay of the lower atmosphere",
}
assert set(CORRECTIONS) == {f.name for f in fields(pos.SolveOptions)}

# Bumped when any page solves, so every other open page re-reads the results.
results_version = 0


# --- running the stage ------------------------------------------------------------------

def truth_of(src: IQSource) -> list[float] | None:
    """The true position of a synthetic recording (lat, lon, height), else None."""
    receiver = (read_truth(src.path) or {}).get("receiver")
    return [receiver[k] for k in ("lat", "lon", "height")] if receiver else None


def truth_error(summary: dict, truth: list[float]) -> tuple[float, float, float]:
    """East, north, up (m) from the true position to the computed one."""
    e, n, u = pos.east_north_up(truth[0], truth[1]) @ (
        np.array(summary["ecef"]) - pos.lla_to_ecef(*truth))
    return float(e), float(n), float(u)


def run_solve(src: IQSource) -> dict:
    """Solve the position of `src` and write fix.json and fixes.csv, exactly like
    05_positioning/solve.py. Returns the fix document (load_fix's dict)."""
    navigation = nav.load_navigation(navigation_json(src.path))
    satellites, fs = pos.load_measurements(tracking_dir(src.path), navigation)
    if len(satellites) < MIN_SATS:
        raise ValueError(f"a position needs {MIN_SATS} satellites with a complete "
                         f"message; this recording has {len(satellites)}")
    truth = truth_of(src)
    # A synthetic sky has no atmosphere to correct for.
    options = pos.SolveOptions(troposphere=truth is None)
    series = pos.fix_series(satellites, fs, options=options)
    if series is None:
        raise ValueError("the tracks have no common stretch long enough to measure in")
    summary = pos.summarize(series)
    week = nav.full_week(int(next(iter(satellites.values()))["ephemeris"]["week"]),
                         nav.recording_date(src.path))
    extra = {"week": week,
             "utc_at_sample0": nav.utc_from_gps(week, summary["gps_time_at_sample0"]).isoformat(),
             "doppler_check": pos.doppler_check(satellites, summary, fs)}
    if truth:
        e, n, u = truth_error(summary, truth)
        extra["truth"] = {"lat": truth[0], "lon": truth[1], "height": truth[2],
                          "error_east": e, "error_north": n, "error_up": u}
    out = fix_json(src.path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pos.save_fix(out, fixes_csv(src.path), series, summary, options, extra)
    write_meta(out, "position", src.path, src.fmt, src.fs,
               {"step_s": pos.DEFAULT_STEP_S, "prns": summary["satellites"], "without": []})
    return pos.load_fix(out)


def saved_fix(src: IQSource) -> bool:
    """Whether this recording has a position that is still current."""
    path = fix_json(src.path)
    return (path.is_file() and fixes_csv(src.path).is_file()
            and not result_is_for_other_recording(path, src)
            and not older_than_previous_step(STAGE_BY_KEY["position"], src))


def try_solve(src: IQSource, prns: list[int], off: tuple[str, ...]) -> dict | None:
    """The position with only `prns` and without the corrections in `off`, one
    per second. Returns summarize()'s dict, or None if it cannot be solved."""
    navigation = nav.load_navigation(navigation_json(src.path))
    satellites, fs = pos.load_measurements(tracking_dir(src.path), navigation)
    base = pos.SolveOptions(troposphere=truth_of(src) is None)
    options = replace(base, **{name: False for name in off})
    series = pos.fix_series(satellites, fs, step_s=TRY_STEP_S, options=options, prns=prns)
    return None if series is None else pos.summarize(series)


# --- figures and texts ------------------------------------------------------------------

def enu_of(doc: dict, fixes: dict) -> np.ndarray:
    """Every position of fixes.csv as (east, north, up) metres from the mean."""
    s = doc["summary"]
    xyz = np.column_stack([fixes["x"], fixes["y"], fixes["z"]])
    return (xyz - np.array(s["ecef"])) @ pos.east_north_up(s["lat"], s["lon"]).T


def thin(n: int, keep: int) -> slice:
    return slice(None, None, max(1, n // keep))


def scatter_figure(enu: np.ndarray, truth_enu=None) -> dict:
    """The positions seen from above, around their mean."""
    reach = max(float(np.abs(enu[:, :2]).max()) * 1.1, 1.0)
    data = [{"type": "scatter", "mode": "markers", "x": enu[:, 0].tolist(),
             "y": enu[:, 1].tolist(), "name": "one position",
             "marker": {"size": 4, "color": ENU_COLOURS[0], "opacity": 0.45},
             "hovertemplate": "%{x:+.1f} m east, %{y:+.1f} m north<extra></extra>"},
            {"type": "scatter", "mode": "markers", "x": [0], "y": [0], "name": "their mean",
             "marker": {"symbol": "cross-thin", "size": 16,
                        "line": {"width": 2.5, "color": "#212121"}}}]
    if truth_enu is not None:
        data.append({"type": "scatter", "mode": "markers", "x": [truth_enu[0]],
                     "y": [truth_enu[1]], "name": "the true position",
                     "marker": {"symbol": "star", "size": 14, "color": DETECTED}})
        reach = max(reach, 1.2 * float(np.hypot(*truth_enu[:2])))
    return figure(data, height=380,
                  xaxis={"title": "east (m)", "range": [-reach, reach]},
                  yaxis={"title": "north (m)", "range": [-reach, reach],
                         "scaleanchor": "x", "scaleratio": 1})


def series_figure(t: np.ndarray, enu: np.ndarray) -> dict:
    """East, north and up of each position against time."""
    sl = thin(len(t), SERIES_POINTS)
    return figure([{"type": "scatter", "mode": "lines", "x": t[sl].tolist(),
                    "y": enu[sl, j].tolist(), "name": name,
                    "line": {"width": 1.2, "color": ENU_COLOURS[j]}}
                   for j, name in enumerate(("east", "north", "up"))],
                  height=380, xaxis={"title": "time in the recording (s)"},
                  yaxis={"title": "metres from the mean"})


def sky_figure(rows: list[dict], used: list[int] | None = None) -> dict:
    """Where the satellites are: north up, the centre is straight overhead."""
    used = [r["prn"] for r in rows] if used is None else used
    return {"data": [{"type": "scatterpolar", "mode": "markers+text",
                      "theta": [r["azimuth"] for r in rows],
                      "r": [90 - r["elevation"] for r in rows],
                      "text": [f"PRN {r['prn']}" for r in rows], "textposition": "top center",
                      "marker": {"size": 14, "color": [DETECTED if r["prn"] in used else NOISE
                                                       for r in rows]},
                      "customdata": [[r["azimuth"], r["elevation"]] for r in rows],
                      "hovertemplate": "%{text}<br>azimuth %{customdata[0]:.0f}°, elevation "
                                       "%{customdata[1]:.0f}°<extra></extra>"}],
            "config": figure([])["config"],
            "layout": {"height": 380, "template": "plotly_white", "showlegend": False,
                       "margin": {"l": 40, "r": 40, "t": 30, "b": 30},
                       "polar": {"angularaxis": {"direction": "clockwise", "rotation": 90,
                                                 "tickmode": "array",
                                                 "tickvals": [0, 90, 180, 270],
                                                 "ticktext": ["N", "E", "S", "W"]},
                                 "radialaxis": {"range": [0, 90], "tickmode": "array",
                                                "tickvals": [0, 30, 60, 90],
                                                "ticktext": ["90°", "60°", "30°", "horizon"]}}}}


def iterations_figure(history: list[list[float]]) -> dict:
    """How far each successive estimate is from the final one."""
    h = np.array(history)
    far = np.linalg.norm(h[:, :3] - h[-1, :3], axis=1)
    start = float(np.linalg.norm(h[-1, :3]))             # the first guess: the Earth's centre
    y = [start] + [max(v, 1e-4) for v in far[:-1]]
    return figure([{"type": "scatter", "mode": "lines+markers", "x": list(range(len(y))),
                    "y": y, "marker": {"size": 9, "color": ENU_COLOURS[0]},
                    "hovertemplate": "after %{x} iterations: %{y:,.4g} m away<extra></extra>"}],
                  height=300, showlegend=False,
                  xaxis={"title": "iterations (0 = the first guess, the centre of the Earth)",
                         "dtick": 1},
                  yaxis={"title": "distance from the final position (m)", "type": "log"})


def iterations_text(history: list[list[float]]) -> str:
    h = np.array(history)
    far = np.linalg.norm(h[:, :3] - h[-1, :3], axis=1)
    steps = [f"{np.linalg.norm(h[-1, :3]) / 1e3:,.0f} km"] + [
        f"{v / 1e3:,.0f} km" if v >= 1e3 else f"{v:,.1f} m" if v >= 0.1 else f"{v * 1e3:.1f} mm"
        for v in far[:4]]
    return "Distance from the final position: " + " → ".join(steps) + "."


RANGE_COLUMNS = [
    {"name": "prn", "label": "PRN", "field": "prn", "align": "left"},
    {"name": "az", "label": "Azimuth", "field": "az", "align": "right"},
    {"name": "el", "label": "Elevation", "field": "el", "align": "right"},
    {"name": "pr", "label": "Pseudorange (km)", "field": "pr", "align": "right"},
    {"name": "clock", "label": "Satellite clock (µs → km)", "field": "clock", "align": "right"},
    {"name": "tropo", "label": "Air (m)", "field": "tropo", "align": "right"},
    {"name": "range", "label": "True distance found (km)", "field": "range", "align": "right"},
    {"name": "travel", "label": "Flight time (ms)", "field": "travel", "align": "right"},
]


def range_rows(doc: dict) -> list[dict]:
    return [{"prn": r["prn"], "az": f"{r['azimuth']:.0f}°", "el": f"{r['elevation']:.0f}°",
             "pr": f"{r['pseudorange'] / 1e3:,.3f}",
             "clock": f"{r['clock_error'] * 1e6:+.1f} → "
                      f"{r['clock_error'] * pos.C_LIGHT / 1e3:+.1f}",
             "tropo": f"{r['troposphere']:.1f}", "range": f"{r['range'] / 1e3:,.3f}",
             "travel": f"{r['range'] / pos.C_LIGHT * 1e3:.3f}"}
            for r in doc["satellites"]]


DOPPLER_COLUMNS = [
    {"name": "prn", "label": "PRN", "field": "prn", "align": "left"},
    {"name": "measured", "label": "Measured by tracking (Hz)", "field": "measured",
     "align": "right"},
    {"name": "predicted", "label": "Predicted from the position (Hz)", "field": "predicted",
     "align": "right"},
    {"name": "difference", "label": "Difference (Hz)", "field": "difference", "align": "right"},
    {"name": "left", "label": "Minus the common part (Hz)", "field": "left", "align": "right"},
]


def doppler_rows(check: dict) -> list[dict]:
    return [{"prn": int(prn), "measured": f"{r['measured']:+.1f}",
             "predicted": f"{r['predicted']:+.1f}", "difference": f"{r['difference']:+.2f}",
             "left": f"{r['difference'] - check['common']:+.2f}"}
            for prn, r in sorted(check["satellites"].items(), key=lambda kv: int(kv[0]))]


def doppler_text(check: dict) -> str:
    verdict = ("✅ The predictions agree with the measurements to "
               f"{check['spread']:.2f} Hz once the common part is removed: position, time "
               "and orbits fit together. One kilometre of position error would show up as "
               "about 1 Hz." if check["spread"] < 2.0 else
               f"⚠️ The predictions disagree by up to {check['spread']:.1f} Hz between "
               "satellites: the position, the time or an orbit is wrong.")
    return (f"{verdict} The common part, {check['common']:+.1f} Hz, is the receiver's own "
            f"frequency error. The drift of its clock found by the solver predicts "
            f"{check['from_clock_drift']:+.1f} Hz.")


def result_text(doc: dict) -> tuple[str, str]:
    """(headline, details) of a fix document."""
    s = doc["summary"]
    dop = s["dop"]
    details = (f"The mean of {s['n_fixes']} positions, one every "
               f"{pos.DEFAULT_STEP_S * 1000:.0f} ms, from {len(s['satellites'])} satellites "
               f"(PRN {', '.join(map(str, s['satellites']))}). A single position scatters "
               f"by {s['east_std']:.1f} m east, {s['north_std']:.1f} m north and "
               f"{s['up_std']:.1f} m up. Height is above the WGS 84 ellipsoid, not above "
               f"sea level. The first sample was taken at "
               f"{doc['utc_at_sample0'][:23].replace('T', ' ')} UTC, and the receiver's "
               f"oscillator is off by {s['clock_drift'] * 1e6:+.2f} ppm. Geometry: HDOP "
               f"{dop['hdop']:.1f}, VDOP {dop['vdop']:.1f}.")
    return position_text(s), details


def truth_text(doc: dict) -> str:
    t = doc.get("truth")
    if not t:
        return ""
    return (f"✅ This is a synthetic recording, so the truth is known: the result is "
            f"{np.hypot(t['error_east'], t['error_north']):.1f} m from it horizontally "
            f"({t['error_east']:+.1f} m east, {t['error_north']:+.1f} m north) and "
            f"{t['error_up']:+.1f} m in height.")


def shift_text(reference: dict, tried: dict | None, prns: list[int],
               off: tuple[str, ...]) -> str:
    """What a Try-it run changed, in a sentence."""
    if tried is None:
        return (f"With {len(prns)} satellites there are fewer equations than unknowns "
                f"(x, y, z and the clock): no position. Select at least {MIN_SATS}.")
    e, n, u = pos.east_north_up(reference["lat"], reference["lon"]) @ (
        np.array(tried["ecef"]) - np.array(reference["ecef"]))
    moved = float(np.linalg.norm([e, n, u]))
    what = []
    if off:
        what.append("without " + ", ".join(CORRECTIONS[name].split(":")[0].lower()
                                           for name in off))
    if len(prns) < len(reference["satellites"]):
        what.append(f"with only PRN {', '.join(map(str, prns))}")
    size = f"{moved / 1e3:,.1f} km" if moved >= 1e4 else f"{moved:,.1f} m"
    head = " and ".join(what) or "with everything on"
    return (head[0].upper() + head[1:]
            + f", the position moves by {size} ({e:+,.1f} m east, {n:+,.1f} m north, "
              f"{u:+,.1f} m up). Geometry: HDOP {tried['dop']['hdop']:.1f}, VDOP "
              f"{tried['dop']['vdop']:.1f}; one position scatters by "
              f"{tried['horizontal_rms']:.1f} m horizontally.")


# --- the tab ------------------------------------------------------------------------------

class PositionTab:
    """One instance per browser tab (each client builds its own page)."""

    def __init__(self, go_to=None, on_solved=None) -> None:
        self.go_to = go_to
        self.on_solved = on_solved          # called after a run wrote new results
        self.doc: dict | None = None
        self._pending = self._busy = False
        self._seen_version = results_version
        self._try_seq = 0

    def request_update(self) -> None:
        self._pending = True

    # --- layout -----------------------------------------------------------------------

    @staticmethod
    def _card(title: str, caption: str = ""):
        card = ui.card().classes("w-full").props("flat bordered")
        with card:
            ui.label(title).classes("text-subtitle1 font-bold")
            if caption:
                ui.label(caption).classes("text-sm text-grey-8")
        return card

    def build(self) -> None:
        with self._card("Where are we?"):
            with ui.row().classes("items-center gap-3"):
                self.run_button = ui.button("Compute the position", icon="place",
                                            on_click=self.start_run).props("no-caps")
                self.goto_decode = ui.button("Go to step 4", on_click=lambda: self.go_to(
                    "decode") if self.go_to else None).props("flat no-caps")
            self.status_label = ui.label().classes("font-medium")
            self.headline = ui.label().classes("text-h5")
            self.details = ui.label().classes("text-sm text-grey-8")
            self.truth_label = ui.label().classes("text-sm")
            self.file_label = ui.label().classes("text-xs text-grey-7")

        self.results = ui.column().classes("w-full gap-4")
        with self.results:
            with self._card("On the map",
                            "Blue dots: single positions. Marker: their mean. The map "
                            "tiles come from openstreetmap.org, so showing it tells "
                            "that server which area you look at."):
                self.show_map = ui.switch("Show the map", value=False,
                                          on_change=lambda: self._show_map())
                self.map_box = ui.column().classes("w-full")
            with self._card("How much do the positions scatter?",
                            "Each dot is one position, computed from one instant of the "
                            "recording. Their spread is the precision of a single "
                            "measurement; their mean is better."):
                with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4"):
                    self.p_scatter = ui.plotly(figure([])).classes("w-full")
                    self.p_series = ui.plotly(figure([])).classes("w-full")
            with self._card("Reading the satellites' clocks",
                            "At one instant (the middle of the recording), what each "
                            "satellite contributes. The pseudorange is the raw reading: "
                            "the speed of light times (our clock minus the satellite's "
                            "clock as read in its signal)."):
                self.ranges = ui.table(columns=RANGE_COLUMNS, rows=[], row_key="prn") \
                    .classes("w-full").props("dense flat")
                explain_panel("position", "clocks")
                explain_panel("position", "pseudoranges")
            with self._card("Solving: from the centre of the Earth to the antenna",
                            "The solver starts with no idea at all, at the centre of "
                            "the Earth, and corrects its guess a few times."):
                self.p_iterations = ui.plotly(figure([])).classes("w-full")
                self.iterations_label = ui.label().classes("text-sm text-grey-8")
                explain_panel("position", "solver")
            with self._card("The sky, and what it does to the precision",
                            "Where each satellite is, seen from the position found "
                            "(north up, straight overhead in the centre)."):
                self.p_sky = ui.plotly(figure([])).classes("w-full md:w-1/2")
                self.dop_label = ui.label().classes("text-sm text-grey-8")
                explain_panel("position", "dop")
            with self._card("Try it: what is each ingredient worth?",
                            "Switch a correction off, or leave a satellite out, and see "
                            "where the position goes."):
                with ui.row().classes("w-full gap-x-6 gap-y-0"):
                    self.c_corrections = {
                        name: ui.checkbox(label, value=True, on_change=lambda: self._try())
                        for name, label in CORRECTIONS.items()}
                self.c_prns = ui.select([], multiple=True, label="Satellites used",
                                        on_change=lambda: self._try()) \
                    .props("use-chips").classes("w-full md:w-96")
                self.try_label = ui.label().classes("text-sm")
            with self._card("An independent check: the Doppler",
                            "The position was computed from the code timing alone. The "
                            "carrier frequency tracking measured was not used, so it can "
                            "test the answer."):
                self.doppler = ui.table(columns=DOPPLER_COLUMNS, rows=[], row_key="prn") \
                    .classes("w-full").props("dense flat")
                self.doppler_label = ui.label().classes("text-sm")
                explain_panel("position", "doppler")
        ui.timer(0.3, self._tick)
        self.request_update()

    # --- running ------------------------------------------------------------------------

    async def start_run(self) -> None:
        global results_version
        src = state.source
        if src is None or self._busy:
            return
        self._busy = True
        self.run_button.disable()
        self.status_label.set_text("Computing…")
        try:
            await run.io_bound(run_solve, src)
        except (OSError, ValueError, KeyError) as err:
            self.status_label.set_text("")
            ui.notify(f"No position: {err}", type="negative", multi_line=True)
            self.request_update()
            return
        finally:
            self._busy = False
            self.run_button.enable()
        results_version += 1
        self._seen_version = results_version
        if self.on_solved is not None:
            self.on_solved()
        self.request_update()

    async def _tick(self) -> None:
        if self._seen_version != results_version:
            self._seen_version = results_version
            self.request_update()
        if self._pending and not self._busy:
            self._pending = False
            self._busy = True
            try:
                await self._update()
            finally:
                self._busy = False

    # --- results ------------------------------------------------------------------------

    def _show_nothing(self, message: str, can_run: bool, to_decode: bool = False) -> None:
        self.doc = None
        self.status_label.set_text(message)
        for label in (self.headline, self.details, self.truth_label, self.file_label):
            label.set_text("")
        self.results.set_visibility(False)
        self.run_button.set_enabled(can_run)
        self.goto_decode.set_visibility(to_decode)

    async def _update(self) -> None:
        src = state.source
        if src is None or src.problems():
            self._show_nothing("Load a recording first (top bar, or the Overview tab).", False)
            return
        nav_path = navigation_json(src.path)
        if not nav_path.is_file():
            self._show_nothing("First decode the satellites' messages (step 4): the position "
                               "needs their orbits and their time.", False, True)
            return
        try:
            usable = sorted(p for p, s in nav.load_navigation(nav_path).items() if s["usable"])
        except (OSError, ValueError, KeyError) as err:
            self._show_nothing(f"Could not read the decoded messages: {err}", False, True)
            return
        if len(usable) < MIN_SATS:
            self._show_nothing(
                f"A position has four unknowns (x, y, z and the receiver's clock), so it "
                f"needs {MIN_SATS} satellites with a complete message. This recording has "
                f"{len(usable)}" + (f" (PRN {', '.join(map(str, usable))})" if usable else "")
                + ". Record at least 36 s outdoors, with an open sky.", False, True)
            return
        if not saved_fix(src):
            stale = fix_json(src.path).is_file()
            self._show_nothing(("The position on disk is older than the decoded messages. "
                                if stale else "Not computed yet. ")
                               + "Press “Compute the position”.", True)
            return
        try:
            doc = pos.load_fix(fix_json(src.path))
            fixes = pos.load_fixes(fixes_csv(src.path))
        except (OSError, ValueError, KeyError) as err:
            self._show_nothing(f"Could not read the position: {err}", True)
            return
        self.doc = doc
        self.run_button.enable()
        self.goto_decode.set_visibility(False)
        self.status_label.set_text("")
        headline, details = result_text(doc)
        self.headline.set_text(headline)
        self.details.set_text(details)
        self.truth_label.set_text(truth_text(doc))
        self.file_label.set_text(f"Results: {rel_path(fix_json(src.path))}, "
                                 f"{rel_path(fixes_csv(src.path))}")
        self.results.set_visibility(True)

        s = doc["summary"]
        enu = enu_of(doc, fixes)
        self._fixes = fixes
        truth = doc.get("truth")
        truth_enu = ([-truth["error_east"], -truth["error_north"]] if truth else None)
        self.p_scatter.update_figure(scatter_figure(enu, truth_enu))
        self.p_series.update_figure(series_figure(fixes["t_s"], enu))
        self.ranges.rows = range_rows(doc)
        self.p_iterations.update_figure(iterations_figure(doc["history"]))
        self.iterations_label.set_text(iterations_text(doc["history"]))
        self.p_sky.update_figure(sky_figure(doc["satellites"]))
        dop = s["dop"]
        self.dop_label.set_text(
            f"HDOP {dop['hdop']:.1f}, VDOP {dop['vdop']:.1f}: 1 m of error on the distances "
            f"becomes {dop['hdop']:.1f} m horizontally and {dop['vdop']:.1f} m in height. "
            + ("The satellites are spread across the sky: a good geometry."
               if dop["pdop"] < 4 else
               "The satellites sit in the same part of the sky, so their distances all say "
               "nearly the same thing: a weak geometry. More satellites, in other "
               "directions, would tighten the result."))
        self.doppler.rows = doppler_rows(doc["doppler_check"])
        self.doppler_label.set_text(doppler_text(doc["doppler_check"]))
        for box in self.c_corrections.values():
            box.set_value(True)
        self.c_corrections["troposphere"].set_visibility(doc["options"]["troposphere"])
        self.c_prns.set_options(s["satellites"], value=list(s["satellites"]))
        self.try_label.set_text(self._idle_try_text())
        self._show_map()

    def _idle_try_text(self) -> str:
        """The Try-it's text while everything is on."""
        n = len(self.doc["summary"]["satellites"]) if self.doc else 0
        return ("Everything is on: this is the position shown above."
                + (" With exactly four satellites there is none to spare: leaving one out "
                   "leaves three equations for four unknowns." if n == MIN_SATS else ""))

    def _show_map(self) -> None:
        self.map_box.clear()
        if self.doc is None or not self.show_map.value:
            return
        s, fixes = self.doc["summary"], self._fixes
        sl = thin(len(fixes["lat"]), MAP_POINTS)
        with self.map_box:
            m = ui.leaflet(center=(s["lat"], s["lon"]), zoom=18).classes("w-full h-96")
            for lat, lon in zip(fixes["lat"][sl], fixes["lon"][sl]):
                m.generic_layer(name="circleMarker", args=[
                    [float(lat), float(lon)],
                    {"radius": 3, "color": ENU_COLOURS[0], "weight": 0, "fillOpacity": 0.45}])
            m.marker(latlng=(s["lat"], s["lon"]))

    # --- try it -------------------------------------------------------------------------

    def _try(self) -> None:
        if self.doc is None or state.source is None:
            return
        self._try_seq += 1
        ui.timer(0.01, lambda seq=self._try_seq: self._run_try(seq), once=True)

    async def _run_try(self, seq: int) -> None:
        doc, src = self.doc, state.source
        if doc is None or src is None or seq != self._try_seq:
            return
        prns = sorted(int(p) for p in self.c_prns.value or [])
        off = tuple(name for name, box in self.c_corrections.items() if not box.value)
        everything = not off and prns == doc["summary"]["satellites"]
        self.p_sky.update_figure(sky_figure(doc["satellites"], prns))
        if everything:
            self.try_label.set_text(self._idle_try_text())
            return
        if len(prns) < MIN_SATS:
            self.try_label.set_text(shift_text(doc["summary"], None, prns, off))
            return
        self.try_label.set_text("Computing…")
        try:
            # Compare like with like: both at one position per second.
            reference, tried = await run.io_bound(
                lambda: (try_solve(src, doc["summary"]["satellites"], ()),
                         try_solve(src, prns, off)))
        except (OSError, ValueError, KeyError, np.linalg.LinAlgError) as err:
            self.try_label.set_text(f"Could not solve: {err}")
            return
        if seq != self._try_seq:
            return
        self.try_label.set_text(shift_text(reference, tried, prns, off)
                                if reference else "Could not solve.")
