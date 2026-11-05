"""Tests for the Signal tab's analysis and level advice (no browser)."""
import numpy as np
import pytest
from nicegui import ui
from nicegui.testing import User

from state import IQSource, state
from tabs.signal import Settings, analyse, level_messages

FS = 4e6


@pytest.fixture
def recording(tmp_path):
    """A 50 ms int8 recording: Gaussian noise (sigma 1 code) with a DC offset."""
    rng = np.random.default_rng(2)
    n = int(FS * 0.05)
    raw = np.empty(2 * n, dtype=np.int8)
    raw[0::2] = np.round(rng.normal(3.5, 1.0, n))
    raw[1::2] = np.round(rng.normal(-3.5, 1.0, n))
    path = tmp_path / "noise.bin"
    raw.tofile(path)
    return IQSource(path, "int8", FS)


def test_analyse_produces_every_plot_input(recording):
    r = analyse(recording, Settings(window_ms=10, nfft=1024))
    freqs, power = r["psd"]
    assert len(freqs) == len(power) == 1024
    assert len(r["time"][0]) == 200
    assert r["stats"]["clip_fraction"] == 0
    assert r["stats"]["dc_i"] == pytest.approx(3.5, abs=0.1)
    xc, yc, z = r["density"]
    counts = np.nan_to_num(z)
    assert counts.shape == (len(yc), len(xc))
    assert counts.sum() >= 0.999 * 40000              # +/-4 sigma holds ~all samples
    assert abs(np.average(xc, weights=counts.sum(0))) < 0.1   # DC removed: centred
    assert xc.min() <= 0 <= xc.max() and yc.min() <= 0 <= yc.max()  # origin shown


def test_analyse_keeps_dc_when_asked(recording):
    r = analyse(recording, Settings(window_ms=10, remove_dc=False))
    xc, _, z = r["density"]
    counts = np.nan_to_num(z)
    assert np.average(xc, weights=counts.sum(0)) == pytest.approx(3.5, abs=0.1)
    freqs, power = r["psd"]
    assert freqs[np.argmax(power)] == 0.0      # the DC spike dominates


def test_window_is_clamped_to_the_recording(recording):
    r = analyse(recording, Settings(start_s=10.0, window_ms=10))
    assert r["start"] == pytest.approx(0.04)


def test_level_messages():
    base = {"std_i": 1.0, "std_q": 1.0, "dc_i": 3.5, "dc_q": -3.5,
            "clip_fraction": 0.0, "levels_used": 17}
    kinds = [k for k, _ in level_messages(base, True)]
    assert kinds == ["info", "info"]           # few levels used + DC offset
    clipped = {**base, "std_i": 40, "std_q": 40, "clip_fraction": 0.05}
    assert level_messages(clipped, True)[0][0] == "warning"
    healthy = {**base, "std_i": 20, "std_q": 20, "dc_i": 0.1, "dc_q": 0.1}
    assert level_messages(healthy, True) == [level_messages(healthy, True)[0]]
    assert level_messages(healthy, True)[0][0] == "positive"


async def test_signal_tab_shows_plots_and_panels(user: User, recording) -> None:
    state.source = recording
    await user.open("/")
    for text in ["Spectrum", "Waterfall: spectrum over the whole recording",
                 "I and Q against time", "IQ plane", "ADC levels used",
                 "Spectrum: where the energy is", "ADC levels: is the gain right?"]:
        await user.should_see(text)
    panel = next(p for p in user.find(kind=ui.tab_panel).elements
                 if p.props["name"] == "signal")
    assert sum(isinstance(e, ui.plotly) for e in panel.descendants()) == 5
