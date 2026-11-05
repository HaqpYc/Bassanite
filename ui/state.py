"""Application state for the Bassanite UI.

Bassanite is a local, single-user app, so one module-level `state` object is
shared by every browser tab that connects to the server. A source loaded in one
tab shows up in the others.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.iq_loader import BYTES_PER_SAMPLE, normalize_format  # noqa: E402

GPS_L1_HZ = 1575.42e6
DEFAULT_FS = 4e6
DEFAULT_RECORDING = REPO_ROOT / "recordings" / "capture.bin"

# File extensions whose sample format can be guessed. hackrf_transfer writes int8;
# GNU Radio / GNSS-SDR file sinks write gr_complex (complex64).
_EXT_FORMATS = {
    ".bin": "int8", ".raw": "int8", ".i8": "int8", ".cs8": "int8",
    ".dat": "complex64", ".cf32": "complex64", ".fc32": "complex64",
    ".cfile": "complex64",
}


def guess_format(path: str | Path) -> str:
    """Guess the IQ sample format from the file extension (default: int8)."""
    return _EXT_FORMATS.get(Path(path).suffix.lower(), "int8")


@dataclass
class IQSource:
    """A raw IQ recording and how to interpret it."""

    path: Path
    fmt: str = "int8"
    fs: float = DEFAULT_FS
    center_hz: float = GPS_L1_HZ

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.fmt = normalize_format(self.fmt)

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.exists else 0

    @property
    def n_samples(self) -> int:
        return self.size_bytes // BYTES_PER_SAMPLE[self.fmt]

    @property
    def duration_s(self) -> float:
        return self.n_samples / self.fs if self.fs > 0 else 0.0

    def problems(self) -> list[str]:
        """Reasons this source can't be used as is (empty list = fine)."""
        if not self.exists:
            return [f"File not found: {self.path}"]
        issues = []
        if self.size_bytes == 0:
            issues.append("The file is empty.")
        elif self.size_bytes % BYTES_PER_SAMPLE[self.fmt]:
            issues.append(
                f"File size is not a multiple of {BYTES_PER_SAMPLE[self.fmt]} bytes, "
                f"so it is probably not {self.fmt}.")
        if self.fs <= 0:
            issues.append("Sample rate must be positive.")
        return issues

    def describe(self) -> str:
        """One-line summary, e.g. 'capture.bin · int8 · 4 MSPS · 30.0 s'."""
        return (f"{self.path.name} · {self.fmt} · {self.fs / 1e6:g} MSPS · "
                f"{self.duration_s:.1f} s")


@dataclass
class AppState:
    source: IQSource | None = None
    mode: str = "beginner"          # "beginner" or "expert"
    show_math: bool = False          # global "Show the math" toggle

    @property
    def expert(self) -> bool:
        return self.mode == "expert"


def default_source() -> IQSource | None:
    """The bundled reference recording, if it is present."""
    return IQSource(DEFAULT_RECORDING) if DEFAULT_RECORDING.is_file() else None


state = AppState(source=default_source())
