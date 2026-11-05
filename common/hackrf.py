#!/usr/bin/env python3
"""
hackrf.py -- record GPS L1 IQ from a HackRF One (receive only).

Wraps the official command-line tools (`hackrf_info`, `hackrf_transfer`), which
ship with radioconda on Windows and with the `hackrf` package on Linux/macOS:

  * find_tool()        locate the tools (PATH, $BASSANITE_HACKRF_DIR, radioconda)
  * device_info()      is a HackRF plugged in? serial, firmware
  * CaptureSettings    frequency, sample rate, gains, bias-tee, duration
  * Capture            one running `hackrf_transfer -r` (progress, cancel)
  * capture_blocking() a short capture, waited for
  * bias_tee_check()   noise floor with the antenna power off, then on

Every recording gets a JSON "sidecar" next to it (same name, .json) with the
settings used, so the file can be interpreted later.

Notes
-----
* Only receive (-r) commands are ever built. Nothing here transmits.
* On Windows, `hackrf_transfer -r -` (stdout) corrupts the data: stdout is in
  text mode, so every byte 0x0A gets a 0x0D inserted before it. Captures
  therefore always go to a file.
* Only one program can open the HackRF at a time: hold DEVICE_LOCK while using it.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.iq_loader import load_iq  # noqa: E402
from common.signal_view import burst_stats, psd  # noqa: E402

GPS_L1_HZ = 1575.42e6
LNA_STEPS = tuple(range(0, 41, 8))       # HackRF IF ("LNA") gain, dB
VGA_STEPS = tuple(range(0, 63, 2))       # HackRF baseband ("VGA") gain, dB
SAMPLE_RATES = (2e6, 4e6, 8e6, 10e6)
BYTES_PER_SAMPLE = 2                     # hackrf_transfer writes int8 I,Q

DEVICE_LOCK = threading.Lock()           # one user of the HackRF at a time
_active: set = set()                     # running Capture objects (for stop_all)


# --- finding the tools ------------------------------------------------------------

def _candidate_dirs():
    env = os.environ.get("BASSANITE_HACKRF_DIR")
    if env:
        yield Path(env)
    home = Path.home()
    program_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    for base in (home / "radioconda", home / "AppData" / "Local" / "radioconda",
                 program_data / "radioconda", home / "miniforge3", home / "mambaforge"):
        yield base / "Library" / "bin"       # conda layout on Windows
        yield base / "bin"                   # conda layout on Linux/macOS


def find_tool(name):
    """Path of a HackRF command-line tool, or None if it can't be found."""
    exe = name + (".exe" if os.name == "nt" else "")
    env = os.environ.get("BASSANITE_HACKRF_DIR")
    if env and (Path(env) / exe).is_file():
        return Path(env) / exe
    found = shutil.which(name)
    if found:
        return Path(found)
    for d in _candidate_dirs():
        if (d / exe).is_file():
            return d / exe
    return None


_NOT_FOUND = ("hackrf_transfer was not found. Install the HackRF tools (radioconda "
              "on Windows, the 'hackrf' package on Linux/macOS), or set "
              "BASSANITE_HACKRF_DIR to the folder that contains them.")


# --- device -----------------------------------------------------------------------------

@dataclass
class DeviceInfo:
    found: bool
    serial: str = ""
    board: str = ""
    firmware: str = ""
    message: str = ""


