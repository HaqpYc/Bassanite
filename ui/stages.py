"""The pipeline stages shown as tabs, and how to tell how far each one got.

Status is read from the files each stage writes (the file interface between
stages), so results produced by the command-line scripts show up here too.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from state import DEFAULT_RECORDING, REPO_ROOT, IQSource

# A tracked satellite counts as locked when |Q_P|/|I_P| stays well below this
# (see 03_tracking/NOTES.md, "C/N0 and the lock indicator").
LOCK_QI_MAX = 0.3
MIN_SATS_FOR_FIX = 4


@dataclass(frozen=True)
class Stage:
    key: str           # tab name and folder name under ui/explain/
    number: int
    title: str
    tagline: str       # one line for the pipeline diagram
    built: bool        # is the backend for this stage implemented?
    hands_over: str    # what this stage passes to the next one
    output_files: tuple[str, ...] = ()   # repo-relative, may contain globs


STAGES: tuple[Stage, ...] = (
    Stage("signal", 1, "Signal", "Antenna → raw IQ samples", True,
          "IQ samples: complex numbers at 4 million per second, scaled to about ±1, "
          "DC offset removed (common/iq_loader.py)."),
    Stage("acquisition", 2, "Find satellites", "Which satellites are overhead?", True,
          "For each satellite found: its PRN, code phase and Doppler, the starting "
          "point for tracking.",
          ("02_acquisition/scan_results.csv",)),
    Stage("tracking", 3, "Track", "Follow each one → data bits", True,
          "For each locked satellite: the 1 kHz prompt correlator, Doppler and code "
          "phase, and the 50 bit/s navigation bits.",
          ("03_tracking/tracking_summary.csv", "03_tracking/track_PRN*.npz")),
    Stage("decode", 4, "Decode", "Read the satellites' messages", False,
          "Each satellite's orbit (ephemeris), clock corrections and transmit time."),
    Stage("position", 5, "Position", "Distances → your position", False,
          "Latitude, longitude, altitude and the receiver clock error."),
)

STAGE_BY_KEY = {s.key: s for s in STAGES}

ICONS = {"done": "✅", "attention": "⚠️", "running": "⏳", "locked": "🔒"}


@dataclass(frozen=True)
class StageStatus:
    state: str        # "done" | "attention" | "running" | "locked"
    summary: str      # short sentence, e.g. "7 satellites locked"
    detail: str = ""  # where the information came from

    @property
    def icon(self) -> str:
        return ICONS[self.state]


def _read_csv(rel_path: str) -> list[dict] | None:
    path = REPO_ROOT / rel_path
    if not path.is_file():
        return None
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _signal_status(source: IQSource | None) -> StageStatus:
    if source is None:
        return StageStatus("locked", "No signal loaded yet")
    problems = source.problems()
    if problems:
        return StageStatus("attention", problems[0])
    return StageStatus("done", source.describe())


def _acquisition_status() -> StageStatus:
    path = "02_acquisition/scan_results.csv"
    rows = _read_csv(path)
    if rows is None:
        return StageStatus("locked", "Not run yet")
    detected = [r for r in rows if r.get("detected") == "True"]
    if not detected:
        return StageStatus("attention", "No satellite detected", f"from {path}")
    prns = ", ".join(r["prn"] for r in detected)
    return StageStatus("done", f"{len(detected)} satellites detected (PRN {prns})",
                       f"from {path}")


def _tracking_status() -> StageStatus:
    path = "03_tracking/tracking_summary.csv"
    rows = _read_csv(path)
    if rows is None:
        return StageStatus("locked", "Not run yet")
    locked = [r for r in rows if float(r["pll_lock_qi"]) < LOCK_QI_MAX]
    if not locked:
        return StageStatus("attention", "No satellite locked", f"from {path}")
    cn0 = [float(r["mean_cn0_dbhz"]) for r in locked]
    summary = (f"{len(locked)} of {len(rows)} satellites locked, "
               f"C/N0 {min(cn0):.0f}–{max(cn0):.0f} dB-Hz")
    if len(locked) < MIN_SATS_FOR_FIX:
        return StageStatus("attention",
                           f"{summary} (a fix needs {MIN_SATS_FOR_FIX})", f"from {path}")
    return StageStatus("done", summary, f"from {path}")


def _results_from_other_recording(stage: Stage, source: IQSource | None) -> bool:
    """True when the stage's result files can't belong to the loaded recording.

    The result files don't record which recording they came from, and file dates
    are unreliable (copying a file changes them). The stage scripts read
    recordings/capture.bin by default, so results on disk are taken to come from
    it; any other loaded recording is flagged. (The planned run history will
    record the real provenance.)
    """
    if source is None or not stage.output_files:
        return False
    return Path(source.path).resolve() != DEFAULT_RECORDING.resolve()


def stage_status(stage: Stage, source: IQSource | None) -> StageStatus:
    if stage.key == "signal":
        return _signal_status(source)
    if not stage.built:
        return StageStatus("locked", "Not built yet")
    if stage.key == "acquisition":
        status = _acquisition_status()
    elif stage.key == "tracking":
        status = _tracking_status()
    else:
        raise ValueError(f"no status rule for stage {stage.key!r}")
    if status.state == "done" and _results_from_other_recording(stage, source):
        return StageStatus("attention",
                           f"Results on disk are for {DEFAULT_RECORDING.name}, not "
                           f"{source.path.name}: run this step on it",
                           status.detail)
    return status


def furthest_done(source: IQSource | None) -> Stage | None:
    """The last stage, in pipeline order, that completed without problems."""
    last = None
    for stage in STAGES:
        if stage_status(stage, source).state != "done":
            break
        last = stage
    return last
