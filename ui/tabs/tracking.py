"""Track tab (stage 03, tracking): follow each satellite with two feedback loops,
then replay the recording to watch them lock and the data bits appear."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from nicegui import background_tasks, context, run, ui

from state import REPO_ROOT, IQSource, state  # first: puts the repo root on sys.path

import tabs.acquisition as acquisition  # noqa: E402  (its results_csv is the seed file)
from bassanite.provenance import read_meta  # noqa: E402,I001
from bassanite.results import tracking_dir  # noqa: E402
from bassanite.tracking import (DEFAULT_DLL_BW, DEFAULT_PLL_BW,  # noqa: E402
                                LOCK_QI_MAX, MIN_SECONDS_FOR_EPHEMERIS, MS_PER_BIT,
                                SUMMARY_CSV, bit_sync_scores,
                                read_summary, summarize, track_file_name, track_prn)
from bassanite.tracking import load_track as read_track_file  # noqa: E402
from common.iq_loader import load_iq  # noqa: E402
from components import explain_panel  # noqa: E402
from plots import DETECTED, HIGHLIGHT, LIMIT, NOISE, empty_figure, figure, layout  # noqa: E402
from stages import MIN_SATS_FOR_FIX, rel_path, result_is_for_other_recording  # noqa: E402

TRACK_SCRIPT = REPO_ROOT / "03_tracking" / "track.py"
RESULT_GLOBS = ("track_PRN*.npz", "track_PRN*.png", "tracking_summary.csv",
                "tracking_summary.meta.json")
DEFAULT_SECONDS = None         # the whole recording (see MIN_SECONDS_FOR_EPHEMERIS)
SECONDS_PER_SECOND = 0.25      # measured: ~7 s to track 30 s of one satellite
WINDOWS_MS = {20: "20 ms (one bit)", 100: "100 ms", 200: "200 ms", 1000: "1 s"}
DEFAULT_WINDOW_MS = 200
CURVE_POINTS = 1500            # points per full-length curve sent to the browser
BITS_SHOWN = 40                # bits in the strip around the scrubber
PLL_CHOICES = (2, 5, 8, 12, 25, 50, 100, 150, 300)
TRY_SECONDS = 1.5
TRY_SETTLE_MS = 500            # lock is judged after this much pull-in time
PLAY_TICK_S = 0.2


# --- settings and results on disk ---------------------------------------------------------

@dataclass(frozen=True)
class TrackSettings:
    seconds: float | None = DEFAULT_SECONDS    # None: the rest of the recording
    pll_bw: float = DEFAULT_PLL_BW
    dll_bw: float = DEFAULT_DLL_BW
    prns: tuple[int, ...] = ()          # empty: every detected satellite


def seconds_to_track(src: IQSource, start_s: float, s: TrackSettings) -> float:
    left = max(src.duration_s - start_s, 0.0)
    return left if not s.seconds else min(s.seconds, left)


def too_short_for_decode(seconds: float) -> str:
    return (f"⚠️ {seconds:.0f} s is too short for step 4: a satellite's orbit and clock "
            f"(subframes 1–3) are all complete only in {MIN_SECONDS_FOR_EPHEMERIS:.0f} s "
            "or more of signal. Record 60 s outdoors.")


def estimate_minutes(seconds: float, n_prns: int) -> float:
    """How long a tracking run takes. (Memory no longer grows with the length:
    track.py reads the recording block by block.)"""
    return n_prns * seconds * SECONDS_PER_SECOND / 60


def track_dir(src: IQSource) -> Path:
    """Where the tracking results of `src` live (results/<recording>/tracking/)."""
    return tracking_dir(src.path)


def summary_path(folder: Path) -> Path:
    return folder / SUMMARY_CSV


def saved_tracking(src: IQSource, folder: Path | None = None
                   ) -> tuple[list[dict], dict] | None:
    """(summary rows, provenance) of the results on disk, if they belong to `src`."""
    path = summary_path(folder or track_dir(src))
    if not path.is_file() or result_is_for_other_recording(path, src):
        return None
    return read_summary(path), read_meta(path) or {}


# --- one tracked satellite --------------------------------------------------------------------

@dataclass
class Track:
    """A track_PRNnn.npz file, plus where it starts in the recording."""
    prn: int
    fs: float
    t0: float                          # seconds into the recording of the first ms
    i: np.ndarray
    q: np.ndarray
    doppler: np.ndarray
    code_phase: np.ndarray
    bits: np.ndarray
    cn0: np.ndarray
    bit_offset: int
    seed_doppler: float
    seed_code_phase: float
    detail: dict | None = None        # Early/Late and discriminators, if saved

    @property
    def n_ms(self) -> int:
        return len(self.i)

    def seconds(self, ms) -> np.ndarray:
        return self.t0 + np.asarray(ms) / 1000.0


_track_cache: dict[tuple, Track] = {}


def load_track(path: Path, t0: float) -> Track:
    key = (str(path), path.stat().st_mtime_ns, t0)
    if key not in _track_cache:
        d = read_track_file(path)
        _track_cache[key] = Track(
            d["prn"], d["fs"], t0, d["i_prompt"], d["q_prompt"], d["doppler_hz"],
            d["code_phase_samp"], d["nav_bits"], d["cn0_dbhz"], d["bit_offset"],
            d["doppler_seed_hz"], d["code_phase_samp_seed"], d["detail"])
    return _track_cache[key]


def track_file(prn: int, folder: Path) -> Path:
    return folder / track_file_name(prn)


def window_slice(n_ms: int, end_ms: int, w_ms: int) -> slice:
    """The w_ms milliseconds that end at end_ms (clamped to the track)."""
    end = int(min(max(end_ms, 1), n_ms))
    return slice(max(end - w_ms, 0), end)


def decimate(y: np.ndarray, max_points: int = CURVE_POINTS) -> tuple[np.ndarray, int]:
    """Average groups of k values so at most max_points remain. Returns (y, k)."""
    k = max(int(np.ceil(len(y) / max_points)), 1)
    n = len(y) // k * k
    return y[:n].reshape(-1, k).mean(axis=1), k


def code_drift_chips(tr: Track) -> np.ndarray:
    """How far the code start has slid since the first ms, in chips (the code
    phase minus where it would be if the code ran at exactly 1.023 Mchip/s by
    our clock)."""
    spms = tr.fs * 1e-3
    m = np.arange(tr.n_ms)
    return (tr.code_phase - tr.code_phase[0] - m * spms) * 1023 / spms


def qi_per_bit(i: np.ndarray, q: np.ndarray, offset: int) -> np.ndarray:
    """Lock indicator |Q|/|I| (medians) for each 20 ms bit, aligned like the C/N0."""
    n = (len(i) - offset) // MS_PER_BIT * MS_PER_BIT
    if n <= 0:
        return np.zeros(0)
    ib = np.abs(i[offset:offset + n]).reshape(-1, MS_PER_BIT)
    qb = np.abs(q[offset:offset + n]).reshape(-1, MS_PER_BIT)
    return np.median(qb, axis=1) / (np.median(ib, axis=1) + 1e-9)


def bit_times(tr: Track, count: int) -> np.ndarray:
    """Recording time at the centre of each of the first `count` bits."""
    return tr.seconds(tr.bit_offset + MS_PER_BIT * np.arange(count) + MS_PER_BIT / 2)


def epl(tr: Track, sl: slice) -> tuple[float, float, float] | None:
    """Average |Early|, |Prompt|, |Late| over a window, or None if not saved."""
    if tr.detail is None or sl.stop <= sl.start:
        return None
    d = tr.detail
    return (float(np.hypot(d["i_early"][sl], d["q_early"][sl]).mean()),
            float(np.hypot(tr.i[sl], tr.q[sl]).mean()),
            float(np.hypot(d["i_late"][sl], d["q_late"][sl]).mean()))


def bit_at(tr: Track, ms: int) -> int:
    """Index of the bit that contains millisecond `ms`."""
    return int(np.clip((ms - tr.bit_offset) // MS_PER_BIT, 0, max(len(tr.bits) - 1, 0)))


# --- try it: PLL bandwidth ---------------------------------------------------------------------

def try_pll(x: np.ndarray, fs: float, prn: int, code_phase: float, doppler: float,
            pll_bw: float) -> dict:
    """Track a short stretch with another PLL bandwidth and judge the lock."""
    tr = track_prn(x, fs, prn, code_phase, doppler, pll_bw=pll_bw)
    i, q = tr["i_prompt"], tr["q_prompt"]
    settled = slice(min(TRY_SETTLE_MS, len(i) // 2), None)
    qi = float(np.median(np.abs(q[settled])) / (np.median(np.abs(i[settled])) + 1e-9))
    r = summarize(tr)
    return {"i": i, "q": q, "doppler": tr["doppler_hz"], "qi": qi,
            "locked": qi < LOCK_QI_MAX, "cn0": r["mean_cn0"] if r else float("nan")}


# --- running 03_tracking/track.py -------------------------------------------------------------

_PROGRESS = re.compile(r"^PROGRESS (\d+) (\d+) (\d+) (\d+) (\d+)\s*$", re.MULTILINE)

# The server's tracking run, shared by every browser tab: a run keeps going if the
# page reloads, and whichever page notices that it ended moves its results into
# place (settle_run). results_version tells the other pages to re-read them.
_current: TrackingRun | None = None
results_version = 0


def current_run() -> TrackingRun | None:
    return _current


def start_tracking(src: IQSource, start_s: float, seconds: float, s: TrackSettings,
                   seed_csv: Path, target: Path) -> TrackingRun:
    global _current
    if _current is not None:
        raise RuntimeError("Tracking is already running (maybe in another browser tab).")
    _current = TrackingRun.create(src, start_s, seconds, s, seed_csv, target).start()
    return _current


def settle_run() -> tuple[str, str] | None:
    """Once the current run has ended: move its results into place (if it
    succeeded), delete its temporary folder, and return (kind, message) for a
    notification. None while it runs, or when there is no run."""
    global _current, results_version
    r = _current
    if r is None or r.poll() is None:
        return None
    _current = None
    if r.succeeded():
        moved = r.finish()
        r.cleanup()
        results_version += 1
        n = len([m for m in moved if m.endswith(".npz")])
        return "positive", f"Tracking done: {n} satellite{'s' if n != 1 else ''}"
    if r.cancelled:
        r.cleanup()
        return "info", "Tracking cancelled; the previous results are unchanged."
    tail = r.log_tail()
    r.cleanup()
    return "negative", f"Tracking failed: {tail or 'exit code ' + str(r.proc.poll())}"


def track_command(src: IQSource, start_s: float, seconds: float, s: TrackSettings,
                  seed_csv: Path, out_dir: Path) -> list[str]:
    cmd = [sys.executable, "-u", str(TRACK_SCRIPT), "--iq", str(src.path),
           "--format", src.fmt, "--fs", repr(float(src.fs)),
           "--skip-seconds", repr(float(start_s)), "--seconds", repr(float(seconds)),
           "--from-csv", str(seed_csv), "--pll-bw", repr(float(s.pll_bw)),
           "--dll-bw", repr(float(s.dll_bw)), "--out-dir", str(out_dir), "--progress"]
    if s.prns:
        cmd += ["--prns", ",".join(str(p) for p in s.prns)]
    return cmd


@dataclass(eq=False)
class TrackingRun:
    """One run of 03_tracking/track.py in its own process.

    It writes into a temporary folder; the results replace those in `target`
    only when the run succeeds, so a cancelled run leaves the old ones intact.
    A separate process also keeps the UI responsive.
    """
    command: list[str]
    out_dir: Path
    target: Path
    proc: subprocess.Popen | None = field(default=None, repr=False)
    cancelled: bool = False

    @classmethod
    def create(cls, src, start_s, seconds, s, seed_csv, target) -> TrackingRun:
        out_dir = Path(tempfile.mkdtemp(prefix="bassanite_track_"))
        return cls(track_command(src, start_s, seconds, s, seed_csv, out_dir), out_dir,
                   Path(target))

    @property
    def log_path(self) -> Path:
        return self.out_dir / "log.txt"

    def start(self) -> TrackingRun:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with open(self.log_path, "wb") as log:
            self.proc = subprocess.Popen(
                self.command, stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, creationflags=flags, cwd=str(REPO_ROOT),
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg"})
        return self

    def poll(self) -> int | None:
        return self.proc.poll() if self.proc else None

    def log_text(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def progress(self) -> tuple[float, str]:
        """(fraction done, e.g. 'PRN 20 (3 of 8): 12 of 30 s')."""
        found = _PROGRESS.findall(self.log_text())
        if not found:
            return 0.0, "Loading the recording…"
        k, n, prn, done, total = (int(v) for v in found[-1])
        frac = (k - 1 + done / max(total, 1)) / max(n, 1)
        return frac, f"PRN {prn} ({k} of {n}): {done / 1000:.0f} of {total / 1000:.0f} s"

    def log_tail(self, lines: int = 4) -> str:
        text = [ln for ln in self.log_text().splitlines()
                if ln.strip() and not ln.startswith("PROGRESS")]
        return "\n".join(text[-lines:])

    def succeeded(self) -> bool:
        return (not self.cancelled and self.proc is not None and self.proc.poll() == 0
                and summary_path(self.out_dir).is_file())

    def finish(self) -> list[str]:
        """Move the results into place. Returns the file names moved."""
        moved = []
        self.target.mkdir(parents=True, exist_ok=True)
        for pattern in RESULT_GLOBS:
            for f in sorted(self.out_dir.glob(pattern)):
                shutil.move(str(f), str(self.target / f.name))
                moved.append(f.name)
        return moved

    def cancel(self) -> None:
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def cleanup(self) -> None:
        """Delete the temporary folder (after the process has ended)."""
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                return
        shutil.rmtree(self.out_dir, ignore_errors=True)


def stop_all_runs() -> None:
    """Server shutdown: stop the tracking process and drop its temporary folder."""
    r = _current
    if r is not None:
        r.cancel()
        r.cleanup()


# --- figures ----------------------------------------------------------------------------------

def _cursor(x0: float, x1: float) -> dict:
    return {"type": "rect", "xref": "x", "yref": "paper", "x0": x0, "x1": x1, "y0": 0,
            "y1": 1, "fillcolor": "rgba(255,179,0,0.25)",
            "line": {"color": HIGHLIGHT, "width": 1}}


def iq_figure(i, q, lim: float, title_note: str = "") -> dict:
    n = len(i)
    return figure(
        [{"type": "scatter", "mode": "markers", "x": np.round(i, 2).tolist(),
          "y": np.round(q, 2).tolist(),
          "marker": {"size": 4, "color": list(range(n)), "colorscale": "Blues",
                     "cmin": -n * 0.3, "cmax": n},
          "hovertemplate": "I %{x:.0f}<br>Q %{y:.0f}<extra></extra>"}],
        showlegend=False, height=320,
        xaxis={"title": "Prompt I" + title_note, "range": [-lim, lim], "zeroline": True},
        yaxis={"title": "Prompt Q", "range": [-lim, lim], "zeroline": True,
               "scaleanchor": "x", "scaleratio": 1})


def epl_figure(values: tuple[float, float, float] | None, top: float) -> dict:
    if values is None:
        return empty_figure("Not saved with these results.<br>"
                            "Press “Track the satellites” to record them.")
    e, p, l_ = values
    x = np.linspace(-1.5, 1.5, 61)
    return figure(
        [{"type": "scatter", "mode": "lines", "x": x.tolist(),
          "y": np.round(p * np.clip(1 - np.abs(x), 0, None), 2).tolist(),
          "name": "ideal triangle", "line": {"dash": "dot", "color": NOISE},
          "hoverinfo": "skip"},
         {"type": "scatter", "mode": "markers+text", "x": [-0.5, 0, 0.5], "y": [e, p, l_],
          "text": ["Early", "Prompt", "Late"], "textposition": "top center",
          "name": "measured", "marker": {"size": 12, "color": ["#1565c0", HIGHLIGHT,
                                                                 "#1565c0"]},
          "hovertemplate": "%{text}: %{y:.0f}<extra></extra>"}],
        height=320, xaxis={"title": "Replica offset (chips)", "range": [-1.6, 1.6]},
        yaxis={"title": "Correlator amplitude", "range": [0, top]})


def prompt_figure(tr: Track, sl: slice, lim: float) -> dict:
    ms = np.arange(sl.start, sl.stop)
    t = tr.seconds(ms)
    edges = ms[(ms - tr.bit_offset) % MS_PER_BIT == 0]
    shapes = [{"type": "line", "xref": "x", "yref": "paper", "x0": float(e), "x1": float(e),
               "y0": 0, "y1": 1, "line": {"dash": "dot", "color": "#bdbdbd", "width": 1}}
              for e in tr.seconds(edges)]
    notes = []
    if len(edges) <= 20:
        for start in range(sl.start - (sl.start - tr.bit_offset) % MS_PER_BIT, sl.stop,
                           MS_PER_BIT):
            mid = start + MS_PER_BIT / 2
            if sl.start <= mid < sl.stop and 0 <= start - tr.bit_offset:
                k = bit_at(tr, start)
                notes.append({"x": float(tr.seconds(mid)), "y": 1, "yref": "paper",
                              "yanchor": "bottom", "showarrow": False,
                              "text": str(int(tr.bits[k])), "font": {"size": 12}})
    line = {"type": "scatter", "mode": "lines", "x": np.round(t, 4).tolist()}
    return figure(
        [{**line, "y": np.round(tr.i[sl], 1).tolist(), "name": "I (carries the bits)",
          "line": {"width": 1.2, "color": "#1565c0"}},
         {**line, "y": np.round(tr.q[sl], 1).tolist(), "name": "Q (should stay near 0)",
          "line": {"width": 1, "color": "#ef6c00"}}],
        height=280, shapes=shapes, annotations=notes, margin={"l": 60, "r": 20, "t": 30,
                                                              "b": 45},
        xaxis={"title": "Time in recording (s)"},
        yaxis={"title": "Prompt correlator", "range": [-lim, lim]})


def bits_figure(tr: Track, current: int) -> dict:
    lo = int(np.clip(current - BITS_SHOWN // 2, 0, max(len(tr.bits) - BITS_SHOWN, 0)))
    hi = min(lo + BITS_SHOWN, len(tr.bits))
    ks = np.arange(lo, hi)
    starts = tr.seconds(tr.bit_offset + MS_PER_BIT * ks)
    fig = figure(
        [{"type": "heatmap", "z": [tr.bits[lo:hi].astype(int).tolist()],
          "x": ks.tolist(), "y": ["bit"], "showscale": False, "zmin": 0, "zmax": 1,
          "colorscale": [[0, "#eceff1"], [1, "#37474f"]], "xgap": 2,
          "text": [[str(int(b)) for b in tr.bits[lo:hi]]], "texttemplate": "%{text}",
          "customdata": [np.round(starts, 2).tolist()],
          "hovertemplate": "bit %{x}<br>starts at %{customdata:.2f} s<br>value %{z}"
                           "<extra></extra>"}],
        height=125, margin={"l": 40, "r": 20, "t": 5, "b": 50},
        xaxis={"title": "Bit number (50 per second)"}, yaxis={"visible": False},
        shapes=[{"type": "rect", "x0": current - 0.5, "x1": current + 0.5, "y0": -0.5,
                 "y1": 0.5, "line": {"color": HIGHLIGHT, "width": 3}}])
    fig["config"] = {**fig["config"], "displayModeBar": False}   # it would cover the bits
    return fig


def sync_figure(scores: np.ndarray, best: int) -> dict:
    top = scores.max() if scores.max() > 0 else 1.0
    return figure(
        [{"type": "bar", "x": list(range(MS_PER_BIT)),
          "y": np.round(scores / top, 3).tolist(),
          "marker": {"color": [DETECTED if k == best else NOISE for k in range(MS_PER_BIT)]},
          "hovertemplate": "offset %{x} ms<br>%{y:.2f} of the best<extra></extra>"}],
        height=220, showlegend=False,
        xaxis={"title": "Where a bit starts (ms offset)", "dtick": 1},
        yaxis={"title": "Energy (relative)", "range": [0, 1.1]})


def curve_figure(tr: Track, y: np.ndarray, title: str, cursor: tuple[float, float],
                 color: str = "#1565c0") -> dict:
    yd, k = decimate(y)
    x = tr.seconds(np.arange(len(yd)) * k + (k - 1) / 2)
    return figure(
        [{"type": "scatter", "mode": "lines", "x": np.round(x, 3).tolist(),
          "y": np.round(yd, 3).tolist(), "line": {"width": 1.2, "color": color},
          "hovertemplate": "%{x:.2f} s<br>%{y:.2f}<extra></extra>"}],
        height=240, showlegend=False, shapes=[_cursor(*cursor)],
        xaxis={"title": "Time in recording (s)"}, yaxis={"title": title})


def quality_figure(tr: Track, cursor: tuple[float, float]) -> dict:
    qi = qi_per_bit(tr.i, tr.q, tr.bit_offset)
    n = min(len(qi), len(tr.cn0))
    t = np.round(bit_times(tr, n), 3).tolist()
    return figure(
        [{"type": "scatter", "mode": "lines", "x": t, "y": np.round(tr.cn0[:n], 1).tolist(),
          "name": "C/N0 (dB-Hz)", "line": {"width": 1.2, "color": DETECTED},
          "hovertemplate": "%{x:.2f} s<br>%{y:.1f} dB-Hz<extra></extra>"},
         {"type": "scatter", "mode": "lines", "x": t, "y": np.round(qi[:n], 3).tolist(),
          "name": "|Q| / |I| (lock indicator)", "yaxis": "y2",
          "line": {"width": 1, "color": "#8e24aa"},
          "hovertemplate": "%{x:.2f} s<br>|Q|/|I| %{y:.2f}<extra></extra>"}],
        height=260, shapes=[_cursor(*cursor),
                            {"type": "line", "xref": "paper", "yref": "y2", "x0": 0, "x1": 1,
                             "y0": LOCK_QI_MAX, "y1": LOCK_QI_MAX,
                             "line": {"dash": "dash", "color": LIMIT, "width": 1}}],
        margin={"l": 60, "r": 60, "t": 10, "b": 45},
        xaxis={"title": "Time in recording (s)"},
        yaxis={"title": "C/N0 (dB-Hz)"},
        yaxis2={"title": {"text": "|Q| / |I|"}, "overlaying": "y", "side": "right",
                "range": [0, 1.5], "showgrid": False, "tickmode": "array",
                "tickvals": [0, LOCK_QI_MAX, 1.0, 1.5]})


def discriminator_figure(tr: Track, sl: slice) -> dict:
    if tr.detail is None:
        return empty_figure("Not saved with these results.<br>"
                            "Press “Track the satellites” to record them.")
    t = np.round(tr.seconds(np.arange(sl.start, sl.stop)), 4).tolist()
    return figure(
        [{"type": "scatter", "mode": "lines", "x": t,
          "y": np.round(tr.detail["carr_err"][sl] * 360, 2).tolist(),
          "name": "carrier phase error (°)", "line": {"width": 1, "color": "#1565c0"}},
         {"type": "scatter", "mode": "lines", "x": t,
          "y": np.round(tr.detail["code_err"][sl], 3).tolist(), "yaxis": "y2",
          "name": "code error (E−L)/(E+L)", "line": {"width": 1, "color": "#ef6c00"}}],
        height=260, margin={"l": 60, "r": 60, "t": 10, "b": 45},
        xaxis={"title": "Time in recording (s)"},
        yaxis={"title": "Carrier error (°)", "range": [-90, 90]},
        yaxis2={"title": {"text": "Code error"}, "overlaying": "y", "side": "right",
                "range": [-1, 1], "showgrid": False, "tickmode": "array",
                "tickvals": [-1, -0.5, 0, 0.5, 1]})


def sparkline(cn0: np.ndarray, color: str) -> dict:
    y, _ = decimate(cn0, 100)
    return {"data": [{"type": "scatter", "mode": "lines", "y": np.round(y, 1).tolist(),
                      "line": {"width": 1.2, "color": color}, "hoverinfo": "skip"}],
            "config": {"staticPlot": True, "displayModeBar": False},
            "layout": layout(height=40, margin={"l": 0, "r": 0, "t": 2, "b": 2},
                             xaxis={"visible": False},
                             yaxis={"visible": False, "range": [15, 50]},
                             showlegend=False, paper_bgcolor="rgba(0,0,0,0)",
                             plot_bgcolor="rgba(0,0,0,0)")}


def try_figures(r: dict, t0: float) -> tuple[dict, dict]:
    i, q = r["i"], r["q"]
    lim = float(np.percentile(np.abs(np.concatenate([i, q])), 99.5)) * 1.15 or 1.0
    t = np.round(t0 + np.arange(len(i)) / 1000, 3).tolist()
    iq = figure([{"type": "scatter", "mode": "markers", "x": np.round(i, 1).tolist(),
                  "y": np.round(q, 1).tolist(),
                  "marker": {"size": 3, "color": t, "colorscale": "Viridis",
                             "colorbar": {"title": {"text": "s"}, "thickness": 10}},
                  "hovertemplate": "I %{x:.0f}<br>Q %{y:.0f}<extra></extra>"}],
                showlegend=False, height=300,
                xaxis={"title": "Prompt I", "range": [-lim, lim]},
                yaxis={"title": "Prompt Q", "range": [-lim, lim], "scaleanchor": "x",
                       "scaleratio": 1})
    dop = figure([{"type": "scatter", "mode": "lines", "x": t,
                   "y": np.round(r["doppler"], 2).tolist(),
                   "line": {"width": 1, "color": "#1565c0"}}],
                 showlegend=False, height=300, xaxis={"title": "Time in recording (s)"},
                 yaxis={"title": "Doppler followed by the loop (Hz)"})
    return iq, dop


# --- the tab ------------------------------------------------------------------------------------

HANDOVER_COLUMNS = [
    {"name": "prn", "label": "PRN", "field": "prn", "align": "left"},
    {"name": "locked", "label": "Locked", "field": "locked"},
    {"name": "cn0", "label": "C/N0 (dB-Hz)", "field": "cn0"},
    {"name": "doppler", "label": "Doppler at the end (Hz)", "field": "doppler"},
    {"name": "bits", "label": "Bits", "field": "bits"},
    {"name": "offset", "label": "Bit edge (ms)", "field": "offset"},
    {"name": "first", "label": "First bits", "field": "first", "align": "left"},
]


class TrackingTab:
    """One instance per browser tab (each client builds its own page)."""

    def __init__(self, go_to=None, on_tracked=None) -> None:
        self.go_to = go_to                  # switch tabs (to send the user to step 2)
        self.on_tracked = on_tracked        # called after a run wrote new results
        self.settings = TrackSettings()
        self.rows: list[dict] = []
        self.meta: dict = {}
        self.tracks: dict[int, Track] = {}
        self.selected: int | None = None
        self.end_ms = DEFAULT_WINDOW_MS
        self.window_ms = DEFAULT_WINDOW_MS
        self.speed = 1.0
        self._can_track = False             # recording and step-2 seeds are ready
        self._seen_version = results_version
        self._pending = self._busy = False
        self._detected: list[int] = []
        self._scan_start = 0.0
        self._try_x = None
        self._try_key = None
        self._try_seq = 0
        self._figs: dict[str, dict] = {}

    def request_update(self) -> None:
        self._pending = True

    # --- layout -----------------------------------------------------------------------

    def build(self) -> None:
        with ui.row().classes("w-full items-start gap-4"):
            self._build_controls()
            with ui.column().classes("flex-1 min-w-[18rem] gap-4"):
                self._build_run()
                self._build_satellites()
        self._build_detail()
        self._build_try_it()
        self._build_handover()
        ui.timer(0.3, self._tick)
        self.play_timer = ui.timer(PLAY_TICK_S, self._play_tick, active=False)
        context.client.on_disconnect(self._on_disconnect)
        self.request_update()

    @staticmethod
    def _card(title: str, caption: str = ""):
        card = ui.card().classes("w-full").props("flat bordered")
        with card:
            ui.label(title).classes("text-subtitle1 font-bold")
            if caption:
                ui.label(caption).classes("text-sm text-grey-8")
        return card

    def _build_controls(self) -> None:
        with ui.card().classes("w-full md:w-72").props("flat bordered") \
                .bind_visibility_from(state, "mode", value="expert"):
            ui.label("Tracking settings").classes("text-subtitle1 font-bold")
            ui.label("Used by the next run.").classes("text-xs text-grey-7")
            self.c_seconds = ui.number("Seconds to track (empty: all)",
                                       value=DEFAULT_SECONDS, min=1, step=5, format="%.0f",
                                       on_change=lambda e: self._set(seconds=e.value or None)) \
                .classes("w-full")
            self.c_pll = ui.number("PLL bandwidth (Hz)", value=DEFAULT_PLL_BW, min=1,
                                   max=100, step=1,
                                   on_change=lambda e: self._set(pll_bw=e.value)) \
                .classes("w-full")
            self.c_dll = ui.number("DLL bandwidth (Hz)", value=DEFAULT_DLL_BW, min=0,
                                   max=10, step=0.5,
                                   on_change=lambda e: self._set(dll_bw=e.value)) \
                .classes("w-full")
            self.c_prns = ui.select([], multiple=True, label="Satellites (empty: all found)",
                                    on_change=lambda e: self._set_prns(e.value)) \
                .props("use-chips").classes("w-full")
            ui.label("Tracking starts where the scan of step 2 looked, so the seeds "
                     "line up. Like track.py, the run writes "
                     "results/<recording>/tracking/.") \
                .classes("text-xs text-grey-7")

    def _build_run(self) -> None:
        with self._card("Follow every satellite found",
                        "Each satellite gets its own two loops, run once per millisecond "
                        "over the whole recording."):
            self.status_label = ui.label().classes("text-sm")
            self.detail_label = ui.label().classes("text-xs text-grey-7")
            with ui.row().classes("w-full items-center gap-3"):
                self.track_button = ui.button("Track the satellites", icon="track_changes",
                                              on_click=self.start_run) \
                    .props("no-caps unelevated")
                self.goto_scan = ui.button("Go to step 2", icon="arrow_back",
                                           on_click=lambda: self.go_to and
                                           self.go_to("acquisition")) \
                    .props("no-caps outline")
                self.goto_scan.set_visibility(False)
                self.cancel_button = ui.button("Cancel", on_click=self.cancel_run) \
                    .props("no-caps flat color=negative")
                self.cancel_button.set_visibility(False)
            self.estimate_label = ui.label().classes("text-xs text-grey-7")
            with ui.column().classes("w-full gap-0") as self.progress_box:
                self.progress = ui.linear_progress(value=0, show_value=False)
                self.progress_label = ui.label().classes("text-xs text-grey-8")
            self.progress_box.set_visibility(False)

    def _build_satellites(self) -> None:
        with self._card("Satellites",
                        "Click a satellite to replay how its loops locked."):
            self.cards = ui.row().classes("w-full gap-2")

    def _build_detail(self) -> None:
        with ui.card().classes("w-full").props("flat bordered") as self.detail_card:
            self.detail_title = ui.label().classes("text-subtitle1 font-bold")
            ui.label("Drag the time slider, or press Play, to replay the recording. The "
                     "plots show the window of signal that ends at the slider.") \
                .classes("text-sm text-grey-8")
            with ui.row().classes("w-full items-center gap-2"):
                self.play_button = ui.button(icon="play_arrow", on_click=self.toggle_play) \
                    .props("round dense unelevated").tooltip("Play / pause")
                ui.button(icon="chevron_left", on_click=lambda: self._nudge(-MS_PER_BIT)) \
                    .props("round dense flat").tooltip("One bit back")
                ui.button(icon="chevron_right", on_click=lambda: self._nudge(MS_PER_BIT)) \
                    .props("round dense flat").tooltip("One bit forward")
                self.time_label = ui.label().classes("text-sm font-mono min-w-[9rem]")
                self.slider = ui.slider(min=0, max=1, step=0.02, value=0,
                                        on_change=lambda e: self._set_time(e.value)) \
                    .classes("flex-1 min-w-[10rem]")
                ui.select(WINDOWS_MS, value=self.window_ms, label="Window",
                          on_change=lambda e: self._set_window(e.value)).classes("w-36")
                ui.select({0.5: "×0.5", 1.0: "×1", 5.0: "×5"}, value=self.speed,
                          label="Speed", on_change=lambda e: setattr(self, "speed", e.value)) \
                    .classes("w-20")
            self.window_note = ui.label().classes("text-sm")
            with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4"):
                with ui.column().classes("w-full gap-0"):
                    ui.label("Prompt I/Q: the carrier loop").classes("text-sm font-medium")
                    ui.label("Locked: two blobs on the I axis (the two bit values). "
                             "Not locked: a ring.").classes("text-xs text-grey-8")
                    self.p_iq = ui.plotly(empty_figure()).classes("w-full")
                    explain_panel("tracking", "iq")
                with ui.column().classes("w-full gap-0"):
                    ui.label("Early, prompt, late: the code loop").classes("text-sm font-medium")
                    ui.label("Centred: early and late equally strong, prompt on the peak.") \
                        .classes("text-xs text-grey-8")
                    self.p_epl = ui.plotly(empty_figure()).classes("w-full")
                    explain_panel("tracking", "epl")
            ui.label("The prompt over time: bits appear").classes("text-sm font-medium")
            ui.label("Dotted lines are bit edges (every 20 ms). With a short window, each "
                     "bit's value is written on top.").classes("text-xs text-grey-8")
            self.p_prompt = ui.plotly(empty_figure()).classes("w-full")
            self.p_bits = ui.plotly(empty_figure()).classes("w-full")
            with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4 items-start"):
                with ui.column().classes("w-full gap-0"):
                    ui.label("Bit sync: which millisecond starts a bit?") \
                        .classes("text-sm font-medium")
                    self.p_sync = ui.plotly(empty_figure()).classes("w-full")
                with ui.column().classes("w-full gap-0"):
                    ui.label("What the loops measure each millisecond") \
                        .classes("text-sm font-medium")
                    self.p_disc = ui.plotly(empty_figure()).classes("w-full")
            explain_panel("tracking", "bits")
            ui.label("What the loops follow over the whole recording") \
                .classes("text-sm font-medium")
            with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4"):
                self.p_doppler = ui.plotly(empty_figure()).classes("w-full")
                self.p_drift = ui.plotly(empty_figure()).classes("w-full")
            explain_panel("tracking", "loops")
            self.p_quality = ui.plotly(empty_figure()).classes("w-full")
            explain_panel("tracking", "quality")
        self.detail_card.set_visibility(False)

    def _build_try_it(self) -> None:
        with self._card("Try it: how wide should the carrier loop be?",
                        f"Re-track the first {TRY_SECONDS:g} s of the selected satellite "
                        "with another PLL bandwidth.") as self.try_card:
            with ui.row().classes("w-full items-center gap-4"):
                self.try_label = ui.label().classes("text-sm min-w-[10rem]")
                self.try_slider = ui.slider(min=0, max=len(PLL_CHOICES) - 1, step=1,
                                            value=PLL_CHOICES.index(12),
                                            on_change=lambda e: self._try(int(e.value))) \
                    .classes("flex-1 min-w-[12rem]")
            self.try_verdict = ui.label().classes("text-sm")
            with ui.grid().classes("w-full grid-cols-1 lg:grid-cols-2 gap-4"):
                self.p_try_iq = ui.plotly(empty_figure()).classes("w-full")
                self.p_try_dop = ui.plotly(empty_figure()).classes("w-full")
            explain_panel("tracking", "pll")
        self.try_card.set_visibility(False)

    def _build_handover(self) -> None:
        with self._card("What goes to decoding",
                        "For each satellite: the 50 bit/s data stream (and, for the "
                        "position, when each code period arrived).") as self.handover_card:
            self.handover = ui.table(columns=HANDOVER_COLUMNS, rows=[], row_key="prn") \
                .props("flat dense").classes("w-full")

    # --- settings -----------------------------------------------------------------------

    def _set(self, **changes) -> None:
        clean = {k: v for k, v in changes.items() if v is not None}
        if clean:
            self.settings = replace(self.settings, **clean)
            self._show_estimate()

    def _set_prns(self, value) -> None:
        self.settings = replace(self.settings, prns=tuple(sorted(int(p) for p in value or [])))
        self._show_estimate()

    def _run_prns(self) -> list[int]:
        return list(self.settings.prns) or self._detected

    def _show_estimate(self) -> None:
        src = state.source
        if src is None or not self._detected:
            self.estimate_label.set_text("")
            return
        seconds = seconds_to_track(src, self._scan_start, self.settings)
        n = len(self._run_prns())
        minutes = estimate_minutes(seconds, n)
        took = f"about {minutes:.0f} min" if minutes >= 1.5 else f"about {minutes * 60:.0f} s"
        text = f"Tracking {n} satellite{'s' if n != 1 else ''} over {seconds:.0f} s takes {took}."
        if seconds < MIN_SECONDS_FOR_EPHEMERIS:
            text += " " + too_short_for_decode(seconds)
        self.estimate_label.set_text(text)

    # --- running ------------------------------------------------------------------------

    def _seeds(self, src: IQSource):
        """(detected PRNs, scan start) from step 2's results for this recording."""
        saved = acquisition.saved_results(src)
        if saved is None:
            return None
        results, settings = saved
        return acquisition.detected_prns(results), settings.skip_seconds

    async def start_run(self) -> None:
        src = state.source
        if src is None or src.problems():
            ui.notify("Load a recording first.", type="warning")
            return
        if not self._detected:
            ui.notify("Find the satellites first (step 2).", type="warning")
            return
        seconds = seconds_to_track(src, self._scan_start, self.settings)
        s = replace(self.settings, prns=tuple(self._run_prns()))
        self.stop_play()
        try:
            start_tracking(src, self._scan_start, seconds, s, acquisition.results_csv(src),
                           track_dir(src))
        except (OSError, RuntimeError) as err:
            ui.notify(f"Could not start tracking: {err}", type="warning")
            return
        self.progress.set_value(0)
        self.progress_label.set_text("Starting…")
        self._sync_run_controls()

    def cancel_run(self) -> None:
        r = current_run()
        if r is not None:
            r.cancel()
            self.progress_label.set_text("Cancelling…")

    def _on_disconnect(self) -> None:
        # Also called on short disconnects that reconnect. A tracking run is not
        # tied to this page: it keeps going, and any page finishes it.
        self.stop_play()

    def _sync_run_controls(self) -> None:
        """Show the server's tracking run (if any) in this page's controls."""
        r = current_run()
        running = r is not None
        if self.cancel_button.visible != running:
            self.cancel_button.set_visibility(running)
        if self.progress_box.visible != running:
            self.progress_box.set_visibility(running)
        enabled = self._can_track and not running
        if self.track_button.enabled != enabled:
            self.track_button.set_enabled(enabled)
        if running and not r.cancelled:
            frac, text = r.progress()
            self.progress.set_value(round(frac, 3))
            if self.progress_label.text != text:
                self.progress_label.set_text(text)

    async def _tick(self) -> None:
        outcome = settle_run()
        if outcome is not None:
            kind, message = outcome
            ui.notify(message, type=kind, multi_line=kind == "negative")
            if kind == "positive" and self.on_tracked is not None:
                self.on_tracked()          # this page's other tabs and the Overview
        if self._seen_version != results_version:
            self._seen_version = results_version
            self.request_update()
        self._sync_run_controls()
        if self._pending and not self._busy:
            self._pending = False
            self._busy = True
            try:
                await self._update()
            finally:
                self._busy = False

    # --- results -------------------------------------------------------------------------

    async def _update(self) -> None:
        src = state.source
        problem = ("Load a recording first (top bar, or the Overview tab)."
                   if src is None else (src.problems() or [None])[0])
        seeds = None if problem else self._seeds(src)
        self._detected, self._scan_start = seeds if seeds else ([], 0.0)
        keep = [p for p in (self.c_prns.value or []) if p in self._detected]
        self.c_prns.set_options(self._detected, value=keep)
        self.goto_scan.set_visibility(not problem and not self._detected)
        self._can_track = not problem and bool(self._detected)
        self._sync_run_controls()
        if problem:
            self._show_nothing(problem)
            return
        if seeds is None:
            self._show_nothing("First find the satellites in this recording (step 2): "
                               "tracking starts from what the scan found.")
        elif not self._detected:
            self._show_nothing("Step 2 found no satellite in this recording, so there is "
                               "nothing to track.")
        self._show_estimate()
        if not self._detected:
            return

        saved = saved_tracking(src)
        if saved is None:
            other = summary_path(track_dir(src)).is_file()
            self._show_nothing(("The tracking results on disk are for another recording. "
                                if other else "Not tracked yet. ")
                               + "Press “Track the satellites”.")
            return
        self.rows, self.meta = saved
        t0 = float((self.meta.get("settings") or {}).get("skip_seconds") or 0.0)
        try:
            tracks = await run.io_bound(self._load_tracks, track_dir(src),
                                        [r["prn"] for r in self.rows], t0)
        except (OSError, ValueError, KeyError) as err:
            self._show_nothing(f"Could not read the tracking results: {err}")
            return
        self.tracks = tracks
        self._show_summary()
        locked = [r for r in self.rows if r["locked"] and r["prn"] in tracks]
        if self.selected not in tracks:
            best = max(locked or self.rows, key=lambda r: r["mean_cn0_dbhz"])
            self.selected = best["prn"] if best["prn"] in tracks else next(iter(tracks), None)
        self._show_cards()
        self._show_handover()
        self._select(self.selected)

    @staticmethod
    def _load_tracks(folder: Path, prns: list[int], t0: float) -> dict[int, Track]:
        return {p: load_track(track_file(p, folder), t0) for p in prns
                if track_file(p, folder).is_file()}

    def _show_nothing(self, message: str) -> None:
        self.status_label.set_text(message)
        self.detail_label.set_text("")
        self.rows, self.tracks = [], {}
        self.cards.clear()
        self.detail_card.set_visibility(False)
        self.try_card.set_visibility(False)
        self.handover.rows = []
        self.stop_play()

    def _show_summary(self) -> None:
        locked = [r for r in self.rows if r["locked"]]
        if locked:
            cn0 = [r["mean_cn0_dbhz"] for r in locked]
            text = (f"{len(locked)} of {len(self.rows)} satellites locked, C/N0 "
                    f"{min(cn0):.0f}–{max(cn0):.0f} dB-Hz. ")
            text += ("Enough for a position fix." if len(locked) >= MIN_SATS_FOR_FIX else
                     f"A position fix needs {MIN_SATS_FOR_FIX}.")
        else:
            text = "No satellite locked."
        self.status_label.set_text(text)
        s = (self.meta.get("settings") or {})
        seconds = max((r["ms_tracked"] for r in self.rows), default=0) / 1000
        self.detail_label.set_text(
            f"Tracked {seconds:.1f} s from {float(s.get('skip_seconds') or 0):.1f} s, "
            f"PLL {float(s.get('pll_bw_hz', DEFAULT_PLL_BW)):g} Hz, "
            f"DLL {float(s.get('dll_bw_hz', DEFAULT_DLL_BW)):g} Hz. "
            f"Results: {rel_path(track_dir(state.source))}/ "
            "(tracking_summary.csv, track_PRNnn.npz)"
            + (" " + too_short_for_decode(seconds)
               if seconds < MIN_SECONDS_FOR_EPHEMERIS else ""))

    def _show_cards(self) -> None:
        self.cards.clear()
        with self.cards:
            for r in sorted(self.rows, key=lambda r: -r["mean_cn0_dbhz"]):
                tr = self.tracks.get(r["prn"])
                selected = r["prn"] == self.selected
                color = DETECTED if r["locked"] else NOISE
                card = ui.card().classes("w-40 cursor-pointer q-pa-sm gap-0").props(
                    "flat bordered").style(
                    f"border: {'3px solid ' + HIGHLIGHT if selected else '1px solid #ddd'}")
                card.on("click", lambda _, p=r["prn"]: self._select(p))
                with card:
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(f"PRN {r['prn']}").classes("font-bold")
                        ui.label("✅ locked" if r["locked"] else "⚠️ no lock") \
                            .classes("text-xs")
                    cn0 = (f"{r['mean_cn0_dbhz']:.0f} dB-Hz" if r["locked"]
                           else f"|Q|/|I| {r['pll_lock_qi']:.2f}")
                    ui.label(cn0).classes("text-sm")
                    if tr is not None and len(tr.cn0):
                        ui.plotly(sparkline(tr.cn0, color)).classes("w-full h-10")

    def _show_handover(self) -> None:
        rows = []
        for r in sorted(self.rows, key=lambda r: r["prn"]):
            tr = self.tracks.get(r["prn"])
            first = "".join(str(int(b)) for b in tr.bits[:40]) + "…" if tr is not None else ""
            rows.append({"prn": r["prn"], "locked": "✅" if r["locked"] else "⚠️",
                         "cn0": f"{r['mean_cn0_dbhz']:.1f}" if r["locked"] else "—",
                         "doppler": f"{r['final_doppler_hz']:+.0f}", "bits": r["nbits"],
                         "offset": r["bit_offset"], "first": first})
        self.handover.rows = rows

    # --- the replay ----------------------------------------------------------------------

    def _select(self, prn: int | None) -> None:
        if prn is None or prn not in self.tracks:
            self.detail_card.set_visibility(False)
            self.try_card.set_visibility(False)
            return
        changed = prn != self.selected
        self.selected = prn
        tr = self.tracks[prn]
        row = next((r for r in self.rows if r["prn"] == prn), None)
        self.detail_card.set_visibility(True)
        self.try_card.set_visibility(True)
        state_text = ("locked" if row and row["locked"] else "not locked")
        self.detail_title.set_text(f"PRN {prn} up close ({state_text})")
        if changed:
            self._show_cards()
        self.stop_play()
        self.slider.props["min"] = round(tr.t0 + MS_PER_BIT / 1000, 2)
        self.slider.props["max"] = round(tr.t0 + tr.n_ms / 1000, 2)
        self.slider.update()
        self._lim = float(np.percentile(np.abs(np.concatenate([tr.i, tr.q])), 99.8)) * 1.1
        all_p = np.hypot(tr.i, tr.q)
        self._epl_top = float(np.percentile(all_p, 99)) * 1.25
        scores = bit_sync_scores(tr.i)
        self.p_sync.update_figure(sync_figure(scores, int(np.argmax(scores))))
        cursor = self._cursor_span(tr)
        self._figs = {
            "doppler": curve_figure(tr, tr.doppler, "Doppler (Hz)", cursor),
            "drift": curve_figure(tr, code_drift_chips(tr), "Code drift (chips)", cursor,
                                  color="#ef6c00"),
            "quality": quality_figure(tr, cursor),
        }
        self.p_doppler.update_figure(self._figs["doppler"])
        self.p_drift.update_figure(self._figs["drift"])
        self.p_quality.update_figure(self._figs["quality"])
        self.end_ms = min(max(self.end_ms, self.window_ms), tr.n_ms)
        self.slider.set_value(round(tr.t0 + self.end_ms / 1000, 2))
        self._show_window(move_cursors=False)
        self._try(int(self.try_slider.value))

    def _cursor_span(self, tr: Track) -> tuple[float, float]:
        sl = window_slice(tr.n_ms, self.end_ms, self.window_ms)
        return float(tr.seconds(sl.start)), float(tr.seconds(sl.stop))

    def _set_time(self, value) -> None:
        tr = self.tracks.get(self.selected)
        if tr is None or value is None:
            return
        self.end_ms = int(round((float(value) - tr.t0) * 1000))
        self._show_window()

    def _set_window(self, w_ms: int) -> None:
        self.window_ms = int(w_ms)
        self._show_window()

    def _nudge(self, ms: int) -> None:
        tr = self.tracks.get(self.selected)
        if tr is not None:
            self.slider.set_value(round(tr.t0 + (self.end_ms + ms) / 1000, 3))

    def _show_window(self, move_cursors: bool = True) -> None:
        tr = self.tracks.get(self.selected)
        if tr is None:
            return
        sl = window_slice(tr.n_ms, self.end_ms, self.window_ms)
        t_start, t_end = float(tr.seconds(sl.start)), float(tr.seconds(sl.stop))
        self.time_label.set_text(f"{t_start:.2f}–{t_end:.2f} s")
        qi = (float(np.median(np.abs(tr.q[sl])) / (np.median(np.abs(tr.i[sl])) + 1e-9))
              if sl.stop > sl.start else float("nan"))
        verdict = "locked" if qi < LOCK_QI_MAX else "not locked (yet)"
        self.window_note.set_text(f"In this window: |Q|/|I| = {qi:.2f} → carrier {verdict}.")
        self.p_iq.update_figure(iq_figure(tr.i[sl], tr.q[sl], self._lim))
        self.p_epl.update_figure(epl_figure(epl(tr, sl), self._epl_top))
        self.p_prompt.update_figure(prompt_figure(tr, sl, self._lim))
        self.p_bits.update_figure(bits_figure(tr, bit_at(tr, sl.stop - 1)))
        self.p_disc.update_figure(discriminator_figure(tr, sl))
        if move_cursors:
            for key, plot in (("doppler", self.p_doppler), ("drift", self.p_drift),
                              ("quality", self.p_quality)):
                shape = self._figs[key]["layout"]["shapes"][0]
                shape["x0"], shape["x1"] = t_start, t_end
                plot.run_plot_method("relayout", {"shapes[0].x0": t_start,
                                                  "shapes[0].x1": t_end})

    def toggle_play(self) -> None:
        if self.play_timer.active:
            self.stop_play()
            return
        tr = self.tracks.get(self.selected)
        if tr is None:
            return
        if self.end_ms >= tr.n_ms:
            self.slider.set_value(round(tr.t0 + self.window_ms / 1000, 2))
        self.play_timer.activate()
        self.play_button.props("icon=pause")

    def stop_play(self) -> None:
        if hasattr(self, "play_timer") and self.play_timer.active:
            self.play_timer.deactivate()
            self.play_button.props("icon=play_arrow")

    def _play_tick(self) -> None:
        tr = self.tracks.get(self.selected)
        if tr is None:
            self.stop_play()
            return
        step = int(PLAY_TICK_S * 1000 * self.speed)
        nxt = min(self.end_ms + step, tr.n_ms)
        self.slider.set_value(round(tr.t0 + nxt / 1000, 3))
        if nxt >= tr.n_ms:
            self.stop_play()

    # --- try it ----------------------------------------------------------------------------

    def _try(self, index: int) -> None:
        bw = PLL_CHOICES[index]
        self.try_label.set_text(f"PLL bandwidth: {bw} Hz")
        background_tasks.create(self._run_try(bw), name="pll try-it")

    async def _run_try(self, bw: float) -> None:
        tr, src = self.tracks.get(self.selected), state.source
        if tr is None or src is None:
            return
        self._try_seq += 1
        seq = self._try_seq
        self.try_verdict.set_text("Tracking…")
        key = (str(src.path), src.size_bytes, src.fmt, src.fs, tr.t0)
        try:
            if key != self._try_key:
                self._try_x = await run.io_bound(load_iq, src.path, src.fmt, src.fs,
                                                 TRY_SECONDS, tr.t0)
                self._try_key = key
            r = await run.io_bound(try_pll, self._try_x, src.fs, tr.prn,
                                   tr.seed_code_phase, tr.seed_doppler, bw)
        except (OSError, ValueError) as err:
            self.try_verdict.set_text(f"Could not track: {err}")
            return
        if seq != self._try_seq:
            return                   # the slider moved on meanwhile
        if r["locked"]:
            verdict = (f"✅ Locks: after {TRY_SETTLE_MS} ms, |Q|/|I| = {r['qi']:.2f} "
                       f"and C/N0 ≈ {r['cn0']:.0f} dB-Hz.")
        else:
            verdict = (f"❌ No lock: |Q|/|I| = {r['qi']:.2f} (needs < {LOCK_QI_MAX}). "
                       + ("Too narrow: the loop cannot catch the frequency error left "
                          "after acquisition." if bw < DEFAULT_PLL_BW else
                          "Too wide: the loop reacts to noise and goes unstable."))
        self.try_verdict.set_text(f"PRN {tr.prn}, {bw} Hz: {verdict}")
        iq, dop = try_figures(r, tr.t0)
        self.p_try_iq.update_figure(iq)
        self.p_try_dop.update_figure(dop)