def device_info(timeout=10.0):
    """Ask hackrf_info whether a HackRF is connected."""
    tool = find_tool("hackrf_info")
    if tool is None:
        return DeviceInfo(False, message=_NOT_FOUND.replace("hackrf_transfer", "hackrf_info"))
    try:
        out = subprocess.run([str(tool)], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return DeviceInfo(False, message="hackrf_info did not answer in time.")
    text = out.stdout + out.stderr
    if "Found HackRF" not in text:
        last = [ln for ln in text.splitlines() if ln.strip()][-1:] or [""]
        return DeviceInfo(False, message="No HackRF found. Is it plugged in (and, under "
                                         f"WSL, attached with usbipd)? {last[0]}".strip())

    def grab(pattern):
        m = re.search(pattern, text)
        return m.group(1).strip() if m else ""
    return DeviceInfo(True, serial=grab(r"Serial number:\s*(\S+)"),
                      board=grab(r"Board ID Number:\s*\d+\s*\(([^)]*)\)"),
                      firmware=grab(r"Firmware Version:\s*(.+)"))


# --- capture settings ---------------------------------------------------------------------

@dataclass(frozen=True)
class CaptureSettings:
    seconds: float = 30.0
    fs: float = 4e6
    center_hz: float = GPS_L1_HZ
    lna_db: int = 32
    vga_db: int = 30
    amp: bool = False            # HackRF front-end RF amplifier
    bias_tee: bool = True        # DC power on the antenna port, for an active antenna

    @property
    def n_samples(self):
        return int(round(self.seconds * self.fs))

    @property
    def expected_bytes(self):
        return self.n_samples * BYTES_PER_SAMPLE

    def problems(self):
        issues = []
        if self.lna_db not in LNA_STEPS:
            issues.append(f"LNA gain must be one of {LNA_STEPS} dB.")
        if self.vga_db not in VGA_STEPS:
            issues.append("VGA gain must be 0 to 62 dB in 2 dB steps.")
        if not 2e6 <= self.fs <= 20e6:
            issues.append("Sample rate must be 2 to 20 MSPS.")
        if not 1e6 <= self.center_hz <= 6e9:
            issues.append("Frequency must be 1 MHz to 6 GHz.")
        if not 0 < self.seconds <= 600:
            issues.append("Duration must be between 0 and 600 s.")
        return issues

    def command(self, tool, out_path):
        """hackrf_transfer arguments for this capture (receive only)."""
        return [str(tool), "-r", str(out_path),
                "-f", str(int(round(self.center_hz))),
                "-s", str(int(round(self.fs))),
                "-p", "1" if self.bias_tee else "0",
                "-a", "1" if self.amp else "0",
                "-l", str(self.lna_db), "-g", str(self.vga_db),
                "-n", str(self.n_samples)]


def sidecar_path(iq_path):
    return Path(iq_path).with_suffix(".json")


def write_sidecar(iq_path, settings, device=None):
    meta = {"format": "int8", "fs": settings.fs, "center_hz": settings.center_hz,
            "settings": asdict(settings), "recorded": datetime.now().isoformat(timespec="seconds"),
            "device": asdict(device) if device else None}
    sidecar_path(iq_path).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def read_sidecar(iq_path):
    """Settings saved next to a recording, or None."""
    p = sidecar_path(iq_path)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (OSError, ValueError):
        return None


# --- running a capture ------------------------------------------------------------------------

@dataclass(eq=False)          # eq=False keeps it hashable (stored in _active)
class Capture:
    """One `hackrf_transfer -r` run. Call start(), then poll() until it returns."""
    settings: CaptureSettings
    out_path: Path
    proc: subprocess.Popen = field(default=None, repr=False)
    _log: object = field(default=None, repr=False)
    cancelled: bool = False

    def start(self):
        tool = find_tool("hackrf_transfer")
        if tool is None:
            raise FileNotFoundError(_NOT_FOUND)
        problems = self.settings.problems()
        if problems:
            raise ValueError(" ".join(problems))
        self.out_path = Path(self.out_path)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = tempfile.TemporaryFile()           # hackrf_transfer's messages
        self.proc = subprocess.Popen(self.settings.command(tool, self.out_path),
                                     stdout=subprocess.DEVNULL, stderr=self._log,
                                     stdin=subprocess.DEVNULL)
        _active.add(self)
        return self

    def poll(self):
        """None while running, else the exit code."""
        code = self.proc.poll()
        if code is not None:
            _active.discard(self)
        return code

    def progress(self):
        """Fraction of the requested samples written so far (0..1)."""
        try:
            size = self.out_path.stat().st_size
        except OSError:
            return 0.0
        return min(size / self.settings.expected_bytes, 1.0)

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def log_tail(self, lines=4):
        if self._log is None:
            return ""
        self._log.seek(0)
        text = self._log.read().decode(errors="replace")
        return "\n".join([ln for ln in text.splitlines() if ln.strip()][-lines:])

    def succeeded(self):
        return (not self.cancelled and self.proc.poll() == 0
                and self.out_path.is_file()
                and self.out_path.stat().st_size == self.settings.expected_bytes)

    def failure_message(self):
        if self.cancelled:
            return "Capture cancelled."
        tail = self.log_tail()
        if "Resource busy" in tail or "busy" in tail.lower():
            return "The HackRF is busy: another program (SDR#, GNU Radio, ...) is using it."
        if "not found" in tail.lower() or "No HackRF" in tail:
            return "No HackRF found. Is it plugged in?"
        got = self.out_path.stat().st_size if self.out_path.is_file() else 0
        return (f"hackrf_transfer stopped early ({got:,} of "
                f"{self.settings.expected_bytes:,} bytes). Last messages:\n{tail}")


def stop_all():
    """Stop every running capture (e.g. when the app shuts down)."""
    for cap in list(_active):
        cap.cancel()


def capture_blocking(settings, out_path, timeout=None):
    """Record and wait. Returns (ok, message). The caller holds DEVICE_LOCK."""
    cap = Capture(settings, out_path).start()
    deadline = time.monotonic() + (timeout or settings.seconds + 15)
    while cap.poll() is None:
        if time.monotonic() > deadline:
            cap.cancel()
            cap.proc.wait(5)
            return False, "hackrf_transfer did not finish in time."
        time.sleep(0.02)
    return (True, "") if cap.succeeded() else (False, cap.failure_message())


# --- bias-tee check -----------------------------------------------------------------------------

def band_noise_db(x, fs, half_band_hz=1.0e6, nfft=1024):
    """Average noise power (dBFS per bin) across +/-half_band_hz around the
    centre, skipping the bins next to 0 Hz where DC residue sits."""
    freqs, power_db = psd(x, fs, nfft)
    band = (np.abs(freqs) <= half_band_hz) & (np.abs(freqs) > 2 * fs / nfft)
    return float(10 * np.log10(np.mean(10 ** (power_db[band] / 10))))


def steady_noise_db(x, fs, nfft=1024, ffts_per_block=4):
    """The usual noise floor, ignoring bursts: band_noise_db() of each ~1 ms
    block, then the median over blocks. Intermittent interference raises a few
    blocks but not the median."""
    n = nfft * ffts_per_block
    levels = [band_noise_db(x[i:i + n], fs, nfft=nfft) for i in range(0, len(x) - n + 1, n)]
    return float(np.median(levels))


def bias_tee_check(settings, workdir=None, seconds=0.25):
    """Capture twice with the same gains, antenna power off then on.

    An active antenna's LNA amplifies the sky noise before the HackRF, so when
    the bias-tee really powers it the noise floor rises. The floor is the
    median over ~1 ms blocks, so interference bursts picked up by the powered
    antenna don't inflate it; they are reported separately as `bursts`.
    The caller holds DEVICE_LOCK.
    """
    workdir = Path(workdir or tempfile.gettempdir())
    result = {}
    for label, on in (("off", False), ("on", True)):
        s = CaptureSettings(seconds=seconds, fs=settings.fs, center_hz=settings.center_hz,
                            lna_db=settings.lna_db, vga_db=settings.vga_db,
                            amp=settings.amp, bias_tee=on)
        path = workdir / f"bassanite_biascheck_{label}.bin"
        ok, msg = capture_blocking(s, path)
        if not ok:
            raise RuntimeError(msg)
        x = load_iq(path, "int8", s.fs)
        result[label] = {"noise_db": steady_noise_db(x, s.fs), "psd": psd(x, s.fs, 1024),
                         "bursts": burst_stats(x, s.fs)}
        path.unlink(missing_ok=True)
    result["rise_db"] = result["on"]["noise_db"] - result["off"]["noise_db"]
    return result


def recording_path(folder):
    """recordings/capture_YYYYmmdd_HHMMSS.bin"""
    return Path(folder) / f"capture_{datetime.now():%Y%m%d_%H%M%S}.bin"


if __name__ == "__main__":
    info = device_info()
    print(info)
    if info.found:
        with DEVICE_LOCK:
            r = bias_tee_check(CaptureSettings())
        print(f"noise floor: bias-tee off {r['off']['noise_db']:.1f} dBFS, "
              f"on {r['on']['noise_db']:.1f} dBFS, rise {r['rise_db']:+.1f} dB")
        b = r["on"]["bursts"]
        print(f"bursts with antenna powered: {100 * b['burst_fraction']:.0f} % of 1 ms "
              f"blocks, strongest {b['burst_max_db']:+.1f} dB above the floor")
