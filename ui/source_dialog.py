"""Dialog for choosing the raw IQ recording to work on."""
from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from state import GPS_L1_HZ, IQSource, default_source, guess_format, state

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
                fmt.set_value(guess_format(text))   # triggers refresh() via fmt
            refresh()

        def load(src: IQSource | None) -> None:
            if src is None or src.problems():
                return
            state.source = src
            dialog.close()
            on_loaded()
            ui.notify(f"Loaded {src.describe()}", type="positive")

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
