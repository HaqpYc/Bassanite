"""The "Live HackRF" card of the Signal tab: device status, live preview,
recording and the bias-tee check. Receive only."""
from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from nicegui import context, run, ui

from state import REPO_ROOT, IQSource, state

from bassanite.tracking import MIN_SECONDS_FOR_EPHEMERIS
from bassanite.hackrf import (DEVICE_LOCK, LNA_STEPS, SAMPLE_RATES, Capture,  # noqa: I001
                           CaptureSettings, DeviceInfo, bias_tee_check, capture_blocking,
                           device_info, recording_path, write_sidecar)
from components import explain_panel
from plots import figure
from tabs.signal import burst_message

RECORDINGS_DIR = REPO_ROOT / "recordings"
PREVIEW_SECONDS = 0.1
PREVIEW_FILE = Path(tempfile.gettempdir()) / "bassanite_preview.bin"
BIAS_RISE_OK_DB, BIAS_RISE_WEAK_DB = 3.0, 1.0


def bias_verdict(rise_db: float) -> tuple[str, str]:
    """(icon, text) explaining a bias-tee check result."""
    if rise_db >= BIAS_RISE_OK_DB:
        return "✅", (f"The noise floor rises by {rise_db:.1f} dB when the antenna is powered: "
                     "the bias-tee is powering the antenna's amplifier.")
    if rise_db >= BIAS_RISE_WEAK_DB:
        return "⚠️", (f"The noise floor rises by only {rise_db:.1f} dB. The amplifier may be "
                     "weakly powered, or the antenna has little gain.")
    return "❌", (f"No clear change ({rise_db:+.1f} dB): nothing is being powered. Is the "
                 "antenna an active one, and does this HackRF's bias-tee work?")


def capture_summary(s: CaptureSettings) -> str:
    """The Beginner-mode sentence describing what Record will do with these settings."""
    default = CaptureSettings()
    power = ("with the antenna powered (bias-tee on)" if s.bias_tee
             else "with the antenna power OFF (bias-tee off)")
    gains = f"LNA {s.lna_db} dB, VGA {s.vga_db} dB" + (", RF amplifier on" if s.amp else "")
    medium = "medium " if (s.lna_db, s.vga_db, s.amp) == \
        (default.lna_db, default.vga_db, default.amp) else ""
    if s.seconds >= MIN_SECONDS_FOR_EPHEMERIS:
        length = "long enough to decode every satellite's orbit (step 4)"
    else:
        length = (f"too short for step 4, which needs at least "
                  f"{MIN_SECONDS_FOR_EPHEMERIS:.0f} s")
    text = (f"Records {s.seconds:g} s at {s.center_hz / 1e6:.2f} MHz {power} and "
            f"{medium}gains ({gains}): {length}.")
    if not s.bias_tee:
        text += " ⚠️ Without power, an active antenna receives no satellites."
    text += " Put the antenna outdoors with a clear view of the sky."
    if s != default:
        text += " These settings were changed in Expert mode."
    return text


# The server's recording, shared by every browser tab (like tabs.tracking._current): it
# keeps going if the page reloads, and whichever page notices that it ended settles it.
# recordings_version tells the pages that a recording ended; last_message is its outcome.
@dataclass(eq=False)
class Recording:
    capture: Capture
    settings: CaptureSettings
    path: Path
    device: DeviceInfo | None


_current: Recording | None = None
recordings_version = 0
last_message = ""
last_became_source = False


def current_recording() -> Recording | None:
    return _current


def begin_recording(settings: CaptureSettings, device: DeviceInfo | None,
                    path: Path, capture_cls=Capture) -> Recording:
    """Start recording. The caller holds DEVICE_LOCK; it is released here if starting fails."""
    global _current
    try:
        capture = capture_cls(settings, path).start()
    except BaseException:
        DEVICE_LOCK.release()
        raise
    _current = Recording(capture, settings, path, device)
    return _current


