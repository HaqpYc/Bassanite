"""Find satellites tab (stage 02, acquisition): how a satellite buried below the
noise is found by its code, and which satellites are in the recording."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, fields, replace
from functools import lru_cache
from pathlib import Path

import numpy as np
from nicegui import run, ui

from state import IQSource, state  # first: puts the repo root on sys.path

import bassanite.acquisition as backend  # noqa: E402,I001
from bassanite.acquisition import (DEFAULT_DOP_RANGE_HZ, DEFAULT_DOP_STEP_HZ,  # noqa: E402
                                   DEFAULT_MS, DEFAULT_THRESHOLD, acquire_prn, cn0_from_peak,
                                   FINE_MS, correlation_surface, read_results,
                                   refine_doppler, scan, scan_seconds, seconds_needed,
                                   write_results)
from bassanite.provenance import read_meta, write_meta  # noqa: E402
from bassanite.results import scan_csv  # noqa: E402
from bassanite.synthetic import read_truth  # noqa: E402
from common.ca_code import (CODE_LENGTH, G1_FEEDBACK, G2_FEEDBACK, G2_TAPS,  # noqa: E402
                            REGISTER_STAGES, ca_code, register_states)
from common.iq_loader import load_iq  # noqa: E402
from components import explain_panel  # noqa: E402
from plots import DETECTED, HIGHLIGHT, LIMIT, NOISE, empty_figure, figure  # noqa: E402
from stages import MIN_SATS_FOR_FIX, rel_path, result_is_for_other_recording  # noqa: E402

PRNS = tuple(range(1, 33))
SURFACE_COLUMNS = 512      # code-phase columns sent to the browser for the surface
TRY_MAX_MS = 20            # the integration-time slider goes up to this
CHIPS_SHOWN = 100          # chips drawn in the "Meet the codes" waveform
PERSISTENCE_POINTS = 5     # times at which the persistence check rescans
PERSISTENCE_CONTRAST = 2   # undetected PRNs rescanned too, to show what noise does
LABEL_BG = "rgba(255,255,255,0.85)"   # behind plot labels that may overlap bars


def samples_per_ms(fs: float) -> int:
    return int(round(fs * 1e-3))


# --- settings and results on disk -------------------------------------------------------

@dataclass(frozen=True)
class ScanSettings:
    """The search grid and detection rule. Field names match the provenance file
    that 02_acquisition/scan_prns.py writes."""
    dop_range_hz: float = DEFAULT_DOP_RANGE_HZ
    dop_step_hz: float = DEFAULT_DOP_STEP_HZ
    ms: int = DEFAULT_MS
    skip_seconds: float = 0.0
    threshold: float = DEFAULT_THRESHOLD

    @classmethod
    def from_meta(cls, meta: dict | None) -> ScanSettings:
        saved = (meta or {}).get("settings", {})
        names = {f.name for f in fields(cls)}
        return cls(**{k: int(v) if k == "ms" else float(v)
                      for k, v in saved.items() if k in names})

    def search(self) -> tuple[float, float, float, int]:
        """(dop_min, dop_max, dop_step, nms), the arguments acquire_prn takes."""
        return -self.dop_range_hz, self.dop_range_hz, self.dop_step_hz, self.ms


DEFAULT_SETTINGS = ScanSettings()


def window_start(src: IQSource, skip_s: float, ms: int) -> float:
    """`skip_s`, moved back if needed so `ms` of signal fit in the recording."""
    return backend.window_start(src.duration_s, skip_s, ms)


def load_window(src: IQSource, skip_s: float, ms: int, extra_ms: int = 0) -> np.ndarray:
    """The samples a scan integrating `ms` reads (at least FINE_MS, for the Doppler
    refinement), plus `extra_ms` more if available."""
    x = load_iq(src.path, src.fmt, src.fs, seconds=scan_seconds(ms + extra_ms),
                skip_seconds=window_start(src, skip_s, ms))
    if len(x) < ms * samples_per_ms(src.fs):
        raise ValueError(f"The recording is too short: integrating {ms} ms needs "
                         f"{seconds_needed(ms) * 1000:.0f} ms of signal.")
    return x


def results_csv(src: IQSource) -> Path:
    """Where the scan results of `src` live (results/<recording>/acquisition/)."""
    return scan_csv(src.path)


def run_scan(src: IQSource, s: ScanSettings, progress=None,
             csv_path: Path | None = None) -> list[dict]:
    """Scan all 32 PRNs and write the results where stage 03 reads them."""
    csv_path = csv_path or results_csv(src)
    s = replace(s, skip_seconds=window_start(src, s.skip_seconds, s.ms))
    x = load_window(src, s.skip_seconds, s.ms)
    dop_min, dop_max, dop_step, nms = s.search()
    results = scan(x, src.fs, dop_min, dop_max, dop_step, nms, s.threshold,
                   progress=progress)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_results(results, csv_path)
    write_meta(csv_path, "acquisition", src.path, src.fmt, src.fs, asdict(s))
    return results


def saved_results(src: IQSource, csv_path: Path | None = None
                  ) -> tuple[list[dict], ScanSettings] | None:
    """The scan results on disk, if they belong to `src`."""
    csv_path = csv_path or results_csv(src)
    if not csv_path.is_file() or result_is_for_other_recording(csv_path, src):
        return None
    return read_results(csv_path), ScanSettings.from_meta(read_meta(csv_path))


def detected_prns(results: list[dict] | None) -> list[int]:
    return [r["prn"] for r in results or [] if r["detected"]]


def satellites(n: int) -> str:
    return f"{n} satellite" + ("" if n == 1 else "s")


# --- one satellite in detail ---------------------------------------------------------------

def max_pool(a: np.ndarray, columns: int) -> tuple[np.ndarray, int]:
    """Shrink the last axis to about `columns` by keeping the maximum of each group
    of k neighbours, so a narrow peak survives. Returns (pooled, k)."""
    n = a.shape[-1]
    k = max(int(np.ceil(n / columns)), 1)
    pad = -n % k
    if pad:
        a = np.concatenate([a, np.full(a.shape[:-1] + (pad,), -np.inf)], axis=-1)
    return a.reshape(a.shape[:-1] + (-1, k)).max(axis=-1), k


@dataclass
class PrnView:
    """Everything the tab shows about one PRN."""
    prn: int
    code_phase_samp: int
    doppler_hz: float
    peak_over_mean: float
    fine_doppler_hz: float
    dopplers: np.ndarray
    surface: np.ndarray        # max-pooled to ~SURFACE_COLUMNS, in units of its mean
    surface_chips: np.ndarray  # code phase (chips) at the centre of each pooled column
    code_slice: np.ndarray     # full row at the best Doppler, in units of the row mean
    doppler_slice: np.ndarray  # column at the best code phase, in units of the surface mean


def prn_view(x: np.ndarray, fs: float, prn: int, s: ScanSettings) -> PrnView:
    cp, dop, ratio, dopplers, surface = acquire_prn(x, fs, prn, *s.search(),
                                                    return_surface=True)
    i = int(np.argmin(np.abs(dopplers - dop)))
    spms = surface.shape[1]
    scale = surface.mean()
    pooled, k = max_pool(surface / scale, SURFACE_COLUMNS)
    centres = np.minimum(np.arange(pooled.shape[1]) * k + (k - 1) / 2, spms - 1)
    return PrnView(prn, cp, dop, ratio,
                   refine_doppler(x, fs, prn, dop, cp, FINE_MS, s.dop_step_hz),
                   dopplers, pooled, centres * CODE_LENGTH / spms,
                   surface[i] / surface[i].mean(), surface[:, cp] / scale)


# --- try it: Doppler error and integration time --------------------------------------------

def doppler_loss(df_hz, coherent_s: float = 1e-3):
    """Fraction of the correlation power left when the carrier wipe-off is off by
    df_hz: sinc^2(df * T) for a coherent integration of T seconds."""
    return np.sinc(np.asarray(df_hz) * coherent_s) ** 2


def signal_power(row: np.ndarray, code_phase: int) -> float:
    """The satellite's power in one correlation row: the cell at its code phase
    (+/-1 sample) minus the row average, which is almost all noise."""
    cols = (code_phase + np.arange(-1, 2)) % len(row)
    return float(row[cols].max() - row.mean())


def highest_noise(row: np.ndarray, code_phase: int, guard: int) -> float:
    """The largest cell away from the satellite's peak (+/-guard samples)."""
    keep = np.ones(len(row), dtype=bool)
    keep[(code_phase + np.arange(-guard, guard + 1)) % len(row)] = False
    return float(row[keep].max())


