"""Signal tab (stage 01): what the SDR recorded, before any GPS processing."""
from __future__ import annotations

import time
from dataclasses import dataclass, replace

import numpy as np
from nicegui import run, ui

from state import GPS_L1_HZ, IQSource, state  # first: puts the repo root on sys.path

from common.signal_view import adc_stats, code_histogram, psd, raw_samples, waterfall  # noqa: E402,I001
from components import explain_panel  # noqa: E402

CA_HALF_WIDTH_HZ = 1.023e6      # GPS C/A main lobe: +/- one chip rate around L1
TIME_SAMPLES = 200              # samples shown in the time plot
WATERFALL_NFFT, WATERFALL_ROWS = 512, 150

PLOT_CONFIG = {"displaylogo": False, "responsive": True,
               "modeBarButtonsToRemove": ["lasso2d", "select2d"]}


def _layout(**kwargs) -> dict:
    layout = {"margin": {"l": 60, "r": 20, "t": 10, "b": 45}, "height": 300,
              "template": "plotly_white",
              "legend": {"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0}}
    layout.update(kwargs)
    return layout


def _empty_figure(message: str = "") -> dict:
    return {"data": [], "config": PLOT_CONFIG, "layout": _layout(
        xaxis={"visible": False}, yaxis={"visible": False},
        annotations=[{"text": message, "showarrow": False, "font": {"size": 14}}]
        if message else [])}


@dataclass(frozen=True)
class Settings:
    start_s: float = 0.0
    window_ms: int = 100
    nfft: int = 1024
    remove_dc: bool = True


# --- analysis (runs in a worker thread) ---------------------------------------------

def analyse(src: IQSource, s: Settings) -> dict:
    """Everything the tab shows, computed from one window of the recording."""
    seconds = s.window_ms / 1000
    start = min(s.start_s, max(src.duration_s - seconds, 0.0))
    i, q = raw_samples(src.path, src.fmt, src.fs, seconds=seconds, skip_seconds=start)
    stats = adc_stats(i, q, src.fmt)
    int8 = src.fmt == "int8"

    ci, cq = (i - i.mean(), q - q.mean()) if s.remove_dc else (i, q)
    x = (ci + 1j * cq) / (128.0 if int8 else 1.0)
    freqs, power = psd(x, src.fs, min(s.nfft, len(x)))

    result = {"start": start, "stats": stats, "int8": int8,
              "psd": (freqs, power),
              "time": (ci[:TIME_SAMPLES], cq[:TIME_SAMPLES]),
              "density": _iq_density(i, q, int8, s.remove_dc)}
    if int8:
        result["hist"] = (code_histogram(i), code_histogram(q))
    return result


def _iq_density(i: np.ndarray, q: np.ndarray, int8: bool, remove_dc: bool):
    """2-D histogram of the IQ plane, centred on the cloud and spanning +/-4 sigma
    (and at least the origin). int8: one bin per ADC code, so the grid of whole
    codes stays visible. Empty bins are None (drawn transparent)."""
    half = 4 * max(i.std(), q.std(), 1e-12)
    if int8:
        half = max(np.ceil(half), 3.0)
        axes = []
        for v in (i, q):
            centre = np.round(v.mean())
            lo = min(centre - half, 0) - 0.5
            hi = max(centre + half, 0) + 0.5
            axes.append(np.arange(lo, hi + 1e-9, 1.0))
    else:
        axes = [np.linspace(min(v.mean() - half, 0), max(v.mean() + half, 0), 61)
                for v in (i, q)]
    counts, xe, ye = np.histogram2d(i, q, bins=axes)
    xc, yc = (xe[:-1] + xe[1:]) / 2, (ye[:-1] + ye[1:]) / 2
    if remove_dc:
        xc, yc = xc - i.mean(), yc - q.mean()
    z = np.where(counts.T > 0, counts.T, np.nan)   # rows = Q, columns = I
    return xc, yc, z


_waterfall_cache: dict[tuple, tuple] = {}


def cached_waterfall(src: IQSource, remove_dc: bool):
    key = (str(src.path), src.fmt, src.fs, src.size_bytes, remove_dc)
    if key not in _waterfall_cache:
        _waterfall_cache.clear()
        _waterfall_cache[key] = waterfall(src.path, src.fmt, src.fs, nfft=WATERFALL_NFFT,
                                          rows=WATERFALL_ROWS, remove_dc=remove_dc)
    return _waterfall_cache[key]


# --- figures --------------------------------------------------------------------------

def spectrum_figure(src: IQSource, freqs, power) -> dict:
    mhz = (src.center_hz + freqs) / 1e6
    shapes, notes = [], []
    if abs(src.center_hz - GPS_L1_HZ) < src.fs / 2:
        l1 = GPS_L1_HZ / 1e6
        w = CA_HALF_WIDTH_HZ / 1e6
        shapes.append({"type": "rect", "xref": "x", "yref": "paper",
                       "x0": l1 - w, "x1": l1 + w, "y0": 0, "y1": 1,
                       "fillcolor": "rgba(255,179,0,0.12)", "line": {"width": 0}})
        notes.append({"x": l1, "y": 1, "xref": "x", "yref": "paper", "yanchor": "top",
                      "showarrow": False, "font": {"size": 11, "color": "#8a6d00"},
                      "text": "GPS C/A signal is here, buried below the noise"})
    return {"config": PLOT_CONFIG,
            "data": [{"type": "scatter", "mode": "lines", "x": mhz.tolist(),
                      "y": np.round(power, 2).tolist(), "line": {"width": 1.2},
                      "name": "power", "hovertemplate": "%{x:.4f} MHz<br>%{y:.1f} dBFS"
                                                       "<extra></extra>"}],
            "layout": _layout(shapes=shapes, annotations=notes, showlegend=False,
                              xaxis={"title": "Frequency (MHz)"},
                              yaxis={"title": "Power per bin (dBFS)"})}


def waterfall_figure(src: IQSource, times, freqs, power) -> dict:
    mhz = (src.center_hz + freqs) / 1e6
    lo, hi = np.percentile(power, [1, 99.9])
    return {"config": PLOT_CONFIG,
            "data": [{"type": "heatmap", "x": np.round(mhz, 4).tolist(),
                      "y": np.round(times, 3).tolist(), "z": np.round(power, 1).tolist(),
                      "zmin": float(lo), "zmax": float(hi), "colorscale": "Viridis",
                      "colorbar": {"title": {"text": "dBFS"}, "thickness": 12},
                      "hovertemplate": "%{x:.4f} MHz<br>t = %{y:.2f} s<br>%{z:.1f} dBFS"
                                       "<extra></extra>"}],
            "layout": _layout(xaxis={"title": "Frequency (MHz)"},
                              yaxis={"title": "Time in recording (s)",
                                     "autorange": "reversed"})}


def time_figure(src: IQSource, ci, cq, int8: bool) -> dict:
    t_us = (np.arange(len(ci)) / src.fs * 1e6).tolist()
    unit = "ADC code" if int8 else "amplitude"
    trace = {"type": "scatter", "mode": "lines+markers", "marker": {"size": 3},
             "line": {"width": 1}, "x": t_us}
    return {"config": PLOT_CONFIG,
            "data": [{**trace, "y": np.round(ci, 3).tolist(), "name": "I"},
                     {**trace, "y": np.round(cq, 3).tolist(), "name": "Q"}],
            "layout": _layout(xaxis={"title": "Time (µs)"}, yaxis={"title": unit})}


def iq_figure(xc, yc, counts, int8: bool) -> dict:
    unit = " (ADC code)" if int8 else ""
    z = [[None if np.isnan(c) else int(c) for c in row] for row in counts]
    return {"config": PLOT_CONFIG,
            "data": [{"type": "heatmap", "x": np.round(xc, 3).tolist(),
                      "y": np.round(yc, 3).tolist(), "z": z,
                      "colorscale": "Blues", "reversescale": True, "showscale": False,
                      "hoverongaps": False,
                      "hovertemplate": "I %{x:.2f}, Q %{y:.2f}<br>%{z} samples"
                                       "<extra></extra>"}],
            "layout": _layout(
                xaxis={"title": "I" + unit, "zeroline": True, "zerolinecolor": "#999"},
                yaxis={"title": "Q" + unit, "zeroline": True, "zerolinecolor": "#999",
                       "scaleanchor": "x", "scaleratio": 1})}


def histogram_figure(hist_i, hist_q) -> dict:
    (codes, ci), (_, cq) = hist_i, hist_q
    bar = {"type": "bar", "x": codes.tolist(), "opacity": 0.65}
    limit = {"type": "line", "yref": "paper", "y0": 0, "y1": 1,
             "line": {"dash": "dash", "color": "#c62828", "width": 1.5}}
    return {"config": PLOT_CONFIG,
            "data": [{**bar, "y": ci.tolist(), "name": "I"},
                     {**bar, "y": cq.tolist(), "name": "Q"}],
            "layout": _layout(
                barmode="overlay", bargap=0,
                xaxis={"title": "ADC code", "range": [-135, 135]},
                yaxis={"title": "Number of samples"},
                shapes=[{**limit, "x0": -128, "x1": -128}, {**limit, "x0": 127, "x1": 127}],
                annotations=[{"x": x, "y": 1, "yref": "paper", "showarrow": False,
                              "yanchor": "top", "xanchor": a, "text": "ADC limit",
                              "font": {"color": "#c62828", "size": 11}}
                             for x, a in ((-128, "left"), (127, "right"))])}


# --- level verdict -------------------------------------------------------------------

def level_messages(stats: dict, int8: bool) -> list[tuple[str, str]]:
    """(kind, text) pairs, kind in positive / warning / info."""
    if not int8:
        return [("info", "This file stores floats, so ADC clipping can't be checked.")]
    msgs = []
    spread = max(stats["std_i"], stats["std_q"])
    clip = stats["clip_fraction"]
    if clip > 1e-3:
        msgs.append(("warning", f"{100 * clip:.2f} % of samples hit the ADC limits "
                                "(clipping). Lower the gain (LNA or VGA)."))
    elif spread < 2:
        msgs.append(("info", f"The noise spans only about ±{spread:.1f} ADC levels "
                             f"({stats['levels_used']} of 256 levels used). More gain "
                             "would use the ADC better, but GPS copes: many GPS chips "
                             "use only 1 or 2 bits per sample."))
    else:
        msgs.append(("positive", f"Healthy level: no clipping, noise spread over "
                                 f"±{spread:.1f} ADC levels."))
    dc = max(abs(stats["dc_i"]), abs(stats["dc_q"]))
    if dc > spread:
        msgs.append(("info", f"DC offset (I {stats['dc_i']:+.1f}, Q {stats['dc_q']:+.1f} "
                             "codes) is larger than the noise. It is removed before "
                             "processing. Turn off 'Remove DC offset' in Expert mode "
                             "to see its spike at the centre of the spectrum."))
    return msgs


# --- the tab ---------------------------------------------------------------------------

class SignalTab:
    """One instance per browser tab (each client builds its own page)."""

    def __init__(self) -> None:
        self.settings = Settings()
        self._pending = False
        self._busy = False

    def request_update(self) -> None:
        self._pending = True

    def _set(self, **changes) -> None:
        self.settings = replace(self.settings, **changes)
        self.request_update()

    def build(self) -> None:
        with ui.row().classes("w-full items-start gap-4"):
            self._build_controls()
            with ui.column().classes("flex-1 min-w-[18rem] gap-4"):
                self._build_status()
                self._build_plots()
        ui.timer(0.3, self._tick)
        self.request_update()

    def _build_controls(self) -> None:
        with ui.card().classes("w-full md:w-72").props("flat bordered") \
                .bind_visibility_from(state, "mode", value="expert"):
            ui.label("Analysis settings").classes("text-subtitle1 font-bold")
            self.start_label = ui.label("Start: 0.0 s").classes("text-sm")
            self.start_slider = ui.slider(min=0, max=1, step=0.1, value=0,
                                          on_change=lambda e: self._set(start_s=e.value))
            ui.select({1: "1 ms", 10: "10 ms", 100: "100 ms", 1000: "1 s"},
                      label="Window length", value=self.settings.window_ms,
                      on_change=lambda e: self._set(window_ms=e.value)).classes("w-full")
            ui.select({256: "256", 1024: "1024", 4096: "4096"}, label="FFT size",
                      value=self.settings.nfft,
                      on_change=lambda e: self._set(nfft=e.value)).classes("w-full")
            ui.switch("Remove DC offset", value=self.settings.remove_dc,
                      on_change=lambda e: self._set(remove_dc=e.value))
            ui.label("The waterfall always covers the whole recording.") \
                .classes("text-xs text-grey-7")
            ui.separator()
            ui.label("Live capture").classes("text-subtitle1 font-bold")
            ui.label("Gains, sample rate and bias-tee controls arrive with live capture.") \
                .classes("text-xs text-grey-7")

    def _build_status(self) -> None:
        with ui.row().classes("w-full items-center gap-2"):
            self.spinner = ui.spinner(size="sm")
            self.window_label = ui.label().classes("text-sm text-grey-8")
        self.stats_row = ui.row().classes("w-full gap-2")
        self.messages = ui.column().classes("w-full gap-1")

    def _plot_card(self, title: str, caption: str, panel: str) -> ui.plotly:
        with ui.card().classes("w-full").props("flat bordered"):
            ui.label(title).classes("text-subtitle1 font-bold")
            ui.label(caption).classes("text-sm text-grey-8")
            plot = ui.plotly(_empty_figure("Analysing…")).classes("w-full")
            explain_panel("signal", panel)
        return plot

    def _build_plots(self) -> None:
        self.spectrum = self._plot_card(
            "Spectrum",
            "Average power at each frequency. Flat in the middle, falling at the edges "
            "(the HackRF's filter). No GPS bump: the satellites are below the noise.",
            "spectrum")
        self.waterfall = self._plot_card(
            "Waterfall: spectrum over the whole recording",
            "Each row is one spectrum; time runs downward. A steady, even texture means "
            "no interference came and went during the recording.", "waterfall")
        with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4"):
            self.time = self._plot_card(
                "I and Q against time",
                f"The first {TIME_SAMPLES} samples of the window: random-looking "
                "noise.", "samples")
            self.iq = self._plot_card(
                "IQ plane",
                "Each sample is a point (I, Q); darker = more samples. A round cloud "
                "centred on 0 is pure noise.", "iq_plane")
        self.histogram = self._plot_card(
            "ADC levels used",
            "How often each of the 256 ADC codes occurs. The red lines are the limits; "
            "samples piling up there would mean clipping.", "adc")

    async def _tick(self) -> None:
        if self._pending and not self._busy:
            self._pending = False
            self._busy = True
            try:
                await self._update()
            finally:
                self._busy = False

    async def _update(self) -> None:
        src = state.source
        self.spinner.set_visibility(True)
        self.messages.clear()
        problem = ("Load a recording first (top bar, or the Overview tab)."
                   if src is None else (src.problems() or [None])[0])
        if problem:
            for plot in (self.spectrum, self.waterfall, self.time, self.iq, self.histogram):
                plot.update_figure(_empty_figure(problem))
            self.window_label.set_text("")
            self.stats_row.clear()
            self.spinner.set_visibility(False)
            return

        s = self.settings
        self.start_slider.props["max"] = round(max(src.duration_s - s.window_ms / 1000, 0), 1)
        self.window_label.set_text("Analysing…")
        t0 = time.perf_counter()
        result = await run.io_bound(analyse, src, s)
        wf = await run.io_bound(cached_waterfall, src, s.remove_dc)
        elapsed = time.perf_counter() - t0

        self.start_label.set_text(f"Start: {result['start']:.1f} s")
        self.window_label.set_text(
            f"{src.path.name}: window {result['start']:.1f} s to "
            f"{result['start'] + s.window_ms / 1000:.3f} s, FFT size {s.nfft} "
            f"(computed in {elapsed:.1f} s)")
        self.spectrum.update_figure(spectrum_figure(src, *result["psd"]))
        self.waterfall.update_figure(waterfall_figure(src, *wf))
        self.time.update_figure(time_figure(src, *result["time"], result["int8"]))
        self.iq.update_figure(iq_figure(*result["density"], result["int8"]))
        if result["int8"]:
            self.histogram.update_figure(histogram_figure(*result["hist"]))
        else:
            self.histogram.update_figure(_empty_figure("Not available for float files"))
        self._show_stats(result["stats"], result["int8"])
        self.spinner.set_visibility(False)

    def _show_stats(self, stats: dict, int8: bool) -> None:
        unit = " codes" if int8 else ""
        chips = [("Level", f"{stats['rms_dbfs']:.1f} dBFS"),
                 ("Noise spread σ", f"I {stats['std_i']:.2f} · Q {stats['std_q']:.2f}{unit}"),
                 ("DC offset", f"I {stats['dc_i']:+.2f} · Q {stats['dc_q']:+.2f}{unit}")]
        if int8:
            chips += [("Clipped", f"{100 * stats['clip_fraction']:.3f} %"),
                      ("Levels used", f"{stats['levels_used']} of 256")]
        self.stats_row.clear()
        with self.stats_row:
            for name, value in chips:
                with ui.card().classes("px-3 py-1").props("flat bordered"):
                    ui.label(name).classes("text-xs text-grey-7")
                    ui.label(value).classes("text-sm font-medium")
        icons = {"positive": "✅", "warning": "⚠️", "info": "ℹ️"}
        with self.messages:
            for kind, text in level_messages(stats, int8):
                ui.label(f"{icons[kind]} {text}").classes("text-sm")
