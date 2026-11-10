"""Overview tab: home page and summary of the whole pipeline."""
from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from components import md
from stages import STAGES, furthest_done, results_history, stage_status
from state import IQSource, default_source, state

INTRO = """
**Bassanite turns raw radio samples into a GPS position, one visible step at a time.**
Each tab above is one stage of a GPS receiver, in order. Follow them from left to
right to see how a position is extracted from the raw signal. Switch on
**Show the math** in the top bar for the equations behind each step.
"""


def _pipeline(go_to: Callable[[str], None]) -> None:
    ui.label("The pipeline").classes("text-h6")
    with ui.row().classes("w-full items-stretch gap-2"):
        for i, stage in enumerate(STAGES):
            status = stage_status(stage, state.source)
            if i:
                ui.icon("arrow_forward", size="sm").classes("self-center text-grey-6")
            with ui.card().classes("w-44 cursor-pointer hover:shadow-lg") \
                    .props("flat bordered") \
                    .on("click", lambda s=stage: go_to(s.key)):
                ui.label(f"Step {stage.number}").classes("text-xs text-grey-7")
                ui.label(stage.title).classes("text-subtitle1 font-bold leading-tight")
                ui.label(stage.tagline).classes("text-xs text-grey-8")
                ui.label(f"{status.icon} {status.summary}").classes("text-sm break-all")
                if status.detail:
                    ui.label(status.detail).classes("text-xs text-grey-6 font-mono break-all")


def _input_card(open_source_dialog: Callable[[], None],
                go_to: Callable[[str], None]) -> None:
    with ui.card().classes("flex-1 min-w-[16rem]").props("flat bordered"):
        ui.label("Input signal").classes("text-subtitle1 font-bold")
        if state.source is None:
            ui.label("No recording loaded.").classes("text-grey-7")
        else:
            ui.label(state.source.describe())
            ui.label(str(state.source.path)).classes("text-xs text-grey-7 break-all")
        with ui.row().classes("gap-2"):
            ui.button("Load a recording", icon="folder_open",
                      on_click=open_source_dialog).props("no-caps")
            ui.button("Capture live", icon="sensors", on_click=lambda: go_to("signal")) \
                .props("no-caps outline")
        ui.label("Capture live opens the Signal tab, where the HackRF is controlled.") \
            .classes("text-xs text-grey-7")


def _result_card() -> None:
    with ui.card().classes("flex-1 min-w-[16rem]").props("flat bordered"):
        ui.label("Result").classes("text-subtitle1 font-bold")
        last = furthest_done(state.source)
        if last is None:
            ui.label("Nothing computed yet. Start by loading a recording.") \
                .classes("text-grey-7")
            return
        status = stage_status(last, state.source)
        ui.label(f"Furthest step reached: {last.number} · {last.title}")
        ui.label(status.summary).classes("text-grey-8")
        if last.number < len(STAGES):
            ui.label("A position needs stages 4 (Decode) and 5 (Position), which are "
                     "not built yet.").classes("text-xs text-grey-7")


def _history_card(on_changed: Callable[[], None]) -> None:
    with ui.card().classes("flex-1 min-w-[16rem]").props("flat bordered"):
        ui.label("Run history").classes("text-subtitle1 font-bold")
        runs = results_history()
        if not runs:
            ui.label("No results yet. Each recording gets its own folder, "
                     "results/<recording>/, when a step runs on it.").classes("text-grey-7")
            return

        def open_run(src: IQSource) -> None:
            state.source = src
            on_changed()

        for run in runs:
            current = (state.source is not None and run.source is not None
                       and state.source.path.resolve() == run.source.path.resolve())
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                with ui.column().classes("gap-0 flex-1 min-w-0"):
                    ui.label(run.name).classes("font-medium break-all")
                    steps = ", ".join(f"{s.number} {s.title}" for s in run.stages)
                    ui.label(f"{steps}" + (f" · {run.created}" if run.created else "")) \
                        .classes("text-xs text-grey-7")
                if current:
                    ui.label("loaded").classes("text-xs text-positive")
                elif run.source is not None:
                    ui.button("Open", on_click=lambda s=run.source: open_run(s)) \
                        .props("flat dense no-caps")
                else:
                    ui.label("recording missing").classes("text-xs text-grey-6")


def _first_run_checklist(open_source_dialog: Callable[[], None],
                         on_changed: Callable[[], None],
                         go_to: Callable[[str], None]) -> None:
    with ui.card().classes("w-full").props("flat bordered") \
            .bind_visibility_from(state, "mode", value="beginner"):
        ui.label("Getting started").classes("text-subtitle1 font-bold")
        ui.label("Option A: no hardware needed. Use a recording.").classes("font-medium")
        bundled = default_source()
        if bundled is not None:
            def use_bundled() -> None:
                state.source = bundled
                on_changed()
            ui.button("Use the bundled 30 s outdoor recording", icon="play_arrow",
                      on_click=use_bundled).props("no-caps")
        else:
            ui.button("Load a recording", icon="folder_open",
                      on_click=open_source_dialog).props("no-caps")

        ui.label("Option B: your own HackRF.").classes("font-medium q-mt-sm")
        ui.checkbox("HackRF plugged in and detected (run hackrf_info)")
        ui.checkbox("GPS patch antenna connected, bias-tee on to power its amplifier")
        ui.checkbox("Antenna outdoors, with a clear view of the sky")
        with ui.row().classes("items-center gap-2"):
            ui.button("Go to the Live HackRF controls", icon="sensors",
                      on_click=lambda: go_to("signal")).props("no-caps outline")
            ui.label("Use Check bias-tee there to confirm the antenna is powered.") \
                .classes("text-xs text-grey-7")


@ui.refreshable
def overview(go_to: Callable[[str], None], open_source_dialog: Callable[[], None],
             on_changed: Callable[[], None]) -> None:
    with ui.column().classes("w-full gap-4"):
        with ui.card().classes("w-full bg-blue-1").props("flat bordered") \
                .bind_visibility_from(state, "mode", value="beginner"):
            md(INTRO)
        _pipeline(go_to)
        with ui.row().classes("w-full items-stretch gap-4"):
            _input_card(open_source_dialog, go_to)
            _result_card()
            _history_card(on_changed)
        _first_run_checklist(open_source_dialog, on_changed, go_to)
