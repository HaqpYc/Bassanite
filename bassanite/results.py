"""
results.py -- where each stage writes its result files: one folder per recording.

    results/<recording name>/acquisition/scan_results.csv (+ .meta.json, scan_plot.png)
    results/<recording name>/tracking/tracking_summary.csv (+ .meta.json),
                                      track_PRNnn.npz, track_PRNnn.png

The folder is named after the recording file (without its extension), so the
results of several recordings live side by side, and running a stage on one never
touches another's. `results/` is git-ignored: results are regenerated from the
recordings. Each main result file carries a provenance file (provenance.py) naming
the exact recording, which catches two recordings that share a name.
"""
from pathlib import Path

from bassanite.acquisition import SCAN_CSV

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_ROOT = REPO_ROOT / "results"      # tests redirect this to a temporary folder

STAGE_FOLDERS = ("acquisition", "tracking")


def results_dir(iq_path):
    """The folder holding every stage's results for the recording at `iq_path`."""
    return RESULTS_ROOT / Path(iq_path).stem


def stage_dir(iq_path, stage):
    """The folder of one stage ("acquisition", "tracking", ...) for that recording."""
    return results_dir(iq_path) / stage


def scan_csv(iq_path):
    """Where stage 02 writes, and stage 03 reads, the scan results."""
    return stage_dir(iq_path, "acquisition") / SCAN_CSV


def tracking_dir(iq_path):
    return stage_dir(iq_path, "tracking")


def recordings_with_results():
    """Folders under results/, newest first: [(name, folder)]."""
    if not RESULTS_ROOT.is_dir():
        return []
    folders = [p for p in RESULTS_ROOT.iterdir() if p.is_dir()]
    folders.sort(key=lambda p: max((f.stat().st_mtime for f in p.rglob("*")),
                                   default=p.stat().st_mtime), reverse=True)
    return [(p.name, p) for p in folders]