def _saved_message(path: Path) -> str:
    try:
        shown = path.relative_to(REPO_ROOT)
    except ValueError:
        shown = path
    return (f"✅ Saved {shown} ({path.stat().st_size / 1e6:.0f} MB). It is now the "
            "signal being analysed; run the next steps on it.")


def settle_recording() -> bool:
    """Once the current recording's process has ended: save it as the source (or delete
    the partial file), release the HackRF and tell the pages. Safe to call twice.
    Returns True if it settled one."""
    global _current, recordings_version, last_message, last_became_source
    r = _current
    if r is None or r.capture.poll() is None:
        return False
    _current = None
    message, became_source = "", False
    try:
        if r.capture.succeeded():
            try:
                write_sidecar(r.path, r.settings, r.device)
                state.source = IQSource(r.path, "int8", r.settings.fs, r.settings.center_hz)
                message, became_source = _saved_message(r.path), True
            except Exception as err:       # e.g. disk full: still free the HackRF below
                message = f"❌ The recording could not be saved: {err}"
        else:
            message = f"❌ {r.capture.failure_message()}"
            r.path.unlink(missing_ok=True)   # don't leave a partial recording behind
    finally:
        DEVICE_LOCK.release()
        last_message, last_became_source = message, became_source
        recordings_version += 1
    return True


