"""Dialog for choosing the raw IQ recording to work on."""
from __future__ import annotations

from collections.abc import Callable

from pathlib import Path

from nicegui import run, ui

from state import (GPS_L1_HZ, REPO_ROOT, IQSource, default_source,  # sets sys.path
                   guess_format, state)

from bassanite.synthetic import read_truth, write_recording  # noqa: E402
from common.hackrf import read_sidecar  # noqa: E402

RECORDINGS_DIR = REPO_ROOT / "recordings"     # git-ignored


def synthetic_recording_path(cn0_dbhz: int, seconds: float, fs: float) -> Path:
    """Where a synthetic recording with these settings is written (and reused)."""
    return RECORDINGS_DIR / f"synthetic_{cn0_dbhz}dBHz_{seconds:g}s_{fs / 1e6:g}MSPS.bin"


FORMATS = {
    "int8": "int8: HackRF (hackrf_transfer)",
    "complex64": "complex64: GNU Radio / GNSS-SDR",
}


def _clean_path(text: str) -> str:
    # Windows "Copy as path" wraps the path in double quotes.
    return text.strip().strip('"').strip("'").strip()


def source_dialog(on_loaded: Callable[[], None]) -> ui.dialog:
    """Create the dialog. Call `.open()` on the result to show it."""
    current = state.source
    with ui.dialog() as dialog, ui.card().classes("w-[36rem] max-w-full"):
        ui.label("Load a raw IQ recording").classes("text-h6")
        ui.label("Give the path of a file on this computer. It is read where it is, "
                 "not copied.").classes("text-grey-8")

        path = ui.input("File path", value=str(current.path) if current else "",
                        placeholder=r"C:\...\recordings\capture.bin").classes("w-full")
        fmt = ui.select(FORMATS, label="Sample format",
                        value=current.fmt if current else "int8").classes("w-full")
        with ui.row().classes("w-full no-wrap gap-4"):
            fs = ui.number("Sample rate", value=(current.fs if current else 4e6) / 1e6,
                           min=0.1, step=0.5, suffix="MSPS").classes("flex-1")
            fc = ui.number("Centre frequency", format="%.3f", suffix="MHz",
                           value=(current.center_hz if current else GPS_L1_HZ) / 1e6) \
                .classes("flex-1")
        info = ui.label().classes("text-sm")

        def candidate() -> IQSource | None:
            text = _clean_path(path.value or "")
            if not text:
                return None
            return IQSource(text, fmt.value, (fs.value or 0) * 1e6, (fc.value or 0) * 1e6)

        def refresh() -> None:
            src = candidate()
            problems = ["Enter a file path."] if src is None else src.problems()
            if problems:
                info.set_text(problems[0])
                info.classes(replace="text-sm text-negative")
                load_button.disable()
            else:
                info.set_text(f"{src.n_samples:,} samples · {src.duration_s:.1f} s of signal")
                info.classes(replace="text-sm text-positive")
                load_button.enable()

        def on_path_change() -> None:
            text = _clean_path(path.value or "")
            if text:
                meta = read_sidecar(text)      # settings saved by the app's recorder
                if meta:
                    fmt.set_value(meta.get("format", "int8"))
                    fs.set_value(meta["fs"] / 1e6)
                    fc.set_value(meta["center_hz"] / 1e6)
                else:
                    fmt.set_value(guess_format(text))
            refresh()

        def load(src: IQSource | None) -> None:
            if src is None or src.problems():
                return
            state.source = src
            dialog.close()
            on_loaded()
            ui.notify(f"Loaded {src.describe()}", type="positive")

        with ui.expansion("Or make a synthetic recording", icon="science") \
                .classes("w-full border rounded-lg"):
            ui.label("Six satellites with known PRN, code phase and Doppler, in noise. "
                     "Lower the signal strength to watch them sink into the noise: the "
                     "Find satellites tab shows the true values next to what it finds.") \
                .classes("text-sm text-grey-8")
            with ui.row().classes("w-full items-center no-wrap gap-2"):
                ui.label("Signal strength (C/N0)").classes("text-sm w-44")
                cn0 = ui.slider(min=30, max=50, step=1, value=45).classes("flex-1")
                ui.label().bind_text_from(cn0, "value", lambda v: f"{v} dB-Hz") \
                    .classes("text-sm w-16")
            with ui.row().classes("w-full items-end no-wrap gap-4"):
                seconds = ui.number("Length", value=3, min=1, max=60, step=1,
                                    suffix="s").classes("flex-1")
                rate = ui.select({4e6: "4 MSPS", 2e6: "2 MSPS"}, value=4e6,
                                 label="Sample rate").classes("flex-1")
                make_button = ui.button("Make and load", icon="play_arrow") \
                    .props("no-caps")
            synth_info = ui.label("Real satellites are 37–43 dB-Hz in capture.bin. "
                                  "Generating takes about 2 s per second of signal.") \
                .classes("text-xs text-grey-7")

            async def make() -> None:
                c, s, f = int(cn0.value), float(seconds.value or 3), float(rate.value)
                path = synthetic_recording_path(c, s, f)
                make_button.disable()
                synth_info.set_text(f"Generating {path.name}…")
                try:
                    if not path.is_file() or read_truth(path) is None:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        await run.io_bound(write_recording, path, s, float(c), f)
                except OSError as err:
                    synth_info.set_text(f"Could not write the recording: {err}")
                    return
                finally:
                    make_button.enable()
                load(IQSource(path, "int8", f))

            make_button.on_click(make)

        with ui.row().classes("w-full justify-end gap-2"):
            bundled = default_source()
            if bundled is not None:
                ui.button("Use the bundled recording", icon="restore",
                          on_click=lambda: load(bundled)).props("flat no-caps")
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
            load_button = ui.button("Load", icon="check",
                                    on_click=lambda: load(candidate())).props("no-caps")

        path.on_value_change(on_path_change)
        fmt.on_value_change(refresh)
        fs.on_value_change(refresh)
        fc.on_value_change(refresh)
        refresh()
    return dialog