def try_row(x, fs, prn, doppler, nms) -> np.ndarray:
    """Correlation power at one Doppler, summed over nms ms, for every code phase."""
    return correlation_surface(x, fs, prn, [doppler], nms)[0]


# --- persistence --------------------------------------------------------------------------------

def persistence(src: IQSource, s: ScanSettings, prns: list[int], detected: list[int],
                points: int = PERSISTENCE_POINTS, progress=None) -> dict:
    """The persistence check (bassanite/acquisition.py) on the loaded recording."""
    return backend.persistence(src.path, src.fmt, src.fs, s.search(), prns, detected,
                               points, progress)


# --- meet the codes -----------------------------------------------------------------------------

@lru_cache(maxsize=8)
def _registers(prn: int):
    return register_states(prn)


def circular_correlation(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """sum_n a[n + k] * b[n] for every lag k (integers for +/-1 codes)."""
    return np.rint(np.fft.ifft(np.fft.fft(a) * np.conj(np.fft.fft(b))).real).astype(int)


def code_correlations(prn: int, other: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(lags, autocorrelation of prn, cross-correlation prn x other), lags -511..511."""
    lags = np.arange(-(CODE_LENGTH // 2), CODE_LENGTH // 2 + 1)
    a, b = ca_code(prn), ca_code(other)
    return (lags, circular_correlation(a, a)[lags % CODE_LENGTH],
            circular_correlation(a, b)[lags % CODE_LENGTH])


def chip_explanation(prn: int, step: int) -> str:
    g1, g2, chips = _registers(prn)
    s1, s2 = G2_TAPS[prn]
    a, b, c = g1[step][-1], g2[step][s1 - 1], g2[step][s2 - 1]
    chip = chips[step]
    return (f"Chip {step + 1} of {CODE_LENGTH}: G1 stage 10 = {a}; G2 stages "
            f"{s1} ⊕ {s2} = {b} ⊕ {c} = {b ^ c}; chip = {a} ⊕ {b ^ c} = {chip}, "
            f"sent as {'+1' if chip == 0 else '−1'}.")


def chips_so_far(prn: int, step: int, shown: int = 60) -> str:
    bits = "".join(str(b) for b in _registers(prn)[2][:step + 1])
    return bits if len(bits) <= shown else "…" + bits[-shown:]


_CELL, _X0, _G1_TOP, _G2_TOP = 40, 110, 50, 165
_WIRE, _ON, _OFF = "#78909c", "#37474f", "#ffffff"


def _cx(stage: int) -> float:
    return _X0 + (stage - 1) * _CELL + _CELL / 2


def _wire(*points, arrow: bool = False) -> str:
    pts = " ".join(f"{x},{y}" for x, y in points)
    end = ' marker-end="url(#bassanite-arrow)"' if arrow else ""
    return (f'<polyline points="{pts}" fill="none" stroke="{_WIRE}" '
            f'stroke-width="1.5"{end}/>')


def _xor(x: float, y: float) -> str:
    return (f'<circle cx="{x}" cy="{y}" r="9" fill="white" stroke="#546e7a" stroke-width="1.5"/>'
            f'<path d="M{x - 9},{y} H{x + 9} M{x},{y - 9} V{y + 9}" stroke="#546e7a" '
            f'stroke-width="1.5"/>')


def _dot(x: float, y: float) -> str:
    return f'<circle cx="{x}" cy="{y}" r="3" fill="{_WIRE}"/>'


def _register(bits, top: int, feedback: tuple[int, ...], selected=()) -> list[str]:
    out = []
    for i, bit in enumerate(bits, start=1):
        x = _X0 + (i - 1) * _CELL
        sel = i in selected
        stroke = HIGHLIGHT if sel else "#90a4ae"
        fill, ink = (_ON, "white") if bit else (_OFF, "#263238")
        out.append(f'<rect x="{x}" y="{top}" width="{_CELL}" height="{_CELL}" fill="{fill}" '
                   f'stroke="{stroke}" stroke-width="{3 if sel else 1}"/>')
        out.append(f'<text x="{x + 4}" y="{top + 11}" font-size="9" fill="{ink}" '
                   f'opacity="0.7">{i}</text>')
        out.append(f'<text x="{x + _CELL / 2}" y="{top + 27}" font-size="16" '
                   f'text-anchor="middle" fill="{ink}" font-family="monospace">{bit}</text>')
    bus = top - 25
    for t in feedback:                      # taps up to the feedback bus
        out += [_wire((_cx(t), top), (_cx(t), bus)), _dot(_cx(t), bus)]
    out.append(_wire((_cx(max(feedback)), bus), (79, bus)))
    out.append(_xor(70, bus))
    out.append(_wire((70, bus + 9), (70, top + _CELL / 2), (_X0, top + _CELL / 2), arrow=True))
    return out


def lfsr_svg(prn: int, step: int) -> str:
    """The two shift registers of the C/A code generator, as they are just before
    chip `step` comes out (stage 1 on the left)."""
    g1, g2, chips = _registers(prn)
    s1, s2 = G2_TAPS[prn]
    right = _X0 + REGISTER_STAGES * _CELL
    sel_y, sel_x, out_x, out_y = _G2_TOP + _CELL + 30, 600, 660, 150
    parts = [
        '<svg viewBox="0 0 790 265" xmlns="http://www.w3.org/2000/svg" '
        'style="width:100%;max-width:790px;font-family:sans-serif">',
        '<defs><marker id="bassanite-arrow" markerWidth="8" markerHeight="8" refX="7" '
        f'refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="{_WIRE}"/></marker></defs>',
        f'<text x="18" y="{_G1_TOP + 26}" font-size="16" font-weight="bold">G1</text>',
        f'<text x="18" y="{_G2_TOP + 26}" font-size="16" font-weight="bold">G2</text>',
    ]
    parts += _register(g1[step], _G1_TOP, G1_FEEDBACK)
    parts += _register(g2[step], _G2_TOP, G2_FEEDBACK, selected=(s1, s2))
    # G1 output (stage 10) down to the final XOR
    parts.append(_wire((right, _G1_TOP + _CELL / 2), (out_x, _G1_TOP + _CELL / 2),
                       (out_x, out_y - 11), arrow=True))
    # G2 phase selection: two stages of G2, XORed together
    for s in (s1, s2):
        parts += [_wire((_cx(s), _G2_TOP + _CELL), (_cx(s), sel_y)), _dot(_cx(s), sel_y)]
    parts.append(_wire((_cx(min(s1, s2)), sel_y), (sel_x - 9, sel_y)))
    parts.append(_xor(sel_x, sel_y))
    parts.append(_wire((sel_x + 9, sel_y), (out_x, sel_y), (out_x, out_y + 11), arrow=True))
    parts.append(_xor(out_x, out_y))
    parts.append(f'<text x="{sel_x}" y="{sel_y + 22}" font-size="11" text-anchor="middle" '
                 f'fill="#8a6d00">PRN {prn}: stages {s1} ⊕ {s2}</text>')
    # Output chip
    chip = int(chips[step])
    parts.append(_wire((out_x + 9, out_y), (712, out_y), arrow=True))
    fill, ink = (_ON, "white") if chip else (_OFF, "#263238")
    parts.append(f'<rect x="715" y="{out_y - 20}" width="40" height="40" fill="{fill}" '
                 f'stroke="{HIGHLIGHT}" stroke-width="3"/>')
    parts.append(f'<text x="735" y="{out_y + 7}" font-size="18" text-anchor="middle" '
                 f'fill="{ink}" font-family="monospace">{chip}</text>')
    parts.append(f'<text x="735" y="{out_y - 28}" font-size="12" text-anchor="middle">'
                 'chip</text>')
    parts.append('</svg>')
    return "".join(parts)


# --- figures ----------------------------------------------------------------------------------

def chips_figure(prn: int, step: int) -> dict:
    bits = _registers(prn)[2][:CHIPS_SHOWN]
    values = 1 - 2 * bits                      # 0 -> +1, 1 -> -1
    n_done = min(step + 1, CHIPS_SHOWN)
    x = np.arange(CHIPS_SHOWN + 1)
    y = np.append(values, values[-1])

    def trace(lo, hi, color, name):
        return {"type": "scatter", "mode": "lines", "line": {"shape": "hv", "color": color,
                                                              "width": 2},
                "x": x[lo:hi + 1].tolist(), "y": y[lo:hi + 1].tolist(), "name": name,
                "hovertemplate": "chip %{x}<br>%{y}<extra></extra>"}
    data = [trace(0, n_done, _ON, "produced")]
    if n_done < CHIPS_SHOWN:
        data.append(trace(n_done, CHIPS_SHOWN, "#cfd8dc", "still to come"))
    return figure(data, height=200, showlegend=False,
                  xaxis={"title": f"Chip number (first {CHIPS_SHOWN} of {CODE_LENGTH})"},
                  yaxis={"tickvals": [-1, 1], "range": [-1.4, 1.4], "title": "Value"})


def correlation_figure(prn: int, other: int) -> dict:
    lags, auto, cross = code_correlations(prn, other)
    line = {"type": "scatter", "mode": "lines", "x": lags.tolist()}
    return figure(
        [{**line, "y": auto.tolist(), "name": f"PRN {prn} with itself",
          "line": {"color": "#1565c0", "width": 1.5},
          "hovertemplate": "shift %{x} chips<br>%{y}<extra></extra>"},
         {**line, "y": cross.tolist(), "name": f"PRN {prn} with PRN {other}",
          "line": {"color": "#ef6c00", "width": 1.5},
          "hovertemplate": "shift %{x} chips<br>%{y}<extra></extra>"}],
        xaxis={"title": "Shift between the two codes (chips)"},
        yaxis={"title": "Sum of chip products"},
        annotations=[{"x": 0, "y": CODE_LENGTH, "text": "1023: codes aligned",
                      "showarrow": True, "ax": 70, "ay": 10}])


def bars_figure(results: list[dict], threshold: float, selected: int,
                truth: dict | None = None) -> dict:
    """The peak/mean bar chart; with a synthetic recording's `truth`, a star marks
    each satellite that is really in the signal."""
    by_prn = {r["prn"]: r for r in results}
    rows = [by_prn[p] for p in PRNS if p in by_prn]
    noise = [r["peak_over_mean"] for r in rows if not r["detected"]]
    floor = float(np.median(noise)) if noise else None
    shapes = [{"type": "line", "xref": "paper", "x0": 0, "x1": 1, "y0": threshold,
               "y1": threshold, "line": {"dash": "dash", "color": LIMIT, "width": 1.5}}]
    notes = [{"xref": "paper", "x": 1, "y": threshold, "xanchor": "right", "yanchor": "bottom",
              "showarrow": False, "text": f"detection threshold {threshold:g}",
              "font": {"color": LIMIT, "size": 11}, "bgcolor": LABEL_BG}]
    if floor is not None:
        shapes.append({"type": "line", "xref": "paper", "x0": 0, "x1": 1, "y0": floor,
                       "y1": floor, "line": {"dash": "dot", "color": "#616161", "width": 1}})
        notes.append({"xref": "paper", "x": 0, "y": floor, "xanchor": "left",
                      "yanchor": "top", "showarrow": False,
                      "text": f"noise floor ≈ {floor:.1f}", "bgcolor": LABEL_BG,
                      "font": {"color": "#616161", "size": 11}})
    extra = []
    if truth:
        true_prns = [t["prn"] for t in truth["satellites"] if t["prn"] in by_prn]
        extra.append({"type": "scatter", "mode": "markers", "x": true_prns,
                      "y": [round(by_prn[p]["peak_over_mean"], 2) * 1.12 + 0.6
                            for p in true_prns],
                      "marker": {"symbol": "star", "size": 12, "color": HIGHLIGHT,
                                 "line": {"color": "#5d4037", "width": 1}},
                      "hovertemplate": "PRN %{x} is in the signal<extra></extra>"})
        notes.append({"xref": "paper", "yref": "paper", "x": 0, "y": 1.0,
                      "xanchor": "left", "yanchor": "bottom", "showarrow": False,
                      "text": f"★ really in this synthetic signal "
                              f"(C/N0 {truth['cn0_dbhz']:g} dB-Hz)",
                      "font": {"size": 11, "color": "#5d4037"}})
    return figure(
        [{"type": "bar", "x": [r["prn"] for r in rows],
          "y": [round(r["peak_over_mean"], 2) for r in rows],
          "marker": {"color": [DETECTED if r["detected"] else NOISE for r in rows],
                     "line": {"color": [HIGHLIGHT if r["prn"] == selected else "rgba(0,0,0,0)"
                                        for r in rows], "width": 3}},
          "text": [f"{r['peak_over_mean']:.0f}" if r["detected"] else "" for r in rows],
          "textposition": "outside",
          "customdata": [[r["doppler_hz"], r["code_phase_chips"]] for r in rows],
          "hovertemplate": "PRN %{x}<br>peak/mean %{y}<br>Doppler %{customdata[0]:+.0f} Hz"
                           "<br>code phase %{customdata[1]:.1f} chips<extra></extra>"}]
        + extra,
        shapes=shapes, annotations=notes, showlegend=False, bargap=0.25,
        xaxis={"title": "Satellite (PRN) · click a bar to look at it", "tickmode": "array",
               "tickvals": list(PRNS), "tickfont": {"size": 10}},
        yaxis={"title": "peak / mean"})


def surface_figure(v: PrnView, three_d: bool) -> dict:
    z = np.round(v.surface, 2).tolist()
    common = {"x": np.round(v.surface_chips, 2).tolist(), "y": v.dopplers.tolist(), "z": z,
              "colorscale": "Viridis",
              "hovertemplate": "code phase %{x:.1f} chips<br>Doppler %{y:+.0f} Hz<br>"
                               "%{z:.1f} × average<extra></extra>"}
    if three_d:
        return figure([{**common, "type": "surface", "showscale": False}], height=430,
                      margin={"l": 0, "r": 0, "t": 0, "b": 0},
                      scene={"xaxis": {"title": "code phase (chips)"},
                             "yaxis": {"title": "Doppler (Hz)"},
                             "zaxis": {"title": "× average"},
                             "camera": {"eye": {"x": 1.5, "y": -1.6, "z": 0.9}}})
    return figure([{**common, "type": "heatmap",
                    "colorbar": {"title": {"text": "× avg"}, "thickness": 12}}],
                  height=380, xaxis={"title": "Code phase (chips)"},
                  yaxis={"title": "Doppler (Hz)"})


def code_slice_figure(v: PrnView) -> dict:
    n = len(v.code_slice)
    chips = np.arange(n) * CODE_LENGTH / n
    peak_chip = v.code_phase_samp * CODE_LENGTH / n
    return figure(
        [{"type": "scatter", "mode": "lines", "x": np.round(chips, 3).tolist(),
          "y": np.round(v.code_slice, 2).tolist(), "line": {"width": 1, "color": "#1565c0"},
          "hovertemplate": "%{x:.2f} chips<br>%{y:.1f} × average<extra></extra>"}],
        showlegend=False, xaxis={"title": f"Code phase (chips) at Doppler "
                                          f"{v.doppler_hz:+.0f} Hz"},
        yaxis={"title": "× average"},
        annotations=[{"x": peak_chip, "y": float(v.code_slice.max()),
                      "text": f"{peak_chip:.1f} chips", "showarrow": True, "ax": 40,
                      "ay": -10}])


def doppler_slice_figure(v: PrnView) -> dict:
    peak = float(v.doppler_slice.max())
    fine = np.arange(v.dopplers[0], v.dopplers[-1] + 1, 10.0)
    theory = 1 + (peak - 1) * doppler_loss(fine - v.fine_doppler_hz)
    peak_chip = v.code_phase_samp * CODE_LENGTH / len(v.code_slice)
    return figure(
        [{"type": "scatter", "mode": "lines", "x": fine.tolist(),
          "y": np.round(theory, 3).tolist(), "name": "theory: sinc²(Δf · 1 ms)",
          "line": {"dash": "dot", "color": "#9e9e9e", "width": 1.5}, "hoverinfo": "skip"},
         {"type": "scatter", "mode": "markers+lines", "x": v.dopplers.tolist(),
          "y": np.round(v.doppler_slice, 2).tolist(), "name": "measured",
          "marker": {"size": 5, "color": "#1565c0"}, "line": {"width": 1, "color": "#1565c0"},
          "hovertemplate": "%{x:+.0f} Hz<br>%{y:.1f} × average<extra></extra>"}],
        xaxis={"title": f"Doppler (Hz) at code phase {peak_chip:.1f} chips"},
        yaxis={"title": "× average"})


def try_figure(row: np.ndarray, code_phase: int, y_max: float) -> dict:
    n = len(row)
    rel = row / row.mean()
    chips = np.arange(n) * CODE_LENGTH / n
    return figure(
        [{"type": "scatter", "mode": "lines", "x": np.round(chips, 2).tolist(),
          "y": np.round(rel, 2).tolist(), "line": {"width": 1, "color": "#1565c0"},
          "hovertemplate": "%{x:.1f} chips<br>%{y:.1f} × average<extra></extra>"}],
        showlegend=False, height=260,
        xaxis={"title": "Code phase (chips)"},
        yaxis={"title": "× average", "range": [0, y_max]},
        shapes=[{"type": "line", "x0": code_phase * CODE_LENGTH / n,
                 "x1": code_phase * CODE_LENGTH / n, "yref": "paper", "y0": 0, "y1": 1,
                 "line": {"color": HIGHLIGHT, "width": 1, "dash": "dot"}}])


def loss_figure(points: list[tuple[float, float]], current: tuple[float, float] | None) -> dict:
    df = np.arange(-2000, 2001, 10.0)
    data = [{"type": "scatter", "mode": "lines", "x": df.tolist(),
             "y": np.round(doppler_loss(df), 4).tolist(), "name": "theory: sinc²(Δf · 1 ms)",
             "line": {"color": "#9e9e9e", "dash": "dot"}, "hoverinfo": "skip"}]
    if points:
        data.append({"type": "scatter", "mode": "markers", "name": "your measurements",
                     "x": [p[0] for p in points], "y": [round(p[1], 3) for p in points],
                     "marker": {"color": "#1565c0", "size": 6},
                     "hovertemplate": "%{x:+.0f} Hz: %{y:.2f}<extra></extra>"})
    if current:
        data.append({"type": "scatter", "mode": "markers", "name": "now",
                     "x": [current[0]], "y": [round(current[1], 3)],
                     "marker": {"color": HIGHLIGHT, "size": 12, "line": {"width": 1}},
                     "hoverinfo": "skip"})
    return figure(data, height=260, xaxis={"title": "Doppler error (Hz)"},
                  yaxis={"title": "Satellite power left", "range": [-0.15, 1.2]})


def persistence_figures(p: dict, detected: list[int]) -> tuple[dict, dict]:
    times = p["times"].tolist()
    dop_data, chip_data = [], []
    for prn, tr in p["tracks"].items():
        real = prn in detected
        style = ({"mode": "lines+markers"} if real else
                 {"mode": "markers+lines", "line": {"dash": "dot", "color": NOISE},
                  "marker": {"color": NOISE, "symbol": "x"}})
        name = f"PRN {prn}" + ("" if real else " (not detected)")
        dop_data.append({"type": "scatter", "x": times, "y": tr["doppler"], "name": name,
                         **style, "hovertemplate": f"PRN {prn}<br>%{{x:.1f}} s<br>"
                                                   "%{y:+.0f} Hz<extra></extra>"})
        chips = np.array(tr["chips"], dtype=float)
        y = chips.tolist()
        if real:   # break the line where the code phase wraps past 0 / 1023
            y = [None if i and abs(chips[i] - chips[i - 1]) > CODE_LENGTH / 2 else c
                 for i, c in enumerate(y)]
        chip_data.append({"type": "scatter", "x": times, "y": y, "name": name, **style,
                          "connectgaps": False,
                          "hovertemplate": f"PRN {prn}<br>%{{x:.1f}} s<br>%{{y:.1f}} chips"
                                           "<extra></extra>"})
    return (figure(dop_data, height=320, xaxis={"title": "Time in recording (s)"},
                   yaxis={"title": "Doppler (Hz)"}),
            figure(chip_data, height=320, xaxis={"title": "Time in recording (s)"},
                   yaxis={"title": "Code phase (chips)", "range": [0, CODE_LENGTH]}))


# --- the tab ------------------------------------------------------------------------------------

HANDOVER_COLUMNS = [
    {"name": "prn", "label": "PRN", "field": "prn", "align": "left"},
    {"name": "doppler", "label": "Doppler (Hz)", "field": "doppler"},
    {"name": "fine", "label": "Refined Doppler (Hz)", "field": "fine"},
    {"name": "chips", "label": "Code phase (chips)", "field": "chips"},
    {"name": "samples", "label": "Code phase (samples)", "field": "samples"},
    {"name": "ratio", "label": "peak / mean", "field": "ratio"},
    {"name": "detected", "label": "Detected", "field": "detected"},
]


TRUTH_COLUMN = {"name": "truth", "label": "True values (synthetic)", "field": "truth"}


def handover_columns(truth: dict | None) -> list[dict]:
    return HANDOVER_COLUMNS + ([TRUTH_COLUMN] if truth else [])


def handover_rows(results: list[dict], truth: dict | None = None) -> list[dict]:
    true = {t["prn"]: t for t in (truth or {}).get("satellites", [])}
    rows = _handover_rows(results)
    for row in rows:
        t = true.get(row["prn"])
        row["truth"] = (f"{t['doppler_hz']:+.0f} Hz · {t['code_phase_chips']:.1f} chips"
                        if t else ("not in the signal" if truth else ""))
    return rows


def _handover_rows(results: list[dict]) -> list[dict]:
    return [{"prn": r["prn"], "doppler": f"{r['doppler_hz']:+.0f}",
             "fine": ("" if r.get("fine_doppler_hz") is None
                      else f"{r['fine_doppler_hz']:+.0f}"),
             "chips": f"{r['code_phase_chips']:.1f}", "samples": r["code_phase_samp"],
             "ratio": f"{r['peak_over_mean']:.1f}", "detected": "✅" if r["detected"] else ""}
            for r in results]


class AcquisitionTab:
    """One instance per browser tab (each client builds its own page)."""

    def __init__(self, on_scanned=None) -> None:
        self.on_scanned = on_scanned        # called after a scan wrote new results
        self.settings = DEFAULT_SETTINGS    # edited in Expert mode, used by the next scan
        self.active = DEFAULT_SETTINGS      # settings of the results on screen
        self.results: list[dict] | None = None
        self.selected = 1
        self.view: PrnView | None = None
        self.x: np.ndarray | None = None
        self._x_key = None
        self._pending = self._busy = False
        self._view_seq = 0
        self._progress: tuple[int, int] | None = None
        self.code_prn, self.compare_prn, self.lfsr_step = 1, 2, 0
        self.try_df, self.try_ms = 0.0, DEFAULT_MS
        self._try_ref: tuple[float, float] | None = None   # (power at df=0, y range)
        self._try_points: list[tuple[float, float]] = []
        self._syncing = False

    def request_update(self) -> None:
        self._pending = True

    # --- layout -----------------------------------------------------------------------

    def build(self) -> None:
        self._build_codes()
        with ui.row().classes("w-full items-start gap-4"):
            self._build_controls()
            with ui.column().classes("flex-1 min-w-[18rem] gap-4"):
                self._build_scan()
                self._build_surface()
                self._build_try_it()
                self._build_persistence()
                self._build_handover()
        ui.timer(0.3, self._tick)
        self.request_update()

    @staticmethod
    def _card(title: str, caption: str = ""):
        card = ui.card().classes("w-full").props("flat bordered")
        with card:
            ui.label(title).classes("text-subtitle1 font-bold")
            if caption:
                ui.label(caption).classes("text-sm text-grey-8")
        return card

    def _build_codes(self) -> None:
        prn_options = {p: f"PRN {p}" for p in PRNS}
        with self._card("Meet the codes",
                        "Every satellite sends its own 1023-chip code, over and over, once "
                        "every millisecond. Two small shift registers make it. Step through "
                        "them to see each chip come out."):
            with ui.row().classes("items-center gap-4"):
                ui.select(prn_options, label="Satellite", value=self.code_prn,
                          on_change=lambda e: self._set_code_prn(e.value)).classes("w-32")
                ui.select(prn_options, label="Compare with", value=self.compare_prn,
                          on_change=lambda e: self._set_compare(e.value)).classes("w-32")
            with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4 items-start"):
                with ui.column().classes("w-full gap-1"):
                    self.lfsr = ui.html(lfsr_svg(self.code_prn, 0), sanitize=False) \
                        .classes("w-full")
                    with ui.row().classes("items-center gap-1"):
                        ui.button("Next chip", on_click=lambda: self._step(1)) \
                            .props("no-caps dense unelevated")
                        ui.button("+10", on_click=lambda: self._step(10)) \
                            .props("no-caps dense flat")
                        ui.button("+100", on_click=lambda: self._step(100)) \
                            .props("no-caps dense flat")
                        ui.button("Restart", on_click=lambda: self._step(None)) \
                            .props("no-caps dense flat")
                    self.chip_text = ui.label().classes("text-sm")
                    self.chips_text = ui.label().classes("text-xs font-mono break-all")
                with ui.column().classes("w-full gap-1"):
                    self.chips_plot = ui.plotly(chips_figure(self.code_prn, 0)).classes("w-full")
            ui.label("Slide one code past another and add up chip × chip at each shift. "
                     "A code matches itself only when perfectly aligned (one tall spike); "
                     "it never matches another satellite's code. That is how many "
                     "satellites share one frequency (CDMA).").classes("text-sm text-grey-8")
            self.corr_plot = ui.plotly(correlation_figure(self.code_prn, self.compare_prn)) \
                .classes("w-full")
            explain_panel("acquisition", "codes")
        self._show_lfsr()

    def _build_controls(self) -> None:
        with ui.card().classes("w-full md:w-72").props("flat bordered") \
                .bind_visibility_from(state, "mode", value="expert"):
            ui.label("Search settings").classes("text-subtitle1 font-bold")
            ui.label("Used by the next scan.").classes("text-xs text-grey-7")
            self.c_range = ui.number("Doppler range ± (Hz)", value=self.settings.dop_range_hz,
                                     min=1000, max=50000, step=1000, format="%.0f",
                                     on_change=lambda e: self._set(dop_range_hz=e.value)) \
                .classes("w-full")
            self.c_step = ui.select({100.0: "100 Hz", 250.0: "250 Hz", 500.0: "500 Hz"},
                                    label="Doppler step", value=self.settings.dop_step_hz,
                                    on_change=lambda e: self._set(dop_step_hz=e.value)) \
                .classes("w-full")
            self.c_ms = ui.select({1: "1 ms", 2: "2 ms", 5: "5 ms", 10: "10 ms", 20: "20 ms"},
                                  label="Integration (non-coherent)", value=self.settings.ms,
                                  on_change=lambda e: self._set(ms=e.value)).classes("w-full")
            self.c_threshold = ui.number("Detection threshold (peak/mean)",
                                         value=self.settings.threshold, min=1, max=50,
                                         step=0.5,
                                         on_change=lambda e: self._set(threshold=e.value)) \
                .classes("w-full")
            self.c_skip = ui.number("Start at (s)", value=self.settings.skip_seconds, min=0,
                                    step=1,
                                    on_change=lambda e: self._set(skip_seconds=e.value)) \
                .classes("w-full")
            ui.label("Noise alone reaches about 3 with 10 ms; with fewer ms it climbs "
                     "(about 13 with 1 ms), so lower the integration only with a higher "
                     "threshold.").classes("text-xs text-grey-7")

    def _build_scan(self) -> None:
        with self._card("Which satellites are in this recording?",
                        "For each of the 32 satellites, the strongest correlation peak "
                        "divided by the average. A real satellite stands far above the "
                        "others."):
            with ui.row().classes("w-full items-center gap-3"):
                self.scan_button = ui.button("Scan all 32 satellites", icon="satellite_alt",
                                             on_click=self.scan).props("no-caps unelevated")
                self.spinner = ui.spinner(size="sm")
                self.spinner.set_visibility(False)
                self.progress = ui.linear_progress(value=0, show_value=False) \
                    .classes("flex-1 min-w-[8rem]")
                self.progress.set_visibility(False)
            self.scan_label = ui.label().classes("text-sm")
            self.scan_detail = ui.label().classes("text-xs text-grey-7")
            self.bars = ui.plotly(empty_figure("Analysing…")).classes("w-full")
            self.bars.on("plotly_click", self._on_bar_click)
            explain_panel("acquisition", "prn_bars")

    def _build_surface(self) -> None:
        with self._card("One satellite up close: the search grid",
                        "Every cell is one guess of (code phase, Doppler). Where both guesses "
                        "are right, a spike rises out of the flat noise."):
            with ui.row().classes("items-center gap-4"):
                self.prn_select = ui.select({p: f"PRN {p}" for p in PRNS}, label="Satellite",
                                            value=self.selected,
                                            on_change=self._on_select).classes("w-40")
                self.view_toggle = ui.toggle({"3d": "3D", "map": "Map"}, value="3d",
                                             on_change=lambda: self._show_surface()) \
                    .props("dense no-caps unelevated")
            self.view_label = ui.label().classes("text-sm")
            self.surface_plot = ui.plotly(empty_figure("Analysing…")).classes("w-full")
            with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4"):
                with ui.column().classes("w-full gap-0"):
                    ui.label("Slice along code phase, at the best Doppler") \
                        .classes("text-sm font-medium")
                    self.code_slice_plot = ui.plotly(empty_figure()).classes("w-full")
                with ui.column().classes("w-full gap-0"):
                    ui.label("Slice along Doppler, at the best code phase") \
                        .classes("text-sm font-medium")
                    self.doppler_slice_plot = ui.plotly(empty_figure()).classes("w-full")
            explain_panel("acquisition", "surface")

    def _build_try_it(self) -> None:
        with self._card("Try it: what if the guess is a little off?",
                        "Real samples from this recording, correlated with the selected "
                        "satellite's code. Detune the Doppler guess, or change how many "
                        "milliseconds are added up."):
            with ui.grid().classes("w-full grid-cols-1 md:grid-cols-2 gap-x-6"):
                with ui.column().classes("w-full gap-0"):
                    self.df_label = ui.label().classes("text-sm")
                    self.df_slider = ui.slider(min=-2000, max=2000, step=50, value=0,
                                               on_change=lambda e: self._set_try(df=e.value))
                with ui.column().classes("w-full gap-0"):
                    self.ms_label = ui.label().classes("text-sm")
                    self.ms_slider = ui.slider(min=1, max=TRY_MAX_MS, step=1,
                                               value=self.try_ms,
                                               on_change=lambda e: self._set_try(ms=e.value))
            self.try_label = ui.label().classes("text-sm")
            with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4"):
                self.try_plot = ui.plotly(empty_figure("Analysing…")).classes("w-full")
                self.loss_plot = ui.plotly(loss_figure([], None)).classes("w-full")
            explain_panel("acquisition", "try_it")

    def _build_persistence(self) -> None:
        with self._card("Is it real? The persistence check",
                        "A noise peak lands somewhere random each time. A satellite comes "
                        "back at almost the same Doppler, and its code phase slides "
                        "smoothly as it moves. This rescans at several moments of the "
                        "recording."):
            with ui.row().classes("w-full items-center gap-3"):
                self.persist_button = ui.button("Run the persistence check", icon="timeline",
                                                on_click=self.check_persistence) \
                    .props("no-caps outline")
                self.persist_progress = ui.linear_progress(value=0, show_value=False) \
                    .classes("flex-1 min-w-[8rem]")
                self.persist_progress.set_visibility(False)
            self.persist_label = ui.label().classes("text-sm")
            self.persist_plots = ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4")
            self.persist_plots.set_visibility(False)
            with self.persist_plots:
                self.persist_doppler = ui.plotly(empty_figure()).classes("w-full")
                self.persist_chips = ui.plotly(empty_figure()).classes("w-full")
            self.drift_box = ui.column().classes("w-full gap-1")
            explain_panel("acquisition", "persistence")

    def _build_handover(self) -> None:
        with self._card("What goes to tracking",
                        "For each satellite found, where its code starts (code phase) and "
                        "its frequency offset (Doppler): the starting point of step 3."):
            self.handover = ui.table(columns=HANDOVER_COLUMNS, rows=[], row_key="prn") \
                .props("flat dense").classes("w-full")
            with ui.expansion("All 32 satellites").classes("w-full") \
                    .bind_visibility_from(state, "mode", value="expert"):
                self.handover_all = ui.table(columns=HANDOVER_COLUMNS, rows=[],
                                             row_key="prn").props("flat dense") \
                    .classes("w-full")

    # --- meet the codes -------------------------------------------------------------------

    def _set_code_prn(self, prn: int) -> None:
        self.code_prn, self.lfsr_step = prn, 0
        if prn == self.compare_prn:
            self.compare_prn = prn % 32 + 1
        self._show_lfsr()
        self.corr_plot.update_figure(correlation_figure(self.code_prn, self.compare_prn))

    def _set_compare(self, prn: int) -> None:
        self.compare_prn = prn
        self.corr_plot.update_figure(correlation_figure(self.code_prn, self.compare_prn))

    def _step(self, n: int | None) -> None:
        self.lfsr_step = 0 if n is None else min(self.lfsr_step + n, CODE_LENGTH - 1)
        self._show_lfsr()

    def _show_lfsr(self) -> None:
        self.lfsr.set_content(lfsr_svg(self.code_prn, self.lfsr_step))
        self.chip_text.set_text(chip_explanation(self.code_prn, self.lfsr_step))
        self.chips_text.set_text(f"Chips so far: {chips_so_far(self.code_prn, self.lfsr_step)}")
        self.chips_plot.update_figure(chips_figure(self.code_prn, self.lfsr_step))

    # --- scanning -------------------------------------------------------------------------

    def _set(self, **changes) -> None:
        if self._syncing:
            return
        clean = {k: v for k, v in changes.items() if v is not None}
        if clean:
            self.settings = replace(self.settings, **clean)

    def _sync_controls(self) -> None:
        """Show the settings of the results on screen in the Expert controls."""
        self._syncing = True
        try:
            self.c_range.set_value(self.active.dop_range_hz)
            self.c_step.set_value(self.active.dop_step_hz
                                  if self.active.dop_step_hz in self.c_step.options else None)
            self.c_ms.set_value(self.active.ms if self.active.ms in self.c_ms.options else None)
            self.c_threshold.set_value(self.active.threshold)
            self.c_skip.set_value(self.active.skip_seconds)
        finally:
            self._syncing = False
        self.settings = self.active

    def _set_progress(self, done: int, total: int) -> None:
        self._progress = (done, total)        # called from the worker thread

    async def scan(self) -> None:
        src = state.source
        problem = "Load a recording first." if src is None else (src.problems() or [None])[0]
        if problem:
            ui.notify(problem, type="warning")
            return
        settings = self.settings
        self.scan_button.disable()
        self.progress.set_visibility(True)
        self._progress = (0, len(PRNS))
        self.scan_label.set_text("Scanning…")
        t0 = time.perf_counter()
        try:
            results = await run.io_bound(run_scan, src, settings, self._set_progress)
        except (OSError, ValueError) as err:
            ui.notify(f"Scan failed: {err}", type="negative", multi_line=True)
            self.scan_label.set_text(f"Scan failed: {err}")
            return
        finally:
            self._progress = None
            self.progress.set_visibility(False)
            self.scan_button.enable()
        found = detected_prns(results)
        self.selected = found[0] if found else self.selected
        ui.notify(f"Scan done in {time.perf_counter() - t0:.1f} s: "
                  f"{satellites(len(found))} found", type="positive")
        if self.on_scanned is not None:
            self.on_scanned()
        self.request_update()

    async def _tick(self) -> None:
        if self._progress is not None:
            done, total = self._progress
            for bar in (self.progress, self.persist_progress):
                if bar.visible:
                    bar.set_value(done / max(total, 1))
        if self._pending and not self._busy:
            self._pending = False
            self._busy = True
            try:
                await self._update()
            finally:
                self._busy = False

    def _show_problem(self, message: str) -> None:
        for plot in (self.bars, self.surface_plot, self.code_slice_plot,
                     self.doppler_slice_plot, self.try_plot):
            plot.update_figure(empty_figure(message))
        self.scan_label.set_text(message)
        self.scan_detail.set_text("")
        self.view_label.set_text("")
        self.try_label.set_text("")
        self.handover.rows = []
        self.handover_all.rows = []
        self.view, self.x, self._x_key = None, None, None

    async def _update(self) -> None:
        src = state.source
        problem = ("Load a recording first (top bar, or the Overview tab)."
                   if src is None else (src.problems() or [None])[0])
        if problem:
            self._show_problem(problem)
            return
        saved = saved_results(src)
        if saved is None:
            self.results = None
            self.active = self.settings
        else:
            self.results, self.active = saved
            self._sync_controls()
        self._show_results(src)

        ms_needed = self.active.ms
        key = (str(src.path), src.size_bytes, src.fmt, src.fs, self.active.skip_seconds,
               ms_needed)
        if key != self._x_key:
            try:
                self.x = await run.io_bound(load_window, src, self.active.skip_seconds,
                                            ms_needed, max(TRY_MAX_MS - ms_needed, 0))
            except (OSError, ValueError) as err:
                self._show_problem(str(err))
                return
            self._x_key = key
        found = detected_prns(self.results)
        if found and self.selected not in found:
            self.selected = found[0]
        self.prn_select.set_value(self.selected)     # triggers _on_select -> _show_prn
        await self._show_prn()

    def _show_results(self, src: IQSource) -> None:
        s = self.active
        detail = (f"Searched Doppler ±{s.dop_range_hz:,.0f} Hz in {s.dop_step_hz:g} Hz steps, "
                  f"adding up {s.ms} ms of signal from {s.skip_seconds:.1f} s.")
        if self.results is None:
            other = results_csv(src).is_file()
            self.scan_label.set_text(
                ("The results on disk are for another recording. " if other else
                 "Not scanned yet. ") + f"Press Scan to search {src.path.name} "
                "(takes a few seconds).")
            self.scan_detail.set_text("")
            self.bars.update_figure(empty_figure("Press “Scan all 32 satellites”"))
            self.handover.rows = []
            self.handover_all.rows = []
            return
        found = detected_prns(self.results)
        if found:
            verdict = (f"enough for a position fix (it needs {MIN_SATS_FOR_FIX})."
                       if len(found) >= MIN_SATS_FOR_FIX else
                       f"a position fix needs at least {MIN_SATS_FOR_FIX}.")
            self.scan_label.set_text(f"{satellites(len(found))} found: PRN "
                                     f"{', '.join(map(str, found))}. That is {verdict}")
        else:
            self.scan_label.set_text("No satellite found. Is the antenna outdoors with a "
                                     "clear view of the sky, and powered (bias-tee on)?")
        self.scan_detail.set_text(f"{detail} Results: {rel_path(results_csv(src))}")
        truth = read_truth(src.path)
        self.bars.update_figure(bars_figure(self.results, s.threshold, self.selected, truth))
        for table in (self.handover, self.handover_all):
            table.columns = handover_columns(truth)
        self.handover.rows = handover_rows([r for r in self.results if r["detected"]], truth)
        self.handover_all.rows = handover_rows(sorted(self.results, key=lambda r: r["prn"]),
                                               truth)

    # --- one PRN ----------------------------------------------------------------------------

    async def _on_bar_click(self, e) -> None:
        points = (e.args or {}).get("points") or []
        if points and isinstance(points[0].get("x"), (int, float)):
            self.prn_select.set_value(int(points[0]["x"]))

    async def _on_select(self, e) -> None:
        if e.value is None or int(e.value) == self.selected:
            return                      # set by the tab itself, already shown
        self.selected = int(e.value)
        await self._show_prn()

    async def _show_prn(self) -> None:
        if self.x is None or state.source is None:
            return
        self._view_seq += 1
        seq = self._view_seq
        self.spinner.set_visibility(True)
        try:
            view = await run.io_bound(prn_view, self.x, state.source.fs, self.selected,
                                      self.active)
        finally:
            self.spinner.set_visibility(False)
        if seq != self._view_seq:
            return                      # another satellite was picked meanwhile
        self.view = view
        if self.results is not None:
            truth = read_truth(state.source.path) if state.source else None
            self.bars.update_figure(bars_figure(self.results, self.active.threshold,
                                                self.selected, truth))
        found = view.peak_over_mean >= self.active.threshold
        self.view_label.set_text(
            f"PRN {view.prn}: peak at code phase "
            f"{view.code_phase_samp * CODE_LENGTH / len(view.code_slice):.1f} chips, "
            f"Doppler {view.doppler_hz:+.0f} Hz (refined: {view.fine_doppler_hz:+.0f} Hz), "
            f"peak/mean {view.peak_over_mean:.1f} → "
            + (f"a satellite, rough C/N0 ≈ {cn0_from_peak(view.peak_over_mean):.0f} dB-Hz."
               if found else "below the threshold: probably just noise."))
        self._show_surface()
        self.code_slice_plot.update_figure(code_slice_figure(view))
        self.doppler_slice_plot.update_figure(doppler_slice_figure(view))
        self._try_points = []
        self._try_ref = None
        self._show_try()

    def _show_surface(self) -> None:
        if self.view is not None:
            self.surface_plot.update_figure(
                surface_figure(self.view, self.view_toggle.value == "3d"))

    # --- try it -------------------------------------------------------------------------------

    def _set_try(self, df: float | None = None, ms: int | None = None) -> None:
        if df is not None:
            self.try_df = float(df)
        if ms is not None and int(ms) != self.try_ms:
            self.try_ms = int(ms)
            self._try_ref = None
            self._try_points = []
        self._show_try()

    def _show_try(self) -> None:
        self.df_label.set_text(f"Doppler error: {self.try_df:+.0f} Hz")
        self.ms_label.set_text(f"Milliseconds added up: {self.try_ms}")
        v, x = self.view, self.x
        if v is None or x is None or state.source is None:
            return
        fs = state.source.fs
        nms = min(self.try_ms, len(x) // samples_per_ms(fs))
        cp = v.code_phase_samp
        if self._try_ref is None:
            ref = try_row(x, fs, v.prn, v.fine_doppler_hz, nms)
            self._try_ref = (signal_power(ref, cp), 1.15 * float((ref / ref.mean()).max()))
        ref_power, y_max = self._try_ref
        row = try_row(x, fs, v.prn, v.fine_doppler_hz + self.try_df, nms)
        mean = row.mean()
        left = signal_power(row, cp) / ref_power if ref_power > 0 else 0.0
        guard = 2 * int(np.ceil(len(row) / CODE_LENGTH))
        peak = float(row[(cp + np.arange(-1, 2)) % len(row)].max() / mean)
        spike = highest_noise(row, cp, guard) / mean
        if all(abs(p[0] - self.try_df) > 1 for p in self._try_points):
            self._try_points.append((self.try_df, left))
        stands = "stands out" if peak > 1.2 * spike else "is lost in the noise"
        self.try_label.set_text(
            f"PRN {v.prn}, {nms} ms: satellite peak {peak:.1f} × average, highest noise "
            f"spike {spike:.1f} → the satellite {stands}. Power left: {100 * left:.0f} % "
            f"(theory: {100 * float(doppler_loss(self.try_df)):.0f} %).")
        self.try_plot.update_figure(try_figure(row, cp, y_max))
        self.loss_plot.update_figure(loss_figure(self._try_points, (self.try_df, left)))

    # --- persistence --------------------------------------------------------------------------

    async def check_persistence(self) -> None:
        src = state.source
        if src is None or src.problems():
            ui.notify("Load a recording first.", type="warning")
            return
        if self.results is None:
            ui.notify("Scan the recording first.", type="warning")
            return
        found = detected_prns(self.results)
        contrast = [r["prn"] for r in self.results if not r["detected"]][:PERSISTENCE_CONTRAST]
        prns = found + contrast
        self.persist_button.disable()
        self.persist_progress.set_visibility(True)
        self._progress = (0, 1)
        self.persist_label.set_text(f"Rescanning {len(prns)} satellites at "
                                    f"{PERSISTENCE_POINTS} moments…")
        try:
            p = await run.io_bound(persistence, src, self.active, prns, found,
                                   PERSISTENCE_POINTS, self._set_progress)
        except (OSError, ValueError) as err:
            self.persist_label.set_text(f"Persistence check failed: {err}")
            return
        finally:
            self._progress = None
            self.persist_progress.set_visibility(False)
            self.persist_button.enable()
        dop_fig, chip_fig = persistence_figures(p, found)
        self.persist_doppler.update_figure(dop_fig)
        self.persist_chips.update_figure(chip_fig)
        self.persist_plots.set_visibility(True)
        times = p["times"]
        self.persist_label.set_text(
            f"Rescanned at {', '.join(f'{t:.1f}' for t in times)} s. Detected satellites "
            "are solid lines; grey crosses are the strongest undetected PRNs, for contrast.")
        self._show_drift(p)

    def _show_drift(self, p: dict) -> None:
        self.drift_box.clear()
        if not p["drift"]:
            return
        tol = p["tolerance"]
        rows = []
        for prn, d in p["drift"].items():
            ok = abs(d["measured"] - d["predicted"]) <= tol
            rows.append({"prn": prn, "doppler": f"{d['doppler']:+.0f}",
                         "measured": f"{d['measured']:+.3f}",
                         "predicted": f"{d['predicted']:+.3f}",
                         "agree": "✅" if ok else "❌"})
        with self.drift_box:
            ui.label("Code drift: measured against what the Doppler predicts "
                     f"(agreement within ±{tol:.2f} chips/s)").classes("text-sm font-medium")
            ui.table(columns=[
                {"name": "prn", "label": "PRN", "field": "prn", "align": "left"},
                {"name": "doppler", "label": "Doppler (Hz)", "field": "doppler"},
                {"name": "measured", "label": "Measured drift (chips/s)", "field": "measured"},
                {"name": "predicted", "label": "Predicted −1.023 MHz · f_D / f_L1",
                 "field": "predicted"},
                {"name": "agree", "label": "Agree?", "field": "agree"}],
                rows=rows, row_key="prn").props("flat dense").classes("w-full")