class CapturePanel:
    """One instance per browser tab. `on_recorded` is called after a new recording
    becomes the current source."""

    def __init__(self, signal_tab, on_recorded: Callable[[], None]) -> None:
        self.signal_tab = signal_tab
        self.on_recorded = on_recorded
        self.settings = CaptureSettings()
        self.device: DeviceInfo | None = None
        self.previewing = False
        self.busy: str | None = None          # "preview" | "record" | "bias"
        self._seen_version = recordings_version   # don't replay an older recording's message
        self._detecting = False
        self._waiting_for_device = False

    # --- layout --------------------------------------------------------------------------

    def build(self) -> None:
        with ui.card().classes("w-full").props("flat bordered"):
            with ui.row().classes("w-full items-center gap-2"):
                ui.icon("sensors", size="sm")
                ui.label("Live HackRF").classes("text-subtitle1 font-bold")
                self.device_label = ui.label("Looking for a HackRF…").classes("text-sm text-grey-8")
                ui.space()
                ui.button(icon="refresh", on_click=self.detect) \
                    .props("flat round dense").tooltip("Look for the HackRF again")
            self.summary_label = ui.label().classes("text-sm text-grey-8") \
                .bind_visibility_from(state, "mode", value="beginner")
            self._build_settings()
            with ui.row().classes("items-center gap-2"):
                self.preview_button = ui.button("Live preview", icon="play_arrow",
                                                on_click=self.toggle_preview).props("no-caps")
                self.record_button = ui.button(icon="fiber_manual_record", on_click=self.record) \
                    .props("no-caps color=red")
                self.bias_button = ui.button("Check bias-tee", icon="power",
                                             on_click=self.check_bias).props("no-caps outline")
                self.cancel_button = ui.button("Cancel", icon="close", on_click=self.cancel) \
                    .props("no-caps flat")
            self.progress = ui.linear_progress(value=0, show_value=False).props("instant-feedback")
            self.status = ui.label().classes("text-sm whitespace-pre-line")
            self.bias_result = ui.column().classes("w-full gap-1")
            explain_panel("signal", "capture")
        self._refresh_buttons()
        context.client.on_disconnect(self._stop_preview)
        ui.timer(0.1, self.detect, once=True)
        ui.timer(0.25, self._tick)

    def _build_settings(self) -> None:
        s = self.settings
        with ui.row().classes("w-full items-end gap-4") \
                .bind_visibility_from(state, "mode", value="expert"):
            ui.number("Duration", value=s.seconds, min=0.1, max=600, step=1, suffix="s",
                      on_change=lambda e: self._set(seconds=float(e.value or 60))) \
                .classes("w-28")
            ui.select({int(r): f"{r / 1e6:g} MSPS" for r in SAMPLE_RATES}, value=int(s.fs),
                      label="Sample rate",
                      on_change=lambda e: self._set(fs=float(e.value))).classes("w-32")
            ui.select(list(LNA_STEPS), value=s.lna_db, label="LNA gain (dB)",
                      on_change=lambda e: self._set(lna_db=int(e.value))).classes("w-32")
            with ui.column().classes("gap-0 w-48"):
                vga_label = ui.label(f"VGA gain: {s.vga_db} dB").classes("text-xs text-grey-8")
                ui.slider(min=0, max=62, step=2, value=s.vga_db,
                          on_change=lambda e: (self._set(vga_db=int(e.value)),
                                               vga_label.set_text(f"VGA gain: {int(e.value)} dB")))
            ui.switch("RF amplifier", value=s.amp, on_change=lambda e: self._set(amp=e.value))
            ui.switch("Antenna power (bias-tee)", value=s.bias_tee,
                      on_change=lambda e: self._set(bias_tee=e.value))
        ui.label("Changes apply to the live preview immediately.").classes("text-xs text-grey-7") \
            .bind_visibility_from(state, "mode", value="expert")

    def _set(self, **changes) -> None:
        self.settings = replace(self.settings, **changes)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        ready = bool(self.device and self.device.found)
        idle = self.busy is None
        self.summary_label.set_text(capture_summary(self.settings))
        self.record_button.set_text(f"Record {self.settings.seconds:g} s "
                                    f"({self.settings.expected_bytes / 1e6:,.0f} MB)")
        self.record_button.set_enabled(ready and idle)
        self.bias_button.set_enabled(ready and idle)
        self.preview_button.set_enabled(ready and self.busy in (None, "preview"))
        self.preview_button.set_text("Stop preview" if self.previewing else "Live preview")
        self.preview_button.props(f"icon={'stop' if self.previewing else 'play_arrow'}")
        self.cancel_button.set_visibility(self.busy == "record")
        self.progress.set_visibility(self.busy == "record")

    def _set_busy(self, kind: str | None) -> None:
        self.busy = kind
        self._refresh_buttons()

    # --- device ------------------------------------------------------------------------------

    async def detect(self) -> None:
        if DEVICE_LOCK.locked() or self._detecting:
            return                      # hackrf_info can't open a device that is in use
        self._detecting = True
        try:
            self.device_label.set_text("Looking for a HackRF…")
            self.device = await run.io_bound(device_info)
        finally:
            self._detecting = False
        d = self.device
        if d.found:
            self.device_label.set_text(f"✅ {d.board or 'HackRF'} · serial …{d.serial[-6:]} · "
                                       f"firmware {d.firmware}")
        else:
            self.device_label.set_text(f"❌ {d.message}")
        self._refresh_buttons()

    # --- following the server's recording ----------------------------------------------------

    async def _tick(self) -> None:
        """Cheap, every 0.25 s on every page: show the recording whichever page started
        it, settle it when it ended, and notice when the HackRF is used by another page."""
        r = current_recording()
        if r is not None and r.capture.poll() is not None:
            settle_recording()
            r = current_recording()
        if r is not None:
            self._show_recording(r)
            return
        if self.busy == "record":
            self._set_busy(None)
        if self._seen_version != recordings_version:
            self._seen_version = recordings_version
            self.status.set_text(last_message)
            if last_became_source:
                self.on_recorded()
            self._refresh_buttons()
        if DEVICE_LOCK.locked():
            if self.busy is None:       # another page's preview or bias-tee check
                self._waiting_for_device = True
                if self.device is None:
                    self.device_label.set_text("The HackRF is in use by another page.")
        elif self._waiting_for_device:
            self._waiting_for_device = False
            await self.detect()

    def _show_recording(self, r: Recording) -> None:
        if self.busy != "record":
            self._waiting_for_device = True      # look for the device when it ends
            self._set_busy("record")
            if self.device is None:
                self.device_label.set_text("The HackRF is recording.")
        frac = r.capture.progress()
        self.progress.set_value(frac)
        self.status.set_text(f"Recording… {frac * r.settings.seconds:.0f} of "
                             f"{r.settings.seconds:g} s → {r.path.name}")

    # --- live preview ------------------------------------------------------------------------

    async def toggle_preview(self) -> None:
        if self.previewing:
            self._stop_preview()
            return
        if not DEVICE_LOCK.acquire(blocking=False):
            ui.notify("The HackRF is busy.", type="warning")
            return
        self.previewing = True
        self._set_busy("preview")
        self.status.set_text("Live preview: showing the last 0.1 s, refreshed continuously. "
                             "All plots below follow the antenna.")
        self.signal_tab.begin_preview()
        try:
            while self.previewing:
                s = replace(self.settings, seconds=PREVIEW_SECONDS)
                ok, msg = await run.io_bound(capture_blocking, s, PREVIEW_FILE)
                if not ok:
                    self.status.set_text(f"❌ {msg}")
                    break
                await self.signal_tab.show_preview(
                    IQSource(PREVIEW_FILE, "int8", s.fs, s.center_hz))
        finally:
            self.previewing = False
            DEVICE_LOCK.release()
            self._set_busy(None)
            self.signal_tab.end_preview()
            if self.status.text.startswith("Live preview"):
                self.status.set_text("")

    def _stop_preview(self) -> None:
        self.previewing = False

    # --- recording ---------------------------------------------------------------------------

    async def record(self) -> None:
        if not DEVICE_LOCK.acquire(blocking=False):
            ui.notify("The HackRF is busy.", type="warning")
            return
        try:
            r = begin_recording(self.settings, self.device, recording_path(RECORDINGS_DIR))
        except (FileNotFoundError, ValueError) as err:
            self.status.set_text(f"❌ {err}")
            return
        self._show_recording(r)       # the timer follows it from here, on every page

    def cancel(self) -> None:
        r = current_recording()
        if r is not None:
            r.capture.cancel()

    # --- bias-tee check ----------------------------------------------------------------------

    async def check_bias(self) -> None:
        if not DEVICE_LOCK.acquire(blocking=False):
            ui.notify("The HackRF is busy.", type="warning")
            return
        self._set_busy("bias")
        self.status.set_text("Checking: recording 0.25 s with the antenna power off, then on…")
        try:
            result = await run.io_bound(bias_tee_check, self.settings)
        except (RuntimeError, FileNotFoundError, ValueError) as err:
            self.status.set_text(f"❌ {err}")
            return
        finally:
            DEVICE_LOCK.release()
            self._set_busy(None)
        self.status.set_text("")
        self._show_bias_result(result)

    def _show_bias_result(self, r: dict) -> None:
        icon, text = bias_verdict(r["rise_db"])
        self.bias_result.clear()
        with self.bias_result:
            ui.label(f"{icon} {text}").classes("text-sm font-medium")
            ui.label(f"Steady noise floor across ±1 MHz (median over 1 ms blocks): "
                     f"{r['off']['noise_db']:.1f} dBFS with the antenna power off, "
                     f"{r['on']['noise_db']:.1f} dBFS with it on.") \
                .classes("text-xs text-grey-8")
            burst = burst_message(r["on"]["bursts"])
            if burst:
                ui.label(f"⚠️ With the antenna powered: {burst[1]}").classes("text-sm")
            data = []
            for label, key in (("antenna power off", "off"), ("antenna power on", "on")):
                freqs, power = r[key]["psd"]
                data.append({"type": "scatter", "mode": "lines", "name": label,
                             "x": ((self.settings.center_hz + freqs) / 1e6).tolist(),
                             "y": np.round(power, 2).tolist(), "line": {"width": 1.2}})
            ui.plotly(figure(data, height=240, margin={"l": 60, "r": 20, "t": 10, "b": 40},
                             xaxis={"title": "Frequency (MHz)"},
                             yaxis={"title": "Power per bin (dBFS)"})) \
                .classes("w-full")
