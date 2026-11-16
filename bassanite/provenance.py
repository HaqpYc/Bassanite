"""
provenance.py -- remember which recording produced a stage's result files.

Each stage writes a small JSON file next to its main result file, e.g.
results/<recording>/acquisition/scan_results.csv -> .../scan_results.meta.json:

    {"stage": "acquisition", "created": "...", "source": {"path": ..., "format": ...,
     "fs": ..., "size_bytes": ...}, "settings": {...}}

so a reader (the web UI, a later stage, a person) can tell whether the results
belong to the recording at hand. The recording is identified by its absolute
path and size: cheap, and enough to catch "these results are for another file".
"""
import json
import os
from datetime import datetime
from pathlib import Path


def meta_path(result_path):
    p = Path(result_path)
    return p.with_name(p.stem + ".meta.json")


def source_info(iq_path, fmt, fs):
    p = Path(iq_path).resolve()
    return {"path": str(p), "format": fmt, "fs": float(fs),
            "size_bytes": p.stat().st_size if p.is_file() else None}


def write_meta(result_path, stage, iq_path, fmt, fs, settings):
    """Write the provenance file for `result_path`. Returns its path."""
    meta = {"stage": stage, "created": datetime.now().isoformat(timespec="seconds"),
            "source": source_info(iq_path, fmt, fs), "settings": settings}
    path = meta_path(result_path)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return path


def read_meta(result_path):
    """The provenance of `result_path`, or None if it has none (older results)."""
    path = meta_path(result_path)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        return None


def same_recording(meta, iq_path):
    """Whether provenance `meta` describes the recording at `iq_path`."""
    p = Path(iq_path).resolve()
    src = meta.get("source", {})
    if os.path.normcase(src.get("path", "")) != os.path.normcase(str(p)):
        return False
    size = src.get("size_bytes")
    return size is None or (p.is_file() and p.stat().st_size == size)
