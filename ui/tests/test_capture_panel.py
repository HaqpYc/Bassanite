"""The Signal tab's and capture panel's verdicts (no hardware: conftest.py hides
the HackRF tools)."""
import pytest
from nicegui.testing import User

from bassanite.hackrf import DEVICE_LOCK, CaptureSettings
from state import state
from tabs import capture
from tabs.capture import bias_verdict, capture_summary


# --- Beginner-mode summary -----------------------------------------------------------

def test_summary_defaults_read_as_before():
    text = capture_summary(CaptureSettings())
    assert text.startswith("Records 60 s at 1575.42 MHz with the antenna powered (bias-tee on)")
    assert "medium gains" in text and "long enough to decode every satellite's orbit" in text
    assert "changed in Expert mode" not in text


def test_summary_says_when_the_antenna_is_not_powered():
    text = capture_summary(CaptureSettings(bias_tee=False))
    assert "power OFF" in text and "receives no satellites" in text
    assert "changed in Expert mode" in text


def test_summary_warns_when_too_short_for_step_4():
    text = capture_summary(CaptureSettings(seconds=10))
    assert text.startswith("Records 10 s") and "too short for step 4" in text
    assert "long enough" not in text and "changed in Expert mode" in text


# --- the server's recording (fake capture, no hardware) ------------------------------

class FakeCapture:
    """Same methods as bassanite.hackrf.Capture; `code` None means still running."""

    def __init__(self, settings, out_path, code=None, ok=True):
        self.settings, self.out_path = settings, out_path
        self.code, self.ok, self.cancelled = code, ok, False

    def start(self):
        self.out_path.write_bytes(b"x" * 10)
        return self

    def poll(self):
        return self.code

    def progress(self):
        return 0.5

    def succeeded(self):
        return self.ok

    def failure_message(self):
        return "Capture cancelled." if self.cancelled else "hackrf_transfer stopped early"

    def cancel(self):
        self.cancelled, self.code, self.ok = True, 1, False


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    """Isolate the module-level recording, state.source and DEVICE_LOCK."""
    monkeypatch.setattr(capture, "_current", None)
    monkeypatch.setattr(capture, "recordings_version", 0)
    monkeypatch.setattr(capture, "last_message", "")
    monkeypatch.setattr(capture, "last_became_source", False)
    monkeypatch.setattr(capture, "REPO_ROOT", tmp_path)
    old_source = state.source
    assert DEVICE_LOCK.acquire(blocking=False)       # record() holds it before starting

    def begin(code=None, ok=True, name="rec.bin"):
        return capture.begin_recording(
            CaptureSettings(seconds=1), None, tmp_path / name,
            capture_cls=lambda s, p: FakeCapture(s, p, code, ok))
    yield begin
    state.source = old_source
    if DEVICE_LOCK.locked():
        DEVICE_LOCK.release()


def test_nothing_happens_while_recording(recorder):
    r = recorder()
    assert capture.settle_recording() is False
    assert capture.current_recording() is r
    assert DEVICE_LOCK.locked() and capture.recordings_version == 0


def test_success_sets_source_releases_lock_and_bumps_counter(recorder, tmp_path):
    recorder(code=0)
    before = state.source
    assert capture.settle_recording() is True
    assert capture.current_recording() is None
    assert not DEVICE_LOCK.locked()
    assert capture.recordings_version == 1 and capture.last_became_source
    assert capture.last_message.startswith("✅ Saved")
    assert state.source is not before and state.source.path == tmp_path / "rec.bin"
    assert (tmp_path / "rec.json").is_file()          # the sidecar


def test_failure_deletes_partial_file_and_releases_lock(recorder, tmp_path):
    before = state.source
    recorder(code=1, ok=False)
    assert (tmp_path / "rec.bin").exists()
    capture.settle_recording()
    assert not (tmp_path / "rec.bin").exists() and not DEVICE_LOCK.locked()
    assert capture.last_message.startswith("❌") and not capture.last_became_source
    assert capture.recordings_version == 1 and state.source is before


def test_settling_twice_works_once(recorder):
    recorder(code=0)
    assert capture.settle_recording() is True
    assert capture.settle_recording() is False
    assert capture.recordings_version == 1


def test_lock_is_released_even_if_the_sidecar_fails(recorder, monkeypatch):
    def boom(*a):
        raise OSError("disk full")
    monkeypatch.setattr(capture, "write_sidecar", boom)
    recorder(code=0)
    capture.settle_recording()
    assert not DEVICE_LOCK.locked() and "disk full" in capture.last_message
    assert capture.recordings_version == 1


def test_failed_start_releases_the_lock(recorder, tmp_path):
    class Broken(FakeCapture):
        def start(self):
            raise FileNotFoundError("no hackrf_transfer")
    with pytest.raises(FileNotFoundError):
        capture.begin_recording(CaptureSettings(), None, tmp_path / "x.bin", capture_cls=Broken)
    assert not DEVICE_LOCK.locked() and capture.current_recording() is None


async def test_a_new_page_shows_a_recording_started_elsewhere(user: User, recorder):
    recorder()                                       # a recording another page started
    await user.open("/")
    await user.should_see("Recording…", retries=20)
    await user.should_not_see("Looking for a HackRF…")


@pytest.mark.parametrize("rise,icon", [(5.2, "✅"), (2.0, "⚠️"), (0.2, "❌"), (-1.0, "❌")])
def test_bias_verdict(rise, icon):
    assert bias_verdict(rise)[0] == icon


def test_burst_message_thresholds():
    from tabs.signal import burst_message
    assert burst_message({"burst_fraction": 0.11, "burst_max_db": 15}) is not None
    assert burst_message({"burst_fraction": 0.01, "burst_max_db": 15}) is None
    assert burst_message({"burst_fraction": 0.5, "burst_max_db": 1}) is None
    assert burst_message({}) is None
