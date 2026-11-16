"""Decode tab (stage 04): find the subframes in each satellite's bits, check the
parity, and read the orbit, the clock correction and the time out of the fields."""
from __future__ import annotations

from datetime import datetime

import numpy as np
from nicegui import run, ui

from components import explain_panel
from plots import HIGHLIGHT, LIMIT, figure
from stages import (MIN_SATS_FOR_FIX, STAGE_BY_KEY, older_than_previous_step, rel_path,
                    result_is_for_other_recording)
from state import IQSource, state

from bassanite import navigation as nav
from bassanite.provenance import write_meta
from bassanite.results import navigation_json, tracking_dir
from bassanite.tracking import MIN_SECONDS_FOR_EPHEMERIS, SUMMARY_CSV

ROW_BITS = nav.BITS_PER_SUBFRAME        # the bit stream is drawn 300 bits per row
# Bit-stream colours: (class, bit) -> colour. Classes: 0 not in a subframe, 1 data,
# 2 parity, 3 preamble.
STREAM_COLOURS = ("#eeeeee", "#bdbdbd", "#bbdefb", "#1976d2", "#e1bee7", "#8e24aa",
                  "#ffe082", "#ff8f00")
STREAM_CLASSES = ("not in a decoded subframe", "data", "parity", "preamble")
# Subframe anatomy: the first categories are fixed, then one colour per field.
ANATOMY_FIXED = (("not used by a position", "#f5f5f5"), ("parity", "#cfd8dc"),
                 ("preamble", "#ffb300"), ("time of week", "#81c784"),
                 ("subframe number", "#4db6ac"))
FIELD_COLOURS = ("#90caf9", "#f48fb1", "#ce93d8", "#ffcc80", "#a5d6a7", "#80deea",
                 "#e6ee9c", "#ffab91", "#b39ddb")
DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")

# Bumped when any page decodes, so every other open page re-reads the results.
results_version = 0


def discrete_scale(colours) -> list:
    """A Plotly colourscale that paints the integers 0..n-1 in `colours`."""
    n = len(colours)
    return [[edge, c] for i, c in enumerate(colours) for edge in (i / n, (i + 1) / n)]


def time_of_week_text(tow: float) -> str:
    """A time of week as a day and a time (GPS time; UTC is 18 s behind)."""
    day, rest = divmod(int(tow), 86400)
    return f"{DAYS[day % 7]} {rest // 3600:02d}:{rest % 3600 // 60:02d}:{rest % 60:02d}"


def subframes_text(sat: dict) -> str:
    """The subframes found, in order; one in brackets failed a parity check."""
    return " ".join(str(sf["number"]) if nav.complete(sf) else f"({sf['number']})"
                    for sf in sat["subframes"]) or "none"


# --- running the stage ------------------------------------------------------------------

def run_decode(src: IQSource) -> dict[int, dict]:
    """Decode every tracked satellite of `src` and write navigation.json, exactly
    like 04_decoding/decode.py. Returns {prn: satellite}."""
    folder = tracking_dir(src.path)
    satellites = nav.decode_tracks(folder)
    out = navigation_json(src.path)
    out.parent.mkdir(parents=True, exist_ok=True)
    nav.save_navigation(out, satellites)
    write_meta(out, "decode", src.path, src.fmt, src.fs,
               {"tracking_dir": str(folder),
                "usable_prns": sorted(p for p, s in satellites.items() if nav.usable(s))})
    return satellites


def saved_decode(src: IQSource) -> bool:
    """Whether this recording has decode results that are still current."""
    path = navigation_json(src.path)
    return (path.is_file() and not result_is_for_other_recording(path, src)
            and not older_than_previous_step(STAGE_BY_KEY["decode"], src))


# --- figures ------------------------------------------------------------------------------

