"""The pipeline stages shown as tabs, and how to tell how far each one got.

Status is read from the files each stage writes (the file interface between
stages), so results produced by the command-line scripts show up here too.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from state import REPO_ROOT, IQSource  # first: sets sys.path

from bassanite.acquisition import read_results  # noqa: E402
from bassanite.provenance import read_meta, same_recording  # noqa: E402
from bassanite.results import recordings_with_results, results_dir  # noqa: E402
from bassanite.tracking import read_summary  # noqa: E402

MIN_SATS_FOR_FIX = 4


@dataclass(frozen=True)
class Stage:
    key: str           # tab name and folder name under ui/explain/
    number: int
    title: str
    tagline: str       # one line for the pipeline diagram
    built: bool        # is the backend for this stage implemented?
    hands_over: str    # what this stage passes to the next one
    # In the recording's results folder (bassanite/results.py); may contain globs.
    # The first one is the stage's main result, which carries the provenance file.
    output_files: tuple[str, ...] = ()


STAGES: tuple[Stage, ...] = (
    Stage("signal", 1, "Signal", "Antenna → raw IQ samples", True,
          "IQ samples: complex numbers at 4 million per second, scaled to about ±1, "
          "DC offset removed (common/iq_loader.py)."),
    Stage("acquisition", 2, "Find satellites", "Which satellites are overhead?", True,
          "For each satellite found: its PRN, code phase and Doppler, the starting "
          "point for tracking.",
          ("acquisition/scan_results.csv",)),
    Stage("tracking", 3, "Track", "Follow each one → data bits", True,
          "For each locked satellite: the 1 kHz prompt correlator, Doppler and code "
          "phase, and the 50 bit/s navigation bits.",
          ("tracking/tracking_summary.csv", "tracking/track_PRN*.npz")),
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


def output_paths(stage: Stage, source: IQSource) -> list[Path]:
    """The stage's result files for `source` that exist (globs expanded)."""
    folder = results_dir(source.path)
    return [p for pattern in stage.output_files for p in sorted(folder.glob(pattern))]


def _main_result(stage: Stage, source: IQSource) -> Path:
    return results_dir(source.path) / stage.output_files[0]


def rel_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _signal_status(source: IQSource | None) -> StageStatus:
    if source is None:
        return StageStatus("locked", "No signal loaded yet")
    problems = source.problems()
    if problems:
        return StageStatus("attention", problems[0])
    return StageStatus("done", source.describe())


def _acquisition_status(path: Path) -> StageStatus:
    if not path.is_file():
        return StageStatus("locked", "Not run yet")
    detected = [r for r in read_results(path) if r["detected"]]
    if not detected:
        return StageStatus("attention", "No satellite detected", f"from {rel_path(path)}")
    prns = ", ".join(str(r["prn"]) for r in detected)
    return StageStatus("done", f"{len(detected)} satellites detected (PRN {prns})",
                       f"from {rel_path(path)}")


def _tracking_status(path: Path) -> StageStatus:
    if not path.is_file():
        return StageStatus("locked", "Not run yet")
    rows = read_summary(path)
    locked = [r for r in rows if r["locked"]]
    if not locked:
        return StageStatus("attention", "No satellite locked", f"from {rel_path(path)}")
    cn0 = [r["mean_cn0_dbhz"] for r in locked]
    summary = (f"{len(locked)} of {len(rows)} satellites locked, "
               f"C/N0 {min(cn0):.0f}–{max(cn0):.0f} dB-Hz")
    if len(locked) < MIN_SATS_FOR_FIX:
        return StageStatus("attention", f"{summary} (a fix needs {MIN_SATS_FOR_FIX})",
                           f"from {rel_path(path)}")
    return StageStatus("done", summary, f"from {rel_path(path)}")


def result_is_for_other_recording(result_path: Path, source: IQSource) -> bool:
    """True when a result file was made from another recording than `source`.

    Results live in a folder named after their recording (bassanite/results.py),
    and carry a provenance file (<result>.meta.json, bassanite/provenance.py)
    naming the exact file. It catches two recordings with the same name in
    different folders. A result without provenance is taken to belong to its
    folder's recording.
    """
    meta = read_meta(result_path)
    return meta is not None and not same_recording(meta, source.path)


def results_source_name(result_path: Path) -> str:
    """File name of the recording a result file came from."""
    meta = read_meta(result_path)
    return Path(meta["source"]["path"]).name if meta else result_path.parent.parent.name


def stage_status(stage: Stage, source: IQSource | None) -> StageStatus:
    if stage.key == "signal":
        return _signal_status(source)
    if not stage.built:
        return StageStatus("locked", "Not built yet")
    if source is None:
        return StageStatus("locked", "No signal loaded yet")
    path = _main_result(stage, source)
    if stage.key == "acquisition":
        status = _acquisition_status(path)
    elif stage.key == "tracking":
        status = _tracking_status(path)
    else:
        raise ValueError(f"no status rule for stage {stage.key!r}")
    if status.state != "locked" and result_is_for_other_recording(path, source):
        return StageStatus("attention",
                           f"Results in {rel_path(path.parent)} are for "
                           f"{results_source_name(path)}, another file with the same "
                           f"name: run this step on {source.path.name}", status.detail)
    return status


@dataclass(frozen=True)
class PastRun:
    """One recording's folder under results/, for the Overview's run history."""
    name: str
    stages: tuple[Stage, ...]      # stages whose main result file is there
    created: str                   # latest result's date, "YYYY-MM-DD HH:MM"
    source: IQSource | None        # the recording, if it still exists


def results_history() -> list[PastRun]:
    """Every recording that has results, most recent first."""
    runs = []
    for name, folder in recordings_with_results():
        found, metas = [], []
        for stage in STAGES:
            if stage.output_files and (folder / stage.output_files[0]).is_file():
                found.append(stage)
                meta = read_meta(folder / stage.output_files[0])
                if meta:
                    metas.append(meta)
        if not found:
            continue
        metas.sort(key=lambda m: m.get("created", ""))
        source = None
        if metas:
            src = metas[-1].get("source", {})
            path = Path(src.get("path", ""))
            if path.is_file():
                source = IQSource(path, src.get("format", "int8"), float(src.get("fs", 4e6)))
        created = metas[-1].get("created", "").replace("T", " ")[:16] if metas else ""
        runs.append(PastRun(name, tuple(found), created, source))
    return runs


def furthest_done(source: IQSource | None) -> Stage | None:
    """The last stage, in pipeline order, that completed without problems."""
    last = None
    for stage in STAGES:
        if stage_status(stage, source).state != "done":
            break
        last = stage
    return last
