"""The Signal tab's and capture panel's verdicts (no hardware: conftest.py hides
the HackRF tools)."""
import pytest

from tabs.capture import bias_verdict


@pytest.mark.parametrize("rise,icon", [(5.2, "✅"), (2.0, "⚠️"), (0.2, "❌"), (-1.0, "❌")])
def test_bias_verdict(rise, icon):
    assert bias_verdict(rise)[0] == icon


def test_burst_message_thresholds():
    from tabs.signal import burst_message
    assert burst_message({"burst_fraction": 0.11, "burst_max_db": 15}) is not None
    assert burst_message({"burst_fraction": 0.01, "burst_max_db": 15}) is None
    assert burst_message({"burst_fraction": 0.5, "burst_max_db": 1}) is None
    assert burst_message({}) is None