def bit_classes(sat: dict) -> np.ndarray:
    """For each bit of the stream: 0 outside the decoded subframes, 1 data,
    2 parity, 3 preamble."""
    cls = np.zeros(len(sat["bits"]), dtype=int)
    for sf in sat["subframes"]:
        s = sf["start_bit"]
        cls[s:s + nav.BITS_PER_SUBFRAME] = 1
        for w in range(nav.WORDS_PER_SUBFRAME):
            cls[s + 30 * w + 24:s + 30 * w + 30] = 2
        cls[s:s + 8] = 3
    return cls


def bitstream_figure(sat: dict) -> dict:
    """The whole bit stream, 300 bits per row. Rows start at bit 0 of the stream,
    not at a subframe: the preambles line up in one column anyway, because
    subframes follow each other exactly 300 bits apart."""
    bits = np.array(sat["bits"], dtype=int)
    cls = bit_classes(sat)
    rows = -(-len(bits) // ROW_BITS)
    z = np.full(rows * ROW_BITS, np.nan)
    z[:len(bits)] = 2 * cls + bits
    where = {}
    for sf in sat["subframes"]:
        for k in range(nav.BITS_PER_SUBFRAME):
            where[sf["start_bit"] + k] = (sf["number"], k // 30 + 1)
    text = [f"bit {n}: {bits[n]}"
            + (f"<br>subframe {where[n][0]}, word {where[n][1]}, {STREAM_CLASSES[cls[n]]}"
               if n in where else "<br>not in a decoded subframe")
            if n < len(bits) else "" for n in range(rows * ROW_BITS)]
    data = [{"type": "heatmap", "z": z.reshape(rows, ROW_BITS).tolist(),
             "text": np.array(text, dtype=object).reshape(rows, ROW_BITS).tolist(),
             "hovertemplate": "%{text}<extra></extra>", "zmin": -0.5, "zmax": 7.5,
             "colorscale": discrete_scale(STREAM_COLOURS), "showscale": False,
             "xgap": 0, "ygap": 3}]
    if sat["lookalikes"]:
        data.append({"type": "scatter", "mode": "markers", "name": "preamble by chance",
                     "x": [n % ROW_BITS + 3.5 for n in sat["lookalikes"]],
                     "y": [n // ROW_BITS for n in sat["lookalikes"]],
                     "marker": {"symbol": "x-thin", "size": 9, "color": LIMIT,
                                "line": {"width": 2, "color": LIMIT}},
                     "hovertemplate": "The preamble pattern, but the parity that "
                                      "follows is wrong<extra></extra>"})
    starts = [sf["start_bit"] for sf in sat["subframes"]]
    if starts:
        data.append({"type": "scatter", "mode": "text", "showlegend": False,
                     "x": [s % ROW_BITS + 9 for s in starts],
                     "y": [s // ROW_BITS for s in starts],
                     "text": [f"<b>{sf['number']}</b>" for sf in sat["subframes"]],
                     "textposition": "middle right", "hoverinfo": "skip",
                     "textfont": {"size": 12, "color": "#ffffff"}})
    return figure(data, height=70 + 30 * rows,
                  xaxis={"title": "bit in the row (300 bits = 6 s per row)", "dtick": 30,
                         "range": [-0.5, ROW_BITS - 0.5]},
                  yaxis={"autorange": "reversed", "tickmode": "array",
                         "tickvals": list(range(rows)),
                         "ticktext": [f"bit {r * ROW_BITS}" for r in range(rows)]},
                  showlegend=bool(sat["lookalikes"]))


def anatomy(sf: dict) -> tuple[np.ndarray, list[str], list[tuple[int, int, str]]]:
    """Which part of the subframe each of its 300 bits belongs to.

    Returns (category index per bit, category names, labels): labels are
    (first bit index, number of bits, name) for each run to write a name on.
    """
    names = [name for name, _ in ANATOMY_FIXED]
    cat = np.zeros(nav.BITS_PER_SUBFRAME, dtype=int)
    labels = [(0, 8, "preamble"), (30, 17, "time of week"), (49, 3, "subframe")]
    for w in range(nav.WORDS_PER_SUBFRAME):
        cat[30 * w + 24:30 * w + 30] = 1
    cat[0:8], cat[30:47], cat[49:52] = 2, 3, 4
    for row in nav.fields_of(sf):
        names.append(row["name"])
        for first, n in row["parts"]:
            cat[first - 1:first - 1 + n] = len(names) - 1
            labels.append((first - 1, n, row["name"]))
    return cat, names, labels


def anatomy_figure(sf: dict) -> dict:
    """One subframe as 10 words of 30 bits, coloured by field."""
    cat, names, labels = anatomy(sf)
    colours = [c for _, c in ANATOMY_FIXED] + [FIELD_COLOURS[i % len(FIELD_COLOURS)]
                                               for i in range(len(names) - len(ANATOMY_FIXED))]
    bits = np.array(sf["data"])
    text = np.where(cat == 1, "·", bits.astype(str)).reshape(10, 30)
    hover = np.array([f"bit {n + 1} of the subframe (word {n // 30 + 1}, bit {n % 30 + 1})"
                      f"<br>{names[cat[n]]}" for n in range(300)], dtype=object).reshape(10, 30)
    annotations = [{"x": first % 30 + (n - 1) / 2, "y": first // 30, "text": name,
                    "showarrow": False, "yshift": 21, "font": {"size": 10, "color": "#37474f"}}
                   for first, n, name in labels]
    return figure(
        [{"type": "heatmap", "z": cat.reshape(10, 30).tolist(), "text": text.tolist(),
          "customdata": hover.tolist(), "texttemplate": "%{text}",
          "textfont": {"size": 10, "color": "#263238"},
          "hovertemplate": "%{customdata}<extra></extra>",
          "zmin": -0.5, "zmax": len(colours) - 0.5, "colorscale": discrete_scale(colours),
          "showscale": False, "xgap": 1, "ygap": 16}],
        height=470, margin={"l": 80, "r": 20, "t": 24, "b": 40},
        xaxis={"title": "bit in the word (25 to 30: parity)", "tickmode": "array",
               "tickvals": [0, 4, 9, 14, 19, 23, 29],
               "ticktext": ["1", "5", "10", "15", "20", "24", "30"]},
        yaxis={"autorange": "reversed", "tickmode": "array", "tickvals": list(range(10)),
               "ticktext": [f"word {w + 1} {'✓' if ok else '✗'}"
                            for w, ok in enumerate(sf["parity_ok"])]},
        annotations=annotations)


FIELD_COLUMNS = [
    {"name": "name", "label": "Field", "field": "name", "align": "left"},
    {"name": "meaning", "label": "Meaning", "field": "meaning", "align": "left"},
    {"name": "bits", "label": "Bits as sent", "field": "bits", "align": "left"},
    {"name": "raw", "label": "As an integer", "field": "raw", "align": "right"},
    {"name": "unit", "label": "× one unit", "field": "unit_value", "align": "right"},
    {"name": "value", "label": "= value", "field": "value", "align": "right"},
]


def field_rows(sf: dict) -> list[dict]:
    return [{"name": r["name"], "meaning": r["meaning"], "bits": r["bits"],
             "raw": f"{r['raw']:,}",
             "unit_value": "1" if r["unit_value"] == 1 else f"{r['unit_value']:.6g}",
             "value": f"{r['value']:.10g} {r['unit']}".strip()}
            for r in nav.fields_of(sf)]


HANDOVER_COLUMNS = [
    {"name": "prn", "label": "PRN", "field": "prn", "align": "left"},
    {"name": "ok", "label": "Usable", "field": "ok", "align": "left"},
    {"name": "subframes", "label": "Subframes read", "field": "subframes", "align": "left"},
    {"name": "time", "label": "Satellite time of code period 0 (s of the week)",
     "field": "time", "align": "right"},
    {"name": "a", "label": "Orbit radius (km)", "field": "a", "align": "right"},
    {"name": "e", "label": "Eccentricity", "field": "e", "align": "right"},
    {"name": "clock", "label": "Clock offset (µs)", "field": "clock", "align": "right"},
]


def handover_rows(satellites: dict[int, dict]) -> list[dict]:
    rows = []
    for prn, sat in sorted(satellites.items()):
        eph = sat["ephemeris"]
        rows.append({
            "prn": prn, "ok": "✅" if nav.usable(sat) else "⚠️",
            "subframes": subframes_text(sat),
            "time": f"{sat['period0_time']:.3f}" if sat["period0_time"] is not None else "—",
            "a": f"{eph['sqrt_a'] ** 2 / 1e3:,.0f}" if eph else "—",
            "e": f"{eph['e']:.5f}" if eph else "—",
            "clock": f"{eph['a_f0'] * 1e6:+.1f}" if eph else "—"})
    return rows


def status_text(satellites: dict[int, dict]) -> str:
    good = sorted(p for p, s in satellites.items() if nav.usable(s))
    text = (f"{len(good)} of {len(satellites)} satellites have a complete message "
            f"(subframes 1, 2 and 3)")
    text += f": PRN {', '.join(map(str, good))}. " if good else ". "
    return text + ("Enough for a position: go to step 5." if len(good) >= MIN_SATS_FOR_FIX
                   else f"A position needs {MIN_SATS_FOR_FIX}.")


def time_text(sat: dict, sf: dict, near: datetime) -> str:
    """What the selected subframe says about time, in a sentence."""
    k = sat["bit_offset"] + 20 * sf["start_bit"]
    text = (f"Word 2 of this subframe says it started at **{sf['tow']:,} s** of the week "
            f"(GPS time): {time_of_week_text(sf['tow'])}.")
    eph = sat["ephemeris"]
    if eph:
        week = nav.full_week(int(eph["week"]), near)
        text += (f" Subframe 1 gives the week, {int(eph['week'])} (modulo 1024: week "
                 f"{week} since 1980), so that is "
                 f"**{nav.utc_from_gps(week, sf['tow']):%Y-%m-%d %H:%M:%S} UTC**.")
    text += (f" The subframe begins at bit {sf['start_bit']} of the stream, which is "
             f"tracking's code period {k:,} (bit 0 starts at period {sat['bit_offset']}, "
             f"and a bit is 20 periods).")
    if sat["period0_time"] is not None:
        text += (f" So code period 0 left the satellite at **{sat['period0_time']:,.3f} s**, "
                 "and every code period after it 1 ms later: tracking's code timing is now "
                 "a clock that shows satellite time.")
    return text


# --- the tab ------------------------------------------------------------------------------

class DecodeTab:
    """One instance per browser tab (each client builds its own page)."""

    def __init__(self, go_to=None, on_decoded=None) -> None:
        self.go_to = go_to
        self.on_decoded = on_decoded        # called after a run wrote new results
        self.satellites: dict[int, dict] = {}
        self.selected: int | None = None
        self.subframe_index = 0
        self._pending = self._busy = False
        self._seen_version = results_version

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
        with self._card("Read the messages"):
            with ui.row().classes("items-center gap-3"):
                self.run_button = ui.button("Decode the messages", icon="mark_email_read",
                                            on_click=self.start_run).props("no-caps")
                self.goto_track = ui.button("Go to step 3", on_click=lambda: self.go_to(
                    "tracking") if self.go_to else None).props("flat no-caps")
            self.status_label = ui.label().classes("font-medium")
            self.detail_label = ui.label().classes("text-xs text-grey-7")
            self.cards = ui.row().classes("w-full gap-2")

        self.detail = ui.column().classes("w-full gap-4")
        with self.detail:
            with self._card("The bit stream",
                            "Every bit tracking read from the selected satellite, 300 "
                            "per row. Amber: the preamble 10001011 that opens each "
                            "subframe (its number is written next to it). Blue: data. "
                            "Purple: parity. Grey: bits outside a decoded subframe. "
                            "Dark is 1, light is 0."):
                self.p_stream = ui.plotly(figure([])).classes("w-full")
                self.stream_note = ui.label().classes("text-sm text-grey-8")
                explain_panel("decode", "frame_sync")
            with self._card("One subframe, field by field",
                            "The ten words of a subframe, 30 bits each. Colours mark "
                            "which field each bit belongs to; the last 6 bits of every "
                            "word are its parity (✓ or ✗ on the left)."):
                self.c_subframe = ui.select({}, label="Subframe",
                                            on_change=lambda e: self._select_subframe(e.value)) \
                    .classes("w-full md:w-96")
                self.p_anatomy = ui.plotly(figure([])).classes("w-full")
                explain_panel("decode", "parity")
                self.fields_note = ui.label().classes("text-sm text-grey-8")
                self.fields = ui.table(columns=FIELD_COLUMNS, rows=[], row_key="name") \
                    .classes("w-full").props("dense flat wrap-cells")
                explain_panel("decode", "fields")
            with self._card("What time is it?"):
                self.time_md = ui.markdown().classes("w-full")
                explain_panel("decode", "time")
        with self._card("Handed to step 5",
                        "One row per tracked satellite: its orbit, its clock correction "
                        "and the time label, if its message is complete."):
            self.handover = ui.table(columns=HANDOVER_COLUMNS, rows=[], row_key="prn") \
                .classes("w-full").props("dense flat")
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
        try:
            await run.io_bound(run_decode, src)
        except (OSError, ValueError, KeyError) as err:
            ui.notify(f"Decoding failed: {err}", type="negative", multi_line=True)
            return
        finally:
            self._busy = False
            self.run_button.enable()
        results_version += 1
        self._seen_version = results_version
        if self.on_decoded is not None:
            self.on_decoded()
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

    def _show_nothing(self, message: str, can_run: bool, to_tracking: bool = False) -> None:
        self.status_label.set_text(message)
        self.detail_label.set_text("")
        self.satellites = {}
        self.cards.clear()
        self.detail.set_visibility(False)
        self.handover.rows = []
        self.run_button.set_enabled(can_run)
        self.goto_track.set_visibility(to_tracking)

    async def _update(self) -> None:
        src = state.source
        if src is None or src.problems():
            self._show_nothing("Load a recording first (top bar, or the Overview tab).", False)
            return
        if not (tracking_dir(src.path) / SUMMARY_CSV).is_file():
            self._show_nothing("First track the satellites of this recording (step 3): "
                               "decoding reads the bits tracking produced.", False, True)
            return
        if not saved_decode(src):
            stale = navigation_json(src.path).is_file()
            self._show_nothing(("The decoded messages on disk are older than the tracking "
                                "results. " if stale else "Not decoded yet. ")
                               + "Press “Decode the messages”.", True)
            return
        try:
            satellites = await run.io_bound(nav.decode_tracks, tracking_dir(src.path))
        except (OSError, ValueError, KeyError) as err:
            self._show_nothing(f"Could not read the tracking results: {err}", True)
            return
        self.satellites = satellites
        self.run_button.enable()
        self.goto_track.set_visibility(False)
        self.status_label.set_text(status_text(satellites))
        longest = max((s["nbits"] for s in satellites.values()), default=0) * nav.BIT_S
        self.detail_label.set_text(
            f"Results: {rel_path(navigation_json(src.path))}"
            + (f". Only {longest:.0f} s of bits: subframes 1, 2 and 3 are all complete "
               f"only from {MIN_SECONDS_FOR_EPHEMERIS:.0f} s."
               if longest < MIN_SECONDS_FOR_EPHEMERIS else ""))
        self.handover.rows = handover_rows(satellites)
        if self.selected not in satellites:
            good = [p for p, s in satellites.items() if nav.usable(s)]
            self.selected = (max(good or satellites, key=lambda p: satellites[p]["cn0_dbhz"])
                             if satellites else None)
        self._show_cards()
        self._select(self.selected)

    def _show_cards(self) -> None:
        self.cards.clear()
        with self.cards:
            for prn, sat in sorted(self.satellites.items()):
                selected = prn == self.selected
                card = ui.card().classes("w-44 cursor-pointer q-pa-sm gap-0").props(
                    "flat bordered").style(
                    f"border: {'3px solid ' + HIGHLIGHT if selected else '1px solid #ddd'}")
                card.on("click", lambda _, p=prn: self._select(p, redraw_cards=True))
                with card:
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(f"PRN {prn}").classes("font-bold")
                        ui.label("✅ complete" if nav.usable(sat) else "⚠️ incomplete") \
                            .classes("text-xs")
                    ui.label(f"subframes {subframes_text(sat)}").classes("text-sm")
                    words = sum(sum(sf["parity_ok"]) for sf in sat["subframes"])
                    ui.label(f"parity {words}/{10 * len(sat['subframes'])} words"
                             if sat["subframes"] else "no subframe found") \
                        .classes("text-xs text-grey-7")

    def _select(self, prn: int | None, redraw_cards: bool = False) -> None:
        sat = self.satellites.get(prn)
        if sat is None:
            self.detail.set_visibility(False)
            return
        self.selected = prn
        if redraw_cards:
            self._show_cards()
        self.detail.set_visibility(True)
        self.p_stream.update_figure(bitstream_figure(sat))
        note = (f"PRN {prn}: {sat['nbits']:,} bits. The preamble pattern appears "
                f"{len(sat['subframes']) + len(sat['lookalikes'])} times; "
                f"{len(sat['subframes'])} are subframe starts confirmed by parity")
        note += (f", {len(sat['lookalikes'])} are chance matches (red crosses). "
                 if sat["lookalikes"] else ". ")
        if sat["inverted"] is not None:
            note += ("The bits arrived inverted (the carrier loop locked upside down), "
                     "which changes nothing: see the parity panel below."
                     if sat["inverted"] else "The bits arrived upright.")
        for problem in sat["problems"]:
            note += f" ⚠️ {problem[0].upper()}{problem[1:]}."
        self.stream_note.set_text(note)

        options = {i: (f"Subframe {sf['number']} at bit {sf['start_bit']} "
                       f"({time_of_week_text(sf['tow'])})"
                       + ("" if nav.complete(sf) else " ⚠️ parity error"))
                   for i, sf in enumerate(sat["subframes"])}
        first_1 = next((i for i, sf in enumerate(sat["subframes"])
                        if sf["number"] == 1 and nav.complete(sf)), 0)
        self.subframe_index = first_1
        self.c_subframe.set_options(options, value=first_1 if options else None)
        self._select_subframe(first_1 if options else None)

    def _select_subframe(self, index) -> None:
        sat = self.satellites.get(self.selected)
        has = sat is not None and index is not None and index < len(sat["subframes"])
        for element in (self.p_anatomy, self.fields, self.fields_note, self.time_md):
            element.set_visibility(has)
        if not has:
            return
        self.subframe_index = index
        sf = sat["subframes"][index]
        self.p_anatomy.update_figure(anatomy_figure(sf))
        rows = field_rows(sf)
        self.fields.rows = rows
        self.fields.set_visibility(bool(rows))
        self.fields_note.set_text(
            f"The {len(rows)} numbers subframe {sf['number']} carries. Each is an integer "
            "of a fixed number of bits, multiplied by the value of one unit."
            if rows else f"Subframe {sf['number']} carries slow data (the other satellites' "
                         "rough orbits, the ionosphere model) that a position does not need: "
                         "nothing is read from it.")
        near = nav.recording_date(state.source.path) if state.source else datetime.now()
        self.time_md.set_content(time_text(sat, sf, near))
