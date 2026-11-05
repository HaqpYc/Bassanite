"""Tests for common/hackrf.py and the capture panel logic. No hardware needed:
conftest.py hides the HackRF tools from every test."""
import numpy as np
import pytest

import common.hackrf as hackrf
from common.hackrf import (CaptureSettings, band_noise_db, read_sidecar,
                           recording_path, write_sidecar)
from tabs.capture import bias_verdict


def test_command_is_receive_only_and_complete(tmp_path):
    s = CaptureSettings(seconds=2, fs=4e6, lna_db=16, vga_db=20, amp=True, bias_tee=False)
    cmd = s.command("hackrf_transfer", tmp_path / "x.bin")
    assert "-t" not in cmd and "-c" not in cmd          # never transmit
    args = dict(zip(cmd[1::2], cmd[2::2]))
    assert args == {"-r": str(tmp_path / "x.bin"), "-f": "1575420000", "-s": "4000000",
                    "-p": "0", "-a": "1", "-l": "16", "-g": "20", "-n": "8000000"}
    assert s.expected_bytes == 16_000_000


@pytest.mark.parametrize("change,word", [({"lna_db": 12}, "LNA"), ({"vga_db": 63}, "VGA"),
                                         ({"fs": 1e6}, "Sample rate"),
                                         ({"seconds": 0}, "Duration")])
def test_invalid_settings_are_reported(change, word):
    problems = CaptureSettings(**change).problems()
    assert problems and word in problems[0]


def test_default_settings_are_valid():
    assert CaptureSettings().problems() == []


def test_sidecar_round_trip(tmp_path):
    path = recording_path(tmp_path)
    assert path.name.startswith("capture_") and path.suffix == ".bin"
    write_sidecar(path, CaptureSettings(fs=8e6), hackrf.DeviceInfo(True, serial="abc"))
    meta = read_sidecar(path)
    assert meta["fs"] == 8e6 and meta["format"] == "int8"
    assert meta["settings"]["lna_db"] == 32 and meta["device"]["serial"] == "abc"
    assert read_sidecar(tmp_path / "other.bin") is None


def test_band_noise_ignores_dc_and_measures_the_band():
    rng = np.random.default_rng(3)
    n = 1024 * 200
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * 0.01
    louder = noise * np.sqrt(2)                          # +3 dB
    assert band_noise_db(louder, 4e6) - band_noise_db(noise, 4e6) == pytest.approx(3.01, abs=0.05)
    with_dc = noise + 0.5                                # huge DC offset
    assert band_noise_db(with_dc, 4e6) == pytest.approx(band_noise_db(noise, 4e6), abs=0.3)


def _noise_with_bursts(rng, fs=4e6, seconds=0.25, burst_every_ms=10, burst_gain=5.0):
    n = int(fs * seconds)
    x = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * 0.01
    block = int(fs * 1e-3)
    for start in range(0, n, burst_every_ms * block):      # 1 ms burst every 10 ms
        x[start:start + block] *= burst_gain
    return x


def test_steady_noise_ignores_bursts_and_burst_stats_sees_them():
    rng = np.random.default_rng(4)
    clean = _noise_with_bursts(rng, burst_gain=1.0)
    bursty = _noise_with_bursts(rng, burst_gain=5.0)        # +14 dB bursts, 10 % of time
    assert hackrf.steady_noise_db(bursty, 4e6) == pytest.approx(
        hackrf.steady_noise_db(clean, 4e6), abs=0.3)
    assert band_noise_db(bursty, 4e6) > band_noise_db(clean, 4e6) + 5   # plain mean is fooled
    from common.signal_view import burst_stats
    b = burst_stats(bursty, 4e6)
    assert b["burst_fraction"] == pytest.approx(0.1, abs=0.02)
    assert b["burst_max_db"] == pytest.approx(14, abs=1)
    assert burst_stats(clean, 4e6)["burst_fraction"] == 0.0


def test_burst_message_thresholds():
    from tabs.signal import burst_message
    assert burst_message({"burst_fraction": 0.11, "burst_max_db": 15}) is not None
    assert burst_message({"burst_fraction": 0.01, "burst_max_db": 15}) is None
    assert burst_message({"burst_fraction": 0.5, "burst_max_db": 1}) is None
    assert burst_message({}) is None


@pytest.mark.parametrize("rise,icon", [(5.2, "✅"), (2.0, "⚠️"), (0.2, "❌"), (-1.0, "❌")])
def test_bias_verdict(rise, icon):
    assert bias_verdict(rise)[0] == icon


def test_missing_tools_are_reported():
    info = hackrf.device_info()                          # tools hidden by conftest
    assert not info.found and "not found" in info.message
    with pytest.raises(FileNotFoundError):
        hackrf.Capture(CaptureSettings(), "x.bin").start()
